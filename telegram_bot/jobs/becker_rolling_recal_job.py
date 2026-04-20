"""
Becker Rolling Recalibration Job (Phase 75+)

Scheduled job: Every Sunday 00:00 UTC
Rebuilds Becker δ(p) calibration curves using rolling window analysis
with exponential decay on older samples.

Prevents calibration drift from market regime changes (halving, liquidity shifts, etc.)

Accessible via /becker_recal_status command.
"""
import logging
from datetime import datetime, timedelta
from telegram import Chat

logger = logging.getLogger("polypaper.jobs.becker_rolling_recal")


async def becker_rolling_recal_job(context):
    """
    Weekly Becker rolling recalibration job.
    Runs every Sunday 00:00 UTC.

    Context job data:
      context.job.context = {
        'db': database instance,
        'bot': Telegram bot,
        'admin_chat_id': int,
      }
    """
    try:
        # Phase 78-fix: python-telegram-bot 21.x stores job data in .data
        context_data = context.job.data or {}
        db = context_data.get("db")
        bot = context_data.get("bot")
        admin_chat_id = context_data.get("admin_chat_id")

        if not db or not bot:
            logger.warning("becker_rolling_recal_job: missing db or bot")
            return

        # Import here to avoid circular imports
        from core.becker_rolling_recal import BeckerRollingRecalibrator

        enabled = context_data.get("becker_rolling_enabled", False)
        if not enabled:
            logger.debug("becker_rolling_recal_job: disabled, skipping")
            return

        recalibrator = BeckerRollingRecalibrator(db, enabled=True)
        result = await recalibrator.weekly_recalibration_job()

        # Format report for Telegram
        if result.get("success"):
            lines = ["🔄 <b>Becker Rolling Recalibration</b>\n"]
            lines.append(f"<code>{result['activated_at']}</code>\n")

            assets = result.get("assets", {})
            for asset, stats in assets.items():
                status = stats.get("status", "unknown")
                if status == "updated":
                    confidence = stats.get("confidence", 0.0)
                    shift = stats.get("recommended_shift_bps", 0.0)
                    recent = stats.get("recent_trades", 0)
                    emoji = "✅" if confidence > 0.7 else "⚠️"

                    lines.append(
                        f"{emoji} <code>{asset:5s}</code> "
                        f"conf={confidence:5.1%} shift={shift:+5.1f}bps "
                        f"recent={recent:3d}\n"
                    )
                elif status == "skipped_low_confidence":
                    lines.append(
                        f"⏭️  <code>{asset:5s}</code> "
                        f"(low confidence, kept current)\n"
                    )

            text = "".join(lines)

            if admin_chat_id and len(text) > 20:
                try:
                    await bot.send_message(
                        chat_id=admin_chat_id,
                        text=text,
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to send Becker recal report: {e}"
                    )

            logger.info(f"✅ Becker rolling recalibration completed: {result}")
        else:
            error = result.get("error", "unknown error")
            logger.error(f"❌ Becker rolling recalibration failed: {error}")

    except Exception as e:
        logger.error(
            f"becker_rolling_recal_job crashed: {e}",
            exc_info=True,
        )


def schedule_becker_rolling_recal(job_queue, context_data: dict):
    """
    Register weekly Sunday 00:00 UTC recurring job.

    Args:
        job_queue: APScheduler JobQueue from app
        context_data: dict with 'db', 'bot', 'admin_chat_id'
    """
    try:
        # Calculate first run time (next Sunday 00:00 UTC)
        now = datetime.utcnow()
        days_until_sunday = (6 - now.weekday()) % 7
        if days_until_sunday == 0 and now.hour > 0:
            days_until_sunday = 7

        first_run = now + timedelta(days=days_until_sunday)
        first_run = first_run.replace(hour=0, minute=0, second=0, microsecond=0)

        logger.info(
            f"Scheduling Becker rolling recal for {first_run.isoformat()}"
        )

        # Phase 78-fix: python-telegram-bot 21.x uses 'data' not 'context'
        job_queue.run_repeating(
            becker_rolling_recal_job,
            interval=timedelta(weeks=1),
            first=first_run,
            data=context_data,
            name="becker_rolling_recal",
        )

        logger.info("✅ Becker rolling recal job scheduled (weekly Sunday 00:00 UTC)")

    except Exception as e:
        logger.error(f"Failed to schedule Becker rolling recal job: {e}")
