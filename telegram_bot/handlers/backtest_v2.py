"""
PolyPaper Bot - Backtest Telegram Handlers (replay-only)

2026-05-21 (Heddas direktifi tam temizlik):
  • engine_v2 (sentetik snapshot) yolu tamamen silindi.
  • polybacktest API/gamma_hist bagimliligi kaldirildi.
  • PolyCop interactive config panel + STRATEGY_CATALOG silindi
    (config_callback, bt2c_* + bt2_* callback'leri unwired).
  • /backtest_v2 + /bt2 shim'i silindi 2026-05-22 (#9) — /backtest LAB tek
    gorunur kapi. Bu modulde /compare + /backtest_replay CLI motoru kalir.
  • /compare replay_engine'e refactor edildi (gercek L2 ob_snapshots).
  • /backtest_replay (Phase 51 P51-03) korundu — replay panel + button
    flow + multi-strategy compare hepsi replay_engine uzerinde.

Bu dosya artik SADECE gercek L2 ob_snapshots tabanli backtest yollarini
icerir — Heddas direktifi "topladığımız veri üstünden yaptığımız backtest
en gerçekçi olanı".

T11.8-B (2026-04-24): `# noqa: BLE001` koru — replay zinciri ReplayEngine
+ ChartGenerator + asyncio + telegram I/O'yu kapsar.
"""

import asyncio
import io
import logging
from datetime import UTC, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from backtest.strategies.base import StrategyRegistryV2
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.backtest_v2")

# Phase 79 S1-12: Cancel mechanism for heavy commands
# Maps chat_id -> asyncio.Event to signal cancellation
_cancel_events: dict[int, asyncio.Event] = {}


# Strategy display names — 2026-05-21 (Heddas direktifi): 11 hazir Python
# stratejisi silindi (hicbiri para kazandirmadi). Sadece RuleBasedStrategy
# (LAB no-code kural sistemi) kaldi.
REPLAY_STRATEGIES = {
    "rule_based": "📋 Rule-based (LAB kurallarini koşturur)",
}


# 2026-05-22 (Heddas #9 "akilli tam"): /backtest_v2 + /bt2 shim'i (backtest_v2_cmd)
# silindi — saf yonlendirme idi. /backtest LAB tek gorunur kapi. Bu moduldeki
# /compare + /backtest_replay (gercek replay motoru, esnek asset/tf) KORUNDU.


# ════════════════════════════════════════════════════════════════════
# /compare — multi-strategy karsilastirma (replay_engine)
# ════════════════════════════════════════════════════════════════════


async def compare_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /compare command.

    2026-05-21 (Heddas direktifi): engine_v2 (sentetik snapshot) silindi.
    Compare artik replay_engine (gercek L2 ob_snapshots) kullanir.

    Args: strateji isimleri (StrategyRegistryV2'de kayitli olan herhangi
    biri — eski hardcoded STRATEGY_CATALOG kisitlamasi kaldirildi).
    """
    db = context.bot_data.get("db")
    args = context.args if context.args else []

    if not db:
        await update.message.reply_text("⚠️ DB bulunamadi.", parse_mode="HTML")
        return

    if len(args) < 2:
        await update.message.reply_text(
            "📊 <b>Strateji Karsilastirma</b> (gercek L2 replay)\n\n"
            "Kullanim: <code>/compare strat1 strat2 [strat3...]</code>\n\n"
            "<i>2026-05-21: hazir Python stratejileri silindi. Su an sadece "
            "<code>rule_based</code> kayitli. Multi-ruleset karsilastirma "
            "icin /lab → Karsilastir paneline gec.</i>",
            parse_mode="HTML",
        )
        return

    # Strateji listesini validate et
    available = set(StrategyRegistryV2.list_all())
    strategies = [a.lower() for a in args]
    invalid = [s for s in strategies if s not in available]
    if invalid:
        await update.message.reply_text(
            f"❌ Bilinmeyen strateji(ler): {', '.join(invalid)}\n"
            "<i>Kayitli stratejileri /strategies veya /lab → Kurucu ile gor.</i>",
            parse_mode="HTML",
        )
        return

    await _run_compare(update, db, strategy_names=strategies)


# ════════════════════════════════════════════════════════════════════
# /backtest_replay (Phase 51 P51-03) — replay panel + execute
# ════════════════════════════════════════════════════════════════════


async def backtest_replay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /backtest_replay command — interactive replay panel."""
    args = context.args if context.args else []
    db = context.bot_data.get("db")

    if not db:
        return await update.message.reply_text("⚠️ DB bulunamadi.", parse_mode="HTML")

    # Check if we have any recorded data
    try:
        r = await db.conn.execute_fetchall(
            "SELECT COUNT(*), COUNT(DISTINCT slug) FROM ob_snapshots"
        )
        snap_count = r[0][0] if r else 0
        market_count = r[0][1] if r else 0
    except Exception:  # noqa: BLE001
        snap_count = 0
        market_count = 0

    if snap_count < 10:
        return await update.message.reply_text(
            "⚠️ <b>Yetersiz Veri</b>\n\n"
            f"Kayitli snapshot: {snap_count}\n"
            "Scanner + WS veri topladikca artar (bot calistikca).\n",
            parse_mode="HTML",
        )

    if not args:
        await _show_replay_panel(update, db, snap_count, market_count)
        return

    # Parse: /backtest_replay strategy [asset] [tf]
    strategy_name = args[0].lower()
    asset = args[1].upper() if len(args) > 1 else ""
    tf = args[2] if len(args) > 2 else ""

    await _run_replay(update, db, strategy_name, asset, tf)


