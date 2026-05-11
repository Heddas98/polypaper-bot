"""
PolyPaper Bot - Backtest v2 Telegram Handler
Yeni esnek backtest motoru için Telegram komutları.
PolyCop AFK-style interactive config panel.

Commands:
  /backtest_v2                → Interactive config panel
  /backtest_v2 hour_edge      → Saat bazlı edge testi (eski format)
  /backtest_v2 streak_reversal → Streak reversal testi (eski format)
  /backtest_v2 taker_flow     → Taker flow testi (eski format)
  /backtest_v2 composite      → Multi-signal fusion testi (eski format)
  /compare hour_edge streak_reversal → İki strateji karşılaştır

T11.8-B (2026-04-24): Every catch in this module is annotated `# noqa:
BLE001`. Backtest v2 touches ReplayEngine + ParquetWriter + matplotlib +
DB + asyncio.to_thread + telegram send_photo — heterogeneous failure
surface across 6+ libraries. T11.6 render policy preserved on user-facing
reply paths via `render_user_exception()` where present.
"""

import asyncio
import io
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from telegram_bot.handlers._exc_render import render_user_exception
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.backtest_v2")

# Phase 79 S1-12: Cancel mechanism for heavy commands
# Maps chat_id -> asyncio.Event to signal cancellation
_cancel_events: dict[int, asyncio.Event] = {}

# Available strategies and their display names
STRATEGY_CATALOG = {
    "hour_edge": ("🕐 Hour Edge", "Saat bazlı yön biası (57.8% WR)"),
    "streak_reversal": ("🔄 Streak Reversal", "N ardışık aynı yönden sonra ters bet"),
    "late_convergence": ("⏰ Late Convergence", "Son dakikada dominant yöne bet (96-98.9% WR)"),
    "taker_flow": ("📊 Taker Flow", "Binance agresif hacim dominansı (62.7% WR)"),
    "orderbook_imbalance": ("📚 OB Imbalance", "Bid/ask depth asimetrisi (57.6%)"),
    "fade_rip": ("↩️ Fade the Rip", "Büyük BTC hareketinden sonra ters yön"),
    "cross_coin": ("🔗 Cross-Coin", "BTC→ETH/SOL korelasyon sinyali"),
    "opening_breakout": ("💥 Opening Breakout", "İlk dakika breakout ($10+ move)"),
    "funding_rate": ("💰 Funding Rate", "Binance funding rate sinyali"),
    "calibration_arb": ("📐 Calibration Arb", "Fiyat-olasılık sapma tespiti"),
    "composite": ("🧠 Composite", "Birden fazla sinyali birleştir"),
}

# Available assets
AVAILABLE_ASSETS = ["BTC", "ETH", "SOL", "XRP"]

# Available timeframes
AVAILABLE_TIMEFRAMES = ["5m", "15m"]

# Default config
DEFAULT_CONFIG = {
    "coin": "BTC",
    "tf": "5m",
    "strategy": "composite",
    "limit": 50,
}


async def backtest_v2_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /backtest_v2 command."""
    # Phase 41c: soft deprecation banner — point users to /backtest_replay
    # which uses real L2 orderbook history (Phase 37 ReplayEngine).
    try:
        await update.message.reply_text(
            "⚠️ <b>backtest_v2 deprecated</b>\n\n"
            "v2 senthetik snapshot kullanır (4/10 realism).\n"
            "Yeni backtest'ler için <b>/backtest_replay</b> kullan — "
            "gerçek L2 orderbook geçmişi (9/10 realism).\n\n"
            "v2 sadece 11 legacy strateji port edilene kadar açık.",
            parse_mode="HTML",
        )
    except Exception:  # noqa: BLE001
        pass
    args = context.args if context.args else []

    if not args:
        # Show interactive config panel
        if "bt2_config" not in context.user_data:
            context.user_data["bt2_config"] = DEFAULT_CONFIG.copy()
        await _show_config_panel(update, context)
        return

    strategy_name = args[0].lower()

    if strategy_name not in STRATEGY_CATALOG:
        await update.message.reply_text(
            f"❌ Bilinmeyen strateji: {esc(strategy_name)}\n\n"
            f"Mevcut stratejiler:\n" + "\n".join(f"  • {k}" for k in STRATEGY_CATALOG),
            parse_mode="HTML",
        )
        return

    # Parse optional params: /backtest_v2 hour_edge BTC 5m 50 [split]
    # Phase 47f.10 P3#14: trailing "split" arg triggers train/test 70/30
    split_mode = False
    norm_args = list(args)
    if norm_args and norm_args[-1].lower() == "split":
        split_mode = True
        norm_args = norm_args[:-1]

    coin = norm_args[1].upper() if len(norm_args) > 1 else "BTC"
    market_type = norm_args[2] if len(norm_args) > 2 else "5m"
    limit = int(norm_args[3]) if len(norm_args) > 3 else 50

    await _run_backtest(update, strategy_name, coin, market_type, limit, split=split_mode)


async def compare_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /compare command."""
    args = context.args if context.args else []

    if len(args) < 2:
        await update.message.reply_text(
            "📊 <b>Strateji Karşılaştırma</b>\n\n"
            "Kullanım: <code>/compare strat1 strat2 [strat3...]</code>\n\n"
            "Örnek: <code>/compare hour_edge streak_reversal taker_flow</code>",
            parse_mode="HTML",
        )
        return

    # Trailing "split" keyword → train/test split with overfit gate
    split_mode = False
    if args and args[-1].lower() == "split":
        split_mode = True
        args = args[:-1]

    strategies = [a.lower() for a in args]
    invalid = [s for s in strategies if s not in STRATEGY_CATALOG]
    if invalid:
        await update.message.reply_text(f"❌ Bilinmeyen strateji(ler): {', '.join(invalid)}")
        return

    await _run_comparison(update, strategies, split=split_mode)


