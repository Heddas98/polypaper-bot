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
from datetime import UTC, datetime

import aiosqlite  # T1.4 Faz 1: narrow DB exception handling

try:
    from telegram.error import BadRequest as TelegramBadRequest, TelegramError
except ImportError:  # pragma: no cover - python-telegram-bot is a hard dep

    class TelegramBadRequest(Exception):  # type: ignore[no-redef]
        ...

    class TelegramError(Exception):  # type: ignore[no-redef]
        ...


logger = logging.getLogger("polypaper.core.live")


# ═══ Cross-module shared creds cache (P1.X Cloudflare 403 fix) ═══
# Boot'ta live_trader.start() derive PASS yapar; sonuçtaki creds bu global'e
# yazılır. data/polymarket_portfolio.py ve diğer modüller buradan okur,
# kendi derive denemesi yapmaz → Cloudflare 403 spam yok.
SHARED_CREDS_CACHE = {
    "creds": None,
    "fetched_at": 0.0,
    "wallet": "",
}


def get_shared_creds():
    """Diğer modüller bu fonksiyonu çağırıp derived creds'i alır.

    Returns: (creds, fetched_ts) tuple veya (None, 0).
    """
    return SHARED_CREDS_CACHE.get("creds"), SHARED_CREDS_CACHE.get("fetched_at", 0)


def set_shared_creds(creds, wallet: str = ""):
    """live_trader.start() derive PASS sonrası çağrılır."""
    import time

    SHARED_CREDS_CACHE["creds"] = creds
    SHARED_CREDS_CACHE["fetched_at"] = time.time()
    SHARED_CREDS_CACHE["wallet"] = wallet


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