async def _show_replay_panel(update: Update, db, snap_count: int, market_count: int):
    """Show interactive replay config panel."""
    r = await db.conn.execute_fetchall("SELECT MIN(ts_ms), MAX(ts_ms) FROM ob_snapshots")

    def _ms_to_iso(ts_ms):
        if not ts_ms:
            return "N/A"
        return datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC).isoformat()[:16]

    oldest = _ms_to_iso(r[0][0]) if r else "N/A"
    newest = _ms_to_iso(r[0][1]) if r else "N/A"

    # Get asset breakdown
    r = await db.conn.execute_fetchall(
        "SELECT asset, timeframe, COUNT(*) FROM ob_snapshots "
        "GROUP BY asset, timeframe ORDER BY COUNT(*) DESC LIMIT 6"
    )
    breakdown = ""
    for row in r:
        breakdown += f"  {row[0]} {row[1]}: {row[2]:,} snap\n"

    text = (
        "🔄 <b>Replay Backtest — Gercek Veri</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📸 Kayitli: <b>{snap_count:,}</b> snapshot\n"
        f"📊 Market: <b>{market_count}</b> benzersiz\n"
        f"🕐 Aralik: {oldest} → {newest}\n\n"
        f"📋 <b>Veri Dagilimi</b>\n{breakdown}\n"
        "🎯 <b>Fill Mode:</b> REAL_ORDERBOOK\n"
        "<i>Gercek L2 depth walk — VWAP fill</i>\n\n"
        "Strateji sec ve baslat:"
    )

    # Build keyboard — 3 per row
    kb_rows = []
    strats = list(REPLAY_STRATEGIES.items())
    for i in range(0, len(strats), 3):
        row = []
        for key, label in strats[i : i + 3]:
            row.append(InlineKeyboardButton(label, callback_data=f"replay_{esc(key)}"))
        kb_rows.append(row)

    # All strategies button
    kb_rows.append(
        [
            InlineKeyboardButton(
                "🏆 Tum Stratejileri Karsilastir", callback_data="replay_compare_all"
            ),
        ]
    )

    kb = InlineKeyboardMarkup(kb_rows)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def replay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle replay button callbacks (replay_*)."""
    query = update.callback_query
    data = query.data
    db = context.bot_data.get("db")

    if not db:
        await query.answer("DB bulunamadi", show_alert=True)
        return

    if data == "replay_compare_all":
        await query.answer("⏳ Tum stratejiler karsilastiriliyor...")
        await _run_compare(query, db, strategy_names=None)  # None → REPLAY_STRATEGIES
        return

    # replay_STRATEGY_NAME
    strategy_name = data.replace("replay_", "")

    if strategy_name not in REPLAY_STRATEGIES and not StrategyRegistryV2.get(strategy_name):
        await query.answer(f"Bilinmeyen strateji: {esc(strategy_name)}", show_alert=True)
        return

    await query.answer(f"⏳ {esc(strategy_name)} replay baslatiliyor...")
    await _run_replay(query, db, strategy_name)


async def _run_replay(source, db, strategy_name: str, asset: str = "", timeframe: str = ""):
    """Execute single-strategy replay backtest and send results.

    2026-05-21 (Heddas direktifi): eski replay_engine (schema-broken) silindi,
    yeni minimal `backtest.runner.BacktestRunner` kullaniliyor. Sadece
    rule_based stratejisi calisir (11 hazir plugin silindi).
    """
    try:
        from backtest.runner import BacktestRunner, RunConfig

        # Check strategy exists
        strat_cls = StrategyRegistryV2.get(strategy_name)
        if not strat_cls:
            await _reply(
                source,
                f"❌ Strateji bulunamadi: {esc(strategy_name)}\n"
                f"Mevcut: {', '.join(StrategyRegistryV2.list_all())}\n"
                "<i>Kendi kuralini /lab → 🛠 Strateji Kurucu ile yaz.</i>",
            )
            return

        # 2026-05-21: rule_based icin kayitli ruleset yukle. /backtest_replay
        # rule_based komutu strategy_params vermez → bos default → 0 trade.
        # Kullanicinin en son kaydettigi ruleset'i otomatik yukle (birden
        # fazlaysa Adim 3 wizard'da secim gelir). Hic ruleset yoksa uyar.
        strategy_params: dict = {}
        loaded_ruleset_name = ""
        if strategy_name == "rule_based":
            from backtest.strategies.rule_based import list_rulesets

            rulesets = list_rulesets()
            if not rulesets:
                await _reply(
                    source,
                    "⚠️ <b>Kayitli kural yok</b>\n\n"
                    "rule_based stratejisi bir kural seti gerektirir.\n"
                    "Once <code>/lab</code> → 🛠 Strateji Kurucu → 🧙 Preset "
                    "Sihirbazi ile bir kural olustur, sonra tekrar dene.",
                )
                return
            strategy_params = rulesets[0]  # en son / ilk kayitli
            loaded_ruleset_name = strategy_params.get("name", "?")

        rs_line = (
            f"Kural seti: <code>{esc(loaded_ruleset_name)}</code>\n"
            if loaded_ruleset_name
            else ""
        )
        await _reply(
            source,
            f"🔄 <b>Replay Backtest</b>\n\n"
            f"Strateji: {esc(strategy_name)}\n"
            f"{rs_line}"
            f"Asset: {esc(asset or 'TÜMÜ')} | TF: {esc(timeframe or 'TÜMÜ')}\n"
            "Veri: ob_snapshots (modern schema)\n\n"
            "⏳ Hesaplaniyor...",
        )

        cfg = RunConfig(
            asset=asset.upper() if asset else "",
            timeframe=timeframe if timeframe else "",
            strategy_name=strategy_name,
            strategy_params=strategy_params,
            last_n=100,
        )

        runner = BacktestRunner(db)
        summary = await runner.run(cfg)

        # Format results
        result_text = _format_replay_results(strategy_name, summary)
        await _reply(source, result_text)

    except Exception as e:  # noqa: BLE001
        logger.error("Replay backtest failed: %s", e, exc_info=True)
        await _reply(
            source,
            f"❌ <b>Replay Hatasi</b>\n\n"
            f"Strateji: {esc(strategy_name)}\n"
            f"Detay: {str(e)[:150]}",
        )


async def _run_compare(source, db, strategy_names: list[str] | None = None):
    """Run replay for a list of strategies (or REPLAY_STRATEGIES if None) and compare.

    2026-05-21 (Heddas direktifi): tek birleşik fonksiyon. Eski iki ayrı
    yol (_run_comparison engine_v2 ile, _run_compare_all replay_engine ile)
    yerine replay_engine üzerinde tek fonksiyon. strategy_names=None ise
    REPLAY_STRATEGIES default listesini kullanır (button: "Tum Stratejileri").
    """
    try:
        from backtest.runner import BacktestRunner, RunConfig

        names = strategy_names if strategy_names else list(REPLAY_STRATEGIES.keys())

        results: list[tuple[str, object]] = []
        for name in names:
            strat_cls = StrategyRegistryV2.get(name)
            if not strat_cls:
                continue

            cfg = RunConfig(strategy_name=name, last_n=100)
            runner = BacktestRunner(db)
            summary = await runner.run(cfg)
            results.append((name, summary))

        if not results:
            await _reply(source, "⚠️ Hic strateji calistirilamadi.")
            return

        # Sort by PnL
        results.sort(key=lambda x: x[1].total_pnl, reverse=True)

        text = "🏆 <b>Replay Karsilastirma — Gercek Veri</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━━━\n"
        text += "<i>Veri: ob_snapshots (modern schema)</i>\n\n"

        for i, (name, s) in enumerate(results):
            rank = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i+1}."
            label = REPLAY_STRATEGIES.get(name, name)
            pnl_icon = "🟢" if s.total_pnl > 0 else "🔴"

            text += (
                f"{rank} <b>{esc(label)}</b>\n"
                f"   {pnl_icon} PnL: ${s.total_pnl:+.2f} | "
                f"WR: {s.win_rate:.1f}% | "
                f"Trade: {s.n_trades}\n"
            )

        text += (
            f"\n📊 Market: {results[0][1].n_markets_processed} "
            f"(+{results[0][1].n_markets_skipped} skip)"
        )

        await _reply(source, text)

    except Exception as e:  # noqa: BLE001
        logger.error("Replay compare failed: %s", e, exc_info=True)
        await _reply(source, f"❌ <b>Karsilastirma Hatasi</b>\n\nDetay: {str(e)[:150]}")


def _format_replay_results(strategy_name: str, summary) -> str:
    """Format replay results for Telegram.

    2026-05-21: yeni RunSummary (dataclass) ile uyumlu — eski dict
    access pattern (summary.get('total_pnl')) yerine attribute access.
    """
    pnl_icon = "🟢" if summary.total_pnl > 0 else "🔴" if summary.total_pnl < 0 else "⚪"

    note_block = f"\n<i>⚠️ {esc(summary.note)}</i>" if summary.note else ""

    text = (
        "🔄 <b>Replay Backtest Sonucu</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 Strateji: <b>{esc(strategy_name)}</b>\n"
        "📸 Veri: ob_snapshots (modern schema)\n\n"
        "📊 <b>Performans</b>\n"
        f"  {pnl_icon} PnL: <b>${summary.total_pnl:+.2f}</b>\n"
        f"  🎯 Win Rate: <b>{summary.win_rate:.1f}%</b>\n"
        f"  📈 Trade: {summary.n_trades} "
        f"({summary.wins}W/{summary.losses}L)\n"
        f"  💰 Avg PnL: ${summary.avg_pnl:+.4f}\n\n"
        "💸 <b>Maliyet</b>\n"
        f"  💰 Fee: ${summary.fees_total:.4f}\n\n"
        "📸 <b>Veri</b>\n"
        f"  Market discovered: {summary.n_markets_discovered}\n"
        f"  Market processed: {summary.n_markets_processed} "
        f"(+{summary.n_markets_skipped} skip)\n"
        f"  Final balance: ${summary.final_balance:.2f}"
        f"{note_block}"
    )
    return text


# ════════════════════════════════════════════════════════════════════
# Yardımcılar
# ════════════════════════════════════════════════════════════════════


async def _reply(source, text: str, parse_mode: str = "HTML"):
    """Reply to either a Message or CallbackQuery."""
    try:
        if hasattr(source, "message") and source.message:
            if hasattr(source, "edit_message_text"):
                # CallbackQuery
                await source.message.reply_text(text, parse_mode=parse_mode)
            else:
                await source.message.reply_text(text, parse_mode=parse_mode)
        elif hasattr(source, "reply_text"):
            await source.reply_text(text, parse_mode=parse_mode)
    except Exception as e:  # noqa: BLE001
        logger.error("Reply failed: %s", e)


async def _send_photo(source, photo_bytes: bytes, caption: str = ""):
    """Send a photo to the chat."""
    try:
        bio = io.BytesIO(photo_bytes)
        bio.name = "chart.png"
        if hasattr(source, "message") and source.message:
            await source.message.reply_photo(bio, caption=caption)
        elif hasattr(source, "reply_photo"):
            await source.reply_photo(bio, caption=caption)
    except Exception as e:  # noqa: BLE001
        logger.error("Send photo failed: %s", e)


# Becker block tam silindi (2026-05-20 cleanup) — bkz commit 60a53ad.
# engine_v2 + polybacktest yolu tam silindi (2026-05-21 cleanup) — bu commit.


# ════════════════════════════════════════════════════════════════════
# Cancel callback (Phase 79 S1-12)
# ════════════════════════════════════════════════════════════════════


async def cancel_operation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle cancel button for heavy /compare or /backtest_replay operations.

    `_cancel_events` map (chat_id → asyncio.Event) replay zincirinde
    operatorun iptal isimleri icin kullanilir. 2026-05-21'de engine_v2
    yolu silindigi icin set noktasi azaldi ama event mekanizmasi korunuyor
    (gelecek long-running replay'ler icin altyapı).
    """
    chat_id = update.effective_chat.id
    evt = _cancel_events.get(chat_id)
    if evt:
        evt.set()
        await update.callback_query.answer("İptal işlemi başlatılıyor...")
        await update.callback_query.edit_message_text(
            "❌ <b>İşlem iptal edildi.</b>", parse_mode="HTML"
        )
    else:
        await update.callback_query.answer("Aktif işlem yok.")


# Eski deprecated semboller (backward-compat — yeni bot.py import etmiyor)
# silinen: backtest_v2_callback, backtest_v2_config_callback,
# handle_limit_input, register_handlers, _show_config_panel,
# _run_backtest, _run_comparison, _build_config_text,
# _build_config_keyboard, _show_all_strategies,
# STRATEGY_CATALOG, AVAILABLE_ASSETS, AVAILABLE_TIMEFRAMES, DEFAULT_CONFIG.