async def _show_config_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show interactive PolyCop-style config panel."""
    config = context.user_data.get("bt2_config", DEFAULT_CONFIG.copy())

    # Build text summary
    strategy_label, _ = STRATEGY_CATALOG.get(config["strategy"], ("❓", ""))
    summary = (
        f"📊 <b>Backtest v2 — Konfigürasyon</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 Strateji: {strategy_label}\n"
        f"💰 Asset: {config['coin']}\n"
        f"⏱ Zaman Dilimi: {config['tf']}\n"
        f"📈 Market Limiti: {config['limit']}\n\n"
        f"Her parametreyi düzenlemek için butona tıkla."
    )

    # Build keyboard
    keyboard = []

    # Row 1: Asset toggle buttons
    asset_row = []
    for asset in AVAILABLE_ASSETS:
        check = "✅" if asset == config["coin"] else ""
        asset_row.append(
            InlineKeyboardButton(
                f"{esc(asset)} {check}".strip(), callback_data=f"bt2c_coin_{esc(asset)}"
            )
        )
    keyboard.append(asset_row)

    # Row 2: Timeframe toggle buttons
    tf_row = []
    for tf in AVAILABLE_TIMEFRAMES:
        check = "✅" if tf == config["tf"] else ""
        tf_row.append(
            InlineKeyboardButton(
                f"{check} {tf}".strip() if check else tf, callback_data=f"bt2c_tf_{tf}"
            )
        )
    keyboard.append(tf_row)

    # Row 3: Limit editing button
    keyboard.append(
        [InlineKeyboardButton(f"📈 Limit: {config['limit']}", callback_data="bt2c_limit")]
    )

    # Row 4-5: Strategy selection (first 6 strategies)
    strat_items = list(STRATEGY_CATALOG.items())
    first_6 = strat_items[:6]
    strat_row1 = []
    for key, (label, _) in first_6[:3]:
        check = "✅" if key == config["strategy"] else ""
        strat_row1.append(
            InlineKeyboardButton(f"{esc(label)}", callback_data=f"bt2c_strat_{esc(key)}")
        )
    if strat_row1:
        keyboard.append(strat_row1)

    strat_row2 = []
    for key, (label, _) in first_6[3:]:
        strat_row2.append(
            InlineKeyboardButton(f"{esc(label)}", callback_data=f"bt2c_strat_{esc(key)}")
        )
    if strat_row2:
        keyboard.append(strat_row2)

    # More strategies button if needed
    if len(strat_items) > 6:
        keyboard.append(
            [InlineKeyboardButton("📚 Daha Fazla Strateji", callback_data="bt2c_more_strats")]
        )

    # Row 6: Action buttons
    keyboard.append(
        [
            InlineKeyboardButton("▶️ Backtest Başlat", callback_data="bt2c_run"),
            InlineKeyboardButton("📊 Karşılaştır", callback_data="bt2c_compare"),
        ]
    )

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        summary,
        reply_markup=reply_markup,
        parse_mode="HTML",
    )


async def backtest_v2_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new PolyCop-style config callbacks (bt2c_ prefix)."""
    query = update.callback_query
    data = query.data

    if "bt2_config" not in context.user_data:
        context.user_data["bt2_config"] = DEFAULT_CONFIG.copy()

    config = context.user_data["bt2_config"]

    # Asset toggle: bt2c_coin_BTC, bt2c_coin_ETH, etc.
    if data.startswith("bt2c_coin_"):
        coin = data[10:]
        if coin in AVAILABLE_ASSETS:
            config["coin"] = coin
            await query.answer(f"✅ {coin} seçildi")
            await query.edit_message_text(
                await _build_config_text(config),
                reply_markup=await _build_config_keyboard(config),
                parse_mode="HTML",
            )

    # Timeframe toggle: bt2c_tf_5m, bt2c_tf_15m, etc.
    elif data.startswith("bt2c_tf_"):
        tf = data[8:]
        if tf in AVAILABLE_TIMEFRAMES:
            config["tf"] = tf
            await query.answer(f"✅ {tf} seçildi")
            await query.edit_message_text(
                await _build_config_text(config),
                reply_markup=await _build_config_keyboard(config),
                parse_mode="HTML",
            )

    # Limit editing: bt2c_limit
    elif data == "bt2c_limit":
        await query.answer()
        context.user_data["bt2_editing_limit"] = True
        await query.message.reply_text(
            "📈 <b>Market Limiti</b>\n\n"
            f"Şu anki değer: {config['limit']}\n\n"
            "Yeni sayı girin (1-500):",
            parse_mode="HTML",
        )

    # Strategy selection: bt2c_strat_STRATNAME
    elif data.startswith("bt2c_strat_"):
        strat_name = data[11:]
        if strat_name in STRATEGY_CATALOG:
            config["strategy"] = strat_name
            label, desc = STRATEGY_CATALOG[strat_name]
            await query.answer(f"✅ {esc(label)} seçildi", show_alert=False)
            await query.edit_message_text(
                await _build_config_text(config),
                reply_markup=await _build_config_keyboard(config),
                parse_mode="HTML",
            )

    # More strategies button
    elif data == "bt2c_more_strats":
        await query.answer()
        await _show_all_strategies(query.message, context)

    # Run backtest: bt2c_run
    elif data == "bt2c_run":
        await query.answer("⏳ Backtest başlatılıyor...")
        await _run_backtest(
            query, config["strategy"], config["coin"], config["tf"], config["limit"]
        )

    # Compare mode: bt2c_compare
    elif data == "bt2c_compare":
        await query.answer()
        await query.edit_message_text(
            "📊 <b>Strateji Karşılaştırma</b>\n\n"
            "Karşılaştırma modu için /compare komutunu kullanın:\n"
            "<code>/compare hour_edge streak_reversal taker_flow</code>",
            parse_mode="HTML",
        )

    # Back to main: bt2c_back_main
    elif data == "bt2c_back_main":
        await query.answer()
        await query.edit_message_text(
            await _build_config_text(config),
            reply_markup=await _build_config_keyboard(config),
            parse_mode="HTML",
        )