def _get_live_budget() -> float:
    """``LIVE_BUDGET`` — lifetime budget cap for shadow live trading
    (default 1.49, matches the $1.49 USDC deposited into the Phase 48
    derived-L2 wallet). Runtime re-read per call so an admin
    ``/envt LIVE_BUDGET 5.00`` takes effect immediately on the next
    ``maybe_mirror`` budget gate check — same ghost-toggle class as
    T6.1 PNL_PAUSE and T6.4 rolling-WR knobs (T11.2 [B] 2026-04-22).
    """
    try:
        return float(os.getenv("LIVE_BUDGET", "1.49"))
    except (TypeError, ValueError):
        return 1.49


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
    "M_BTC_5m_any_0.92",  # 35t 89% WR +$139 EV:+3.98  [PROTECTED]
    "BTC High-Threshold Pure",  # 30t 93% WR +$73  EV:+2.43  [PROTECTED]
    "AI_F_BTC_5m_up_0.38",  # 21t 86% WR +$104 EV:+4.93  [experimental]
    # ── P0-08-H placeholders (2026-05-08) ──────────────────────────────
    # Yeni TF/asset kombinasyonları için strategy ID convention:
    #   M_{ASSET}_{TF}_any_0.NN   — Manual baseline (threshold=0.NN)
    #   AI_F_{ASSET}_{TF}_up_0.NN  — AI fusion (autopilot suggests)
    # Lifecycle: yeni strategy 0 trade ile exploration phase başlar.
    # >=20 trade + WR>=55% + PnL>0 → evaluation; >=50t + WR>=60% → proven.
    # **Live para için 100+ paper trade + Heddas manuel onayı gerekir.**
    # Şu an aşağıdaki kombinasyonlar paper-only (LIVE whitelist'e EKLENMEDI):
    #   - M_BTC_15m_any_0.92    (BTC 15m)
    #   - M_ETH_15m_any_0.92    (ETH 15m)
    #   - M_SOL_15m_any_0.92    (SOL 15m)
    #   - M_XRP_15m_any_0.92    (XRP 15m)
    #   - M_BTC_1h_any_0.92     (BTC 1h, series_id=10114)
    #   - M_BTC_24h_any_0.92    (BTC 24h, series_id=41)
    # Heddas onayıyla LIVE'a geçirilince yukarıya whitelist'e eklenecek.
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
        self._open: dict | None = None
        self._total_spent = 0.0
        self._total_pnl = 0.0
        # T11.2 [B] (2026-04-22): `self._budget` is now a @property that
        # re-reads ``LIVE_BUDGET`` on every access (see below). The
        # previous ctor-fixed assignment froze the budget at bot-start
        # time, so ``/envt LIVE_BUDGET 5.00`` patched os.environ but the
        # trader kept using the old ceiling — same ghost-toggle class as
        # T6.1 PNL_PAUSE and T6.4 rolling-WR knobs.
        self._trade_count = 0
        # Phase 49 A-01: derived L2 credentials cache (derived from POLYGON_PRIVATE_KEY)
        self._api_creds = None  # type: Optional[object]
        self._auth_verified = False
        # 2026-04-28 Heddas docs audit fix Bulgu 2 (tick_size + neg_risk):
        # Per-token metadata cache so we don't hammer get_tick_size /
        # get_neg_risk on every order placement. token_id → {"tick_size": str,
        # "neg_risk": bool}. Polymarket docs: order rejection codes
        # INVALID_ORDER_MIN_TICK_SIZE / Neg Risk CTF Exchange contract require
        # explicit pass-through of these per-market parameters.
        self._token_meta: dict = {}

    # T11.2 [B] (2026-04-22): T6.1 parity property — read ``LIVE_BUDGET``
    # from env on every access so ``/envt LIVE_BUDGET <X>`` takes effect
    # at runtime. Read-only; ``_total_spent`` is the only mutable
    # counterpart. DB-persisted state (see _save_state) stores
    # total_spent / total_pnl / trade_count — budget is a pure env knob.
    @property
    def _budget(self) -> float:
        return _get_live_budget()

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
            # 2026-04-30 P0.11: V1 → V2 migration (Heddas direktifi "en güncel ol")
            from py_clob_client_v2 import ApiCreds, ClobClient
        except ImportError as e:
            return (False, f"py-clob-client-v2 not installed: {e}")

        try:
            # 2026-04-28 Heddas docs audit fix (Bulgu 1): Polymarket.com Rabby
            # login akışı CREATE2 ile Gnosis Safe Proxy deploy ediyor — fonlar
            # bu Proxy'de tutuluyor (deposit/withdraw addr), trade'ler proxy
            # adına execute. signature_type=2 (GNOSIS_SAFE) + funder=ProxyAddr.
            # ENV-tunable: CLOB_SIGNATURE_TYPE=0 (EOA) ile geri sarılabilir
            # eğer kullanıcı doğrudan EOA wallet kullanıyorsa.
            sig_type = int(os.getenv("CLOB_SIGNATURE_TYPE", "2"))
            client = ClobClient(
                "https://clob.polymarket.com",
                key=pk,
                chain_id=137,
                signature_type=sig_type,
                funder=wallet,
            )
        except Exception as e:  # noqa: BLE001
            # T1.4 Faz 1: catch-all kept — py-clob-client ctor can raise ValueError,
            # TypeError, or network errors from dependency libs. Emit type for triage.
            # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
            return (False, f"client init failed ({type(e).__name__}): {e}")

        # 2026-04-30 P1.X Cloudflare polish: stored creds VARSA derive ATLA.
        # Derive endpoint Cloudflare bot-detect tetikliyor; stored creds yeterli.
        stored_key = os.getenv("POLYMARKET_API_KEY", "").strip()
        stored_secret = os.getenv("POLYMARKET_API_SECRET", "").strip()
        stored_pass = os.getenv("POLYMARKET_PASSPHRASE", "").strip()
        force_derive = os.getenv("CLOB_FORCE_DERIVE", "false").lower() in {"1", "true", "yes"}
        detail_derived = ""

        # PATH 1: Stored creds varsa direkt set + verify (derive bypass)
        if all([stored_key, stored_secret, stored_pass]) and not force_derive:
            try:
                creds = ApiCreds(
                    api_key=stored_key,
                    api_secret=stored_secret,
                    api_passphrase=stored_pass,
                )
                client.set_api_creds(creds)
                self._api_creds = creds
                detail_derived = f"stored ENV creds (key={stored_key[:8]}...)"
            except Exception as e_stored:  # noqa: BLE001
                detail_derived = f"stored set fail ({type(e_stored).__name__}); will derive"
                # Fall through to derive

        # PATH 2: Stored creds yok veya force → derive
        if not self._api_creds:
            try:
                # 2026-04-30 P0.11 V2 fix: V1 `_creds()` → V2 `_key()`
                derived = client.create_or_derive_api_key()
                client.set_api_creds(derived)
                self._api_creds = derived
                detail_derived = f"derived key={str(getattr(derived, 'api_key', ''))[:8]}..."
            except Exception as e:  # noqa: BLE001
                # T1.4 Faz 1: catch-all kept — derive path wraps HTTP + signature.
                # T7.6 Faz 3 + 2026-04-30 P1.X: Cloudflare 403 graceful handling.
                err_str = str(e)
                if "403" in err_str or "Cloudflare" in err_str:
                    return (
                        False,
                        "Cloudflare 403 derive fail (no stored creds fallback). "
                        "Set POLYMARKET_API_KEY/SECRET/PASSPHRASE in .env to bypass.",
                    )
                # Generic fallback
                if not all([stored_key, stored_secret, stored_pass]):
                    return (
                        False,
                        f"derive failed ({type(e).__name__}: {e}) and no fallback triplet",
                    )
                try:
                    creds = ApiCreds(
                        api_key=stored_key,
                        api_secret=stored_secret,
                        api_passphrase=stored_pass,
                    )
                    client.set_api_creds(creds)
                    self._api_creds = creds
                    detail_derived = f"fallback stored (derive err: {type(e).__name__})"
                except Exception as e2:  # noqa: BLE001
                    return (
                        False,
                        f"both derive ({type(e).__name__}: {e}) and fallback ({type(e2).__name__}: {e2}) failed",
                    )

        # Verify with a cheap authenticated call (get_trades with limit)
        try:
            # 2026-04-30 P0.11: V1 → V2 migration
            from py_clob_client_v2 import TradeParams

            _ = client.get_trades(TradeParams())
            # ✅ PASS: shared cache'e yaz (cross-module Cloudflare bypass)
            set_shared_creds(self._api_creds, wallet=wallet)
            return (True, detail_derived)
        except Exception as e:  # noqa: BLE001
            # 2026-04-30 P1.X Cloudflare polish: stored creds verify FAIL ise
            # (eski/expired creds), derive fallback dene.
            err_str = str(e)
            is_auth_fail = "401" in err_str or "Unauthorized" in err_str or "Invalid" in err_str
            stored_was_used = self._api_creds and detail_derived.startswith("stored")
            if is_auth_fail and stored_was_used:
                logger.warning(
                    "  ⚠ stored ENV creds invalid (verify 401); falling back to derive..."
                )
                self._api_creds = None  # reset
                try:
                    derived = client.create_or_derive_api_key()
                    client.set_api_creds(derived)
                    self._api_creds = derived
                    derive_detail = f"derived after stored-fail key={str(getattr(derived, 'api_key', ''))[:8]}..."
                    # Re-verify with new creds
                    _ = client.get_trades(TradeParams())
                    logger.info("  ✅ derive fallback PASS — stored creds were stale")
                    # ✅ PASS: shared cache'e yaz (cross-module bypass)
                    set_shared_creds(self._api_creds, wallet=wallet)
                    return (True, derive_detail)
                except Exception as e_derive:  # noqa: BLE001
                    derive_err = str(e_derive)
                    if "403" in derive_err or "Cloudflare" in derive_err:
                        return (
                            False,
                            f"{detail_derived} verify 401 + derive Cloudflare 403. "
                            f"Manuel: ENV POLYMARKET_API_KEY/SECRET/PASSPHRASE update gerek "
                            f"veya CLOB_FORCE_DERIVE=true ile retry",
                        )
                    return (
                        False,
                        f"{detail_derived} verify 401 + derive fallback "
                        f"({type(e_derive).__name__}: {derive_err[:120]})",
                    )
            # T1.4 Faz 1: catch-all kept — get_trades can raise HTTP/auth/network.
            return (False, f"{detail_derived} | verify failed ({type(e).__name__}): {e}")

    def is_enabled(self) -> bool:
        # Phase 49 A-01: also require verified L2 auth before mirroring any trade
        return self._enabled and not self._paused and self._auth_verified

    def toggle(self) -> bool:
        """Toggle pause state. Returns new state."""
        self._paused = not self._paused
        logger.info(f"💰 Live Trader {'PAUSED' if self._paused else 'RESUMED'}")
        return not self._paused

    async def reset_budget(self) -> float:
        """Operator-triggered live budget reset (via /live UI, 2-tap confirmed).

        Zeroes the lifetime spend counter so the full ``LIVE_BUDGET``
        ceiling is available again, and persists immediately so a restart
        cannot resurrect the old counter. Returns the prior spend (for the
        confirmation message).

        Deliberately does NOT touch ``_total_pnl`` / ``_daily_pnl`` — only
        the spend gate is reset; PnL history stays intact. This is the
        only spend-counter mutation outside ``_place``.
        """
        old_spent = self._total_spent
        self._total_spent = 0.0
        await self._save_state()
        logger.warning(
            f"💰 LIVE BUDGET RESET by operator — spent ${old_spent:.2f} → "
            f"$0.00 | full ${self._budget:.2f} ceiling available again"
        )
        return old_spent

    async def maybe_mirror(
        self,
        strategy_label: str,
        signal_score: float,
        direction: str,
        token_id: str,
        odds: float,
        slug: str,
    ) -> dict | None:
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
        return await self._place(
            token_id, direction, amount, odds, slug, strategy_label, signal_score
        )

    async def _place(
        self, token_id, direction, amount, odds, slug, strategy, signal
    ) -> dict | None:
        try:
            order_result = await self._execute_clob(token_id, amount, odds, direction)
            oid = order_result.get("id", "") if order_result else ""
            status = order_result.get("status", "failed") if order_result else "failed"

            if status in ("placed", "mock", "filled"):
                self._open = {
                    "token_id": token_id,
                    "direction": direction,
                    "amount": amount,
                    "entry_odds": odds,
                    "slug": slug,
                    "strategy": strategy,
                    "signal": signal,
                    "order_id": oid,
                    "ts": datetime.now(UTC).isoformat(),
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
                        (
                            strategy,
                            slug,
                            direction,
                            token_id,
                            odds,
                            amount,
                            signal,
                            oid,
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                    await self.db.conn.commit()

                live_mode = "🟢 REAL" if status != "mock" else "🟡 MOCK"
                logger.info(
                    f"  💰 {live_mode} TRADE! {strategy} {direction.upper()} "
                    f"{slug[:30]} ${amount:.2f} @{odds:.3f} sig={signal:.2f}"
                )
                # Phase 49 P0-05: HTML-escape untrusted strategy label + market slug
                from telegram_bot.templates.safe_html import esc, esc_code

                await self._notify(
                    f"💰 <b>LIVE TRADE!</b> ({esc(status)})\n"
                    f"📋 {esc(strategy)}\n"
                    f"{'🟢' if direction=='up' else '🔴'} {esc(direction.upper())} @{odds:.3f}\n"
                    f"💵 ${amount:.2f} | sig={signal:.2f}\n"
                    f"<code>{esc_code(slug[:40])}</code>"
                )
                return self._open
            else:
                logger.warning(f"  ⚠️ LIVE FAIL: {slug} — {status}")
                return None

        except Exception as e:  # noqa: BLE001
            # T1.4 Faz 1: catch-all kept — _place body spans CLOB exec + DB write +
            # telegram notify. Use logger.exception to capture traceback for triage.
            # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
            logger.exception(f"Live place failed ({type(e).__name__}): {e}")
            # M-05 (2026-05-15 ultra-audit): surface the failure to the
            # operator. A silent _place exception means a live mirror was
            # attempted but no position opened — previously visible only by
            # reading logs. Only the exception TYPE is sent (M-01 doctrine:
            # never raw str(e) to the user). notify is best-effort.
            try:
                await self._notify(
                    "⚠️ <b>LIVE TRADE BAŞARISIZ</b>\n"
                    f"Hata tipi: <code>{type(e).__name__}</code>\n"
                    "Pozisyon AÇILMADI — detay log'da."
                )
            except Exception:  # noqa: BLE001 — notify is best-effort
                pass
            return None

    async def check_settlement(
        self,
        slug: str,
        won: bool,
        pnl_paper: float,
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
                    (
                        live_pnl,
                        "won" if won else "lost",
                        pnl_paper,
                        actual_exit_price,
                        datetime.now(UTC).isoformat(),
                        slug,
                    ),
                )
                await self.db.conn.commit()
            except aiosqlite.Error as e:
                # T1.4 Faz 1: narrowed from bare Exception — only DB errors expected here.
                logger.debug(f"Live settle DB: {e}")

        emoji = "🟢" if won else "🔴"
        logger.info(
            f"  {emoji} LIVE SETTLE: {slug[:30]} Live=${live_pnl:+.4f} Paper=${pnl_paper:+.2f}"
        )
        # Phase 49 P0-05: numeric-only below, slug not interpolated — safe
        await self._notify(
            f"{emoji} <b>LIVE SONUC</b>\n"
            f"Live PnL: <b>${live_pnl:+.4f}</b>\n"
            f"Paper PnL: ${pnl_paper:+.2f}\n"
            f"Kalan: ${self._budget - self._total_spent:.2f}\n"
            f"Toplam: ${self._total_pnl:+.4f}"
        )

        self._open = None
        await self._save_state()

    async def _execute_clob(self, token_id, amount, price, direction) -> dict | None:
        # T1.4 Faz 1: inner CLOB exceptions caught in _sync_order (L363).
        # Only loop/threadpool-level failures can surface here; let CancelledError
        # propagate so cooperative cancellation still works.
        # P2-04 (2026-05-11): Sentry transaction for live trade execution.
        # Zero-cost when SENTRY_DSN unset. When set, this captures CLOB
        # latency + side metadata for performance investigation.
        from core.observability.sentry_tx import sentry_transaction

        with sentry_transaction(
            op="live_trader.execute_buy",
            name=f"clob_{direction}_{token_id[:8]}",
        ) as _tx:
            if _tx is not None:
                _tx.set_data("amount_usd", amount)
                _tx.set_data("price", price)
                _tx.set_data("direction", direction)
                _tx.set_data("token_id_prefix", token_id[:12])
            try:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(
                    None, self._sync_order, token_id, amount, price
                )
            except RuntimeError as e:
                logger.error(f"CLOB exec: {e}")
                return None

    async def execute_market_order(
        self,
        side: str,
        coin: str,
        direction: str,
        amount: float,
        tf: str = "5m",
    ) -> dict:
        """Manuel market BUY/SELL — Heddas 2026-05-05 direktifi.

        live_handler.py UI'ından çağrılır. Strateji bypass — doğrudan Polymarket
        FOK order. Auth + budget + min/max checks yapılır, sonra _execute_clob.

        Args:
            side: "BUY" or "SELL"
            coin: "BTC" / "ETH" / "SOL" / "XRP"
            direction: "UP" or "DOWN"
            amount: USDC tutarı
            tf: "5m" / "15m" / "1h" / "24h" — P0-08-C (2026-05-08): 1h ve 24h
                Polymarket'ta sadece BTC için Up/Down market sunulur, scanner
                matrix bunu yansıtır. Geçersiz TF → market not found döner.

        Returns:
            {"status": "placed/filled/mock/error", "order_id": str,
             "detail": str, "price": float, "shares": float}
        """
        # ── Pre-flight checks ─────────────────────────────────────
        if not self._auth_verified:
            return {
                "status": "error",
                "detail": "auth_verified=False — /live ekranında 'Live Aç' tıkla",
            }

        if amount <= 0:
            return {"status": "error", "detail": f"amount must be > 0 (got {amount})"}

        max_market = float(os.getenv("LIVE_MAX_MARKET_TRADE", "25.0"))
        if amount > max_market:
            return {
                "status": "error",
                "detail": f"amount ${amount:.2f} > LIVE_MAX_MARKET_TRADE=${max_market:.2f}",
            }

        # Budget check
        remaining = self._budget - self._total_spent
        if amount > remaining and side == "BUY":
            return {
                "status": "error",
                "detail": f"yetersiz risk limit: kalan ${remaining:.2f} < istek ${amount:.2f}",
            }

        # ── Find market via scanner ────────────────────────────────
        scanner = getattr(self, "_engine_scanner", None)
        # Wired by engine.start() if available — fallback: caller passes it.
        if scanner is None:
            return {"status": "error", "detail": "scanner not wired (engine ref missing)"}

        try:
            market = scanner.get_current_market(coin, tf)
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "detail": f"scanner: {type(e).__name__}: {e}"}

        if not market:
            return {
                "status": "error",
                "detail": f"{coin} {tf} active market not found "
                f"(matrix support: {coin} {tf} kombinasyonu Polymarket'ta var mı?)",
            }

        slug = market.get("slug", "")
        token_ids = market.get("clobTokenIds")
        if isinstance(token_ids, str):
            try:
                token_ids = json.loads(token_ids)
            except (ValueError, TypeError):
                token_ids = []
        if not token_ids or len(token_ids) < 2:
            return {"status": "error", "detail": "tokens not parseable"}

        # P0-08-C (2026-05-08): UP=token[0], DOWN=token[1] convention
        # 5m, 15m, 1h, 24h Up/Down market'lerinin tümünde aynı (canlı doğrulandı:
        # outcomes=["Up","Down"] sırası deterministik, clobTokenIds aynı sırayla).
        token_id = token_ids[0] if direction.upper() == "UP" else token_ids[1]

        # ── Get current price ────────────────────────────────────
        odds = scanner.get_current_odds(slug) if hasattr(scanner, "get_current_odds") else None
        if not odds:
            return {"status": "error", "detail": "odds unavailable"}

        if direction.upper() == "UP":
            price = float(odds.get("up_odds", 0))
        else:
            price = float(odds.get("down_odds", 0))
        if price <= 0.001 or price >= 0.999:
            return {"status": "error", "detail": f"invalid price {price}"}

        # ── Execute ──────────────────────────────────────────────
        result = await self._execute_clob(
            token_id,
            amount,
            price,
            "buy" if side == "BUY" else "sell",
        )
        if not result:
            return {"status": "failed", "detail": "_execute_clob returned None"}

        # Update state
        oid = result.get("id", "")
        status = result.get("status", "failed")
        if status in ("placed", "filled", "mock", "matched"):
            self._total_spent += amount
            self._daily_trades += 1
            self._trade_count += 1
            await self._save_state()
            logger.info(
                f"💰 MANUAL {side} {coin} {direction} {tf} ${amount:.2f} "
                f"@{price:.3f} → {status} (id={oid[:12]})"
            )

        return {
            "status": status,
            "order_id": oid,
            "detail": result.get("detail", "manual market order"),
            "price": price,
            "shares": round(amount / price, 4) if price > 0 else 0,
        }

    def _sync_order(self, token_id, amount, price) -> dict | None:
        """
        Phase 49 A-01: Uses cached self._api_creds from start()/derive path.
        If cache is empty (e.g. trader was enabled without going through start()),
        derives on the fly to avoid hard-failing on first trade.
        """
        try:
            # 2026-04-30 P0.11: V1 → V2 migration (Heddas direktifi "en güncel ol")
            from py_clob_client_v2 import (
                ClobClient,
                OrderArgs,
                OrderType,
                PartialCreateOrderOptions,
            )
            from py_clob_client_v2.order_builder.constants import BUY

            pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
            wallet = os.getenv("POLYGON_WALLET", "").strip()

            if not pk or not wallet:
                return {"id": "", "status": "error:missing POLYGON_PRIVATE_KEY/WALLET"}

            # 2026-04-28 Heddas docs audit fix (Bulgu 1): aynı signature_type
            # convention as start() above. Default 2 = GNOSIS_SAFE.
            sig_type = int(os.getenv("CLOB_SIGNATURE_TYPE", "2"))
            client = ClobClient(
                "https://clob.polymarket.com",
                key=pk,
                chain_id=137,
                signature_type=sig_type,
                funder=wallet,
            )

            # Prefer cached creds from start(); derive on the fly as a safety net
            creds = self._api_creds
            if creds is None:
                try:
                    # 2026-04-30 P0.11 V2 fix: V1 `_creds` → V2 `_key`
                    creds = client.create_or_derive_api_key()
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

            # 2026-04-28 Heddas docs audit Bulgu 2: per-market tick_size +
            # neg_risk explicit. Polymarket docs:
            #   - tick_size 0.1/0.01/0.001/0.0001 (market-specific)
            #   - neg_risk True → multi-outcome (3+) markets use Neg Risk CTF
            #     Exchange contract; binary BTC Up/Down → False.
            # Cache per-token to avoid extra REST calls every trade.
            meta = self._token_meta.get(token_id)
            if meta is None:
                try:
                    ts = client.get_tick_size(token_id)
                    nr = client.get_neg_risk(token_id)
                    meta = {"tick_size": str(ts), "neg_risk": bool(nr)}
                    self._token_meta[token_id] = meta
                    logger.info(
                        f"  📐 token meta cached: tick={meta['tick_size']} "
                        f"neg_risk={meta['neg_risk']}"
                    )
                except Exception as _meta_err:  # noqa: BLE001
                    # SDK / HTTP failure → defaults safe for crypto BTC Up/Down
                    # (tick=0.01, neg_risk=False). Rejected orders surface in
                    # post_order response.
                    logger.warning(
                        f"  ⚠ token meta fetch failed ({type(_meta_err).__name__}): "
                        f"{_meta_err}; defaulting tick=0.01 neg_risk=False"
                    )
                    meta = {"tick_size": "0.01", "neg_risk": False}

            # 2026-04-29 Phase D Bulgu 9: pre-flight balance/allowance check.
            # Polymarket docs `/trading/orders/overview#allowances` — BUY için
            # funder pUSD bakiyesi >= amount olmalı. Eksikse `INVALID_ORDER_
            # NOT_ENOUGH_BALANCE` reject. Pre-flight check ile silent reject'i
            # diagnostic log'a çeviriyoruz; opsiyonel skip (BALANCE_PREFLIGHT
            # env false ise kontrol atlanır).
            if os.getenv("BALANCE_PREFLIGHT", "true").lower() == "true":
                try:
                    # 2026-04-30 P0.11: V1 → V2 migration. py-clob-client-v2
                    # 1.0.0 BalanceAllowanceParams dataclass'ı korudu (V2 backward
                    # compat). Eski SDK fallback için ImportError yakala.
                    from py_clob_client_v2 import (
                        AssetType,
                        BalanceAllowanceParams,
                    )

                    bal_params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
                    bal = client.get_balance_allowance(bal_params)
                    avail = float(bal.get("balance", 0) or 0) / 1e6  # raw USDC.e units

                    # 2026-05-05 V2 API fix (Heddas debug session):
                    # V1: bal["allowance"] (tekil string)
                    # V2: bal["allowances"] (çoğul dict per-spender)
                    # Backward-compat: V1 fallback for older SDK responses.
                    allow_val = 0.0
                    if "allowances" in bal and isinstance(bal["allowances"], dict):
                        # V2: en yüksek allowance'ı al (genelde hepsi MAX_UINT256
                        # zaten — 3 contract approve sonrası uniform).
                        max_raw = max(
                            (int(v or 0) for v in bal["allowances"].values()),
                            default=0,
                        )
                        allow_val = float(max_raw) / 1e6
                    elif "allowance" in bal:
                        # V1 backward-compat
                        allow_val = float(bal.get("allowance", 0) or 0) / 1e6

                    allow = allow_val
                    if avail < amount:
                        logger.warning(
                            f"  ⚠ pre-flight: bakiye yetersiz ${avail:.2f} < ${amount:.2f} "
                            f"(allowance ${allow:.2f}) — order skip"
                        )
                        return {
                            "id": "",
                            "status": f"skip:insufficient_balance:${avail:.2f}<${amount:.2f}",
                        }
                    if allow < amount:
                        logger.warning(
                            f"  ⚠ pre-flight: allowance yetersiz ${allow:.2f} < ${amount:.2f} "
                            f"— Polymarket UI'dan allowance approve gerekli"
                        )
                        return {
                            "id": "",
                            "status": f"skip:insufficient_allowance:${allow:.2f}<${amount:.2f}",
                        }
                except ImportError:
                    # Eski SDK fallback — dict pattern dene, hata olursa skip
                    try:
                        bal = client.get_balance_allowance({"asset_type": "COLLATERAL"})
                        avail = float(bal.get("balance", 0) or 0) / 1e6
                        # V2 plural fallback in old SDK fallback path too
                        if "allowances" in bal and isinstance(bal["allowances"], dict):
                            max_raw = max(
                                (int(v or 0) for v in bal["allowances"].values()),
                                default=0,
                            )
                            allow = float(max_raw) / 1e6
                        else:
                            allow = float(bal.get("allowance", 0) or 0) / 1e6
                        if avail < amount:
                            return {
                                "id": "",
                                "status": f"skip:insufficient_balance:${avail:.2f}<${amount:.2f}",
                            }
                        if allow < amount:
                            return {
                                "id": "",
                                "status": f"skip:insufficient_allowance:${allow:.2f}<${amount:.2f}",
                            }
                    except Exception as _bal_err2:  # noqa: BLE001
                        logger.debug(
                            f"  ⓘ pre-flight skip dict-fallback ({type(_bal_err2).__name__}): {_bal_err2}"
                        )
                except (AttributeError, KeyError, ValueError, TypeError) as _bal_err:
                    # Response format değişmiş — log, devam (CLOB'a güven)
                    logger.debug(f"  ⓘ pre-flight skip ({type(_bal_err).__name__}): {_bal_err}")

            # 2026-05-05 V2 SDK fix: MarketOrderArgs + create_and_post_market_order.
            # Polymarket V2 maker_amount max 2 decimals + taker_amount max 4
            # decimals constraint. Limit FOK (OrderArgs+price+size) hesabıyla
            # `price * size` 3+ decimal çıkıyor → reject. Market order method'u
            # SDK otomatik decimal-correct yapar (amount sabit, price 0=auto).
            builder_code = os.getenv("POLYMARKET_BUILDER_CODE", "").strip()
            try:
                from py_clob_client_v2 import (
                    MarketOrderArgs,
                    PartialCreateOrderOptions,
                )

                market_kwargs = dict(
                    token_id=token_id,
                    amount=round(float(amount), 2),  # USDC, 2 decimals max
                    side=BUY,
                    order_type=OrderType.FOK,
                )
                if builder_code:
                    market_kwargs["builder_code"] = builder_code
                market_args = MarketOrderArgs(**market_kwargs)
                opts = PartialCreateOrderOptions(
                    tick_size=meta["tick_size"],
                    neg_risk=meta["neg_risk"],
                )
                logger.info(
                    f"  📤 V2 market order: amount=${amount:.2f} "
                    f"tick={meta['tick_size']} neg_risk={meta['neg_risk']}"
                )
                result = client.create_and_post_market_order(market_args, opts)
            except (ImportError, AttributeError) as _mo_err:
                # SDK eski versiyon — limit FOK fallback (V1 pattern)
                logger.warning(
                    f"  ⚠ MarketOrderArgs unavailable ({type(_mo_err).__name__}): "
                    f"{_mo_err}; falling back to limit FOK"
                )
                order_args_kwargs = dict(
                    price=price,
                    size=round(amount / price, 2),
                    side=BUY,
                    token_id=token_id,
                )
                if builder_code:
                    order_args_kwargs["builder_code"] = builder_code
                order_args = OrderArgs(**order_args_kwargs)
                try:
                    from py_clob_client_v2 import PartialCreateOrderOptions

                    opts = PartialCreateOrderOptions(
                        tick_size=meta["tick_size"],
                        neg_risk=meta["neg_risk"],
                    )
                    signed = client.create_order(order_args, options=opts)
                except (TypeError, ImportError):
                    signed = client.create_order(order_args)
                try:
                    result = client.post_order(signed, OrderType.FOK)
                except TypeError:
                    result = client.post_order(signed)

            # 2026-04-29 Phase C Bulgu 5 fix: post-order heartbeat. Polymarket
            # cancels open orders 10s+5s after last heartbeat. Even though FOK
            # orders fill or cancel immediately, this heartbeat covers
            # marketable-but-delayed scenarios + signals session liveness.
            try:
                client.post_heartbeat("")
            except (AttributeError, Exception) as _hb_err:  # noqa: BLE001
                # SDK eski version'larda post_heartbeat yok; warn-and-continue.
                logger.debug(f"post_heartbeat unavailable: {_hb_err}")

            if result and result.get("orderID"):
                logger.info(f"  ✅ CLOB order: {result['orderID'][:16]}")
                return {"id": result["orderID"], "status": "placed"}

            # 2026-04-29 Phase D Bulgu 11: explicit error code mapping for
            # diagnostic clarity. Polymarket docs error codes:
            # https://docs.polymarket.com/trading/orders/overview#error-messages
            err_msg = (result or {}).get("errorMsg", "") if isinstance(result, dict) else ""
            err_msg_str = str(err_msg) if err_msg else f"unknown:{result}"
            # Surface common reject reasons with friendly hints
            _hint = ""
            if "MIN_TICK_SIZE" in err_msg_str:
                _hint = " (tick_size mismatch — meta cache invalidate önerisi)"
            elif "NOT_ENOUGH_BALANCE" in err_msg_str:
                _hint = " (Polymarket bakiye/allowance yetersiz — Bulgu 9 pre-flight check)"
            elif "MIN_SIZE" in err_msg_str:
                _hint = " (order size eşiği altında — amount artırın)"
            elif "DUPLICATED" in err_msg_str:
                _hint = " (aynı order önceden post edildi — race condition?)"
            elif "EXPIRATION" in err_msg_str:
                _hint = " (GTD expiration past — saat senkron?)"
            elif "POST_ONLY" in err_msg_str:
                _hint = " (post_only + market type çelişkisi)"
            elif "FOK_ORDER_NOT_FILLED" in err_msg_str:
                _hint = " (FOK marketable ama orderbook yetersiz — beklenebilir)"
            elif "MARKET_NOT_READY" in err_msg_str:
                _hint = " (market henüz live değil — wait + retry)"
            logger.warning(f"  ❌ CLOB reject: {err_msg_str}{_hint}")
            return {"id": "", "status": f"rejected:{err_msg_str}"}

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
                FROM live_trades WHERE settled_at IS NOT NULL"""
            )
            r = live[0] if live else (0, 0, 0, 0)

            # Recent trades
            recent = await self.db.conn.execute_fetchall(
                """SELECT strategy_label, direction, entry_price, amount, pnl, paper_pnl, result,
                    created_at FROM live_trades ORDER BY created_at DESC LIMIT 10"""
            )

            return {
                "total_trades": r[0],
                "live_pnl": round(r[1] or 0, 4),
                "paper_pnl_equiv": round(r[2] or 0, 2),
                "wins": r[3],
                "wr": round(r[3] / r[0] * 100, 0) if r[0] > 0 else 0,
                "recent": [
                    {
                        "strat": t[0],
                        "dir": t[1],
                        "price": t[2],
                        "amt": t[3],
                        "live_pnl": t[4],
                        "paper_pnl": t[5],
                        "result": t[6],
                        "ts": str(t[7])[:16],
                    }
                    for t in (recent or [])
                ],
            }
        except aiosqlite.Error as e:
            # T1.4 Faz 1: narrowed from bare Exception — only DB read errors expected.
            return {"error": str(e)}

    async def _save_state(self):
        if not self.db:
            return
        try:
            state = json.dumps(
                {
                    "total_spent": self._total_spent,
                    "total_pnl": self._total_pnl,
                    "trade_count": self._trade_count,
                }
            )
            await self.db.conn.execute(
                "INSERT OR REPLACE INTO bot_settings (key, value, updated_at) VALUES (?,?,?)",
                ("live_state", state, datetime.now(UTC).isoformat()),
            )
            await self.db.conn.commit()
        except (aiosqlite.Error, TypeError) as e:
            # T1.4 Faz 1: narrowed from bare Exception — DB errors (aiosqlite) or
            # json.dumps TypeError (non-serializable field). Upgrade from silent pass
            # to a warning so regressions aren't invisible in logs.
            logger.warning(f"Live _save_state failed ({type(e).__name__}): {e}")

    async def _restore_state(self):
        if not self.db:
            return
        try:
            r = await self.db.conn.execute_fetchall(
                "SELECT value FROM bot_settings WHERE key='live_state'"
            )
            if r:
                s = json.loads(r[0][0])
                self._total_spent = s.get("total_spent", 0)
                self._total_pnl = s.get("total_pnl", 0)
                self._trade_count = s.get("trade_count", 0)
                logger.info(
                    f"  💰 Live state restored: spent=${self._total_spent:.2f} pnl=${self._total_pnl:+.4f}"
                )
        except (aiosqlite.Error, json.JSONDecodeError, KeyError, IndexError) as e:
            # T1.4 Faz 1: narrowed from bare Exception — DB miss, corrupted JSON,
            # missing row index, or missing dict key. Upgrade pass to warning.
            logger.warning(f"Live _restore_state skipped ({type(e).__name__}): {e}")

    def _maybe_reset_daily(self):
        today = datetime.now(UTC).strftime("%Y-%m-%d")
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
        aid = getattr(self.settings, "ADMIN_TELEGRAM_ID", None) if self.settings else None
        if not aid or not self.bot_app:
            return
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
        if hasattr(self, "_recent_trades"):
            history = self._recent_trades
        return history

    async def load_trade_history(self) -> list[dict]:
        """Load from DB — called by live_handler for full history."""
        if not self.db:
            return []
        try:
            rows = await self.db.conn.execute_fetchall(
                """SELECT strategy_label, direction, entry_price, amount, pnl, paper_pnl, result, created_at
                FROM live_trades ORDER BY created_at DESC LIMIT 20"""
            )
            return [
                {
                    "strategy": r[0],
                    "direction": r[1],
                    "entry_odds": r[2],
                    "amount": r[3],
                    "pnl": r[4] or 0,
                    "pnl_paper": r[5] or 0,
                    "result": r[6] or "",
                    "ts": str(r[7])[:16],
                }
                for r in (rows or [])
            ]
        except aiosqlite.Error as e:
            # T1.4 Faz 1: narrowed from bare Exception — only DB read errors expected.
            logger.debug(f"load_trade_history DB: {e}")
            return []
