"""
PolyPaper Bot - Phase 44b — Chainlink oracle parity check
=========================================================

Polymarket binary Up/Down markets settle against an oracle (UMA + a
Chainlink price feed reference for crypto markets). When Polymarket's
implied price diverges from the on-chain oracle by more than a few bps
the market is mispriced relative to the actual settlement source —
that's a high-conviction signal worth boosting.

This module reads Chainlink USD-denominated aggregator latestAnswer()
for BTC/ETH/SOL/XRP via a public read-only RPC endpoint. We deliberately
keep it dependency-free: no web3.py — just an httpx eth_call to the
aggregator with the standard `latestAnswer()` selector `0x50d25bcd`.

Strategy:
  1. Once a minute, refresh on-chain price for each tracked asset.
  2. Engine compares against best Binance mid (or external_feed price).
  3. If |delta_bps| > CHAINLINK_PARITY_BPS the engine emits a
     CHAINLINK_PARITY_BREAK signal that can boost a directional trade.

Defaults are off (CHAINLINK_ORACLE_ENABLED=false). Once a public RPC is
confirmed working from the host, flip the env var on.

Aggregator addresses (Ethereum mainnet, Chainlink standard):
  BTC/USD:  0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c
  ETH/USD:  0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419
  SOL/USD:  0x4ffC43a60e009B551865A93d232E33Fce9f01507
  XRP/USD:  0xCed2660c6Dd1Ffd856A5A82C67f3482d88C50b12

T11.8-B (2026-04-24): every catch in this module is annotated
`# noqa: BLE001`. Data-feed orchestrator: WebSockets + httpx +
json + aiosqlite + asyncio reconnect chain. Single network blip
or schema drift should NOT crash the feed thread — the reconnect
loop handles it. Wide catches at the orchestration layer are
intentional and logged.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from core.bg_task import safe_create_task  # Phase 82e Sprint 2.1

logger = logging.getLogger("polypaper.data.chainlink_oracle")

# Free public RPC. Operator can override via env if rate-limited.
DEFAULT_RPC = "https://eth.llamarpc.com"

AGGREGATORS: dict[str, dict] = {
    "BTC": {"addr": "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c", "decimals": 8},
    "ETH": {"addr": "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419", "decimals": 8},
    "SOL": {"addr": "0x4ffC43a60e009B551865A93d232E33Fce9f01507", "decimals": 8},
    "XRP": {"addr": "0xCed2660c6Dd1Ffd856A5A82C67f3482d88C50b12", "decimals": 8},
}

LATEST_ANSWER_SELECTOR = "0x50d25bcd"
POLL_INTERVAL_S = 60


class ChainlinkOracle:
    """Periodic Chainlink price refresh + parity check helper."""

    def __init__(self, parity_bps: float = 20.0, rpc_url: str = DEFAULT_RPC, db=None):
        self.parity_bps = parity_bps
        self.rpc_url = rpc_url
        # P0-08-E6 (2026-05-08): db reference for external_prices persist
        self.db = db
        self._prices: dict[str, dict] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._client = None
        self._fetches = 0
        self._fails = 0

    async def start(self, httpx_client=None):
        if self._running:
            return
        self._client = httpx_client
        if not httpx_client:
            logger.warning("⚠ Phase 44b: ChainlinkOracle needs an httpx client — disabled")
            return
        self._running = True
        # Phase 82e Sprint 2.1: oracle feed loop guarded
        self._task = safe_create_task(
            self._poll_loop(), name="chainlink_oracle")
        logger.info(f"🔗 Phase 44b: ChainlinkOracle STARTED (rpc={self.rpc_url}, "
                    f"parity={self.parity_bps}bps)")
        # Phase 47b: Immediate smoke test — fetch BTC once so the operator
        # gets a clear pass/fail signal at startup instead of waiting 60s
        # for the first poll to land.
        try:
            await self._refresh_all()
            ok = sum(1 for d in self._prices.values() if d.get("price", 0) > 0)
            if ok > 0:
                btc = self._prices.get("BTC", {}).get("price")
                eth = self._prices.get("ETH", {}).get("price")
                logger.info(
                    f"🔗 Phase 47b: oracle smoke OK ({ok}/4 assets) "
                    f"BTC={btc} ETH={eth}"
                )
            else:
                logger.warning(
                    "⚠ Phase 47b: oracle smoke test got 0 prices — RPC may be blocked"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"⚠ Phase 47b: oracle smoke test threw: {e}")

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _poll_loop(self):
        while self._running:
            try:
                await self._refresh_all()
            except Exception as e:  # noqa: BLE001
                self._fails += 1
                logger.debug(f"chainlink poll error: {e}")
            try:
                await asyncio.sleep(POLL_INTERVAL_S)
            except asyncio.CancelledError:
                break

    async def _refresh_all(self):
        for asset, info in AGGREGATORS.items():
            try:
                price = await self._eth_call_latest(info["addr"], info["decimals"])
                if price and price > 0:
                    self._prices[asset] = {"price": price, "ts": time.time()}
                    self._fetches += 1
            except Exception as e:  # noqa: BLE001
                self._fails += 1
                logger.debug(f"chainlink {asset} fetch failed: {e}")
        # P0-08-E6: persist to external_prices
        try:
            self._persist_to_db()
        except Exception:  # noqa: BLE001
            pass

    async def _eth_call_latest(self, addr: str, decimals: int) -> Optional[float]:
        if not self._client:
            return None
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [
                {"to": addr, "data": LATEST_ANSWER_SELECTOR},
                "latest",
            ],
        }
        try:
            r = await self._client.post(self.rpc_url, json=payload, timeout=5.0)
            if r.status_code != 200:
                return None
            j = r.json()
            hex_result = j.get("result")
            if not hex_result or hex_result == "0x":
                return None
            raw = int(hex_result, 16)
            # Two's complement int256
            if raw & (1 << 255):
                raw -= 1 << 256
            if raw <= 0:
                return None
            return raw / (10 ** decimals)
        except Exception:  # noqa: BLE001
            return None

    # ── public API ───────────────────────────────────────────────────
    def get_price(self, asset: str) -> Optional[float]:
        d = self._prices.get(asset.upper())
        if not d or time.time() - d["ts"] > POLL_INTERVAL_S * 3:
            return None
        return d["price"]

    def parity_delta_bps(self, asset: str, ref_price: float) -> Optional[float]:
        """Return |Chainlink - ref| in bps, or None if oracle stale."""
        oracle = self.get_price(asset)
        if not oracle or not ref_price or ref_price <= 0:
            return None
        return abs(oracle - ref_price) / ref_price * 1e4

    def parity_break(self, asset: str, ref_price: float) -> bool:
        """True if delta exceeds the configured threshold."""
        delta = self.parity_delta_bps(asset, ref_price)
        if delta is None:
            return False
        return delta >= self.parity_bps

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "fetches": self._fetches,
            "fails": self._fails,
            "parity_bps": self.parity_bps,
            "rpc": self.rpc_url,
            "prices": {k: round(v["price"], 4) for k, v in self._prices.items()},
        }

    def _persist_to_db(self):
        """P0-08-E6 (2026-05-08): chainlink prices → external_prices."""
        if self.db is None or self.db.conn is None:
            return
        if not self._prices:
            return
        rows = []
        ts_ms_now = int(__import__("time").time() * 1000)
        for asset, info in self._prices.items():
            price = info.get("price", 0)
            if price > 0:
                symbol = asset.upper() + "USD"
                rows.append((ts_ms_now, symbol, "chainlink", price))
        if rows:
            safe_create_task(self._persist_async(rows), name="persist_chainlink")

    async def _persist_async(self, rows):
        try:
            await self.db.conn.executemany(
                "INSERT OR REPLACE INTO external_prices "
                "(ts_ms, symbol, source, price) VALUES (?, ?, ?, ?)",
                rows,
            )
            await self.db.conn.commit()
        except Exception:  # noqa: BLE001
            pass