async def backtest_v2_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle old-style bt2_ callbacks (backward compatibility)."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("bt2_"):
        return

    strategy_name = data[4:]  # remove "bt2_" prefix

    if strategy_name not in STRATEGY_CATALOG:
        await query.edit_message_text(f"❌ Bilinmeyen strateji: {esc(strategy_name)}")
        return

    label, desc = STRATEGY_CATALOG[strategy_name]
    await query.edit_message_text(
        f"⏳ {esc(label)} backtest başlatılıyor...\n"
        f"📝 {desc}\n\n"
        f"Coin: BTC | TF: 5m | Limit: 50 market",
        parse_mode="HTML",
    )

    # Run the backtest
    await _run_backtest(query, strategy_name, "BTC", "5m", 50)


async def _run_backtest(
    source, strategy_name: str, coin: str, market_type: str, limit: int, split: bool = False
):
    """Execute a backtest and send results.

    If split=True, runs train/test 70/30 split via engine.run_split and
    reports overfit verdict in addition to overall stats. Phase 47f.10 P3#14.
    """
    try:
        # Import here to avoid circular imports
        from backtest.analytics.charts import ChartGenerator
        from backtest.analytics.reporter import BacktestReporter
        from backtest.data_sources.cache import BacktestCache
        from backtest.data_sources.gamma_hist import GammaHistClient
        from backtest.data_sources.polybacktest import PolyBackTestClient
        from backtest.engine_v2 import BacktestConfig, BacktestEngineV2
        from backtest.strategies import StrategyRegistryV2

        # Get strategy class
        strat_cls = StrategyRegistryV2.get(strategy_name)
        if not strat_cls:
            await _reply(source, f"❌ Strateji bulunamadı: {esc(strategy_name)}")
            return

        # Setup config
        config = BacktestConfig(
            strategy_name=strategy_name,
            coin_filter=coin,
            market_type_filter=market_type,
            max_markets=limit,
        )

        # Initialize data sources
        cache = BacktestCache()
        await cache.init()

        # Fetch market data
        await _reply(source, "📡 Veri çekiliyor...")

        gamma = GammaHistClient(cache=cache)
        await gamma.init()

        markets = await gamma.get_resolved_markets(coin=coin, limit=limit, market_type=market_type)

        if not markets:
            await _reply(source, f"⚠️ {coin} {market_type} için resolved market bulunamadı.")
            await gamma.close()
            return

        # Try to get snapshots from PolyBackTest
        polybt = PolyBackTestClient(cache=cache)
        await polybt.init()

        snapshots_by_market = {}
        for m in markets[:limit]:
            mid = m.get("market_id", "")
            snaps = await polybt.get_snapshots(mid, market_dict=m)
            if snaps:
                snapshots_by_market[mid] = snaps

        # Run engine
        await _reply(
            source,
            f"🔄 {len(markets)} market üzerinde " f"{esc(strategy_name)} testi çalıştırılıyor...",
        )

        engine = BacktestEngineV2(config=config)

        # Phase 79 S1-12: Setup cancel event for heavy operations
        chat_id = source.message.chat.id if hasattr(source, "message") else source.effective_chat.id
        cancel_evt = asyncio.Event()
        _cancel_events[chat_id] = cancel_evt

        try:
            if split:
                # Progress message with cancel button
                keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ İptal", callback_data="cancel_backtest")]]
                )
                await _reply(
                    source, "⏳ Backtest çalışıyor... (arka planda)", reply_markup=keyboard
                )
                split_result = await asyncio.to_thread(
                    engine.run_split, markets, snapshots_by_market, 0.70
                )
                split_result["test"] or split_result["train"]
                div = split_result.get("divergence") or {}
                overfit = split_result.get("overfit", False)
                verdict = "🔴 <b>OVERFIT</b>" if overfit else "🟢 <b>GENERALIZES</b>"
                summary = (
                    f"{verdict}\n"
                    f"Train: {div.get('train_wr', 0):.1f}% WR / "
                    f"${div.get('train_pnl', 0):.2f} PnL\n"
                    f"Test:  {div.get('test_wr', 0):.1f}% WR / "
                    f"${div.get('test_pnl', 0):.2f} PnL\n"
                    f"Δ WR: {div.get('wr_delta', 0):+.1f}pp  "
                    f"sign_flip={div.get('sign_flip', False)}"
                )
                await _reply(source, summary, parse_mode="HTML")
            else:
                # Progress message with cancel button
                keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ İptal", callback_data="cancel_backtest")]]
                )
                await _reply(
                    source, "⏳ Backtest çalışıyor... (arka planda)", reply_markup=keyboard
                )
                await asyncio.to_thread(engine.run, markets, snapshots_by_market)
        finally:
            # Clean up cancel event
            _cancel_events.pop(chat_id, None)

        # Generate report — use engine's portfolio object (has trades list)
        reporter = BacktestReporter(engine.portfolio, strategy_name)
        telegram_summary = reporter.generate_telegram_summary()

        await _reply(source, telegram_summary, parse_mode="HTML")

        # Try to send chart
        chart_gen = ChartGenerator(engine.portfolio, strategy_name)
        chart_bytes = chart_gen.equity_curve()
        if chart_bytes:
            await _send_photo(source, chart_bytes, f"📈 Equity Curve: {esc(strategy_name)}")

        # Cleanup
        await polybt.close()
        await gamma.close()

    except Exception as e:  # noqa: BLE001
        logger.error("Backtest v2 failed: %s", e, exc_info=True)
        error_msg = str(e)[:100]
        await _reply(
            source,
            f"❌ <b>Backtest Hatasi</b>\n\nIslem: {esc(strategy_name)}\nDetay: {error_msg}",
            parse_mode="HTML",
        )


