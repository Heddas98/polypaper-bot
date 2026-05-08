"""Auto-redeem periodic job — resolved + winning pozisyonları otomatik redeem.

2026-05-05 Heddas direktifi: kazanan pozisyonlar Polymarket'te idle bekliyor,
manuel redeem gerekiyor — bot otomatik redeem versin + Telegram notification.

Polymarket docs (/trading/ctf/redeem):
  Each winning token = $1.00 pUSD. Losing token = $0.
  Redemption requires CTF.redeemPositions(collateralToken, parent, conditionId, [1,2]).
  Gasless via Polymarket Relayer (RELAYER_API_KEY).

ENV:
  AUTO_REDEEM_ENABLED=false   # default off — kullanıcı isterse açar
  AUTO_REDEEM_INTERVAL_SEC=300 # 5dk default
  AUTO_REDEEM_MIN_VALUE_USD=0.10 # gas-eşdeğeri (gasless ama yine de sınır)
"""
from __future__ import annotations

import logging
import os
from typing import Set

from telegram.ext import ContextTypes

logger = logging.getLogger("polypaper.jobs.auto_redeem")

# Set persists for bot lifetime — pozisyonu yeniden redeem etme (idempotent)
_REDEEMED_CONDITIONS: Set[str] = set()


async def auto_redeem_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resolved + winning pozisyonları otomatik redeem et.

    Schedule: JobQueue interval=AUTO_REDEEM_INTERVAL_SEC (default 5dk).
    Idempotent: aynı condition_id ikinci kez redeem edilmez (in-memory set).
    """
    if os.getenv("AUTO_REDEEM_ENABLED", "false").lower() != "true":
        return

    bot_data = context.bot_data
    engine = bot_data.get("engine")
    if engine is None:
        return

    relayer_key = os.getenv("RELAYER_API_KEY", "").strip()
    if not relayer_key:
        logger.debug("auto_redeem skip: RELAYER_API_KEY not set")
        return

    min_val = float(os.getenv("AUTO_REDEEM_MIN_VALUE_USD", "0.10"))

    try:
        from data.polymarket_portfolio import read_cached_snapshot
        from data.polymarket_actions import redeem_position

        snap = await read_cached_snapshot(engine.db) if engine.db else None
        if not snap or not snap.get("positions"):
            return

        admin_id_str = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
        admin_id = int(admin_id_str) if admin_id_str.isdigit() else None

        redeemed_count = 0
        skipped_count = 0
        for p in snap.get("positions", []):
            cid = p.get("condition_id", "")
            redeemable = bool(p.get("redeemable", False))
            cur_val = float(p.get("cur_value_usd", 0))
            slug = p.get("market_slug", "?")

            if not cid:
                continue
            if not redeemable:
                continue
            if cid in _REDEEMED_CONDITIONS:
                skipped_count += 1
                continue
            if cur_val < min_val:
                logger.debug(
                    f"auto_redeem skip {slug}: cur_val ${cur_val:.4f} "
                    f"< min ${min_val:.2f}"
                )
                continue

            # Mark first to prevent retry on slow Relayer (idempotent)
            _REDEEMED_CONDITIONS.add(cid)
            ok, detail = await redeem_position(cid)
            if ok:
                redeemed_count += 1
                logger.info(
                    f"auto_redeem SUCCESS {slug} ${cur_val:.2f}: "
                    f"{str(detail)[:120]}"
                )
                if admin_id:
                    try:
                        await context.bot.send_message(
                            admin_id,
                            f"🏆 <b>AUTO-REDEEM</b>\n\n"
                            f"<b>Market:</b> {slug}\n"
                            f"<b>Değer:</b> ${cur_val:.2f}\n\n"
                            f"<i>{str(detail)[:200]}</i>",
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
                    except Exception as _alert_err:  # noqa: BLE001
                        logger.warning(f"auto_redeem alert: {_alert_err}")
            else:
                # Fail durumunda set'ten çıkar — bir sonraki cycle tekrar dener
                _REDEEMED_CONDITIONS.discard(cid)
                logger.warning(
                    f"auto_redeem FAIL {slug}: {str(detail)[:200]}"
                )

        if redeemed_count > 0 or skipped_count > 0:
            logger.info(
                f"auto_redeem cycle: {redeemed_count} redeemed, "
                f"{skipped_count} already-redeemed in cache"
            )

    except Exception as e:  # noqa: BLE001
        logger.exception(f"auto_redeem unexpected: {e}")
