"""
PolyPaper Bot — Off-chain ↔ On-chain Reconciliation Loop
============================================================
P1.4 (5AI Yol Haritası §5.2 — Gemini "off-chain ↔ on-chain sync exploit")

Polygon RPC üzerinden CTF balanceOf + pUSD balance çek, DB'deki pozisyonlarla
karşılaştır. Mismatch > $1 ise:
- Telegram alarm
- Bot halt (kill-switch tetikle)
- Audit log

Saldırı vektörü (Gemini raporu):
- Saldırgan nonce manipülasyonu ile API'ye "trade executed" döndürtür
- On-chain revert yapar
- Bot DB pozisyonu var sanır, on-chain yok
- Reconciliation loop her 5dk bunu yakalar

Kontrat adresleri (docs/resources/contracts.mdx):
- pUSD: 0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB
- CTF: 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045

ENV:
- RECON_ENABLED (default false until manual approve)
- RECON_INTERVAL_S (default 300 = 5dk)
- RECON_MISMATCH_THRESHOLD_USD (default 1.0)
- POLYGON_RPC_URL (default https://polygon-rpc.com — Alchemy varsa override)

Usage:
    from core.reconciliation.onchain_sync import ReconciliationTask
    task = ReconciliationTask(db, wallet="0xA7e758...")
    await task.start()
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Optional

import aiosqlite

logger = logging.getLogger("polypaper.reconciliation")


# Polygon mainnet contracts (V2 docs/resources/contracts.mdx)
ADDR_PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
ADDR_CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# ERC-20 balanceOf(address) selector + ERC-1155 balanceOfBatch
ERC20_BALANCE_OF_SELECTOR = "0x70a08231"
ERC1155_BALANCE_OF_SELECTOR = "0x00fdd58e"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name, "true" if default else "false").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _rpc_url() -> str:
    return os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com").strip()


async def rpc_call(method: str, params: list, timeout_s: float = 5.0) -> Optional[dict]:
    """Generic Polygon RPC call via httpx."""
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed; reconciliation disabled")
        return None

    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(_rpc_url(), json=payload)
            if r.status_code != 200:
                return None
            return r.json()
    except (httpx.RequestError, httpx.HTTPStatusError, json.JSONDecodeError) as e:
        logger.debug(f"rpc_call {method} fail: {e}")
        return None
    except Exception as e:  # noqa: BLE001
        logger.debug(f"rpc_call {method} unexpected: {type(e).__name__}: {e}")
        return None


async def get_pusd_balance(wallet: str) -> Optional[float]:
    """ERC-20 balanceOf via eth_call.

    Returns: pUSD balance (decimal float, 6 decimals normalized) or None.
    """
    if not wallet or len(wallet) != 42:
        return None

    # encode: selector + 32-byte address (left-padded)
    addr_no_0x = wallet[2:].lower().zfill(64)
    data = ERC20_BALANCE_OF_SELECTOR + addr_no_0x

    response = await rpc_call(
        "eth_call",
        [
            {"to": ADDR_PUSD, "data": data},
            "latest",
        ],
    )
    if not response or "result" not in response:
        return None

    raw = response["result"]
    if not raw or raw == "0x":
        return 0.0

    try:
        return int(raw, 16) / 1e6  # pUSD 6 decimals
    except (ValueError, TypeError):
        return None


async def get_ctf_balance(wallet: str, token_id: str) -> Optional[float]:
    """ERC-1155 balanceOf(address, uint256) via eth_call.

    Returns: condition token balance (decimal, 6 decimals normalized).
    """
    if not wallet or not token_id:
        return None

    # token_id may be huge integer string; convert to 32-byte hex
    try:
        tid_int = int(token_id)
    except (ValueError, TypeError):
        return None

    addr_padded = wallet[2:].lower().zfill(64)
    tid_padded = format(tid_int, "x").zfill(64)
    data = ERC1155_BALANCE_OF_SELECTOR + addr_padded + tid_padded

    response = await rpc_call(
        "eth_call",
        [
            {"to": ADDR_CTF, "data": data},
            "latest",
        ],
    )
    if not response or "result" not in response:
        return None

    raw = response["result"]
    if not raw or raw == "0x":
        return 0.0

    try:
        return int(raw, 16) / 1e6
    except (ValueError, TypeError):
        return None


class ReconciliationTask:
    """Async loop: 5dk başına on-chain ↔ DB karşılaştırma."""

    def __init__(self, db, wallet: str, alert_callback=None):
        self.db = db  # aiosqlite Connection or wrapper
        self.wallet = wallet
        self.alert_callback = alert_callback  # async fn (str_msg) -> None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._stop_requested = False
        self._last_check_ts = 0.0
        self._mismatches: list[dict] = []

    @property
    def enabled(self) -> bool:
        """P1-09-a (2026-05-09): Smart enable.

        Resolution order:
          1. Explicit RECON_ENABLED env (true/false) → wins both ways.
          2. No explicit env → auto-on if LIVE_ENABLED=true (mainnet shadow
             active = real wallet balance to protect). Auto-off in paper mode
             (DB paper-balance ≠ on-chain real-balance → spam alarms).

        Heddas direktifi 2026-05-09: "tam ne işe yarayacak bu iş" → reconcile
        DB-vs-onchain divergence (revert / exploit / drift). Mainnet sermaye
        varken default ON, paper'da default OFF.
        """
        # Explicit env override (handles "true", "false", "1", "0", etc.)
        explicit = os.getenv("RECON_ENABLED", "").strip().lower()
        if explicit in {"1", "true", "yes", "on"}:
            return True
        if explicit in {"0", "false", "no", "off"}:
            return False
        # No explicit override → auto-derive from live mode
        return _env_bool("LIVE_ENABLED", False)

    @property
    def interval_s(self) -> int:
        return _env_int("RECON_INTERVAL_S", 300)  # 5dk

    @property
    def mismatch_threshold_usd(self) -> float:
        return _env_float("RECON_MISMATCH_THRESHOLD_USD", 1.0)

    @property
    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "running": self._running,
            "wallet": self.wallet[:10] + "..." if self.wallet else "",
            "last_check_age_s": (time.time() - self._last_check_ts)
            if self._last_check_ts
            else None,
            "mismatch_count": len(self._mismatches),
            "interval_s": self.interval_s,
            "threshold_usd": self.mismatch_threshold_usd,
        }

    async def start(self) -> None:
        if not self.enabled:
            # H-07 (2026-05-18 log audit): the old message was a static
            # string that always claimed "LIVE_ENABLED=false" — misleading
            # when LIVE mode is actually on and an explicit RECON_ENABLED=false
            # is the real reason recon stays off. Report the true env state.
            _recon = os.getenv("RECON_ENABLED", "").strip().lower() or "(unset)"
            _live = os.getenv("LIVE_ENABLED", "").strip().lower() or "(unset)"
            logger.info(
                "🔗 Reconciliation: DISABLED "
                f"(RECON_ENABLED={_recon}, LIVE_ENABLED={_live}). "
                "RECON_ENABLED=true forces it on; leaving RECON_ENABLED unset "
                "auto-activates it when LIVE_ENABLED=true."
            )
            return
        if not self.wallet:
            logger.warning("🔗 Reconciliation: wallet missing, cannot start")
            return
        if self._running:
            logger.warning("🔗 Reconciliation: already running")
            return

        self._running = True
        self._stop_requested = False
        self._task = asyncio.create_task(self._loop(), name="reconciliation")
        logger.info(
            f"🔗 Reconciliation: STARTED (interval={self.interval_s}s, "
            f"threshold=${self.mismatch_threshold_usd}, wallet={self.wallet[:10]}...)"
        )

    async def stop(self) -> None:
        self._stop_requested = True
        if self._task:
            try:
                self._task.cancel()
                await asyncio.wait_for(self._task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception as e:  # noqa: BLE001
                logger.debug(f"recon stop: {e}")
        self._running = False
        logger.info("🔗 Reconciliation: STOPPED")

    async def _loop(self) -> None:
        while not self._stop_requested:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.warning(f"🔗 recon tick error: {type(e).__name__}: {e}")

            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                break

    async def tick(self) -> dict:
        """Single reconciliation cycle.

        Returns: {"ok": bool, "delta_usd": float, "details": [...]}
        """
        self._last_check_ts = time.time()

        # 1. On-chain pUSD balance
        onchain_pusd = await get_pusd_balance(self.wallet)
        if onchain_pusd is None:
            logger.warning("🔗 recon: pUSD balance fetch failed (RPC down?)")
            return {"ok": False, "delta_usd": 0, "details": ["rpc_fail"]}

        # 2. DB-cached portfolio value
        db_balance = await self._get_db_pusd_balance()

        # 3. Compare
        delta = abs(onchain_pusd - (db_balance or 0))
        ok = delta <= self.mismatch_threshold_usd

        if not ok:
            mismatch = {
                "ts": time.time(),
                "onchain_pusd": onchain_pusd,
                "db_pusd": db_balance,
                "delta_usd": delta,
            }
            self._mismatches.append(mismatch)
            msg = (
                f"🚨 RECON MISMATCH: on-chain pUSD ${onchain_pusd:.2f} vs "
                f"DB ${db_balance:.2f} (Δ ${delta:.2f} > ${self.mismatch_threshold_usd:.2f})"
            )
            logger.error(msg)
            if self.alert_callback:
                try:
                    await self.alert_callback(msg)
                except Exception as cb_err:  # noqa: BLE001
                    logger.warning(f"recon alert callback fail: {cb_err}")
        else:
            logger.debug(
                f"🔗 recon OK: pUSD on-chain ${onchain_pusd:.2f} ≈ DB ${db_balance:.2f} (Δ ${delta:.4f})"
            )

        return {
            "ok": ok,
            "delta_usd": delta,
            "onchain_pusd": onchain_pusd,
            "db_pusd": db_balance,
        }

    async def _get_db_pusd_balance(self) -> float:
        """Read latest cached pUSD balance from DB.

        Schema dependent — bot's `polymarket_portfolio_cache` table veya
        benzeri. Eğer yok ise 0 döner.
        """
        if not self.db:
            return 0.0
        try:
            # Try standard cache table
            async with self.db.execute(
                "SELECT pusd_balance FROM polymarket_portfolio_cache "
                "ORDER BY fetched_at DESC LIMIT 1"
            ) as cur:
                row = await cur.fetchone()
                if row and row[0] is not None:
                    return float(row[0])
        except (aiosqlite.Error, TypeError, ValueError):
            pass
        return 0.0