async def _run_comparison(update: Update, strategy_names: list, split: bool = False):
    """Run multiple backtests and compare.

    split=True → use engine.run_split(train_ratio=0.70) for every strategy
    and append overfit verdicts per-strategy.
    """
    try:
        from backtest.analytics.comparator import StrategyComparator
        from backtest.data_sources.cache import BacktestCache
        from backtest.data_sources.gamma_hist import GammaHistClient
        from backtest.data_sources.polybacktest import PolyBackTestClient
        from backtest.engine_v2 import BacktestConfig, BacktestEngineV2
        from backtest.strategies import StrategyRegistryV2

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ İptal", callback_data="cancel_backtest")]]
        )
        await update.message.reply_text(
            f"⏳ {len(strategy_names)} strateji karşılaştırılıyor...", reply_markup=keyboard
        )

        cache = BacktestCache()
        await cache.init()

        gamma = GammaHistClient(cache=cache)
        await gamma.init()

        markets = await gamma.get_resolved_markets("btc", limit=50, market_type="5m")
        if not markets:
            await update.message.reply_text("⚠️ Resolved market bulunamadı.")
            await gamma.close()
            return

        # Phase 79 S1-12: Setup cancel event for comparison
        chat_id = update.effective_chat.id
        cancel_evt = asyncio.Event()
        _cancel_events[chat_id] = cancel_evt

        try:
            # Fetch snapshots once, share across all strategies
            polybt = PolyBackTestClient(cache=cache)
            await polybt.init()

            snapshots_by_market = {}
            for m in markets[:50]:
                mid = m.get("market_id", "")
                snaps = await polybt.get_snapshots(mid, market_dict=m)
                if snaps:
                    snapshots_by_market[mid] = snaps

            comparator = StrategyComparator()
            overfit_lines = []

            for name in strategy_names:
                strat_cls = StrategyRegistryV2.get(name)
                if not strat_cls:
                    continue
                cfg = BacktestConfig(
                    strategy_name=name, coin_filter="btc", market_type_filter="5m", max_markets=50
                )
                engine = BacktestEngineV2(config=cfg)
                if split:
                    sp = await asyncio.to_thread(
                        engine.run_split, markets, snapshots_by_market, 0.70
                    )
                    div = sp.get("divergence") or {}
                    is_overfit = sp.get("overfit", False)
                    badge = "🔴 OVERFIT" if is_overfit else "🟢 OK"
                    overfit_lines.append(
                        f"{badge} <b>{esc(name)}</b>: "
                        f"Δ WR {div.get('wr_delta', 0):+.1f}pp "
                        f"flip={div.get('sign_flip', False)}"
                    )
                else:
                    await asyncio.to_thread(engine.run, markets, snapshots_by_market)
                comparator.add_result(name, engine.portfolio)

            result = comparator.compare_telegram()
            if split and overfit_lines:
                result = (
                    "🧪 <b>Train/Test Split (70/30)</b>\n"
                    + "\n".join(overfit_lines)
                    + "\n━━━━━━━━━━━━━━━\n"
                    + result
                )
            await update.message.reply_text(result, parse_mode="HTML")

            await polybt.close()
            await gamma.close()
        finally:
            # Clean up cancel event
            _cancel_events.pop(chat_id, None)

    except Exception as e:  # noqa: BLE001
        logger.error("Comparison failed: %s", e, exc_info=True)
        error_msg = str(e)[:100]
        await update.message.reply_text(
            f"❌ <b>Karsilastirma Hatasi</b>\n\n"
            f"Islem: Strateji karsilastir ({len(strategy_names)})\n"
            f"Detay: {error_msg}",
            parse_mode="HTML",
        )


async def _build_config_text(config: dict) -> str:
    """Build the summary text for the config panel."""
    strategy_label, _ = STRATEGY_CATALOG.get(config["strategy"], ("❓", ""))
    return (
        f"📊 <b>Backtest v2 — Konfigürasyon</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 Strateji: {strategy_label}\n"
        f"💰 Asset: {config['coin']}\n"
        f"⏱ Zaman Dilimi: {config['tf']}\n"
        f"📈 Market Limiti: {config['limit']}\n\n"
        f"Her parametreyi düzenlemek için butona tıkla."
    )


