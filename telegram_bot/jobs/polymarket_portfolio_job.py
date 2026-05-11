"""Polymarket Portfolio refresh job (Aşama 1).

Her 60 saniyede:
  1. ``data.polymarket_portfolio.build_snapshot()`` çağır
  2. JSON serialize + DB cache (polymarket_portfolio_cache, single row id=1)
  3. Hata varsa admin'e cooldown'lı warning push (opsiyonel, sıralı 5 fail
     sonrasında ilk uyarı; bot başlangıçta sürekli spam etmesin)

Telegram /portfolio handler cache'ten okur — anlık response. Stale cache
durumunda ("fetched_at > 5 dk önce") handler kendi fresh fetch yapabilir.

ENV:
  PORTFOLIO_REFRESH_SEC      — Refresh interval (default 60s)
  PORTFOLIO_REFRESH_ENABLED  — Master switch (default true)
  PORTFOLIO_FAIL_ALERT_THRESHOLD — Sıralı fail count for admin alert (default 5)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

import aiosqlite
from telegram.ext import ContextTypes

from data.polymarket_portfolio import build_snapshot

logger = logging.getLogger("polypaper.jobs.polymarket_portfolio")

_consecutive_failures = 0
_last_alert_at: datetime | None = None


async def polymarket_portfolio_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue periodic callback. Refresh portfolio snapshot + DB cache."""
    global _consecutive_failures, _last_alert_at

    if os.getenv("PORTFOLIO_REFRESH_ENABLED", "true").lower() != "true":
        return

    db = context.bot_data.get("db")
    if db is None:
        logger.debug("portfolio_job: db not in bot_data, skip")
        return

    try:
        snap = await build_snapshot()
    except Exception as e:  # noqa: BLE001
        # Top-level safety net — build_snapshot already catches per-fetch
        # errors into snap.fetch_errors, but unexpected exception here = bug.
        logger.exception(f"portfolio_job build_snapshot failed: {e}")
        _consecutive_failures += 1
        await _maybe_alert_admin(context, f"build_snapshot crash: {e}")
        return

    if snap.fetch_errors:
        _consecutive_failures += 1
        logger.debug(
            f"portfolio_job partial fail ({len(snap.fetch_errors)} errors): "
            f"{snap.fetch_errors[:2]}"
        )
        threshold = int(os.getenv("PORTFOLIO_FAIL_ALERT_THRESHOLD", "5"))
        if _consecutive_failures == threshold:
            err_summary = "; ".join(snap.fetch_errors[:3])
            await _maybe_alert_admin(
                context, f"Portfolio fetch {threshold}x sıralı hata. Son: {err_summary}"
            )
    else:
        if _consecutive_failures > 0:
            logger.info(f"portfolio_job recovered after {_consecutive_failures} fails")
        _consecutive_failures = 0

    # Persist snapshot — UPSERT on id=1 (single-row cache pattern)
    try:
        snap_json = json.dumps(snap.to_dict(), ensure_ascii=False, default=str)
        await db.conn.execute(
            "INSERT INTO polymarket_portfolio_cache "
            "(id, user_address, snapshot_json, fetched_at, fetch_latency_ms, error_count) "
            "VALUES (1, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "user_address=excluded.user_address, "
            "snapshot_json=excluded.snapshot_json, "
            "fetched_at=excluded.fetched_at, "
            "fetch_latency_ms=excluded.fetch_latency_ms, "
            "error_count=excluded.error_count",
            (
                snap.user_address,
                snap_json,
                snap.fetched_at,
                snap.fetch_latency_ms,
                len(snap.fetch_errors),
            ),
        )
        await db.conn.commit()
        logger.debug(
            f"portfolio_job ok: ${snap.pusd_balance:.2f} balance, "
            f"{snap.positions_count} positions, "
            f"${snap.portfolio_value_usd:.2f} NAV, "
            f"latency={snap.fetch_latency_ms}ms"
        )
    except aiosqlite.Error as e:
        logger.warning(f"portfolio_job DB write fail: {e}")


async def _maybe_alert_admin(context: ContextTypes.DEFAULT_TYPE, msg: str) -> None:
    """Send admin Telegram alert with 30-min cooldown to avoid spam."""
    global _last_alert_at
    now = datetime.now(UTC)
    cooldown = int(os.getenv("PORTFOLIO_ALERT_COOLDOWN_SEC", "1800"))
    if _last_alert_at and (now - _last_alert_at).total_seconds() < cooldown:
        return
    _last_alert_at = now

    admin_id = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_CHAT_ID")
    if not admin_id:
        return
    try:
        await context.bot.send_message(
            chat_id=int(admin_id),
            text=f"⚠️ <b>Portfolio Refresh Hata</b>\n{msg[:500]}",
            parse_mode="HTML",
        )
    except Exception as _be:  # noqa: BLE001
        logger.debug(f"admin alert send failed: {_be}")
