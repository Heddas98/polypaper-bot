"""
PolyPaper Bot - Live Trader (Phase 34: Shadow Mode)

DUAL MODE: Paper + Real run side-by-side.
- Paper: All strategies, virtual USDC ($10K+)
- Real: Only best 2 strategies, real USDC ($1.49)

Real trade data is logged to live_trades table.
Paper mode reads live_trades for training/calibration.

Toggle via Telegram button or LIVE_ENABLED env var.
Credentials from Replit Secrets ONLY — NEVER in code.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import aiosqlite  # T1.4 Faz 1: narrow DB exception handling

try:
    from telegram.error import BadRequest as TelegramBadRequest, TelegramError
except ImportError:  # pragma: no cover - python-telegram-bot is a hard dep
    class TelegramBadRequest(Exception):  # type: ignore[no-redef]
        ...
    class TelegramError(Exception):  # type: ignore[no-redef]
        ...

logger = logging.getLogger("polypaper.core.live")

# ═══ SAFETY LIMITS (ENV-override, runtime re-read via /env_toggle) ═══
# T7.6 A5 (2026-04-22): module-top floats caused the same ghost-toggle
# defect as T6.1 PNL_PAUSE / T6.4 auto_optimizer — hot-tunes via
# ``/env_toggle`` would patch ``os.environ`` but the constants, imported
# once, never re-read. These helpers re-read on every call, so operator
# tightens/loosens take effect immediately on the next ``maybe_mirror``.
#
# ``MAX_CONCURRENT`` removed — dead constant; concurrency is enforced by
# ``if self._open:`` single-slot guard at ``maybe_mirror`` (L223).
def _get_max_trade() -> float:
    """``LIVE_MAX_TRADE`` — max $ per live trade (default 1.00)."""
    try:
        return float(os.getenv("LIVE_MAX_TRADE", "1.00"))
    except (TypeError, ValueError):
        return 1.00


def _get_max_daily_loss() -> float:
    """``LIVE_MAX_DAILY_LOSS`` — daily loss cutoff in abs $ (default 1.00)."""
    try:
        return float(os.getenv("LIVE_MAX_DAILY_LOSS", "1.00"))
    except (TypeError, ValueError):
        return 1.00


def _get_min_signal() -> float:
    """``LIVE_MIN_SIGNAL`` — min signal_score to mirror (default 0.75)."""
    try:
        return float(os.getenv("LIVE_MIN_SIGNAL", "0.75"))
    except (TypeError, ValueError):
        return 0.75


def _get_min_odds() -> float:
    """``LIVE_MIN_ODDS`` — min odds to mirror (default 0.75)."""
    try:
        return float(os.getenv("LIVE_MIN_ODDS", "0.75"))
    except (TypeError, ValueError):
        return 0.75

# LIVE_STRATEGIES: whitelist of paper strategies that may mirror to
# real-money ($1/trade) via py-clob-client. Selection criterion: proven
# WR + positive EV in paper.
#
# Parity principle (Epic 4 T4.4, 2026-04-20 confirmed): paper and live
# share the SAME governance. If `auto_optimizer` stops a strategy in
# paper (PnL < adaptive threshold, rolling WR kill, loss-streak), the
# same strategy becomes ineligible for live mirroring upstream — engine
# only feeds `maybe_mirror` from active strategies.
#
# NOT identical to `ai_brain.PROTECTED_STRATEGIES` (ai_brain.py:41).
# Those two sets serve different purposes:
#   LIVE_STRATEGIES        — "which strategies get to trade real money"
#   PROTECTED_STRATEGIES   — "which strategies are shielded from LLM noise"
# A strategy can be LIVE without being PROTECTED — e.g. AI_F_* strategies
# are experimental; AI Brain retains the right to stop/tune them on fresh
# performance evidence.
LIVE_STRATEGIES = {
    "M_BTC_5m_any_0.92",       # 35t 89% WR +$139 EV:+3.98  [PROTECTED]
    "BTC High-Threshold Pure",  # 30t 93% WR +$73  EV:+2.43  [PROTECTED]
    "AI_F_BTC_5m_up_0.38",     # 21t 86% WR +$104 EV:+4.93  [experimental]
}


class LiveTrader:

    def __init__(self, db=None, bot_app=None, settings=None):
        self.db = db
        self.bot_app = bot_app
        self.settings = settings
        self._enabled = False
        self._paused = False  # Telegram toggle
        self._daily_pnl = 0.0
        self._daily_trades = 0
        self._daily_date = ""
        self._open: Optional[dict] = None
        self._total_spent = 0.0
        self._total_pnl = 0.0
        self._budget = float(os.getenv("LIVE_BUDGET", "1.49"))
        self._trade_count = 0
        # Phase 49 A-01: derived L2 credentials cache (derived from POLYGON_PRIVATE_KEY)
        self._api_creds = None  # type: Optional[object]
        self._auth_verified = False

    async def start(self):
        """
        Phase 49 A-01 fix:
        - Required: POLYGON_PRIVATE_KEY + POLYGON_WALLET
        - Optional: stored POLYMARKET_API_KEY/SECRET/PASSPHRASE (fallback only)
        - Derive L2 creds on startup via create_or_derive_api_creds() and verify auth
          before enabling. If derive/verify fails, trader stays DISABLED with clear log.
        """
        pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
        wallet = os.getenv("POLYGON_WALLET", "").strip()
        enabled_env = os.getenv("LIVE_ENABLED", "false").lower() == "true"

        if not pk or not wallet:
            logger.info("🔴 Live Trader: POLYGON_PRIVATE_KEY/WALLET missing — DISABLED")
            return

        # Restore state from DB BEFORE deciding enable (so budget/pnl are correct in logs)
        await self._restore_state()

        # Derive + verify L2 auth (runs in executor since py-clob-client is sync)
        # T1.4 Faz 1: inner exceptions are caught inside _derive_and_verify_sync;
        # only loop/threadpool-level failures can surface here.
        try:
            loop = asyncio.get_running_loop()
            ok, detail = await loop.run_in_executor(None, self._derive_and_verify_sync, pk, wallet)
        except RuntimeError as e:
            ok, detail = False, f"derive runtime error: {e}"

        if not ok:
            self._enabled = False
            self._auth_verified = False
            logger.warning(
                f"🔴 Live Trader: L2 auth FAILED — DISABLED "
                f"(wallet {wallet[:10]}... | {detail})"
            )
            return

        self._auth_verified = True
        self._enabled = enabled_env
        logger.info(
            f"{'🟢' if enabled_env else '🟡'} Live Trader: "
            f"{'SHADOW ACTIVE' if enabled_env else 'STANDBY (LIVE_ENABLED=false)'} "
            f"| {wallet[:10]}... | auth=✅ | "
            f"Budget ${self._budget - self._total_spent:.2f}"
        )

    def _derive_and_verify_sync(self, pk: str, wallet: str) -> tuple[bool, str]:
        """
        Derive L2 creds from private key via create_or_derive_api_creds(),
        cache them on self, and verify by calling a cheap authenticated endpoint.
        Returns (ok, detail_string). Runs sync in executor.
        """
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
        except ImportError as e:
            return (False, f"py-clob-client not installed: {e}")

        try:
            client = ClobClient(
                "https://clob.polymarket.com",
                key=pk, chain_id=137,
                signature_type=0,  # EOA
                funder=wallet,
            )
        except Exception as e:  # noqa: BLE001
            # T1.4 Faz 1: catch-all kept — py-clob-client ctor can raise ValueError,
            # TypeError, or network errors from dependency libs. Emit type for triage.
            # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
            return (False, f"client init failed ({type(e).__name__}): {e}")

        # Try derive first
        try:
            derived = client.create_or_derive_api_creds()
            client.set_api_creds(derived)
            self._api_creds = derived
            detail_derived = (
                f"derived key={str(getattr(derived, 'api_key', ''))[:8]}..."
            )
        except Exception as e:  # noqa: BLE001
            # T1.4 Faz 1: catch-all kept — derive path wraps HTTP + signature
            # internals from py-clob-client. Fallback path below is intentional.
            # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
            # Fallback: stored triplet (may be stale — will be logged)
            stored_key = os.getenv("POLYMARKET_API_KEY", "").strip()
            stored_secret = os.getenv("POLYMARKET_API_SECRET", "").strip()
            stored_pass = os.getenv("POLYMARKET_PASSPHRASE", "").strip()
            if not all([stored_key, stored_secret, stored_pass]):
                return (False, f"derive failed ({type(e).__name__}: {e}) and no fallback triplet")
            try:
                creds = ApiCreds(
                    api_key=stored_key,
                    api_secret=stored_secret,
                    api_passphrase=stored_pass,
                )
                client.set_api_creds(creds)
                self._api_creds = creds
                detail_derived = f"fallback stored key={stored_key[:8]}... (derive err: {type(e).__name__}: {e})"
            except Exception as e2:  # noqa: BLE001
                # T1.4 Faz 1: catch-all kept — both derive and stored-creds failed.
                # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
                return (False, f"both derive ({type(e).__name__}: {e}) and fallback ({type(e2).__name__}: {e2}) failed")

        # Verify with a cheap authenticated call (get_trades with limit)
        try:
            from py_clob_client.clob_types import TradeParams
            _ = client.get_trades(TradeParams())
            return (True, detail_derived)
        except Exception as e:  # noqa: BLE001
            # T1.4 Faz 1: catch-all kept — get_trades can raise HTTP/auth/network.
            # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
            return (False, f"{detail_derived} | verify failed ({type(e).__name__}): {e}")

    def is_enabled(self) -> bool:
        # Phase 49 A-01: also require verified L2 auth before mirroring any trade
        return self._enabled and not self._paused and self._auth_verified

    def toggle(self) -> bool:
        """Toggle pause state. Returns new state."""
        self._paused = not self._paused
        logger.info(f"💰 Live Trader {'PAUSED' if self._paused else 'RESUMED'}")
        return not self._paused

    async def maybe_mirror(self, strategy_label: str, signal_score: float,
                           direction: str, token_id: str, odds: float,
                           slug: str) -> Optional[dict]:
        if not self.is_enabled():
            return None
        if strategy_label not in LIVE_STRATEGIES:
            return None
        if signal_score < _get_min_signal():
            return None
        if odds < _get_min_odds():
            return None

        self._maybe_reset_daily()
        if self._daily_pnl <= -_get_max_daily_loss():
            logger.info(f"  🔴 LIVE HALT: daily loss ${self._daily_pnl:.2f}")
            return None
        if self._open:
            return None

        remaining = self._budget - self._total_spent
        if remaining < 0.10:
            logger.info("  🔴 LIVE: Budget exhausted")
            return None

        amount = min(_get_max_trade(), remaining)
        return await self._place(token_id, direction, amount, odds, slug, strategy_label, signal_score)

    async def _place(self, token_id, direction, amount, odds, slug, strategy, signal) -> Optional[dict]:
        try:
            order_result = await self._execute_clob(token_id, amount, odds, direction)
            oid = order_result.get("id", "") if order_result else ""
            status = order_result.get("status", "failed") if order_result else "failed"

            if status in ("placed", "mock", "filled"):
                self._open = {
                    "token_id": token_id, "direction": direction,
                    "amount": amount, "entry_odds": odds, "slug": slug,
                    "strategy": strategy, "signal": signal, "order_id": oid,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                self._total_spent += amount
                self._daily_trades += 1
                self._trade_count += 1

                # Log to DB
                if self.db:
                    await self.db.conn.execute(
                        """INSERT INTO live_trades (strategy_label, slug, direction, token_id,
                            entry_price, amount, signal_score, order_id, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                        (strategy, slug, direction, token_id, odds, amount, signal, oid,
                         datetime.now(timezone.utc).isoformat()))
                    await self.db.conn.commit()

                live_mode = "🟢 REAL" if status != "mock" else "🟡 MOCK"
                logger.info(f"  💰 {live_mode} TRADE! {strategy} {direction.upper()} "
                           f"{slug[:30]} ${amount:.2f} @{odds:.3f} sig={signal:.2f}")
                # Phase 49 P0-05: HTML-escape untrusted strategy label + market slug
                from telegram_bot.templates.safe_html import esc, esc_code
                await self._notify(
                    f"💰 <b>LIVE TRADE!</b> ({esc(status)})\n"
                    f"📋 {esc(strategy)}\n"
                    f"{'🟢' if direction=='up' else '🔴'} {esc(direction.upper())} @{odds:.3f}\n"
                    f"💵 ${amount:.2f} | sig={signal:.2f}\n"
                    f"<code>{esc_code(slug[:40])}</code>")
                return self._open
            else:
                logger.warning(f"  ⚠️ LIVE FAIL: {slug} — {status}")
                return None

        except Exception as e:  # noqa: BLE001
            # T1.4 Faz 1: catch-all kept — _place body spans CLOB exec + DB write +
            # telegram notify. Use logger.exception to capture traceback for triage.
            # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
            logger.exception(f"Live place failed ({type(e).__name__}): {e}")
            return None

    async def check_settlement(
        self, slug: str, won: bool, pnl_paper: float,
        paper_amount: float = 0.0,
    ):
        """Called when paper trade settles. Close live if matching.

        Phase 52 fix: paper_amount is now passed from the execution row
        instead of using a hardcoded $25.  Fallback to self._open["amount"]
        (1:1 scale) if caller doesn't provide it.
        """
        if not self._open or self._open["slug"] != slug:
            return

        live_amount = self._open["amount"]
        if paper_amount <= 0:
            paper_amount = live_amount  # safe fallback: 1:1 scale
        scale = live_amount / max(paper_amount, 0.01)
        live_pnl = round(pnl_paper * scale, 4)

        self._daily_pnl += live_pnl
        self._total_pnl += live_pnl

        # Update DB
        if self.db:
            try:
                # P1-01 FIX: Standard SQLite doesn't support UPDATE...ORDER BY...LIMIT.
                # Use subquery to find the most recent unsettled row for this slug.
                # P1-02 FIX: Record actual entry_odds as exit reference instead of
                # hardcoded 1.0/0.0 — enables accurate live vs paper comparison.
                actual_exit_price = self._open.get("entry_odds", 1.0 if won else 0.0)
                await self.db.conn.execute(
                    """UPDATE live_trades SET pnl=?, result=?, paper_pnl=?, exit_price=?, settled_at=?
                    WHERE rowid = (
                        SELECT rowid FROM live_trades
                        WHERE slug=? AND settled_at IS NULL
                        ORDER BY created_at DESC LIMIT 1
                    )""",
                    (live_pnl, "won" if won else "lost", pnl_paper,
                     actual_exit_price,
                     datetime.now(timezone.utc).isoformat(), slug))
                await self.db.conn.commit()
            except aiosqlite.Error as e:
                # T1.4 Faz 1: narrowed from bare Exception — only DB errors expected here.
                logger.debug(f"Live settle DB: {e}")

        emoji = "🟢" if won else "🔴"
        logger.info(f"  {emoji} LIVE SETTLE: {slug[:30]} Live=${live_pnl:+.4f} Paper=${pnl_paper:+.2f}")
        # Phase 49 P0-05: numeric-only below, slug not interpolated — safe
        await self._notify(
            f"{emoji} <b>LIVE SONUC</b>\n"
            f"Live PnL: <b>${live_pnl:+.4f}</b>\n"
            f"Paper PnL: ${pnl_paper:+.2f}\n"
            f"Kalan: ${self._budget - self._total_spent:.2f}\n"
            f"Toplam: ${self._total_pnl:+.4f}")

        self._open = None
        await self._save_state()

    async def _execute_clob(self, token_id, amount, price, direction) -> Optional[dict]:
        # T1.4 Faz 1: inner CLOB exceptions caught in _sync_order (L363).
        # Only loop/threadpool-level failures can surface here; let CancelledError
        # propagate so cooperative cancellation still works.
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._sync_order, token_id, amount, price)
        except RuntimeError as e:
            logger.error(f"CLOB exec: {e}")
            return None

    def _sync_order(self, token_id, amount, price) -> Optional[dict]:
        """
        Phase 49 A-01: Uses cached self._api_creds from start()/derive path.
        If cache is empty (e.g. trader was enabled without going through start()),
        derives on the fly to avoid hard-failing on first trade.
        """
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import OrderArgs
            from py_clob_client.order_builder.constants import BUY

            pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
            wallet = os.getenv("POLYGON_WALLET", "").strip()

            if not pk or not wallet:
                return {"id": "", "status": "error:missing POLYGON_PRIVATE_KEY/WALLET"}

            client = ClobClient(
                "https://clob.polymarket.com",
                key=pk, chain_id=137,
                signature_type=0,  # EOA
                funder=wallet,
            )

            # Prefer cached creds from start(); derive on the fly as a safety net
            creds = self._api_creds
            if creds is None:
                try:
                    creds = client.create_or_derive_api_creds()
                    self._api_creds = creds
                    logger.info(
                        f"  🔑 derived L2 creds on demand key="
                        f"{str(getattr(creds, 'api_key', ''))[:8]}..."
                    )
                except Exception as e:  # noqa: BLE001
                    # T1.4 Faz 1: catch-all kept — on-demand derive wraps HTTP + sig.
                    # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
                    return {"id": "", "status": f"error:derive failed ({type(e).__name__}): {e}"}

            client.set_api_creds(creds)

            order_args = OrderArgs(
                price=price,
                size=round(amount / price, 2),
                side=BUY,
                token_id=token_id)

            signed = client.create_order(order_args)
            result = client.post_order(signed)

            if result and result.get("orderID"):
                logger.info(f"  ✅ CLOB order: {result['orderID'][:16]}")
                return {"id": result["orderID"], "status": "placed"}
            return {"id": "", "status": f"rejected:{result}"}

        except ImportError:
            logger.warning("py-clob-client not installed — mock order")
            return {"id": f"MOCK_{token_id[:8]}", "status": "mock"}
        except Exception as e:  # noqa: BLE001
            # T1.4 Faz 1: catch-all kept — _sync_order body spans CLOB signature,
            # HTTP post_order, and response parsing. Use logger.exception for traceback.
            # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
            logger.exception(f"CLOB order failed ({type(e).__name__}): {e}")
            return {"id": "", "status": f"error ({type(e).__name__}):{e}"}

    async def get_comparison(self) -> dict:
        """Get paper vs real comparison data for dashboard."""
        if not self.db:
            return {}
        try:
            live = await self.db.conn.execute_fetchall(
                """SELECT COUNT(*), COALESCE(SUM(pnl),0), COALESCE(SUM(paper_pnl),0),
                    COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0)
                FROM live_trades WHERE settled_at IS NOT NULL""")
            r = live[0] if live else (0, 0, 0, 0)

            # Recent trades
            recent = await self.db.conn.execute_fetchall(
                """SELECT strategy_label, direction, entry_price, amount, pnl, paper_pnl, result,
                    created_at FROM live_trades ORDER BY created_at DESC LIMIT 10""")

            return {
                "total_trades": r[0],
                "live_pnl": round(r[1] or 0, 4),
                "paper_pnl_equiv": round(r[2] or 0, 2),
                "wins": r[3],
                "wr": round(r[3]/r[0]*100, 0) if r[0] > 0 else 0,
                "recent": [
                    {"strat": t[0], "dir": t[1], "price": t[2], "amt": t[3],
                     "live_pnl": t[4], "paper_pnl": t[5], "result": t[6],
                     "ts": str(t[7])[:16]} for t in (recent or [])
                ],
            }
        except aiosqlite.Error as e:
            # T1.4 Faz 1: narrowed from bare Exception — only DB read errors expected.
            return {"error": str(e)}

    async def _save_state(self):
        if not self.db: return
        try:
            state = json.dumps({
                "total_spent": self._total_spent, "total_pnl": self._total_pnl,
                "trade_count": self._trade_count})
            await self.db.conn.execute(
                "INSERT OR REPLACE INTO bot_settings (key, value, updated_at) VALUES (?,?,?)",
                ("live_state", state, datetime.now(timezone.utc).isoformat()))
            await self.db.conn.commit()
        except (aiosqlite.Error, TypeError) as e:
            # T1.4 Faz 1: narrowed from bare Exception — DB errors (aiosqlite) or
            # json.dumps TypeError (non-serializable field). Upgrade from silent pass
            # to a warning so regressions aren't invisible in logs.
            logger.warning(f"Live _save_state failed ({type(e).__name__}): {e}")

    async def _restore_state(self):
        if not self.db: return
        try:
            r = await self.db.conn.execute_fetchall(
                "SELECT value FROM bot_settings WHERE key='live_state'")
            if r:
                s = json.loads(r[0][0])
                self._total_spent = s.get("total_spent", 0)
                self._total_pnl = s.get("total_pnl", 0)
                self._trade_count = s.get("trade_count", 0)
                logger.info(f"  💰 Live state restored: spent=${self._total_spent:.2f} pnl=${self._total_pnl:+.4f}")
        except (aiosqlite.Error, json.JSONDecodeError, KeyError, IndexError) as e:
            # T1.4 Faz 1: narrowed from bare Exception — DB miss, corrupted JSON,
            # missing row index, or missing dict key. Upgrade pass to warning.
            logger.warning(f"Live _restore_state skipped ({type(e).__name__}): {e}")

    def _maybe_reset_daily(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_date != today:
            self._daily_pnl = 0.0
            self._daily_trades = 0
            self._daily_date = today

    def get_status(self) -> dict:
        wallet = os.getenv("POLYGON_WALLET", "")
        return {
            "enabled": self._enabled,
            "paused": self._paused,
            "auth_verified": self._auth_verified,
            "active": self.is_enabled(),
            "wallet": f"{wallet[:6]}...{wallet[-4:]}" if wallet else "N/A",
            "total_spent": round(self._total_spent, 4),
            "total_pnl": round(self._total_pnl, 4),
            "daily_pnl": round(self._daily_pnl, 4),
            "daily_trades": self._daily_trades,
            "trade_count": self._trade_count,
            "open": bool(self._open),
            "open_detail": self._open,
            "budget": self._budget,
            "remaining": round(self._budget - self._total_spent, 4),
        }

    async def _notify(self, text):
        aid = getattr(self.settings, 'ADMIN_TELEGRAM_ID', None) if self.settings else None
        if not aid or not self.bot_app: return
        # T1.4 Faz 1: narrowed from bare Exception.
        # BadRequest = HTML parse error → fallback to plain text. Other telegram
        # errors (NetworkError, RetryAfter, Forbidden) still caught by second arm.
        try:
            await self.bot_app.bot.send_message(chat_id=aid, text=text, parse_mode="HTML")
        except TelegramBadRequest:
            try:
                await self.bot_app.bot.send_message(chat_id=aid, text=text)
            except TelegramError as e:
                logger.debug("Fallback notify failed: %s", e)

    def get_trade_history(self) -> list[dict]:
        """Return trade history from in-memory state for live_handler."""
        # Build from get_comparison data or DB cache
        # For now return basic data from internal state
        history = []
        if hasattr(self, '_recent_trades'):
            history = self._recent_trades
        return history

    async def load_trade_history(self) -> list[dict]:
        """Load from DB — called by live_handler for full history."""
        if not self.db:
            return []
        try:
            rows = await self.db.conn.execute_fetchall(
                """SELECT strategy_label, direction, entry_price, amount, pnl, paper_pnl, result, created_at
                FROM live_trades ORDER BY created_at DESC LIMIT 20""")
            return [
                {"strategy": r[0], "direction": r[1], "entry_odds": r[2],
                 "amount": r[3], "pnl": r[4] or 0, "pnl_paper": r[5] or 0,
                 "result": r[6] or "", "ts": str(r[7])[:16]}
                for r in (rows or [])
            ]
        except aiosqlite.Error as e:
            # T1.4 Faz 1: narrowed from bare Exception — only DB read errors expected.
            logger.debug(f"load_trade_history DB: {e}")
            return []