async def _build_config_keyboard(config: dict) -> InlineKeyboardMarkup:
    """Build the keyboard for the config panel."""
    keyboard = []

    # Row 1: Asset toggle buttons
    asset_row = []
    for asset in AVAILABLE_ASSETS:
        check = "✅" if asset == config["coin"] else ""
        asset_row.append(
            InlineKeyboardButton(
                f"{esc(asset)} {check}".strip(), callback_data=f"bt2c_coin_{esc(asset)}"
            )
        )
    keyboard.append(asset_row)

    # Row 2: Timeframe toggle buttons
    tf_row = []
    for tf in AVAILABLE_TIMEFRAMES:
        check = "✅" if tf == config["tf"] else ""
        tf_row.append(
            InlineKeyboardButton(
                f"{check} {tf}".strip() if check else tf, callback_data=f"bt2c_tf_{tf}"
            )
        )
    keyboard.append(tf_row)

    # Row 3: Limit editing button
    keyboard.append(
        [InlineKeyboardButton(f"📈 Limit: {config['limit']}", callback_data="bt2c_limit")]
    )

    # Row 4-5: Strategy selection (first 6 strategies)
    strat_items = list(STRATEGY_CATALOG.items())
    first_6 = strat_items[:6]
    strat_row1 = []
    for key, (label, _) in first_6[:3]:
        strat_row1.append(
            InlineKeyboardButton(f"{esc(label)}", callback_data=f"bt2c_strat_{esc(key)}")
        )
    if strat_row1:
        keyboard.append(strat_row1)

    strat_row2 = []
    for key, (label, _) in first_6[3:]:
        strat_row2.append(
            InlineKeyboardButton(f"{esc(label)}", callback_data=f"bt2c_strat_{esc(key)}")
        )
    if strat_row2:
        keyboard.append(strat_row2)

    # More strategies button if needed
    if len(strat_items) > 6:
        keyboard.append(
            [InlineKeyboardButton("📚 Daha Fazla Strateji", callback_data="bt2c_more_strats")]
        )

    # Row 6: Action buttons
    keyboard.append(
        [
            InlineKeyboardButton("▶️ Backtest Başlat", callback_data="bt2c_run"),
            InlineKeyboardButton("📊 Karşılaştır", callback_data="bt2c_compare"),
        ]
    )

    return InlineKeyboardMarkup(keyboard)


