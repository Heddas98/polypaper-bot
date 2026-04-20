"""
Phase 75+: /becker_recal_status command — monitor rolling recalibration status.

Commands:
  /becker_recal_status      — Show current curve status + next recal time
  /becker_recal_manual      — Manually trigger recalibration (admin only)
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("polypaper.handlers.becker_recal")


async def becker_recal_status_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Show Becker rolling recalibration status."""
    try:
        db = context.application.bot_data.get("db")
        if not db:
            await update.message.reply_text("❌ DB unavailable")
            return

        from core.becker_rolling_recal import BeckerRollingRecalibrator

        enabled = context.application.bot_data.get("becker_rolling_enabled", False)
        recalibrator = BeckerRollingRecalibrator(db, enabled=enabled)

        status = await recalibrator.get_status()

        if not enabled:
            await update.message.reply_text(
                "🔄 <b>Becker Rolling Recalibration</b>\n\n"
                "Status: <b>DISABLED</b>\n"
                "Set <code>BECKER_ROLLING_RECAL_ENABLED=true</code> in .env to enable",
                parse_mode="HTML",
            )
            return

        next_recal = status.get("next_recal", "unknown")
        last_recal = status.get("last_recal_ts", "never")
        time_since = status.get("time_since_recal_hours", 0)

        text = (
            f"🔄 <b>Becker Rolling Recalibration</b>\n\n"
            f"<b>Status:</b> <code>ENABLED</code>\n"
            f"<b>Last Recalibration:</b> {last_recal}\n"
            f"<b>Hours Ago:</b> {time_since}h\n"
            f"<b>Next Recalibration:</b> {next_recal}\n\n"
            f"<b>Curves:</b>\n"
        )

        current = status.get("current_curves", [])
        fallback = status.get("fallback_curves", [])

        if current:
            text += f"  ✅ Current: {', '.join(current)}\n"
        if fallback:
            text += f"  ⏮️  Fallback: {', '.join(fallback)}\n"
        else:
            text += f"  ⏮️  Fallback: none\n"

        await update.message.reply_text(text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"/becker_recal_status failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error: {e}")


async def becker_recal_manual_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Manually trigger Becker rolling recalibration (admin only)."""
    try:
        # Admin check
        admin_id = context.application.bot_data.get("admin_telegram_id")
        if not admin_id or update.effective_user.id != admin_id:
            await update.message.reply_text("❌ Admin only")
            return

        db = context.application.bot_data.get("db")
        if not db:
            await update.message.reply_text("❌ DB unavailable")
            return

        enabled = context.application.bot_data.get("becker_rolling_enabled", False)
        if not enabled:
            await update.message.reply_text(
                "❌ Becker rolling recalibration is disabled. "
                "Set BECKER_ROLLING_RECAL_ENABLED=true in .env"
            )
            return

        await update.message.reply_text(
            "🔄 Running Becker rolling recalibration... (this may take 30s)"
        )

        from core.becker_rolling_recal import BeckerRollingRecalibrator

        recalibrator = BeckerRollingRecalibrator(db, enabled=True)
        result = await recalibrator.weekly_recalibration_job()

        if result.get("success"):
            lines = ["✅ <b>Recalibration Complete</b>\n"]
            assets = result.get("assets", {})

            for asset, stats in assets.items():
                status = stats.get("status", "unknown")
                if status == "updated":
                    confidence = stats.get("confidence", 0.0)
                    shift = stats.get("recommended_shift_bps", 0.0)
                    recent = stats.get("recent_trades", 0)

                    lines.append(
                        f"✅ {asset}: conf={confidence:.1%} shift={shift:+.1f}bps "
                        f"recent={recent}\n"
                    )
                elif status == "skipped_low_confidence":
                    recent = stats.get("recent_trades", 0)
                    lines.append(
                        f"⏭️  {asset}: skipped (recent={recent}, low confidence)\n"
                    )

            text = "".join(lines)
            await update.message.reply_text(text, parse_mode="HTML")

        else:
            error = result.get("error", "unknown error")
            await update.message.reply_text(f"❌ Recalibration failed: {error}")

    except Exception as e:
        logger.error(f"/becker_recal_manual failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error: {e}")
