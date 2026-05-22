"""
Phase 51 P51-04 / P51-05 — /ai natural-language dispatcher.

Users can type `/ai <serbest metin>` and the bot tries to map it to a
real command via `core.intent_parser.parse_intent()`. Three outcomes:

  1. High confidence (>= 0.75) → execute the command immediately and
     tell the user which command ran.
  2. Medium confidence (0.4 - 0.75) → suggest the command with an
     inline "Run" button; user confirms.
  3. No match → show the catalog and ask the user to rephrase.

Phase 51 P51-05 piggy-backs on the same handler for natural-language
backtest queries: any intent that resolves to `/compare`, `/backtest`,
or `/backtest_replay` is treated as a backtest query and we forward
the extracted args directly to the mapped handler.

T11.8-B (2026-04-24): Every catch in this module is annotated `# noqa:
BLE001` because the natural-language layer touches: (a) LLM API (Anthropic
client may surface httpx, anthropic, asyncio.TimeoutError, AnthropicError),
(b) intent parser regex/scoring, (c) downstream sub-handlers (each its own
exception surface), (d) edit_message_text Telegram replies. T11.6 render
policy is preserved on user-facing reply paths (truncated err only on
admin-only diagnostics; generic message otherwise).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.intent_parser import COMMAND_CATALOG, IntentResult, parse_intent
from telegram_bot.templates.callback_proxy import CallbackUpdateProxy
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.ai")

# Pending suggestion store keyed by (chat_id, user_id) — chat_data is
# per-chat so we namespace by user to avoid cross-talk.
_PENDING_KEY = "phase51_ai_pending"


def _catalog_hint(max_items: int = 12) -> str:
    lines = []
    for c in COMMAND_CATALOG[:max_items]:
        lines.append(f"  <code>{esc(c.name)}</code> — {esc(c.description)}")
    if len(COMMAND_CATALOG) > max_items:
        lines.append(f"  <i>… +{len(COMMAND_CATALOG) - max_items} daha</i>")
    return "\n".join(lines)


async def _invoke_mapped_command(
    result: IntentResult,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """Resolve IntentResult.command to its handler and call it.

    Returns True on success, False if the handler can't be resolved.
    """
    # Lazy import — avoids circular import with bot.py
    from telegram_bot.handlers.backtest_lab import backtest_lab_command
    from telegram_bot.handlers.backtest_v2 import compare_cmd
    from telegram_bot.handlers.dashboard import (
        alert_set_cmd,
        alerts_list_cmd,
        dashboard_command,
    )
    from telegram_bot.handlers.positions import positions_command
    from telegram_bot.handlers.risk_handler import risk_hub_command
    from telegram_bot.handlers.settings_handler import (
        canary_command,
        demote_command,
        promote_command,
    )
    from telegram_bot.handlers.start import wallets_command
    from telegram_bot.handlers.stats import (
        stats_chart_command,  # Phase 51 P51-03 Faz-2 — merged from stats_chart.py
        stats_command,
        stats_hub_command,
    )
    from telegram_bot.handlers.strategies import (
        autopilot_command,
        kelly_command,
        maker_stats_command,
        strategies_command,
    )
    # brain_command is defined in this same module — no import needed

    # /h and /db_health live on the bot class — route via bot_data
    dispatch: dict[str, Callable[..., Awaitable[None]]] = {
        "/dashboard": dashboard_command,
        "/wallets": wallets_command,
        "/rs": _route_bot_method("_risk_status"),
        "/risk_hub": risk_hub_command,
        "/strategies": strategies_command,
        "/stats": stats_command,
        "/stats_hub": stats_hub_command,
        "/stats_chart": stats_chart_command,
        "/trades": _route_trades_fallback,
        "/maker_stats": maker_stats_command,
        "/kelly": kelly_command,
        "/h": _route_bot_method("_health_check"),
        "/db_health": _route_bot_method("_db_health"),
        "/autopilot": autopilot_command,
        "/alerts": alerts_list_cmd,
        "/alert": alert_set_cmd,
        "/compare": compare_cmd,
        "/backtest": backtest_lab_command,
        "/promote": promote_command,
        "/canary": canary_command,
        "/demote": demote_command,
        "/positions": positions_command,
        "/shadow": _route_bot_method("_shadow_report_now"),
        "/brain": brain_command,
    }
    handler = dispatch.get(result.command)
    if not handler:
        return False

    # Thread args through context.args — handlers read from there.
    context.args = list(result.args)
    try:
        await handler(update, context)
        return True
    except Exception as e:  # noqa: BLE001
        logger.exception(f"/ai dispatch {result.command} failed: {e}")
        # T11.6-OK reason=/ai admin-only diagnostic, dispatch failures need
        # exception detail for troubleshooting. Truncated to 150 chars.
        await update.effective_message.reply_text(  # noqa: T11.6-OK
            f"❌ <b>Çalıştırma hatası</b>\n"
            f"Komut: <code>{esc(result.command)}</code>\n"
            f"Hata: <code>{esc(str(e)[:150])}</code>",
            parse_mode="HTML",
        )
        return True  # we *did* try to run it


def _route_bot_method(attr: str):
    """Return an async shim that forwards to `context.bot_data['bot'].<attr>`.

    The bot class mounts itself on bot_data["bot"] during startup so other
    handlers can invoke its private methods (e.g. _health_check) without
    circular imports. If the bot object is not mounted we fall back to a
    plain reply asking the user to use the direct command.
    """

    async def _shim(update: Update, context: ContextTypes.DEFAULT_TYPE):
        bot_obj = context.bot_data.get("bot")
        if bot_obj is None or not hasattr(bot_obj, attr):
            await update.effective_message.reply_text(
                f"ℹ️ Bu komutu doğrudan kullan: <code>/{esc(attr.lstrip('_'))}</code>",
                parse_mode="HTML",
            )
            return
        method = getattr(bot_obj, attr)
        await method(update, context)

    return _shim


async def _route_trades_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """No dedicated /trades handler exists — fall back to /stats."""
    from telegram_bot.handlers.stats import stats_command

    await update.effective_message.reply_text(
        "ℹ️ /trades dedike komutu yok, /stats gösteriyorum.",
        parse_mode="HTML",
    )
    await stats_command(update, context)


# ---------------------------------------------------------------------------
# Main /ai command
# ---------------------------------------------------------------------------


async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for `/ai <natural language>`."""
    text = " ".join(context.args or []).strip()
    if not text:
        await update.message.reply_text(
            "🤖 <b>AI Komut Çevirmen</b>\n\n"
            "Doğal dil ile komut yaz, sana uygun komutu bulayım.\n\n"
            "<b>Örnekler:</b>\n"
            "  <code>/ai risk durumum ne</code>\n"
            "  <code>/ai son 10 btc trade</code>\n"
            "  <code>/ai pnl grafiği 30 gün</code>\n"
            "  <code>/ai alarm kur btc > 0.6</code>\n"
            "  <code>/ai karşılaştır hour_edge streak_reversal</code>\n\n"
            f"<b>Bilinen komutlar ({len(COMMAND_CATALOG)}):</b>\n{_catalog_hint()}",
            parse_mode="HTML",
        )
        return

    try:
        result = await parse_intent(text, use_claude=True)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"intent parse failed: {e}")
        await update.message.reply_text(
            "❌ Intent parser hatası — doğrudan komut kullan.",
            parse_mode="HTML",
        )
        return

    if not result.command:
        await update.message.reply_text(
            f"🤔 <b>Anlayamadım:</b> <code>{esc(text[:120])}</code>\n\n"
            f"Başka şekilde dene veya doğrudan komut kullan.\n\n"
            f"<b>Popüler komutlar:</b>\n{_catalog_hint(8)}",
            parse_mode="HTML",
        )
        return

    if result.is_high_confidence:
        # Auto-execute
        args_str = " ".join(result.args)
        banner = (
            f"🤖 <code>{esc(result.command)}</code>"
            + (f" <code>{esc(args_str)}</code>" if args_str else "")
            + f"  <i>({result.source}, {result.confidence:.0%})</i>"
        )
        await update.message.reply_text(banner, parse_mode="HTML")
        ok = await _invoke_mapped_command(result, update, context)
        if not ok:
            await update.message.reply_text(
                f"ℹ️ <code>{esc(result.command)}</code> bu versiyonda /ai router'da yok.\n"
                f"Doğrudan komutu kullan.",
                parse_mode="HTML",
            )
        return

    # Medium confidence → suggestion card
    args_str = " ".join(result.args)
    preview = f"<code>{esc(result.command)}</code>" + (
        f" <code>{esc(args_str)}</code>" if args_str else ""
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Çalıştır", callback_data="ai_run"),
                InlineKeyboardButton("❌ İptal", callback_data="ai_cancel"),
            ]
        ]
    )
    # Stash in chat_data per user
    pending = context.chat_data.setdefault(_PENDING_KEY, {})
    pending[update.effective_user.id] = result.to_dict()
    await update.message.reply_text(
        f"🤖 <b>Öneri</b>\n"
        f"Mesaj: <i>{esc(text[:120])}</i>\n"
        f"Komut: {preview}\n"
        f"Güven: {result.confidence:.0%} ({esc(result.source)})\n"
        f"<i>{esc(result.reasoning)}</i>",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def ai_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ai_run / ai_cancel inline buttons."""
    q = update.callback_query
    await q.answer()
    pending = context.chat_data.get(_PENDING_KEY, {})
    raw = pending.pop(update.effective_user.id, None)
    if raw is None:
        try:
            await q.edit_message_text("⌛ Öneri zaman aşımına uğradı.")
        except Exception:  # noqa: BLE001
            pass
        return

    if q.data == "ai_cancel":
        try:
            await q.edit_message_text("❌ İptal edildi.")
        except Exception:  # noqa: BLE001
            pass
        return

    result = IntentResult(**raw)
    try:
        await q.edit_message_text(
            f"▶️ Çalıştırılıyor: <code>{esc(result.command)}</code>",
            parse_mode="HTML",
        )
    except Exception:  # noqa: BLE001
        pass
    # Phase 51 BUG-FIX — downstream handlers use `update.message.reply_text`
    # which is None on callback queries; wrap with proxy.
    proxy = CallbackUpdateProxy.from_update(update)
    ok = await _invoke_mapped_command(result, proxy, context)
    if not ok:
        await q.message.reply_text(
            f"ℹ️ <code>{esc(result.command)}</code> /ai router'da kayıtlı değil.",
            parse_mode="HTML",
        )


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster D — merged from brain_handler.py
# ═══════════════════════════════════════════════════════════════════════
from telegram import (  # noqa: E402
    InlineKeyboardButton as _BrainBtn,
    InlineKeyboardMarkup as _BrainKB,
)


async def brain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 35: Show AI Brain status panel with toggle buttons."""
    engine = context.bot_data.get("engine")
    db = context.bot_data.get("db")

    if not engine:
        return await update.message.reply_text("Engine bulunamadi.", parse_mode="HTML")

    try:
        wallet = await db.conn.execute_fetchall("SELECT balance FROM wallets LIMIT 1")
        balance = wallet[0][0] if wallet else 0.0

        ai_status = engine.analyst.get_status() if engine.analyst else {}
        spent = ai_status.get("spent", 0.0)
        budget = ai_status.get("budget", 15.0)
        remaining = budget - spent
        cycle = ai_status.get("cycle", 0)
        last_run = ai_status.get("last_run", "?")

        flags = engine.brain_flags

        def fmt_flag(key):
            # Epic 6 T6.3d: kelly_sizing is a virtual flag; authoritative
            # state lives on engine._kelly_mode (shared with /kelly_toggle).
            if key == "kelly_sizing":
                val = bool(getattr(engine, "_kelly_mode", True))
            else:
                val = flags.get(key, True)
            return "✅ AÇIK" if val else "⏸ KAPALI"

        text = (
            f"🧠 <b>AI Brain Kontrol Paneli</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🧠 AI Brain (10dk cycle): {fmt_flag('ai_brain')}\n"
            f"🎯 Thompson Sampling:     {fmt_flag('thompson_sampling')}\n"
            f"🌐 Regime Detection:      {fmt_flag('regime_detection')}\n"
            f"🤖 AutoPilot:            {fmt_flag('autopilot')}\n"
            f"📈 Kelly Sizing:          {fmt_flag('kelly_sizing')}\n"
            f"📊 Candle Collector:      {fmt_flag('candle_collector')}\n\n"
            f"💰 Bütçe: ${spent:.2f}/${budget:.2f} (${remaining:.2f} kaldi)\n"
            f"🔄 Cycle: #{cycle} | Son: {last_run}\n"
            f"💵 Bakiye: ${balance:.2f}\n"
        )

        # Epic 6 T6.3b: drift_monitor button removed (ghost — no engine
        # consumer). T6.3d: kelly_sizing button now retargets
        # engine._kelly_mode directly (virtual flag). T6.3e: market_recorder
        # button added — now matches brain_flags canonical 6-flag set.
        # Layout is 4 rows × 2 buttons, balanced.
        kb = _BrainKB(
            [
                [
                    _BrainBtn("🧠 Brain", callback_data="brain_toggle_ai_brain"),
                    _BrainBtn("🎯 TS", callback_data="brain_toggle_thompson_sampling"),
                ],
                [
                    _BrainBtn("🌐 Regime", callback_data="brain_toggle_regime_detection"),
                    _BrainBtn("🤖 AutoPilot", callback_data="brain_toggle_autopilot"),
                ],
                [
                    _BrainBtn("📈 Kelly", callback_data="brain_toggle_kelly_sizing"),
                    _BrainBtn("📊 Candles", callback_data="brain_toggle_candle_collector"),
                ],
                [
                    _BrainBtn("🔄 Yenile", callback_data="brain_refresh"),
                ],
            ]
        )

        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    except Exception as e:  # noqa: BLE001
        # Epic 10 T10.7 (2026-04-22): exception detay loglara, kullanıcıya
        # generic mesaj — engine.brain_flags / kelly durumu gibi iç durum
        # string'leri Telegram'a sızmasın.
        logger.error(f"Brain command error: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ <b>Brain Hatasi</b>\n\nDetay loglarda.", parse_mode="HTML"
        )


async def brain_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle brain feature toggle buttons.

    Epic 10 T10.2 (C2): admin gate — without it, any Telegram user
    who reaches a brain_toggle_* / brain_refresh callback_data can
    disable ai_brain, autopilot, kelly_sizing and other flags that
    govern live trading decisions.
    """
    query = update.callback_query
    admin_id = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_CHAT_ID")
    if admin_id and str(update.effective_user.id) != str(admin_id):
        await query.answer("⛔ Admin only", show_alert=True)
        return
    engine = context.bot_data.get("engine")
    db = context.bot_data.get("db")

    if not engine or not db:
        await query.answer("Engine/DB bulunamadi.", show_alert=True)
        return

    parts = query.data.split("_")
    if len(parts) < 3:
        await query.answer()
        return

    if parts[-1] == "refresh":
        await query.answer()
        from telegram_bot.templates.callback_proxy import CallbackUpdateProxy

        await brain_command(CallbackUpdateProxy.from_update(update), context)
        return

    feature = "_".join(parts[2:])

    try:
        # Epic 6 T6.3b: drift_monitor removed (ghost). T6.3d: kelly_sizing
        # kept here as virtual flag (toggle retargets engine._kelly_mode).
        # T6.3e: market_recorder added — reverse ghost cleared (engine had
        # flag, UI now exposes it). Canonical UI-toggleable set:
        valid_features = {
            "ai_brain",
            "thompson_sampling",
            "regime_detection",
            "autopilot",
            "kelly_sizing",
            "candle_collector",
        }
        if feature not in valid_features:
            await query.answer(f"Bilinmeyen feature: {feature}", show_alert=True)
            return

        # Epic 6 T6.3d: kelly_sizing is a virtual flag — retarget to
        # engine._kelly_mode (the authoritative state read by engine_signals
        # at sizing time). Epic 6 T6.5: Now persisted to DB via the
        # canonical `engine.kelly_mode` key, mirroring /kelly_toggle
        # command. Boot loader in engine.start() restores this on startup.
        if feature == "kelly_sizing":
            new_state = not bool(getattr(engine, "_kelly_mode", True))
            engine._kelly_mode = new_state
            await db.set_setting("engine.kelly_mode", "1" if new_state else "0")
        else:
            engine.brain_flags[feature] = not engine.brain_flags.get(feature, True)
            new_state = engine.brain_flags[feature]
            await db.set_setting(f"brain_flags.{feature}", "1" if new_state else "0")

        if feature == "candle_collector":
            cc = getattr(engine, "candle_collector", None)
            if cc:
                cc._enabled = new_state

        status = "✅ AÇIK" if new_state else "⚫ KAPALI"
        feature_display = feature.replace("_", " ").title()
        await query.answer(f"✅ {feature_display}: {status}", show_alert=False)
        logger.info(f"🧠 Brain flag toggle: {feature}={new_state}")

        from telegram_bot.templates.callback_proxy import CallbackUpdateProxy

        await brain_command(CallbackUpdateProxy.from_update(update), context)

    except Exception as e:  # noqa: BLE001
        logger.error(f"Brain toggle error: {esc(e)}", exc_info=True)
        await query.answer(f"❌ Hata: {str(e)[:50]}", show_alert=True)


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster D — merged from intelligence_handler.py
# ═══════════════════════════════════════════════════════════════════════


async def regime_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show market regime + strategy fitness."""
    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine bulunamadi.")

    rs = engine.regime.get_status()
    regime_emoji = {"trending": "📈", "ranging": "↔️", "volatile": "🌪"}.get(rs["regime"], "❓")

    text = (
        f"🌐 <b>Market Regime</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{regime_emoji} Regime: <b>{rs['regime'].upper()}</b>\n"
        f"Guven: {rs['confidence']:.0%} | Veri: {rs['data_points']} nokta\n\n"
        f"<b>Strateji Uyumu:</b>\n"
    )

    for stype, fits in engine.regime.STRATEGY_REGIME_FIT.items():
        fit = fits.get(rs["regime"], 0.5)
        bar = "█" * int(fit * 10) + "░" * (10 - int(fit * 10))
        skip = " ❌ SKIP" if fit < 0.4 else ""
        text += f"  {stype:15s} {bar} {fit:.0%}{skip}\n"

    text += "\n<i>/ts = Thompson Sampling | /drift = Sinyal drift</i>"
    await update.message.reply_text(text, parse_mode="HTML")


async def ts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show Thompson Sampling strategy rankings."""
    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine bulunamadi.")

    if not engine.selector:
        return await update.message.reply_text("Thompson Sampler henuz baslatilmadi.")

    status = engine.selector.get_status()
    rankings = status["rankings"]

    text = (
        f"🎰 <b>Thompson Sampling</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"Toplam: {status['total_arms']} strateji\n\n"
    )

    for i, r in enumerate(rankings):
        medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"][i] if i < 10 else "  "
        text += (
            f"{medal} <code>{r['id'][:8]}</code> "
            f"α={r['alpha']:.0f} β={r['beta']:.0f} "
            f"WR={r['win_rate']:.0f}% "
            f"PnL:{r['pnl']:+.0f} "
            f"({r['trades']}t)\n"
        )

    text += "\n<i>α=kazanc, β=kayip | Yuksek α/β = tercih edilen</i>"
    await update.message.reply_text(text, parse_mode="HTML")


async def drift_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show signal drift detection status."""
    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine bulunamadi.")

    try:
        if not hasattr(engine, "drift") or engine.drift is None:
            return await update.message.reply_text(
                "📉 <b>Sinyal Drift Tespiti</b>\n\n" "Drift detector henuz baslatilmadi.",
                parse_mode="HTML",
            )

        status = engine.drift.get_status()

        text = "📉 <b>Sinyal Drift Tespiti</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"

        if not status:
            text += "Henuz yeterli veri yok. Trade'ler settle oldukca dolacak.\n"
        else:
            for name, s in status.items():
                emoji = "🔴" if s["drifting"] else "🟢"
                text += (
                    f"{emoji} <b>{esc(name)}</b>\n"
                    f"  Accuracy: {s['accuracy']:.0f}% | Weight: ×{s['weight']:.2f} | "
                    f"Samples: {s['samples']}\n"
                )

        text += "\n<i>Weight &lt; 1.0 = sinyal zayifliyor, otomatik azaltildi</i>"
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        logger.error("Drift command error: %s", e, exc_info=True)
        await update.message.reply_text(
            f"❌ <b>Drift Tespiti Hatasi</b>\n\nDetay: {str(e)[:100]}", parse_mode="HTML"
        )


# T1.3 Commit 3 (2026-04-20): validate_command komple silindi —
# core.wf_validator ghost modüle bağlıydı, /validate komutu broken durumdaydı.


async def monitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Full monitoring dashboard in Telegram."""
    engine = context.bot_data.get("engine")
    db = context.bot_data.get("db")
    if not engine or not db:
        return await update.message.reply_text("Engine/DB bulunamadi.", parse_mode="HTML")

    # Loading indicator
    await update.message.reply_text("⏳ <b>Monitor Yukleniyor</b>...", parse_mode="HTML")

    try:
        # Balance + PnL
        bal = await db.conn.execute_fetchall("SELECT balance FROM wallets LIMIT 1")
        balance = bal[0][0] if bal else 0
        at = await db.conn.execute_fetchall(
            "SELECT COALESCE(SUM(pnl),0), COUNT(*), COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) FROM executions WHERE result IS NOT NULL"
        )
        pnl, trades, wins = (at[0][0], at[0][1], at[0][2]) if at else (0, 0, 0)
        wr = wins / trades * 100 if trades > 0 else 0

        # Regime
        rs = engine.regime.get_status()
        re = {"trending": "📈", "ranging": "↔️", "volatile": "🌪"}.get(rs["regime"], "❓")

        # AI Brain
        ai = engine.analyst.get_status() if engine.analyst else {}

        # BTC
        btc = ""
        if engine.external_feed and engine.external_feed.is_available:
            p = engine.external_feed.get_price("BTC")
            btc = f"${p:,.0f}" if p else "--"

        text = (
            f"📊 <b>PolyPaper Monitor</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 <b>${balance:,.2f}</b> | PnL: <b>{pnl:+.2f}</b>\n"
            f"📈 {trades}t | WR: {wr:.0f}% | BTC: {btc}\n"
            f"{re} Regime: <b>{rs['regime']}</b> ({rs['confidence']:.0%})\n"
            f"🧠 AI: ${ai.get('spent',0):.2f}/${ai.get('budget',15)} | "
            f"Cycle #{ai.get('cycle',0)}\n"
            f"⚙️ Engine c={engine._cycle}\n\n"
        )

        # Top 5 strategies
        strats = await db.conn.execute_fetchall(
            """SELECT s.label, s.status,
                COUNT(CASE WHEN e.result IS NOT NULL THEN 1 END) as t,
                COALESCE(SUM(CASE WHEN e.pnl>0 AND e.result IS NOT NULL THEN 1 ELSE 0 END),0) as w,
                COALESCE(SUM(CASE WHEN e.result IS NOT NULL THEN e.pnl ELSE 0 END),0) as pnl
            FROM strategies s LEFT JOIN executions e ON e.strategy_id=s.id
            GROUP BY s.id HAVING t>0 ORDER BY pnl DESC LIMIT 5"""
        )
        text += "<b>🏆 Top 5</b>\n"
        for _i, s in enumerate(strats or []):
            wr_s = s[3] / s[2] * 100 if s[2] > 0 else 0
            st = "✅" if s[1] == "active" else "⚫"
            ai_tag = "🤖" if "AI_" in (s[0] or "") else ""
            text += f"  {st}{ai_tag} {s[0]}: {s[2]}t {wr_s:.0f}% <b>{s[4]:+.2f}</b>\n"

        # TS top 3
        rankings = engine.selector.get_rankings()[:3]
        if rankings:
            text += "\n<b>🎰 TS Ranking</b>\n"
            for r in rankings:
                text += f"  <code>{r['id'][:8]}</code> α={r['alpha']:.0f} β={r['beta']:.0f} WR={r['win_rate']:.0f}%\n"

        # Drift
        drift = engine.drift.get_status()
        drifting = [n for n, v in drift.items() if v.get("drifting")]
        if drifting:
            text += f"\n⚠️ <b>Drift:</b> {', '.join(drifting)}\n"

        # Dashboard URL
        text += "\n🌐 <i>Web dashboard: /health URL + /dashboard</i>"

        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Monitor command error: {esc(e)}", exc_info=True)
        await update.message.reply_text(
            f"❌ <b>Monitor Hatasi</b>\n\nDetay: {str(e)[:100]}", parse_mode="HTML"
        )


# ═══ Sprint 3 S3-04: AI Brain Approval Callback ═══
async def ai_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ai_approve / ai_reject inline buttons for low-confidence AI actions."""
    q = update.callback_query
    await q.answer()
    engine = context.bot_data.get("engine")
    if not engine or not engine.analyst:
        try:
            await q.edit_message_text("⚠️ Engine bulunamadi.")
        except Exception:  # noqa: BLE001
            pass
        return

    approved = q.data == "ai_approve"
    msg_id = str(q.message.message_id)

    try:
        result_text = await engine.analyst.handle_approval(approved, msg_id)
        try:
            await q.edit_message_text(result_text, parse_mode="HTML")
        except Exception:  # noqa: BLE001
            await q.message.reply_text(result_text)
    except Exception as e:  # noqa: BLE001
        logger.error(f"AI approval callback: {e}")
        try:
            await q.edit_message_text(f"❌ Hata: {str(e)[:100]}")
        except Exception:  # noqa: BLE001
            pass


# ═══ Phase 79b: /analyze Apply/Skip Callback ═══
async def analyze_apply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle analyze_apply / analyze_skip inline buttons for /analyze actions."""
    q = update.callback_query
    await q.answer()
    engine = context.bot_data.get("engine")
    if not engine or not engine.analyst:
        try:
            await q.edit_message_text("⚠️ Engine bulunamadi.")
        except Exception:  # noqa: BLE001
            pass
        return

    msg_id = str(q.message.message_id)
    approved = q.data == "analyze_apply"

    try:
        if approved:
            result_text = await engine.analyst.execute_analyze_actions(msg_id)
        else:
            # Remove from pending
            engine.analyst.__class__._pending_analyze.pop(msg_id, None)
            result_text = "⏭ <b>Analiz aksiyonlari atlanildi.</b>"
        try:
            await q.edit_message_text(result_text, parse_mode="HTML")
        except Exception:  # noqa: BLE001
            await q.message.reply_text(result_text, parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Analyze apply callback: {e}")
        try:
            await q.edit_message_text(f"❌ Hata: {str(e)[:100]}")
        except Exception:  # noqa: BLE001
            pass


# ═══ Phase 79b: /analyze → Brain Cycle fallback button ═══
async def analyze_brain_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle analyze_brain button — runs brain cycle with auto-execution."""
    q = update.callback_query
    await q.answer("Brain Cycle baslatiliyor...")
    engine = context.bot_data.get("engine")
    if not engine or not engine.analyst:
        try:
            await q.edit_message_text("⚠️ Engine bulunamadi.")
        except Exception:  # noqa: BLE001
            pass
        return

    try:
        await q.edit_message_text(
            "⏳ <b>Brain Cycle calisiyor...</b>\n2-Agent mode: Optimist + Critic", parse_mode="HTML"
        )
        result = await engine.analyst.run_brain_cycle()
        if result:
            safe_result = result[:3500]
            try:
                await q.message.reply_text(
                    f"🧠 <b>Brain Cycle Tamamlandi</b>\n\n{safe_result}", parse_mode="HTML"
                )
            except Exception:  # noqa: BLE001
                await q.message.reply_text(f"🧠 Brain Cycle: {safe_result[:500]}")
        else:
            await q.message.reply_text("⚠️ Brain cycle sonuc uretmedi.")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Analyze brain callback: {e}")
        try:
            await q.message.reply_text(f"❌ Brain Cycle Hatasi: {str(e)[:100]}")
        except Exception:  # noqa: BLE001
            pass


# ═══ Phase 79b: Strategy Suggester Approve/Reject Callback ═══
async def suggest_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle suggest_approve / suggest_reject inline buttons."""
    q = update.callback_query
    await q.answer()

    approved = q.data == "suggest_approve"

    try:
        from core.strategy_suggester import StrategySuggester

        pending = StrategySuggester._pending_suggest

        if not pending or "strategy" not in pending:
            await q.edit_message_text("⚠️ Oneri bulunamadi veya zaman asimina ugradi.")
            return

        if not approved:
            StrategySuggester._pending_suggest = {}
            await q.edit_message_text("⏭ <b>Strateji onerisi reddedildi.</b>", parse_mode="HTML")
            return

        # Approved — create the strategy
        suggester = context.application.bot_data.get("strategy_suggester")
        if not suggester:
            await q.edit_message_text("⚠️ Strategy Suggester bulunamadi.")
            return

        strat = pending["strategy"]
        reasoning = pending.get("reasoning", "Kullanici onayladi")
        bt = pending.get("backtest", {})

        sid = await suggester._create_strategy(strat, reasoning, bt)
        StrategySuggester._pending_suggest = {}

        if sid:
            label = strat.get("label_hint", strat.get("strategy_type", "?"))
            bt_str = ""
            if bt:
                bt_str = f"\nBacktest: {bt.get('trades', 0)}t WR={bt.get('wr', 0):.0f}% PnL={bt.get('pnl', 0):+.2f}"
            await q.edit_message_text(
                f"✅ <b>Strateji Olusturuldu!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"AI_{label} [{strat.get('strategy_type')}]\n"
                f"{strat.get('asset')}/{strat.get('timeframe')} {strat.get('direction')} "
                f"@{strat.get('odds_threshold')}\n"
                f"$1.00 ile paper trading basliyor{bt_str}",
                parse_mode="HTML",
            )
        else:
            await q.edit_message_text("❌ Strateji olusturulamadi (muhtemelen zaten var).")

    except Exception as e:  # noqa: BLE001
        logger.error(f"Suggest callback: {e}")
        try:
            await q.edit_message_text(f"❌ Hata: {str(e)[:100]}")
        except Exception:  # noqa: BLE001
            pass