async def _show_all_strategies(message, context: ContextTypes.DEFAULT_TYPE):
    """Show all strategies in a paginated view."""
    config = context.user_data.get("bt2_config", DEFAULT_CONFIG.copy())

    keyboard = []
    strat_items = list(STRATEGY_CATALOG.items())

    # Show all strategies in 2 columns
    for i in range(0, len(strat_items), 2):
        row = []
        for key, (label, _) in strat_items[i : i + 2]:
            "✅" if key == config["strategy"] else ""
            row.append(
                InlineKeyboardButton(f"{esc(label)}", callback_data=f"bt2c_strat_{esc(key)}")
            )
        keyboard.append(row)

    # Back button
    keyboard.append([InlineKeyboardButton("← Geri", callback_data="bt2c_back_main")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await message.edit_text(
        "📚 <b>Tüm Stratejiler</b>\n\n" "Bir strateji seçin:",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )


async def _reply(source, text: str, parse_mode: str = "HTML"):
    """Reply to either a Message or CallbackQuery."""
    try:
        if hasattr(source, "message") and source.message:
            # It's an Update or CallbackQuery
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


async def handle_limit_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle limit value input from user."""
    if not context.user_data.get("bt2_editing_limit"):
        return

    try:
        limit_val = int(update.message.text)
        if 1 <= limit_val <= 500:
            config = context.user_data.get("bt2_config", DEFAULT_CONFIG.copy())
            config["limit"] = limit_val
            context.user_data["bt2_config"] = config
            context.user_data["bt2_editing_limit"] = False

            await update.message.reply_text(
                f"✅ <b>Market Limiti</b>\n\n" f"Yeni değer: {limit_val}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("❌ Lütfen 1 ile 500 arasında bir sayı girin.")
    except ValueError:
        await update.message.reply_text("❌ Geçersiz sayı. Lütfen bir rakam girin.")


def register_handlers(app):
    """Register backtest v2 handlers with the Telegram application."""
    app.add_handler(CommandHandler("backtest_v2", backtest_v2_cmd))
    app.add_handler(CommandHandler("compare", compare_cmd))
    # New PolyCop-style config callbacks (bt2c_ prefix)
    app.add_handler(CallbackQueryHandler(backtest_v2_config_callback, pattern="^bt2c_"))
    # Old-style callbacks for backward compatibility (bt2_ prefix)
    app.add_handler(CallbackQueryHandler(backtest_v2_callback, pattern="^bt2_"))
    logger.info("Backtest v2 handlers registered")


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster F — merged from backtest_replay.py
# ═══════════════════════════════════════════════════════════════════════
from datetime import UTC

from backtest.strategies.base import StrategyRegistryV2  # noqa: E402

# Strategy display names (subset of most useful for replay)
REPLAY_STRATEGIES = {
    "hour_edge": "🕐 Hour Edge",
    "streak_reversal": "🔄 Streak Reversal",
    "late_convergence": "⏰ Late Conv.",
    "taker_flow": "📊 Taker Flow",
    "orderbook_imbalance": "📚 OB Imbalance",
    "fade_rip": "↩️ Fade Rip",
    "opening_breakout": "💥 Opening BK",
    "calibration_arb": "📐 Calib. Arb",
    "composite": "🧠 Composite",
}


async def backtest_replay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /backtest_replay command."""
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
            f"MarketRecorder'in veri toplamasini bekleyin.\n"
            f"Her 2 saniyede 1 snapshot kaydedilir.\n\n"
            f"Durum icin: /recorder",
            parse_mode="HTML",
        )

    if not args:
        # Show interactive panel
        await _show_replay_panel(update, db, snap_count, market_count)
        return

    # Parse: /backtest_replay strategy [asset] [tf]
    strategy_name = args[0].lower()
    asset = args[1].upper() if len(args) > 1 else ""
    tf = args[2] if len(args) > 2 else ""

    await _run_replay(update, db, strategy_name, asset, tf)


async def _show_replay_panel(update: Update, db, snap_count: int, market_count: int):
    """Show interactive replay config panel."""
    # P0-08-E2 (2026-05-09): ob_snapshots v18 schema artık `ts_ms` (INTEGER)
    # kullanıyor, eski `ts_iso` (TEXT) kaldırıldı. Burası ms epoch'tan ISO'ya
    # dönüştürerek panel'de göstersin.
    from datetime import datetime

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
        f"🔄 <b>Replay Backtest — Gercek Veri</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📸 Kayitli: <b>{snap_count:,}</b> snapshot\n"
        f"📊 Market: <b>{market_count}</b> benzersiz\n"
        f"🕐 Aralik: {oldest} → {newest}\n\n"
        f"📋 <b>Veri Dagilimi</b>\n{breakdown}\n"
        f"🎯 <b>Fill Mode:</b> REAL_ORDERBOOK\n"
        f"<i>Gercek L2 depth walk — VWAP fill</i>\n\n"
        f"Strateji sec ve baslat:"
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
                "🏆 Tum Stratejiler Karsilastir", callback_data="replay_compare_all"
            ),
        ]
    )

    kb = InlineKeyboardMarkup(kb_rows)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def replay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle replay button callbacks."""
    query = update.callback_query
    data = query.data
    db = context.bot_data.get("db")

    if not db:
        await query.answer("DB bulunamadi", show_alert=True)
        return

    if data == "replay_compare_all":
        await query.answer("⏳ Tum stratejiler karsilastiriliyor...")
        await _run_compare_all(query, db)
        return

    # replay_STRATEGY_NAME
    strategy_name = data.replace("replay_", "")

    if strategy_name not in REPLAY_STRATEGIES and not StrategyRegistryV2.get(strategy_name):
        await query.answer(f"Bilinmeyen strateji: {esc(strategy_name)}", show_alert=True)
        return

    await query.answer(f"⏳ {esc(strategy_name)} replay baslatiliyor...")
    await _run_replay(query, db, strategy_name)


async def _run_replay(source, db, strategy_name: str, asset: str = "", timeframe: str = ""):
    """Execute replay backtest and send results."""
    try:
        # Import here to avoid circular imports
        from backtest.replay_engine import ReplayConfig, ReplayEngine
        from backtest.strategies import StrategyRegistryV2  # noqa: triggers auto-registration

        # Check strategy exists
        strat_cls = StrategyRegistryV2.get(strategy_name)
        if not strat_cls:
            await _reply(
                source,
                f"❌ Strateji bulunamadi: {esc(strategy_name)}\n"
                f"Mevcut: {', '.join(StrategyRegistryV2.list_all())}",
            )
            return

        await _reply(
            source,
            f"🔄 <b>Replay Backtest</b>\n\n"
            f"Strateji: {esc(strategy_name)}\n"
            f"Fill: REAL_ORDERBOOK (gercek L2 depth)\n"
            f"Veri: ob_snapshots (canli kayit)\n\n"
            f"⏳ Hesaplaniyor...",
        )

        config = ReplayConfig(
            strategy_name=strategy_name,
            fill_mode="real_orderbook",
            asset_filter=asset,
            timeframe_filter=timeframe,
        )

        engine = ReplayEngine(db, config)
        stats = await engine.run()
        summary = engine.get_summary()

        # Format results
        result_text = _format_replay_results(strategy_name, summary, stats)
        await _reply(source, result_text)

        # Try to send equity chart
        try:
            from backtest.analytics.charts import ChartGenerator

            chart_gen = ChartGenerator(engine.portfolio, strategy_name)
            chart_bytes = chart_gen.equity_curve()
            if chart_bytes:
                await _send_photo(source, chart_bytes, f"📈 Equity: {esc(strategy_name)} (Replay)")
        except Exception:  # noqa: BLE001
            pass  # Charts optional

    except Exception as e:  # noqa: BLE001
        logger.error("Replay backtest failed: %s", e, exc_info=True)
        await _reply(
            source,
            f"❌ <b>Replay Hatasi</b>\n\n"
            f"Strateji: {esc(strategy_name)}\n"
            f"Detay: {str(e)[:150]}",
        )


async def _run_compare_all(source, db):
    """Run replay for all strategies and compare."""
    try:
        from backtest.replay_engine import ReplayConfig, ReplayEngine
        from backtest.strategies import StrategyRegistryV2  # noqa

        results = []

        for name in REPLAY_STRATEGIES:
            strat_cls = StrategyRegistryV2.get(name)
            if not strat_cls:
                continue

            config = ReplayConfig(
                strategy_name=name,
                fill_mode="real_orderbook",
            )
            engine = ReplayEngine(db, config)
            await engine.run()
            summary = engine.get_summary()
            results.append((name, summary))

        if not results:
            await _reply(source, "⚠️ Hic strateji calistirilamadi.")
            return

        # Sort by PnL
        results.sort(key=lambda x: x[1].get("total_pnl", 0), reverse=True)

        text = "🏆 <b>Replay Karsilastirma — Gercek Veri</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━━━\n"
        text += "<i>Fill: REAL_ORDERBOOK | Data: ob_snapshots</i>\n\n"

        for i, (name, s) in enumerate(results):
            rank = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i+1}."
            label = REPLAY_STRATEGIES.get(name, name)
            pnl = s.get("total_pnl", 0)
            wr = s.get("win_rate", 0)
            trades = s.get("total_trades", 0)
            pnl_icon = "🟢" if pnl > 0 else "🔴"

            text += (
                f"{rank} <b>{esc(label)}</b>\n"
                f"   {pnl_icon} PnL: ${pnl:+.2f} | "
                f"WR: {wr:.1f}% | "
                f"Trade: {trades}\n"
            )

        text += (
            f"\n📊 Market: {results[0][1].get('markets_processed', 0)} | "
            f"Snap: {results[0][1].get('total_snapshots', 0)}"
        )

        await _reply(source, text)

    except Exception as e:  # noqa: BLE001
        logger.error("Replay compare failed: %s", e, exc_info=True)
        await _reply(source, f"❌ <b>Karsilastirma Hatasi</b>\n\n" f"Detay: {str(e)[:150]}")


def _format_replay_results(strategy_name: str, summary: dict, stats) -> str:
    """Format replay results for Telegram."""
    pnl = summary.get("total_pnl", 0)
    pnl_icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"

    text = (
        f"🔄 <b>Replay Backtest Sonucu</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 Strateji: <b>{esc(strategy_name)}</b>\n"
        f"🎯 Fill Mode: REAL_ORDERBOOK\n"
        f"📸 Veri: Gercek ob_snapshots\n\n"
        f"📊 <b>Performans</b>\n"
        f"  {pnl_icon} PnL: <b>${pnl:+.2f}</b>\n"
        f"  🎯 Win Rate: <b>{summary.get('win_rate', 0):.1f}%</b>\n"
        f"  📈 Trade: {summary['total_trades']} "
        f"({summary['wins']}W/{summary['losses']}L)\n"
        f"  💰 Avg PnL: ${summary.get('avg_pnl', 0):+.4f}\n\n"
        f"📉 <b>Risk</b>\n"
        f"  📐 Sharpe: {summary.get('sharpe', 0):.2f}\n"
        f"  📐 Sortino: {summary.get('sortino', 0):.2f}\n"
        f"  📉 Max DD: ${summary.get('max_drawdown', 0):.2f}\n"
        f"  ⚖️ Profit Factor: {summary.get('profit_factor', 0):.2f}\n\n"
        f"💸 <b>Maliyet</b>\n"
        f"  💰 Fee: ${summary.get('total_fees', 0):.4f}\n"
        f"  📊 Slippage: ${summary.get('total_slippage', 0):.4f}\n\n"
        f"📸 <b>Veri</b>\n"
        f"  Market: {summary['markets_processed']} "
        f"(+{summary['markets_skipped']} skip)\n"
        f"  Snapshot: {summary['total_snapshots']:,}\n"
        f"  Sinyal: {summary['signals_generated']}\n"
    )
    return text


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


# ═══════════════════════════════════════════════════════════════════════
# Becker block — 2026-04-28 Heddas direktifi: Becker tam silindi.
# Aşama 1: module-level becker_loader import kaldırıldı (data/becker_loader.py
# dosyası rm edildiğinde patlamasın diye). Aşağıdaki becker_*_command fonksiyonları
# bot.py'dan import edilmiyor artık — Aşama 2 cosmetic cleanup'ta tamamen silinecek.
# Şu an dead code; çağrılırsa NameError verir ama hiçbir register zinciri yok.
# ═══════════════════════════════════════════════════════════════════════
import asyncio  # noqa: E402


def _fmt_bytes(n: int) -> str:
    if n is None or n <= 0:
        return "0"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    f = float(n)
    while f >= 1024 and i < len(units) - 1:
        f /= 1024
        i += 1
    return f"{f:.2f} {units[i]}"


# Becker handler functions removed 2026-05-11 (P1-07 cleanup).
# Becker module was fully removed 2026-04-28 (Aşama 1+2 closure) but command
# handler bodies stayed as dead code in this file, referring to undefined
# names (dataset_status, is_dataset_present, ARCHIVE_PATH, RAW_DIR,
# BeckerLoader). They were unwired from bot.py earlier; this drop closes the
# Aşama 2 cosmetic backlog item.


# ---------------------------------------------------------------------------
# Phase 51 P51-03 — /becker_replay (merged from becker_replay_handler.py)
# DuckDB walk-forward historical replay wrapper.
# ---------------------------------------------------------------------------

DEFAULT_REPLAY_STRATEGY = "threshold_70"
DEFAULT_REPLAY_MARKETS = 50


async def becker_replay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/becker_replay [strategy] [markets] [maker] [asset]

    Phase 57: Optional 4th arg = asset filter (btc, eth, sol, xrp).
    Examples:
      /becker_replay threshold_70 10000 maker btc
      /becker_replay threshold_55 500 taker eth
      /becker_replay contra_70 1000 maker
    """
    args = context.args or []
    strategy = args[0] if len(args) >= 1 else DEFAULT_REPLAY_STRATEGY
    try:
        markets = int(args[1]) if len(args) >= 2 else DEFAULT_REPLAY_MARKETS
    except ValueError:
        await update.message.reply_text("❌ markets must be an integer", parse_mode="HTML")
        return
    maker = len(args) >= 3 and args[2].lower() == "maker"
    # Phase 57: asset filter — 4th argument
    asset_filter = args[3].lower().strip() if len(args) >= 4 else None
    valid_assets = {"btc", "eth", "sol", "xrp", "doge", "matic", "ada", "avax"}
    if asset_filter and asset_filter not in valid_assets:
        asset_filter = None  # ignore invalid, use all

    asset_label = f" asset={asset_filter}" if asset_filter else ""
    await update.message.reply_text(
        f"🎞 <b>Becker replay starting</b>\n"
        f"strategy=<code>{esc(strategy)}</code> markets={markets} "
        f"maker={maker}{asset_label}\n"
        f"<i>This runs a DuckDB walk-forward — 10-60s depending on scale.</i>",
        parse_mode="HTML",
    )

    try:
        # CPU-yoğun Becker replay'i ayrı thread'te çalıştır
        result = await asyncio.to_thread(
            _run_replay_blocking, strategy, markets, maker, asset_filter
        )
    except FileNotFoundError as e:
        await update.message.reply_text(
            render_user_exception(e, "❌ <b>Calibration DB missing</b>"), parse_mode="HTML"
        )
        return
    except ValueError as e:
        await update.message.reply_text(
            render_user_exception(e, "❌ <b>Invalid replay input</b>"), parse_mode="HTML"
        )
        return
    except Exception as e:  # noqa: BLE001  # noqa: BLE001
        logger.exception("becker_replay failed")
        await update.message.reply_text(
            render_user_exception(e, "❌ <b>Replay failed</b>"), parse_mode="HTML"
        )
        return

    summary = result.summarize()
    text = (
        f"🎞 <b>Becker Replay Complete</b>\n"
        f"strategy=<code>{esc(strategy)}</code>"
        f"{f'  asset=<code>{esc(asset_filter)}</code>' if asset_filter else ''}\n"
        f"markets seen={summary['markets_seen']} "
        f"traded={summary['markets_traded']}\n"
        f"trades={summary['trades']} "
        f"wr={summary['win_rate']:.2f}%\n"
        f"total pnl=${summary['total_pnl']:+.2f}\n"
        f"avg/trade=${summary['avg_pnl']:+.4f}\n"
        f"sharpe={summary['sharpe']:.3f} "
        f"max_dd=${summary['max_dd']:.2f}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


def _run_replay_blocking(strategy: str, markets: int, maker: bool, asset_filter: str = None):
    from backtest.becker_replay import run_replay

    return run_replay(
        strategy_name=strategy,
        markets=markets,
        min_trades=20,
        maker=maker,
        only_resolved=True,
        asset_filter=asset_filter,
    )


async def becker_deep_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/becker_deep — Phase 60 P2-4: Full deep analysis of Becker dataset.

    Runs 7 comprehensive DuckDB queries: zone calibration, temporal patterns,
    per-asset curves, asset×zone matrix, taker-side split, volume-weighted δ.
    """
    await update.message.reply_text(
        "🔬 <b>Running Becker deep analysis...</b>\n"
        "<i>7 DuckDB queries against calibration DB — 15-60s</i>",
        parse_mode="HTML",
    )
    try:
        from scripts.becker_deep_analysis import format_html, run_deep_analysis

        # CPU-yoğun Becker analizi ayrı thread'te çalıştır
        results = await asyncio.to_thread(run_deep_analysis)
        if "error" in results:
            await update.message.reply_text(f"❌ {esc(results['error'])}", parse_mode="HTML")
            return

        # Save HTML report
        from pathlib import Path

        report_dir = Path("reports")
        report_dir.mkdir(exist_ok=True)
        html = format_html(results)
        report_path = report_dir / "becker_deep_analysis.html"
        report_path.write_text(html, encoding="utf-8")

        # Send compact summary to Telegram
        s = results.get("kalshi_summary", {})
        calib = results.get("kalshi_calibration", [])
        per_asset = results.get("kalshi_per_asset", [])

        lines = ["<b>🔬 Becker Deep Analysis Complete</b>\n"]
        lines.append(
            f"Trades: {s.get('total_trades', 0):,} | "
            f"Markets: {s.get('total_markets', 0):,} | "
            f"Elapsed: {results.get('elapsed_sec', '?')}s\n"
        )

        # Top mispricing zones
        if calib:
            lines.append("<b>Top Mispricing Zones (δ > 0):</b>")
            lines.append("<pre>")
            for r in sorted(calib, key=lambda x: x[3], reverse=True)[:5]:
                lines.append(f"  {r[0]:>3}c  δ={r[3]:+.1%}  n={r[4]:,}")
            lines.append("</pre>")

        # Per-asset summary
        if per_asset:
            lines.append("\n<b>Per-Asset:</b>")
            lines.append("<pre>")
            for r in per_asset:
                delta = r[1] - r[2] if r[1] and r[2] else 0
                lines.append(f"  {r[0]:>4} actual={r[1]:.1%} δ={delta:+.1%} n={r[3]:,}")
            lines.append("</pre>")

        lines.append("\n📄 Full HTML report: <code>reports/becker_deep_analysis.html</code>")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3990] + "\n..."
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:  # noqa: BLE001  # noqa: BLE001
        logger.exception("becker_deep failed")
        await update.message.reply_text(
            render_user_exception(e, "❌ <b>Deep analysis failed</b>"), parse_mode="HTML"
        )


async def becker_zones_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/becker_zones — Phase 60 P2-2: Sub-25c zone mispricing analysis.

    Runs DuckDB queries against becker_calibration.db to show the
    15-25c mispricing zone (Becker finding: 13.8pt average gap).
    """
    await update.message.reply_text(
        "📊 <b>Running Becker zone analysis...</b>\n"
        "<i>DuckDB query against calibration DB — 5-15s</i>",
        parse_mode="HTML",
    )
    try:
        from scripts.becker_zone_analysis import format_telegram, run_analysis

        # CPU-yoğun zone analizi ayrı thread'te çalıştır
        results = await asyncio.to_thread(run_analysis)
        text = format_telegram(results)
        # Telegram message limit = 4096 chars; truncate if needed
        if len(text) > 4000:
            text = text[:3990] + "\n..."
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:  # noqa: BLE001  # noqa: BLE001
        logger.exception("becker_zones failed")
        await update.message.reply_text(
            render_user_exception(e, "❌ <b>Zone analysis failed</b>"), parse_mode="HTML"
        )


# Phase 79 S1-12: Cancel callback handler for heavy operations
async def cancel_operation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle cancel button for /backtest_v2, /compare operations."""
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
