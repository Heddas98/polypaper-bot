"""
PolyPaper Bot - /live command (Phase 34: Shadow Mode)
Button UI — toggle live, paper vs real side by side, trade history.
Real data feeds back into paper model calibration.
"""

import logging
import os  # 2026-05-05: market BUY/SELL slippage env read
from datetime import UTC, datetime

import aiosqlite
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from config.settings import Settings
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.live")


def _is_admin(context: ContextTypes.DEFAULT_TYPE, telegram_id: int) -> bool:
    """H-05 (2026-05-15 ultra-audit): admin gate — mirrors env_toggle pattern.

    Every live_handler entry point drives a real-money flow (toggle live
    trading, /buy, /sell, on-chain allowance approve). Telegram callback
    data is user-controlled — a non-admin who opens a DM with the bot
    could replay a captured `live_toggle_confirm` / `live_market_*`
    payload and move real pUSD. Fail-closed: if settings is missing from
    bot_data, deny.
    """
    settings: Settings | None = context.bot_data.get("settings")
    if settings is None:
        logger.warning(
            "live_handler _is_admin: settings missing in bot_data, denying %s",
            telegram_id,
        )
        return False
    return settings.is_admin(telegram_id)


def _user_error_msg(exc: Exception, where: str = "") -> str:
    """M-01 (2026-05-15 ultra-audit): exception → safe user-facing message.

    Raw ``str(exc)`` echoed to the Telegram user can leak DB schema names,
    file paths and internal identifiers (info disclosure). Full detail
    still reaches the logs via the paired ``logger.exception()`` call —
    the user only sees a category + location line. Always keep a
    ``logger.exception()`` next to each call site so triage data is not
    lost.
    """
    if isinstance(exc, aiosqlite.Error):
        cat = "Veritabanı hatası"
    elif isinstance(exc, TimeoutError | ConnectionError):
        cat = "Bağlantı / zaman aşımı"
    elif isinstance(exc, ValueError | TypeError | KeyError):
        cat = "Geçersiz veri"
    else:
        cat = "Beklenmeyen hata"
    loc = f" ({where})" if where else ""
    return f"⚠️ {cat}{loc} — log'da detay var, sorun sürerse yöneticiye haber ver."


def _progress_bar(frac: float, width: int = 10) -> str:
    """Faz 1 kokpit (2026-05-18): text progress bar — [██████░░░░].

    `frac` 0..1'e clamp edilir. Risk-limit kullanımı gibi oranları
    görsel gösterir.
    """
    frac = max(0.0, min(1.0, frac))
    filled = round(frac * width)
    return "█" * filled + "░" * (width - filled)


def _live_start_dt() -> datetime:
    """LIVE_START_DATE parse — bot mainnet go-live tarihi (UTC datetime).

    2026-05-18 (Heddas direktifi): /live paneli bot performansını bu
    tarihten itibaren sayar — operatörün bot-öncesi kişisel Polymarket
    geçmişi karışmaz. Parse hatası → 2026-05-09 (mainnet go-live default).
    """
    raw = os.getenv("LIVE_START_DATE", "2026-05-09").strip()
    try:
        start = datetime.fromisoformat(raw)
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        return start
    except (ValueError, TypeError):
        return datetime(2026, 5, 9, tzinfo=UTC)


def _mainnet_days() -> int:
    """LIVE_START_DATE'ten bugüne gün — bot'un canlı (mainnet) süresi."""
    return max(0, (datetime.now(UTC) - _live_start_dt()).days)


def _live_pnl_block(lp: dict | None) -> str:
    """Faz 2A (2026-05-18 Heddas): on-chain bot LIVE PnL kokpit bloğu.

    `data.polymarket_portfolio.compute_live_pnl` çıktısını /live kokpitine
    basar. Bu blok bot'un GERÇEK canlı sonucudur — Polymarket on-chain
    `activity` feed'inden hesaplanır (TRADE maliyeti + REDEEM payout).
    `live_trader._total_pnl` sayacı manuel `/live` trade'lerini kaçırdığı
    için $0 gösteriyordu; bu blok onun yerine geçer.

    lp None ise (cache henüz dolmadı) "veri bekleniyor" satırı döner.
    """
    if not lp:
        return (
            "📊 <b>BOT LIVE PnL</b> (Polymarket on-chain)\n"
            "  <i>veri bekleniyor — portfolio cache henüz dolmadı</i>\n\n"
        )
    net = float(lp.get("net_pnl", 0.0))
    icon = "🟢" if net > 0 else ("🔴" if net < 0 else "⚪")
    net_str = f"{'+' if net >= 0 else '-'}${abs(net):.2f}"
    roi = float(lp.get("roi_pct", 0.0))
    pend = int(lp.get("pending_markets", 0))
    pend_str = f"  ·  ⏳ {pend} bekliyor" if pend else ""
    return (
        "📊 <b>BOT LIVE PnL</b> (Polymarket on-chain)\n"
        f"  Net: {icon} <b>{net_str}</b>  ·  ROI {roi:+.1f}%\n"
        f"  {int(lp.get('trades', 0))} trade  ·  "
        f"✅ {int(lp.get('win_markets', 0))} kazanan  ·  "
        f"❌ {int(lp.get('loss_markets', 0))} kaybeden{pend_str}\n"
        f"  Yatırım ${float(lp.get('cost', 0)):.2f}  →  "
        f"Dönüş ${float(lp.get('payout', 0)):.2f}  ·  "
        f"Fee ${float(lp.get('fee', 0)):.2f}\n\n"
    )


def _short_market(title: str) -> str:
    """Uzun market başlığını kısalt — işlem dökümü satırı için.

    'Bitcoin Up or Down - May 18, 2:55PM-3:00PM ET' → 'BTC May 18, 2:55PM…'.
    """
    if not title:
        return "?"
    low = title.lower()
    coin = ""
    for k, v in (
        ("bitcoin", "BTC"),
        ("ethereum", "ETH"),
        ("solana", "SOL"),
        ("ripple", "XRP"),
        ("xrp", "XRP"),
    ):
        if k in low:
            coin = v
            break
    when = title.split(" - ", 1)[1] if " - " in title else title
    when = when.replace(" ET", "").strip()
    out = f"{coin} {when}".strip()
    return out[:30] if len(out) <= 30 else out[:29] + "…"


def _live_pnl_detail_block(per_market: list[dict], limit: int = 12) -> str:
    """📊 İşlem dökümü — `compute_live_pnl` `per_market` listesini basar.

    Heddas direktifi 2026-05-19 ("nerede ne zaman ne olmuş ne kadar
    gitmiş"): her bot market'i tek blok — tarih · market · giriş fiyatı +
    outcome · maliyet→dönüş · net. Yeni → eski sıralı.
    """
    if not per_market:
        return (
            "📊 <b>İŞLEM DÖKÜMÜ</b>\n"
            "  <i>henüz bot trade'i yok</i>\n\n"
        )
    n = min(limit, len(per_market))
    lines = [f"📊 <b>İŞLEM DÖKÜMÜ</b> · on-chain · son {n}"]
    for m in per_market[:limit]:
        try:
            ts = int(m.get("ts", 0) or 0)
        except (TypeError, ValueError):
            ts = 0
        when = (
            datetime.fromtimestamp(ts, UTC).strftime("%m-%d %H:%M")
            if ts
            else "—"
        )
        res = str(m.get("result", ""))
        icon = {"win": "🟢", "loss": "🔴", "pending": "⏳"}.get(res, "⚪")
        title = _short_market(str(m.get("title", "")))
        outcome = str(m.get("outcome", "")) or "?"
        entry = float(m.get("entry_price", 0) or 0)
        cost = float(m.get("cost", 0) or 0)
        payout = float(m.get("payout", 0) or 0)
        net = float(m.get("net", 0) or 0)
        net_str = f"{'+' if net >= 0 else '-'}${abs(net):.2f}"
        lines.append(
            f"{icon} <code>{when}</code> · {esc(title)} · {esc(outcome)} @{entry:.2f}\n"
            f"   ${cost:.2f} → ${payout:.2f}  ·  net <b>{net_str}</b>"
        )
    return "\n".join(lines) + "\n\n"


def _panel_nav_kb(refresh_cb: str) -> InlineKeyboardMarkup:
    """Faz 2B (2026-05-19): alt-panel navigasyonu — Ana Panel + Yenile.

    `refresh_cb` aynı paneli yeniden çizen callback — Heddas direktifi
    "yenileme butonları" (her panel canlı veriyi tazeleyebilmeli).
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("◀️ Ana Panel", callback_data="live_main"),
                InlineKeyboardButton("🔄 Yenile", callback_data=refresh_cb),
            ]
        ]
    )


async def _safe_edit(q, text: str, kb) -> None:
    """Callback panel edit — "message not modified" duplicate'ini engelle.

    2026-05-19 (B1 audit): "🔄 Yenile" panel içeriği değişmemişse Telegram
    `BadRequest: message is not modified` döndürür. Eski desen bunu geniş
    yakalayıp `reply_text` ile YENİ mesaj atıyordu → her gereksiz
    yenilemede duplicate panel. Artık "not modified" sessizce yutulur
    (panel zaten güncel); yalnız gerçek edit hatasında reply fallback.
    """
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return  # panel zaten güncel — duplicate mesaj atma
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    except (TimeoutError, TelegramError):
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def _fetch_price_deltas(engine, coin: str, tf: str) -> dict:
    """Heddas direktifi 2026-05-19: coin/tf için fiyat-hareketi istatistiği.

    `engine.candle_collector` (Binance OHLC, zaten toplanıyor — yeni API
    çağrısı yok) → `compute_price_deltas`. Defensive: candle_collector
    yok / hata → boş dict, panel yine açılır.
    """
    try:
        cc = getattr(engine, "candle_collector", None)
        if cc is None or not hasattr(cc, "get_ext_candles"):
            return {}
        from data.candle_collector import (
            BINANCE_SYMBOLS,
            candles_24h_count,
            compute_price_deltas,
        )

        symbol = BINANCE_SYMBOLS.get(coin.upper())
        if not symbol:
            return {}
        # +1: en yeni mum devam eden pencere olabilir — compute_price_deltas
        # `drop_last` ile onu atar (yalnız tamamlanmış pencereler sayılır).
        limit = candles_24h_count(tf) + 1
        candles = await cc.get_ext_candles(symbol, tf, limit=limit)
        return compute_price_deltas(candles)
    except Exception as _e:  # noqa: BLE001
        logger.debug(f"price deltas {coin}/{tf}: {_e}")
        return {}


def _price_delta_block(coin: str, tf: str, st: dict) -> str:
    """📐 Fiyat hareketi bloğu — `compute_price_deltas` çıktısını basar.

    Market açılış→kapanış delta'sı + son 5/10/24s ortalama |hareket| +
    net yön + up/down pencere sayısı. İşlem alırken volatilite/eğilim
    göstergesi. Veri yoksa boş string döner (panel atlar).
    """
    if not st or int(st.get("n", 0)) == 0:
        return ""
    ld = float(st.get("last_delta", 0.0))
    ldp = float(st.get("last_delta_pct", 0.0))
    arrow = "▲" if ld > 0 else ("▼" if ld < 0 else "▬")
    sign = "+" if ld >= 0 else "-"
    aa = st.get("avg_abs_pct", {}) or {}
    net = float(st.get("net_pct_all", 0.0))
    net_arrow = "▲" if net > 0 else ("▼" if net < 0 else "▬")
    return (
        f"📐 <b>{esc(coin)} {esc(tf)} — Fiyat Hareketi</b>\n"
        f"  Son pencere: {arrow} {sign}${abs(ld):,.0f} ({ldp:+.3f}%)\n"
        f"  Ort.|hareket|: 5p %{float(aa.get('5', 0)):.3f} · "
        f"10p %{float(aa.get('10', 0)):.3f} · "
        f"24s %{float(aa.get('all', 0)):.3f}\n"
        f"  Net yön 24s: {net_arrow} {net:+.3f}%  ·  "
        f"↑{int(st.get('up_count', 0))} / ↓{int(st.get('down_count', 0))} pencere\n"
    )


async def live_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main live dashboard with toggle buttons."""
    # H-05 (2026-05-15 ultra-audit): admin gate — real-money UI.
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Bu komut yöneticiye özel.")
    engine = context.bot_data.get("engine")
    if not engine or not hasattr(engine, "live"):
        return await update.message.reply_text("Live trader bulunamadı.")
    text, kb = await _build_main(engine, context.bot_data.get("db"))
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def live_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all live_ button callbacks."""
    q = update.callback_query
    # H-05 (2026-05-15 ultra-audit): admin gate BEFORE answering. Callback
    # data is user-controlled; a non-admin who DMs the bot could replay a
    # `live_toggle_confirm` / `live_market_*` payload and move real pUSD.
    if not _is_admin(context, q.from_user.id):
        return await q.answer("⛔ Yetkisiz erişim", show_alert=True)
    await q.answer()
    data = q.data
    engine = context.bot_data.get("engine")
    db = context.bot_data.get("db")
    if not engine or not hasattr(engine, "live"):
        return

    if data == "live_toggle":
        # Phase 52 ÖNERİ #6 — enabling live mode now requires explicit
        # confirmation (real USDC is on the line). Disabling stays one
        # tap — users should always be able to kill live flow instantly.
        st = engine.live.get_status()
        currently_on = bool(st.get("enabled", False))
        if not currently_on:
            confirm_text = (
                "⚠️ <b>Live Mode'u etkinleştir?</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Bu adım gerçek <b>pUSD</b> kullanacaktır.\n\n"
                f"🤖 Bot risk limit: <b>${st.get('budget', 1.49):.2f}</b> (LIVE_BUDGET)\n"
                f"📌 Trade başı: <b>$1.00</b>\n"
                f"🎯 Strateji sayısı: <b>3 (whitelist)</b>\n\n"
                "<i>Polymarket gerçek bakiyeni /portfolio'dan kontrol et.</i>\n\n"
                "Emin misin?"
            )
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ Evet, aç", callback_data="live_toggle_confirm"),
                        InlineKeyboardButton("❌ İptal", callback_data="live_toggle_cancel"),
                    ],
                ]
            )
            try:
                await q.edit_message_text(confirm_text, parse_mode="HTML", reply_markup=kb)
            except (TimeoutError, BadRequest, TelegramError):
                # T11.8-B (2026-04-24): narrow from bare Exception. edit
                # BadRequest "not modified" or original message gone — fall
                # back to fresh reply.
                await q.message.reply_text(confirm_text, parse_mode="HTML", reply_markup=kb)
            return
        # Already on → toggle OFF immediately (no confirm)
        new_state = engine.live.toggle()
        logger.info(f"💰 Live trader → {'AKTIF 🟢' if new_state else 'KAPALI 🔴'}")
        text, kb = await _build_main(engine, db)
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            # T11.8-B (2026-04-24): same edit fallback pattern as confirm above.
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    elif data == "live_toggle_confirm":
        new_state = engine.live.toggle()
        logger.info(f"💰 Live trader → {'AKTIF 🟢' if new_state else 'KAPALI 🔴'} (confirmed)")
        text, kb = await _build_main(engine, db)
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            # T11.8-B (2026-04-24): same edit fallback pattern as confirm above.
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    elif data == "live_toggle_cancel":
        text, kb = await _build_main(engine, db)
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            # T11.8-B (2026-04-24): same edit fallback pattern as confirm above.
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    elif data == "live_budget_reset":
        # 2026-05-18 Heddas: live budget reset — 2-tap confirmed (real pUSD
        # spend ceiling). Same confirm/cancel pattern as live_toggle.
        st = engine.live.get_status()
        _budget = st.get("budget", 0.0)
        _spent = _budget - st.get("remaining", 0.0)
        confirm_text = (
            "⚠️ <b>Live Budget Reset?</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Harcanan <b>${_spent:.2f}</b> → <b>$0.00</b> sıfırlanacak.\n"
            f"Tüm <b>${_budget:.2f}</b> risk limiti yeniden açılır — "
            "gerçek pUSD harcaması devam edebilir.\n\n"
            f"📉 Bot: PnL bugün <b>${st.get('daily_pnl', 0.0):+.2f}</b>, "
            f"loss-streak <b>{st.get('loss_streak', '?')}</b>\n\n"
            "<i>Reset etmeden önce /rg ile performansı kontrol etmen "
            "önerilir — kayıp serisindeysen yeni bütçe yeni zarar olabilir.</i>\n\n"
            "Emin misin?"
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Evet, resetle", callback_data="live_budget_reset_confirm"
                    ),
                    InlineKeyboardButton(
                        "❌ İptal", callback_data="live_budget_reset_cancel"
                    ),
                ],
            ]
        )
        try:
            await q.edit_message_text(confirm_text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            await q.message.reply_text(confirm_text, parse_mode="HTML", reply_markup=kb)

    elif data == "live_budget_reset_confirm":
        old_spent = await engine.live.reset_budget()
        # Audit: admin id + amount in the bot log (reset_budget also logs).
        logger.warning(
            f"💰 Live budget reset CONFIRMED via /live UI by admin "
            f"{q.from_user.id} — spent ${old_spent:.2f} → $0.00"
        )
        text, kb = await _build_main(engine, db)
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    elif data == "live_budget_reset_cancel":
        text, kb = await _build_main(engine, db)
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    elif data == "live_compare":
        text = await _build_compare(engine)
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀️ Ana Panel", callback_data="live_main")]]
        )
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            # T11.8-B (2026-04-24): same edit fallback pattern as confirm above.
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    elif data == "live_history":
        text = await _build_history(engine)
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀️ Ana Panel", callback_data="live_main")]]
        )
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            # T11.8-B (2026-04-24): same edit fallback pattern as confirm above.
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    # ── Faz 2B/3 (2026-05-19) — trade istasyonu panelleri ────────────
    elif data == "live_perf":
        # Faz 3: birleşik performans paneli (on-chain PnL + paper×real + geçmiş)
        try:
            text = await _build_performance(engine, db)
        except Exception as _ex:  # noqa: BLE001
            logger.exception(f"live_perf: {_ex}")
            text = _user_error_msg(_ex, "performans paneli")
        kb = _panel_nav_kb("live_perf")
        await _safe_edit(q, text, kb)

    elif data == "live_risk":
        # Faz 2B: risk yöneticisi paneli (RiskManager state + limitler)
        try:
            text = await _build_risk(engine)
        except Exception as _ex:  # noqa: BLE001
            logger.exception(f"live_risk: {_ex}")
            text = _user_error_msg(_ex, "risk paneli")
        kb = _panel_nav_kb("live_risk")
        await _safe_edit(q, text, kb)

    elif data == "live_scan":
        # Faz 2B: piyasa tarama paneli (scanner aktif market'ler + odds)
        try:
            text = await _build_market_scan(engine)
        except Exception as _ex:  # noqa: BLE001
            logger.exception(f"live_scan: {_ex}")
            text = _user_error_msg(_ex, "piyasa tarama")
        kb = _panel_nav_kb("live_scan")
        await _safe_edit(q, text, kb)

    elif data == "live_guards":
        # Faz 2B: 6-guard snapshot — /lg builder'ı panel olarak göm
        try:
            from telegram_bot.handlers.live_guards_handler import build_guards_text

            text = build_guards_text(context)
        except Exception as _ex:  # noqa: BLE001
            logger.exception(f"live_guards: {_ex}")
            text = _user_error_msg(_ex, "guards paneli")
        kb = _panel_nav_kb("live_guards")
        await _safe_edit(q, text, kb)

    elif data == "live_main":
        text, kb = await _build_main(engine, db)
        await _safe_edit(q, text, kb)

    # ═══════════════════════════════════════════════════════════════════
    # 2026-05-05 Heddas direktifi: /live ekranı = LIVE mod manuel trade
    # PAPER trade'ler bot tarafından otomatik yapılır, /live UI içinde yok.
    # Bildirimler ayrı prefix ile gelir ("💰 LIVE TRADE" / "📋 PAPER TRADE").
    # ═══════════════════════════════════════════════════════════════════
    elif data in ("live_market_buy", "live_market_sell"):
        side = "BUY" if data == "live_market_buy" else "SELL"
        try:
            await _show_market_form(q, engine, side)
        except Exception as _ex:  # noqa: BLE001
            logger.exception(f"market_form: {_ex}")
            await q.message.reply_text(_user_error_msg(_ex, "market formu"))

    elif data.startswith("live_market_tf:"):
        # format: live_market_tf:BUY:5m
        try:
            _, side, tf = data.split(":")
            await _show_market_asset_chooser(q, engine, side, tf)
        except Exception as _ex:  # noqa: BLE001
            logger.exception(f"market_tf: {_ex}")
            await q.message.reply_text(_user_error_msg(_ex, "timeframe seçimi"))

    elif data.startswith("live_market_asset:"):
        # format: live_market_asset:BUY:BTC_UP:5m
        try:
            _, side, asset, tf = data.split(":")
            await _show_market_amount_picker(q, engine, side, asset, tf)
        except Exception as _ex:  # noqa: BLE001
            logger.exception(f"market_asset: {_ex}")
            await q.message.reply_text(_user_error_msg(_ex, "asset seçimi"))

    elif data.startswith("live_market_amount:"):
        # format: live_market_amount:BUY:BTC_UP:5m:1
        try:
            _, side, asset, tf, amount_str = data.split(":")
            await _show_market_confirm(q, engine, side, asset, tf, amount_str)
        except Exception as _ex:  # noqa: BLE001
            logger.exception(f"market_amount: {_ex}")
            await q.message.reply_text(
                _user_error_msg(_ex, "tutar seçimi")
                + "\n\n<i>Custom tutar için: /buy {coin} {UP/DOWN} {tutar}</i>",
                parse_mode="HTML",
            )

    elif data.startswith("live_market_exec:"):
        # format: live_market_exec:BUY:BTC_UP:5m:1
        try:
            _, side, asset, tf, amount_str = data.split(":")
            await _execute_market_trade(q, engine, db, side, asset, tf, amount_str)
        except Exception as _ex:  # noqa: BLE001
            logger.exception(f"market_exec: {_ex}")
            await q.message.reply_text(_user_error_msg(_ex, "trade"))

    elif data.startswith("live_redeem:"):
        # 2026-05-05 Heddas direktifi: bot direct redeem (Relayer gasless)
        # format: live_redeem:BTC_UP
        try:
            _, asset_key = data.split(":", 1)
            await q.edit_message_text(
                f"⏳ <b>Redeem gönderiliyor...</b>\n\n"
                f"<i>{asset_key.replace('_', ' ')}</i>\n"
                f"Polymarket Relayer (gasless) — 1-2 dk sürer.",
                parse_mode="HTML",
            )
            positions = await _get_open_positions(engine)
            info = positions.get(asset_key)
            if not info:
                await q.message.reply_text(
                    f"⚠️ Pozisyon bulunamadı: {asset_key}",
                    parse_mode="HTML",
                )
                return
            cid = info.get("condition_id", "")
            if not cid:
                await q.message.reply_text(
                    "⚠️ condition_id eksik — Polymarket data-api'den çekilemedi.\n"
                    "Manuel: polymarket.com/portfolio → Redeem",
                    parse_mode="HTML",
                )
                return
            from data.polymarket_actions import redeem_position

            ok, detail = await redeem_position(cid)
            kb_post = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("◀️ Pozisyon Paneli", callback_data="live_market_sell")],
                    [InlineKeyboardButton("📊 Portfolio", callback_data="live_main")],
                ]
            )
            try:
                await q.edit_message_text(
                    detail, parse_mode="HTML", reply_markup=kb_post, disable_web_page_preview=True
                )
            except (TimeoutError, BadRequest, TelegramError):
                await q.message.reply_text(
                    detail, parse_mode="HTML", reply_markup=kb_post, disable_web_page_preview=True
                )
        except Exception as _ex:  # noqa: BLE001
            logger.exception(f"redeem: {_ex}")
            await q.message.reply_text(_user_error_msg(_ex, "redeem"))

    elif data.startswith("live_sell_pct:"):
        # 2026-05-05 Heddas direktifi: SELL flow PnL panel + % satış
        # format: live_sell_pct:BTC_UP
        try:
            _, asset_key = data.split(":", 1)
            await _show_sell_pct_picker(q, engine, asset_key)
        except Exception as _ex:  # noqa: BLE001
            logger.exception(f"sell_pct: {_ex}")
            await q.message.reply_text(_user_error_msg(_ex, "satış paneli"))

    elif data == "live_approve_allowance":
        # 2026-05-05: Allowance approve via UI tuş
        try:
            await q.edit_message_text(
                "⏳ Allowance approve gönderiliyor...\n"
                "Polygon network on-chain TX, 1-2dk sürer.",
                parse_mode="HTML",
            )
            from data.polymarket_actions import approve_allowance

            ok, detail = await approve_allowance()
            if ok:
                text_post = (
                    f"✅ <b>Allowance Approve Gönderildi</b>\n\n"
                    f"<i>{esc(detail[:300])}</i>\n\n"
                    f"🕐 1-2dk içinde Polygon onaylar.\n"
                    f"Sonrasında /live BUY/SELL tuşları çalışır."
                )
            else:
                text_post = (
                    f"❌ <b>Approve BAŞARISIZ</b>\n\n"
                    f"<i>{esc(detail[:300])}</i>\n\n"
                    f"Manuel: polymarket.com/portfolio → Approve"
                )
            kb_post = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("◀️ Ana Panel", callback_data="live_main")],
                ]
            )
            await q.edit_message_text(
                text_post, parse_mode="HTML", reply_markup=kb_post, disable_web_page_preview=True
            )
        except Exception as _ex:  # noqa: BLE001
            logger.exception(f"approve_allowance UI: {_ex}")
            await q.message.reply_text(_user_error_msg(_ex, "allowance approve"))


# ═══════════════════════════════════════════════════════════════════
# Market BUY/SELL UI (Heddas 2026-05-05)
# ═══════════════════════════════════════════════════════════════════
# Manuel tek-tıkla satın alma/satma. Strateji bağımsız, doğrudan Polymarket.
# Akış: tuş → asset seç → tutar seç → onay → execute.
# Asset seçimi: BTC/ETH/SOL/XRP × UP/DOWN (8 token).
# Tutar: $1, $5, $10, $25, custom (env LIVE_MAX_MARKET_TRADE).
# Güvenlik: live trader auth gerekli, allowance check, FOK order.


async def _show_market_form(q, engine, side: str):
    """Timeframe seçici (BUY) veya pozisyon paneli (SELL).

    2026-05-05 Heddas direktifi: SELL'de "tutar/timeframe seç" mantıksız —
    elindeki pozisyonu satıyorsun. SELL doğrudan position listesine atlar.
    """
    side_emoji = "🟢" if side == "BUY" else "🔴"

    if side == "SELL":
        # 2026-05-05 SELL = position panel (PnL + % satış)
        await _show_position_panel(q, engine)
        return

    # BUY = normal timeframe akışı
    text = (
        f"{side_emoji} <b>LIVE {side} — Timeframe</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚠️ <b>GERÇEK USDC</b>\n\n"
        "<b>Hangi timeframe?</b>\n"
        "5dk hızlı | 15dk orta | 1h uzun"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚡ 5m", callback_data=f"live_market_tf:{side}:5m"),
                InlineKeyboardButton("⏱ 15m", callback_data=f"live_market_tf:{side}:15m"),
            ],
            [
                InlineKeyboardButton("🕐 1h", callback_data=f"live_market_tf:{side}:1h"),
                InlineKeyboardButton("🕓 4h", callback_data=f"live_market_tf:{side}:4h"),
            ],
            [InlineKeyboardButton("◀️ İptal", callback_data="live_main")],
        ]
    )
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (TimeoutError, BadRequest, TelegramError):
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def _show_position_panel(q, engine):
    """SELL ekranı — açık pozisyonları PnL ile listele.

    Her pozisyon için:
      - asset (BTC UP / ETH DOWN vs)
      - shares + cost basis + current value
      - PnL ($ ve %)
    Tıklanınca: o pozisyonun % satış (25/50/75/100) ekranına geç.
    """
    positions = await _get_open_positions(engine)

    if not positions:
        text = (
            "🔴 <b>SELL — Açık Pozisyon Yok</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Satılacak token yok.\n\n"
            "İpucu: önce <b>BUY</b> ile pozisyon aç,\n"
            "sonra burada karını/zararını görüp sat."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🟢 BUY (Yeni Pozisyon)", callback_data="live_market_buy")],
                [InlineKeyboardButton("◀️ Ana Panel", callback_data="live_main")],
            ]
        )
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return

    total_value = sum(p["current_value"] for p in positions.values())
    total_cost = sum(p["cost_basis"] for p in positions.values())
    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100.0) if total_cost > 0 else 0.0
    pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"

    text = (
        f"🔴 <b>SELL — Açık Pozisyonlar</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Toplam:</b> ${total_value:.2f} "
        f"({pnl_emoji} {total_pnl:+.2f} / {total_pnl_pct:+.1f}%)\n"
        f"<i>Maliyet: ${total_cost:.2f}</i>\n\n"
        f"<b>Pozisyon listesi:</b>\n"
    )

    rows = []
    for asset_key, info in positions.items():
        shares = info["shares"]
        cost = info["cost_basis"]
        cur_val = info["current_value"]
        cur_price = info["current_price"]
        pnl = info["pnl"]
        pnl_pct = info["pnl_pct"]
        asset_label = asset_key.replace("_", " ")

        # 2026-05-05 SETTLED detection: cur_value = 0 ya da cur_price = 0
        # Polymarket binary market'inde kaybeden taraf 0'a düşer.
        # Kazanan taraf 1.0 olur ve "redeem" gerektirir (sell yerine).
        is_settled = cur_val < 0.01 or cur_price < 0.001
        # Kazanan side detection: shares > 0 ve cur_price ≈ 1.0
        is_winning = cur_price > 0.95
        # Resolved kazanan = redeem; resolved kaybeden = değersiz; aktif = sell

        if is_settled and not is_winning:
            # Kaybeden taraf — settle, sat imkansız (zaten 0)
            text += (
                f"\n<b>{asset_label}</b> ⚰️ <i>SETTLED (kaybetti)</i>\n"
                f"  • {shares:.2f} hisse, değer $0.00\n"
                f"  • Maliyet: ${cost:.2f} → kayıp -${cost:.2f}\n"
                f"  • <i>Otomatik silinecek (Polymarket settle)</i>\n"
            )
            # Buton yok — bu pozisyon zaten kayıp
        elif is_winning:
            # Kazanan taraf — redeem gerekir
            cid = info.get("condition_id", "")
            text += (
                f"\n<b>{asset_label}</b> 🏆 <i>KAZANDI — Redeem gerekli</i>\n"
                f"  • {shares:.2f} hisse @${cur_price:.3f}\n"
                f"  • Değer: ${cur_val:.2f} (maliyet ${cost:.2f})\n"
                f"  • Kar: 🟢 +${pnl:.2f} ({pnl_pct:+.1f}%)\n"
            )
            if cid:
                # Bot direct redeem (Relayer gasless) — asset_key ile lookup
                rows.append(
                    [
                        InlineKeyboardButton(
                            f"🏆 {asset_label} Redeem (gasless)",
                            callback_data=f"live_redeem:{asset_key}",
                        )
                    ]
                )
            else:
                # Fallback UI link (condition_id eksik)
                rows.append(
                    [
                        InlineKeyboardButton(
                            f"🏆 {asset_label} Redeem (UI)",
                            url="https://polymarket.com/portfolio",
                        )
                    ]
                )
        else:
            # Aktif pozisyon — normal sat akışı
            emoji = "🟢" if pnl >= 0 else "🔴"
            text += (
                f"\n<b>{asset_label}</b>\n"
                f"  • {shares:.2f} hisse @${cur_price:.3f}\n"
                f"  • Değer: ${cur_val:.2f} (maliyet ${cost:.2f})\n"
                f"  • PnL: {emoji} {pnl:+.2f} USDC ({pnl_pct:+.1f}%)\n"
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{emoji} {asset_label} sat → ${cur_val:.2f}",
                        callback_data=f"live_sell_pct:{asset_key}",
                    )
                ]
            )

    rows.append([InlineKeyboardButton("🔄 Yenile", callback_data="live_market_sell")])
    rows.append([InlineKeyboardButton("◀️ Ana Panel", callback_data="live_main")])
    kb = InlineKeyboardMarkup(rows)
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (TimeoutError, BadRequest, TelegramError):
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def _show_sell_pct_picker(q, engine, asset_key: str):
    """Bir pozisyon için % satış seçici (25/50/75/100)."""
    positions = await _get_open_positions(engine)
    info = positions.get(asset_key)

    if not info or info["shares"] <= 0:
        text = (
            "⚠️ <b>Pozisyon bulunamadı</b>\n\n"
            f"<i>{asset_key.replace('_', ' ')}</i> artık açık değil.\n"
            "Belki settle oldu ya da satıldı."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("◀️ Geri", callback_data="live_market_sell")],
            ]
        )
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return

    # 2026-05-05 SETTLED guard: cur_value=0 → satılamaz
    cur_val_check = info.get("current_value", 0.0)
    cur_price_check = info.get("current_price", 0.0)
    if cur_val_check < 0.01 or cur_price_check < 0.001:
        # Kaybeden side veya kazanan-redeem-required
        is_winning = cur_price_check > 0.95
        if is_winning:
            text = (
                f"🏆 <b>{asset_key.replace('_', ' ')} — Redeem Gerekli</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Bu market resolve oldu, kazandın!\n"
                f"  • {info['shares']:.2f} hisse @$1.000\n"
                f"  • Beklenen redeem: ${info['shares']:.2f} pUSD\n\n"
                f"<b>Polymarket UI'dan 'Redeem' bas:</b>\n"
                f"polymarket.com/portfolio\n\n"
                f"<i>(Bot otomatik redeem henüz yok — Aşama 3 backlog)</i>"
            )
        else:
            text = (
                f"⚰️ <b>{asset_key.replace('_', ' ')} — Settled (kaybetti)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Bu market resolve oldu, kaybettin.\n"
                f"  • {info['shares']:.2f} hisse, değer $0.00\n"
                f"  • Kayıp: -${info['cost_basis']:.2f}\n\n"
                f"<i>Polymarket pozisyonu otomatik kapatır.</i>\n"
                f"Satılabilecek bir şey yok."
            )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("◀️ Geri", callback_data="live_market_sell")],
            ]
        )
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return

    shares = info["shares"]
    cur_val = info["current_value"]
    cost = info["cost_basis"]
    pnl = info["pnl"]
    pnl_pct = info["pnl_pct"]
    cur_price = info["current_price"]
    emoji = "🟢" if pnl >= 0 else "🔴"
    asset_label = asset_key.replace("_", " ")

    text = (
        f"🔴 <b>SELL — {asset_label}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Pozisyon:</b>\n"
        f"  • {shares:.2f} hisse @${cur_price:.3f}\n"
        f"  • Değer: <b>${cur_val:.2f}</b> "
        f"(maliyet ${cost:.2f})\n"
        f"  • PnL: {emoji} <b>{pnl:+.2f} USDC ({pnl_pct:+.1f}%)</b>\n\n"
        f"<b>Ne kadarını sat?</b>"
    )

    base = "live_market_amount"
    coin, direction = asset_key.split("_")
    rows = []
    # % butonları — value bazlı amount hesapla
    for label, pct in [("25%", 0.25), ("50%", 0.50), ("75%", 0.75), ("100%", 1.00)]:
        amt = round(cur_val * pct, 2)
        rows.append(
            [
                InlineKeyboardButton(
                    f"{label} sat → ${amt:.2f}",
                    callback_data=f"{base}:SELL:{asset_key}:5m:{amt}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("◀️ Geri", callback_data="live_market_sell")])
    kb = InlineKeyboardMarkup(rows)
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (TimeoutError, BadRequest, TelegramError):
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def _show_market_asset_chooser(q, engine, side: str, tf: str):
    """Asset chooser. SELL için açık pozisyon kontrol (sadece elinde olanlar)."""
    side_emoji = "🟢" if side == "BUY" else "🔴"
    base = "live_market_asset"

    if side == "SELL":
        positions = await _get_open_positions(engine)
        if not positions:
            text = (
                f"{side_emoji} <b>SELL — Açık Pozisyon Yok</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"⚠️ Satılacak token yok.\n"
                f"Önce <b>BUY</b> ile pozisyon aç."
            )
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("◀️ Ana Panel", callback_data="live_main")],
                ]
            )
            try:
                await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
            except (TimeoutError, BadRequest, TelegramError):
                await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
            return

        # Pozisyon var → sadece onları göster
        text = (
            f"{side_emoji} <b>SELL — Açık pozisyonların ({tf}):</b>\n" "━━━━━━━━━━━━━━━━━━━━━\n\n"
        )
        rows = []
        for asset_key, info in positions.items():
            shares = info.get("shares", 0)
            cost = info.get("cost_basis", 0)
            text += f"  • {asset_key.replace('_', ' ')}: {shares:.0f} hisse (cost ${cost:.2f})\n"
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{asset_key.replace('_', ' ')}",
                        callback_data=f"{base}:{side}:{asset_key}:{tf}",
                    )
                ]
            )
        rows.append([InlineKeyboardButton("◀️ Geri", callback_data=f"live_market_{side.lower()}")])
        kb = InlineKeyboardMarkup(rows)
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return

    # BUY: 8 token grid
    text = (
        f"{side_emoji} <b>BUY — Asset Seç ({tf})</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 <b>Polymarket Up/Down kripto</b>\n"
        "  • UP = fiyat yükselirse kazanır\n"
        "  • DOWN = fiyat düşerse kazanır"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("BTC ⬆", callback_data=f"{base}:{side}:BTC_UP:{tf}"),
                InlineKeyboardButton("BTC ⬇", callback_data=f"{base}:{side}:BTC_DOWN:{tf}"),
            ],
            [
                InlineKeyboardButton("ETH ⬆", callback_data=f"{base}:{side}:ETH_UP:{tf}"),
                InlineKeyboardButton("ETH ⬇", callback_data=f"{base}:{side}:ETH_DOWN:{tf}"),
            ],
            [
                InlineKeyboardButton("SOL ⬆", callback_data=f"{base}:{side}:SOL_UP:{tf}"),
                InlineKeyboardButton("SOL ⬇", callback_data=f"{base}:{side}:SOL_DOWN:{tf}"),
            ],
            [
                InlineKeyboardButton("XRP ⬆", callback_data=f"{base}:{side}:XRP_UP:{tf}"),
                InlineKeyboardButton("XRP ⬇", callback_data=f"{base}:{side}:XRP_DOWN:{tf}"),
            ],
            [InlineKeyboardButton("◀️ İptal", callback_data="live_main")],
        ]
    )
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (TimeoutError, BadRequest, TelegramError):
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def _get_open_positions(engine) -> dict:
    """LIVE açık pozisyonları döndür (Polymarket portfolio cache).

    Returns dict[asset_key, info] where info has:
      shares, cost_basis, current_value, current_price, pnl, pnl_pct,
      slug, token_id, condition_id, closed, is_winner, redeemable
    """
    positions = {}
    try:
        from data.polymarket_portfolio import read_cached_snapshot

        snap = await read_cached_snapshot(engine.db) if engine.db else None
        if snap and snap.get("positions"):
            for p in snap.get("positions", []):
                slug = p.get("market_slug", "")
                outcome = p.get("outcome", "").upper()
                coin = slug.split("-")[0].upper() if "-" in slug else "?"
                direction = "UP" if "up" in outcome.lower() or outcome == "YES" else "DOWN"
                key = f"{coin}_{direction}"
                shares = float(p.get("shares", 0))
                cost = float(p.get("cost_basis_usd", 0))
                cur_val = float(p.get("cur_value_usd", 0))
                cur_price = float(p.get("cur_price", 0)) or (
                    (cur_val / shares) if shares > 0 else 0.0
                )
                pnl = cur_val - cost
                pnl_pct = (pnl / cost * 100.0) if cost > 0 else 0.0
                # 2026-05-05 Redeem support
                cid = p.get("condition_id", "")
                closed = bool(p.get("closed", False))
                is_winner = bool(p.get("is_winner", False)) or (closed and cur_price > 0.999)
                redeemable = bool(p.get("redeemable", False)) or (
                    closed and is_winner and shares > 0
                )
                positions[key] = {
                    "shares": shares,
                    "cost_basis": cost,
                    "current_value": cur_val,
                    "current_price": cur_price,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "slug": slug,
                    "token_id": p.get("token_id", ""),
                    "condition_id": cid,
                    "closed": closed,
                    "is_winner": is_winner,
                    "redeemable": redeemable,
                }
    except Exception as _e:  # noqa: BLE001
        logger.debug(f"_get_open_positions: {_e}")
    return positions


async def _show_market_amount_picker(q, engine, side: str, asset: str, tf: str):
    """Tutar seçici. Custom amount için /buy /sell komutu (UI'dan değil)."""
    side_emoji = "🟢" if side == "BUY" else "🔴"
    asset_label = asset.replace("_", " ")
    base = "live_market_amount"

    st = engine.live.get_status()
    remaining = float(st.get("remaining", 0))
    balance_label = f"💰 Bot risk limit kalan: <b>${remaining:.2f}</b>"

    text = (
        f"{side_emoji} <b>LIVE {side} — {asset_label} ({tf})</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Tutar (USDC):</b>\n\n"
        f"{balance_label}\n\n"
        f"<i>Custom: <code>/{side.lower()} {asset.split('_')[0]} {asset.split('_')[1]} 3.50</code></i>"
    )
    rows = [
        [
            InlineKeyboardButton("$1", callback_data=f"{base}:{side}:{asset}:{tf}:1"),
            InlineKeyboardButton("$5", callback_data=f"{base}:{side}:{asset}:{tf}:5"),
            InlineKeyboardButton("$10", callback_data=f"{base}:{side}:{asset}:{tf}:10"),
        ],
        [
            InlineKeyboardButton("$25", callback_data=f"{base}:{side}:{asset}:{tf}:25"),
            InlineKeyboardButton("$50", callback_data=f"{base}:{side}:{asset}:{tf}:50"),
            InlineKeyboardButton("$100", callback_data=f"{base}:{side}:{asset}:{tf}:100"),
        ],
        [InlineKeyboardButton("◀️ Geri", callback_data=f"live_market_tf:{side}:{tf}")],
    ]
    kb = InlineKeyboardMarkup(rows)
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (TimeoutError, BadRequest, TelegramError):
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def _show_market_confirm(q, engine, side: str, asset: str, tf: str, amount_str: str):
    """Onay ekranı — fiyat + hisse + fee + slippage. LIVE-only."""
    side_emoji = "🟢" if side == "BUY" else "🔴"
    asset.replace("_", " ")
    amount = float(amount_str)
    coin, direction = asset.split("_")

    # Auth check
    st = engine.live.get_status()
    if not st.get("auth_verified", False):
        text = (
            "⚠️ <b>Live Trader auth henüz hazır değil</b>\n\n"
            "/live ekranında 'Live Aç' butonuna tıkla ve auth verify'i bekle."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("◀️ Ana Panel", callback_data="live_main")],
            ]
        )
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return

    # Market lookup + price
    info = await _peek_market_info(engine, coin, direction, tf)
    if not info["ok"]:
        text = (
            f"⚠️ <b>Market bilgisi alınamadı</b>\n\n"
            f"<i>{info['error']}</i>\n\n"
            f"Bot scanner offline veya {coin} {tf} market yok olabilir."
        )
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("◀️ Ana Panel", callback_data="live_main")],
            ]
        )
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return

    price = info["price"]
    best_ask = info["best_ask"]
    best_bid = info["best_bid"]
    slug = info["slug"]
    end_str = info["end_iso"]

    # Slippage tolerance — best_ask + 2% for BUY, best_bid - 2% for SELL
    slip_pct = float(os.getenv("LIVE_SLIPPAGE_PCT", "2.0")) / 100
    if side == "BUY":
        limit_price = min(0.999, best_ask * (1 + slip_pct)) if best_ask > 0 else price
    else:
        limit_price = max(0.001, best_bid * (1 - slip_pct)) if best_bid > 0 else price

    shares = amount / limit_price if limit_price > 0 else 0

    # Fee tahmini (Crypto category, 0.072 rate)
    fee_est = 0
    try:
        from core.fees_v2 import polymarket_taker_fee_v2

        fee_est = polymarket_taker_fee_v2(limit_price, amount, category="crypto")
    except Exception:  # noqa: BLE001
        fee_est = amount * 0.018  # rough %1.8 estimate

    # Bakiye kontrol
    st = engine.live.get_status()
    remaining = float(st.get("remaining", 0))
    balance_text = f"💼 Bot risk limit kalan: <b>${remaining:.2f}</b>\n"
    if amount > remaining and side == "BUY":
        balance_text = (
            f"❌ <b>YETERSİZ BAKİYE</b> (kalan ${remaining:.2f} &lt; istek ${amount:.2f})\n"
        )

    # 2026-05-19: orderbook-boş uyarısı. BUY → ask, SELL → bid gerekir.
    # Defter boşsa market order "no match" ile eşleşmez (para gitmez ama
    # trade atlanır). Onaylamadan önce kullanıcıyı bilgilendir.
    book_warn = ""
    if side == "BUY" and not info.get("has_asks", True):
        book_warn = (
            "⚠️ <b>Orderbook ince</b> — alış tarafında satıcı görünmüyor, "
            "order eşleşmeyebilir (\"no match\"). Para riski yok; eşleşmezse "
            "trade atlanır.\n"
        )
    elif side == "SELL" and not info.get("has_bids", True):
        book_warn = (
            "⚠️ <b>Orderbook ince</b> — satış tarafında alıcı görünmüyor, "
            "order eşleşmeyebilir (\"no match\").\n"
        )

    # 2026-05-19 Heddas: fiyat-hareketi bloğu — son pencere açılış→kapanış
    # delta'sı + 5/10/24s ortalama |hareket|. İşlem alırken volatilite göstergesi.
    _delta_blk = _price_delta_block(
        coin, tf, await _fetch_price_deltas(engine, coin, tf)
    )

    text = (
        f"{side_emoji} <b>LIVE {side} ONAYLA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 Market: <b>{coin} {direction} {tf}</b>\n"
        f"📍 Slug: <code>{esc(slug[:32])}</code>\n"
        f"⏰ End: <code>{esc(str(end_str)[:19])}</code>\n\n"
        f"💵 Tutar: <b>${amount:.2f} USDC</b>\n"
        f"📈 Best ask: {best_ask:.4f}\n"
        f"📉 Best bid: {best_bid:.4f}\n"
        f"🎯 Limit fiyat: <b>{limit_price:.4f}</b> "
        f"({'best_ask' if side == 'BUY' else 'best_bid'} ± {slip_pct*100:.1f}%)\n"
        f"📊 Beklenen hisse: <b>{shares:.2f}</b>\n"
        f"💸 Tahmini fee: <b>${fee_est:.4f}</b>\n\n"
        f"{_delta_blk}"
        f"{balance_text}\n"
        f"{book_warn}"
        f"⚡ Tip: FOK (Fill-Or-Kill)\n"
        f"⚠️ <b>GERÇEK USDC harcanır!</b>\n\nEmin misin?"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"✅ EVET, {side} et",
                    callback_data=f"live_market_exec:{side}:{asset}:{tf}:{amount_str}",
                )
            ],
            [
                # 2026-05-19 Heddas: onay ekranında fiyat yenileme — aynı
                # amount picker callback'i _show_market_confirm'ü yeniden
                # çağırır → _peek_market_info taze best_ask/bid çeker.
                InlineKeyboardButton(
                    "🔄 Fiyatı Yenile",
                    callback_data=f"live_market_amount:{side}:{asset}:{tf}:{amount_str}",
                ),
                InlineKeyboardButton("❌ İptal", callback_data="live_main"),
            ],
        ]
    )
    await _safe_edit(q, text, kb)


async def _peek_market_info(engine, coin: str, direction: str, tf: str) -> dict:
    """Market metadata + best ask/bid çek (onay ekranı için)."""
    out = {
        "ok": False,
        "error": "",
        "price": 0.0,
        "best_ask": 0.0,
        "best_bid": 0.0,
        "slug": "",
        "end_iso": "",
        # 2026-05-19: orderbook gerçekten dolu mu — onay ekranı "no match"
        # (likidite yok) riskini önceden uyarmak için. Çekilemezse True
        # varsayılır → yanlış alarm verme.
        "has_asks": True,
        "has_bids": True,
    }
    if not hasattr(engine, "scanner"):
        out["error"] = "scanner unavailable"
        return out
    try:
        market = engine.scanner.get_current_market(coin, tf)
    except Exception as e:  # noqa: BLE001
        out["error"] = f"scanner: {type(e).__name__}: {e}"
        return out
    if not market:
        out["error"] = f"{coin} {tf} active market not found"
        return out

    out["slug"] = market.get("slug", "")
    out["end_iso"] = market.get("endDate", "")
    odds = (
        engine.scanner.get_current_odds(out["slug"])
        if hasattr(engine.scanner, "get_current_odds")
        else None
    )
    if not odds:
        out["error"] = "odds unavailable"
        return out

    if direction == "UP":
        out["price"] = float(odds.get("up_odds", 0))
    else:
        out["price"] = float(odds.get("down_odds", 0))

    # Try to fetch full orderbook for best ask/bid
    try:
        token_ids = market.get("clobTokenIds")
        if isinstance(token_ids, str):
            try:
                import json as _json

                token_ids = _json.loads(token_ids)
            except (ValueError, TypeError):
                token_ids = []
        if token_ids and len(token_ids) >= 2:
            tid = token_ids[0] if direction == "UP" else token_ids[1]
            if hasattr(engine, "polymarket_client") and engine.polymarket_client:
                book = await engine.polymarket_client.get_orderbook(tid)
                if book:
                    asks = book.get("asks") or []
                    bids = book.get("bids") or []
                    # 2026-05-19: gerçek defter derinliği — boşsa onay
                    # ekranı "no match" riski uyarısı verir.
                    out["has_asks"] = bool(asks)
                    out["has_bids"] = bool(bids)
                    if asks:
                        out["best_ask"] = (
                            float(asks[0][0])
                            if isinstance(asks[0], list | tuple)
                            else float(asks[0].get("price", 0))
                        )
                    if bids:
                        out["best_bid"] = (
                            float(bids[0][0])
                            if isinstance(bids[0], list | tuple)
                            else float(bids[0].get("price", 0))
                        )
    except Exception as _ob_e:  # noqa: BLE001
        logger.debug(f"orderbook peek: {_ob_e}")

    # Fallback: spread synthetic
    if out["best_ask"] <= 0:
        out["best_ask"] = out["price"] + 0.005
    if out["best_bid"] <= 0:
        out["best_bid"] = max(0.001, out["price"] - 0.005)
    out["ok"] = True
    return out


async def _execute_market_trade(q, engine, db, side: str, asset: str, tf: str, amount_str: str):
    """LIVE manuel trade execute. Polymarket FOK."""
    asset_label = asset.replace("_", " ")
    amount = float(amount_str)
    coin, direction = asset.split("_")

    text_pre = (
        f"⏳ <b>LIVE {side} işleniyor...</b>\n\n"
        f"Asset: {asset_label} ({tf})\n"
        f"Tutar: ${amount:.2f}\n\n"
        f"Polymarket'e gönderiliyor..."
    )
    try:
        await q.edit_message_text(text_pre, parse_mode="HTML")
    except (TimeoutError, BadRequest, TelegramError):
        pass

    # Execute via live_trader
    try:
        if hasattr(engine.live, "execute_market_order"):
            # P0-08-C (2026-05-08): tf parametresi geçir
            result = await engine.live.execute_market_order(
                side=side,
                coin=coin,
                direction=direction,
                amount=amount,
                tf=tf,
            )
        else:
            result = await _fallback_market_execute(
                engine,
                side,
                coin,
                direction,
                amount,
                tf=tf,
            )
    except Exception as e:  # noqa: BLE001
        result = {"status": "error", "error": str(e)[:200]}

    # Result rendering
    status = (result or {}).get("status", "unknown")
    if status in ("placed", "filled", "matched"):
        emoji = "✅"
        title = f"LIVE {side} BAŞARILI"
    elif status == "mock":
        emoji = "🟡"
        title = f"LIVE {side} (MOCK)"
    elif status.startswith("skip:"):
        # 2026-05-19: skip = trade ATLANDI — başarısızlık değil, para gitmedi.
        emoji = "⏭️"
        title = f"LIVE {side} ATLANDI"
    else:
        emoji = "❌"
        title = f"LIVE {side} BAŞARISIZ"

    detail = (result or {}).get("detail") or (result or {}).get("error") or ""
    order_id = (result or {}).get("order_id", "")
    # 2026-05-19: skip durumlarına kullanıcı-dostu açıklama (ham status yerine).
    skip_hint = ""
    if status == "skip:no_liquidity":
        skip_hint = (
            "Bu markette şu an order defteri (orderbook) ince — market "
            "order'ı dolduracak karşı taraf yoktu. <b>Para harcanmadı.</b> "
            "Birkaç dakika sonra tekrar dene veya daha likit bir "
            "market/timeframe seç."
        )
    elif status.startswith("skip:insufficient_balance"):
        skip_hint = "Polymarket pUSD bakiyesi yetersiz — deposit gerekli."
    elif status.startswith("skip:insufficient_allowance"):
        skip_hint = "Allowance yetersiz — /live panelinden Approve et."

    text_post = (
        f"{emoji} <b>{title}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 Asset: {asset_label} ({tf})\n"
        f"💵 Tutar: ${amount:.2f}\n"
        f"📋 Status: <code>{esc(status)}</code>\n"
    )
    if order_id:
        text_post += f"🆔 Order ID: <code>{esc(str(order_id)[:24])}</code>\n"
    if skip_hint:
        text_post += f"\nℹ️ <i>{skip_hint}</i>\n"
    elif detail:
        text_post += f"\n<i>{esc(detail[:200])}</i>\n"

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📋 Live Geçmiş", callback_data="live_history"),
                InlineKeyboardButton("◀️ Ana Panel", callback_data="live_main"),
            ],
        ]
    )
    try:
        await q.edit_message_text(text_post, parse_mode="HTML", reply_markup=kb)
    except (TimeoutError, BadRequest, TelegramError):
        await q.message.reply_text(text_post, parse_mode="HTML", reply_markup=kb)

    # ── Bildirim — manuel LIVE trade için "💰 LIVE TRADE" prefix ──
    settings = getattr(engine, "settings", None)
    aid = getattr(settings, "ADMIN_TELEGRAM_ID", None) if settings else None
    bot_app = getattr(engine, "bot_app", None)
    if aid and bot_app:
        try:
            await bot_app.bot.send_message(
                chat_id=aid,
                text=(
                    f"💰 <b>LIVE TRADE (manuel)</b>\n"
                    f"{side} {asset_label} ${amount:.2f}\n"
                    f"Status: {status}"
                ),
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001
            pass

    # Audit log
    if db is not None:
        try:
            from datetime import datetime as _dt

            await db.conn.execute(
                "INSERT INTO changelog (event, ts, detail) VALUES (?, ?, ?)",
                (
                    f"MANUAL_LIVE_{side}",
                    _dt.now(UTC).isoformat(),
                    f"asset={asset} amount=${amount} tf={tf} status={status} order={order_id}",
                ),
            )
            await db.conn.commit()
        except Exception:  # noqa: BLE001
            pass


async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Custom amount: /buy BTC UP 3.50 (LIVE MARKET BUY)"""
    await _custom_command(update, context, side="BUY")


async def sell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Custom amount: /sell BTC UP 3.50 (LIVE MARKET SELL)"""
    await _custom_command(update, context, side="SELL")


async def allowance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """2026-05-05: Allowance approve — Polymarket Exchange contract'a
    USDC harcama izni verir. Trade yapmadan önce 1 kez yapılır.
    """
    # H-05 (2026-05-15 ultra-audit): admin gate — on-chain allowance grant.
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Bu komut yöneticiye özel.")
    msg = await update.message.reply_text(
        "⏳ Allowance approve gönderiliyor...\n" "Polygon network on-chain TX, gas öder.",
    )
    try:
        from data.polymarket_actions import approve_allowance

        ok, detail = await approve_allowance()
    except Exception as e:  # noqa: BLE001
        # M-01 (2026-05-15 ultra-audit): log full detail, surface only the
        # exception type to the user. `detail` is rendered to the user via
        # esc(detail[:400]) below — raw str(e) would leak internals.
        logger.exception(f"allowance_command approve failed: {e}")
        ok, detail = False, f"{type(e).__name__} — log'da detay var"

    if ok:
        text = (
            "✅ <b>Allowance Approve Gönderildi</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<i>{esc(detail[:300])}</i>\n\n"
            "🕐 TX 1-2 dakika içinde Polygon'da onaylanır.\n"
            "Onay sonrası /buy /sell veya /live tuşları çalışır.\n\n"
            "Kontrol: <code>/portfolio</code> — Allowance satırı"
        )
    else:
        text = (
            "❌ <b>Allowance Approve BAŞARISIZ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<i>{esc(detail[:400])}</i>\n\n"
            "<b>Manuel çözüm:</b>\n"
            "1. <a href='https://polymarket.com/portfolio'>polymarket.com/portfolio</a> "
            "git\n"
            "2. Wallet bağla (Rabby/MetaMask)\n"
            "3. 'Approve' tuşuna bas (her contract için)\n"
            "4. Polygon'da TX onayla (gas Polygon ödenir)\n"
            "5. Bot'ta /portfolio ile kontrol et"
        )
    try:
        await msg.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:  # noqa: BLE001
        await update.message.reply_text(text, parse_mode="HTML")


def _matrix_supports(settings, coin: str, tf: str) -> bool:
    """P0-08-C (2026-05-08): TF/asset kombinasyonu Polymarket'ta destekleniyor mu?"""
    matrix = getattr(settings, "TF_DISCOVERY_MATRIX", None) or {}
    cfg = matrix.get(tf)
    if not isinstance(cfg, dict):
        return False
    method = cfg.get("method")
    if method == "slug_prefix":
        return coin in (cfg.get("assets") or [])
    if method == "series_id":
        return coin in (cfg.get("series_map") or {})
    return False


async def _custom_command(update: Update, context: ContextTypes.DEFAULT_TYPE, side: str):
    """Custom amount handler — /buy /sell ortak. Direkt LIVE moda gönderir.

    P0-08-C (2026-05-08): 4. opsiyonel arg olarak TF kabul edilir
    (5m / 15m / 1h / 24h). Verilmezse default 5m. Matrix'te asset+tf
    kombinasyonu desteklenmiyorsa hata mesajı + matrix önerisi.
    """
    # H-05 (2026-05-15 ultra-audit): admin gate — /buy + /sell real-money entry.
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Bu komut yöneticiye özel.")
    args = context.args or []
    if len(args) < 3:
        await update.message.reply_text(
            f"Kullanım: <code>/{side.lower()} &lt;coin&gt; &lt;UP/DOWN&gt; &lt;tutar&gt; [tf]</code>\n"
            f"Örnek: <code>/{side.lower()} BTC UP 3.50</code>\n"
            f"Örnek (1h): <code>/{side.lower()} BTC UP 3.50 1h</code>",
            parse_mode="HTML",
        )
        return
    coin = args[0].upper()
    direction = args[1].upper()
    if coin not in ("BTC", "ETH", "SOL", "XRP"):
        await update.message.reply_text(f"❌ Bilinmeyen coin: {coin}. BTC/ETH/SOL/XRP kullan.")
        return
    if direction not in ("UP", "DOWN"):
        await update.message.reply_text(f"❌ Direction UP veya DOWN olmalı, '{direction}' verildi.")
        return
    # H-03 (2026-05-15 ultra-audit): float() accepts 'inf', '-inf', 'nan' —
    # these pass `<=0` check, then downstream math (Kelly sizing, fee calc,
    # CLOB order) produce undefined behaviour. Reject non-finite + cap upper
    # bound. Hard cap 100$ is well above LIVE_BUDGET default (1.49) but
    # gives margin if Heddas hot-tunes LIVE_BUDGET for testing.
    import math as _math
    try:
        amount = float(args[2])
    except ValueError:
        await update.message.reply_text(f"❌ Tutar sayı olmalı: '{args[2]}'")
        return
    if not _math.isfinite(amount):
        await update.message.reply_text(
            f"❌ Tutar sonlu sayı olmalı (inf/nan kabul edilmez): '{args[2]}'"
        )
        return
    if amount <= 0:
        await update.message.reply_text("❌ Tutar pozitif olmalı.")
        return
    if amount > 100.0:
        await update.message.reply_text(
            f"❌ Tutar 100$ ile sınırlı (verilen: {amount:.2f}). "
            f"Daha yüksek için /envt LIVE_BUDGET ve UI guard'ı bypass et."
        )
        return

    # P0-08-C: opsiyonel TF arg (4. position). Default 5m geri uyumluluk için.
    tf = args[3].lower() if len(args) >= 4 else "5m"
    valid_tfs = ("5m", "15m", "1h", "24h")
    if tf not in valid_tfs:
        await update.message.reply_text(f"❌ TF geçersiz: '{tf}'. Geçerli: {', '.join(valid_tfs)}")
        return

    engine = context.bot_data.get("engine")
    if not engine:
        await update.message.reply_text("❌ Engine bulunamadı.")
        return

    # Matrix support check — Polymarket'ta {coin} {tf} kombinasyonu var mı?
    settings = getattr(engine, "settings", None)
    if settings is not None and not _matrix_supports(settings, coin, tf):
        await update.message.reply_text(
            f"❌ <b>{coin} {tf} kombinasyonu desteklenmiyor</b>\n\n"
            f"Polymarket'ta {coin}'in {tf} Up/Down market'i yok.\n"
            f"<i>Konfigürasyonu görüntülemek için: /diagnose</i>",
            parse_mode="HTML",
        )
        return

    asset = f"{coin}_{direction}"
    fake_q = _MagicQueryStub(update)
    await _show_market_confirm(fake_q, engine, side, asset, tf, str(amount))


class _MagicQueryStub:
    """Callback query stub — /buy /sell command çağırırken kullanılır."""

    def __init__(self, update):
        self._update = update
        self.message = update.message

    async def answer(self):
        return None

    async def edit_message_text(self, text, parse_mode=None, reply_markup=None):
        await self._update.message.reply_text(
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )


async def _fallback_market_execute(
    engine,
    side: str,
    coin: str,
    direction: str,
    amount: float,
    tf: str = "5m",
) -> dict:
    """Fallback: scanner → token_id → live_trader._execute_clob.

    live_trader.execute_market_order() yoksa kullanılır. Aktif market'ten
    güncel token_id alır, FOK ile mid-price'a yakın limit gönderir.

    P0-08-C (2026-05-08): tf parametresi eklendi; 5m/15m/1h/24h destekler.
    """
    # Find current market for coin
    if not hasattr(engine, "scanner"):
        return {"status": "error", "error": "scanner unavailable"}

    market = (
        engine.scanner.get_current_market(coin, tf)
        if hasattr(engine.scanner, "get_current_market")
        else None
    )
    if not market:
        return {"status": "error", "error": f"{coin} {tf} market not found"}

    slug = market.get("slug", "")
    token_ids = market.get("clobTokenIds", [])
    if isinstance(token_ids, str):
        try:
            import json as _json

            token_ids = _json.loads(token_ids)
        except (ValueError, TypeError):
            token_ids = []
    if len(token_ids) < 2:
        return {"status": "error", "error": "tokens not found"}

    # UP=index 0, DOWN=index 1 (Polymarket convention — 4 TF için aynı,
    # P0-08-C 2026-05-08 canlı doğrulandı).
    token_id = token_ids[0] if direction == "UP" else token_ids[1]

    # Get current price
    odds = (
        engine.scanner.get_current_odds(slug)
        if hasattr(engine.scanner, "get_current_odds")
        else None
    )
    if not odds:
        return {"status": "error", "error": "odds unavailable"}

    if direction == "UP":
        price = float(odds.get("up_odds", 0))
    else:
        price = float(odds.get("down_odds", 0))
    if price <= 0:
        return {"status": "error", "error": "invalid price"}

    # Execute via live_trader._execute_clob (sync wrapped to async)
    try:
        result = await engine.live._execute_clob(
            token_id,
            amount,
            price,
            "buy" if side == "BUY" else "sell",
        )
        return result or {"status": "failed", "error": "no result"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}


async def _build_main(engine, db):
    """Main dashboard — paper + live side by side.

    2026-04-29 Aşama 3.A: cüzdan tutarlılığı fix. Eski 'Bütçe $1.49' satırı
    LIVE_BUDGET env (bot risk limit) idi — Heddas bunu 'gerçek bakiye'
    gibi görüyordu. Yeni format ayrım yapar:
      - 💰 Polymarket Bakiye (gerçek pUSD, portfolio cache'ten)
      - 🤖 Bot Risk Limit (LIVE_BUDGET env, harcama limiti)
    """
    st = engine.live.get_status()
    active = st.get("active", False)  # is_enabled() = _enabled and not _paused
    on = st["enabled"]
    paused = st.get("paused", False)

    if active:
        status_text = "✅ AKTIF"
    elif on and paused:
        status_text = "⏸ PAUSED"
    else:
        status_text = "🔴 KAPALI"

    # Paper stats
    p_pnl, p_trades, p_wr = 0, 0, 0
    if db:
        try:
            r = await db.conn.execute_fetchall(
                "SELECT COALESCE(SUM(pnl),0), COUNT(*), "
                "COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) "
                "FROM executions WHERE result IS NOT NULL"
            )
            if r:
                p_pnl, p_trades = r[0][0], r[0][1]
                p_wr = r[0][2] / r[0][1] * 100 if r[0][1] > 0 else 0
        except (aiosqlite.Error, IndexError, TypeError, ZeroDivisionError):
            # T11.8-B (2026-04-24): narrow from bare Exception. Aggregate
            # SELECT + row[0][n] indexing + division by zero (defensive).
            pass

    # 2026-04-29 Aşama 3.A: Polymarket gerçek cüzdan bilgileri ortak
    # cache'ten oku. Eğer cache yok ise placeholder göster, tüm UI'lar
    # aynı kaynaktan beslenir (live + dashboard + start + portfolio).
    pm_balance = "N/A"
    pm_allowance = "N/A"
    pm_nav = "N/A"
    pm_age = ""
    live_pnl: dict | None = None  # on-chain bot LIVE PnL (compute_live_pnl)
    try:
        from data.polymarket_portfolio import (
            cache_age_seconds,
            compute_live_pnl,
            read_cached_snapshot,
        )

        pm_snap = await read_cached_snapshot(db)
        if pm_snap:
            pm_balance = f"${float(pm_snap.get('pusd_balance', 0)):.2f}"
            # 2026-05-18: allowance > 1e12 = "unlimited approve" (raw
            # uint256 — 3-contract sınırsız onay). Astronomik rakam
            # ("$1.15e71") yerine ♾️ göster.
            _allow_raw = float(pm_snap.get("pusd_allowance", 0))
            pm_allowance = (
                "♾️ Sınırsız" if _allow_raw > 1e12 else f"${_allow_raw:.2f}"
            )
            pm_nav = f"${float(pm_snap.get('portfolio_value_usd', 0)):.2f}"
            age_s = cache_age_seconds(pm_snap)
            pm_age = f" (veri {age_s}s önce)" if age_s < 999 else " (stale)"
            # 2026-05-18 Faz 2A: bot LIVE PnL — on-chain activity feed'inden
            # hesapla (TRADE maliyeti + REDEEM payout). LIVE_START_DATE
            # öncesi operatörün kişisel geçmişi elenir.
            _activity = pm_snap.get("activity") or []
            if _activity:
                live_pnl = compute_live_pnl(
                    _activity, int(_live_start_dt().timestamp())
                )
    except Exception as _pe:  # noqa: BLE001
        logger.debug(f"pm cache read in live_handler: {_pe}")

    # ── Faz 1 kokpit (2026-05-18 Heddas) — canlı veri toplama ─────────
    # Hepsi defensive: bir kaynak yok/bozuksa o blok atlanır, panel
    # asla crash etmez (mainnet /live — kokpit her zaman açılmalı).

    # Loss streak + risk halt (engine.risk = RiskManager).
    streak, streak_max, risk_halted = 0, 10, False
    try:
        _risk = getattr(engine, "risk", None)
        if _risk is not None:
            _rs = _risk.get_status()
            streak = int(_rs.get("loss_streak", 0))
            risk_halted = bool(_rs.get("halted", False))
            streak_max = int(getattr(_risk.limits, "max_loss_streak", 10))
    except Exception as _re:  # noqa: BLE001
        logger.debug(f"kokpit risk status: {_re}")

    # Binance spot momentum (engine.external_feed).
    momentum_line = ""
    try:
        _ef = getattr(engine, "external_feed", None)
        if _ef is not None and getattr(_ef, "is_available", False):
            _parts = []
            for _a in ("BTC", "ETH", "SOL", "XRP"):
                _mom = _ef.get_spot_momentum(_a, lookback_seconds=60)
                if _mom:
                    _ch = float(_mom.get("change_pct", 0.0))
                    _arrow = "↗" if _ch > 0.02 else ("↘" if _ch < -0.02 else "→")
                    _parts.append(f"{_a} {_arrow}{_ch:+.2f}%")
                else:
                    _px = _ef.get_price(_a)
                    if _px:
                        _parts.append(f"{_a} ${_px:,.0f}")
            if _parts:
                momentum_line = "  " + "  ·  ".join(_parts) + "\n"
    except Exception as _me:  # noqa: BLE001
        logger.debug(f"kokpit momentum: {_me}")

    # Market regime (engine.regime.regime — RegimeClassifier).
    regime_str = ""
    try:
        _rg = getattr(getattr(engine, "regime", None), "regime", None)
        if _rg:
            regime_str = f"  Rejim: ⚖️ {_rg}\n"
    except Exception:  # noqa: BLE001
        pass

    # Kill-switch (engine.kill_switch).
    ks_str = "✅ aktif değil"
    try:
        _ks = getattr(engine, "kill_switch", None)
        if _ks is not None and hasattr(_ks, "is_stopped") and _ks.is_stopped():
            ks_str = "🔴 STOP"
    except Exception:  # noqa: BLE001
        pass

    # 2026-04-29 Aşama 3.B: top-level mode banner
    from telegram_bot.templates.mode_banner import format_banner

    # Risk-limit progress bar — kullanılan / toplam.
    _budget_val = max(0.01, float(st.get("budget", 0.0)))
    _used_val = max(0.0, _budget_val - float(st.get("remaining", 0.0)))
    _bar = _progress_bar(_used_val / _budget_val)
    _days = _mainnet_days()

    # Loss-streak satırı — eşiğe göre uyarı tonu.
    # NOT: bu streak engine.risk (RiskManager) sayacı — PAPER trade
    # settlement'ından beslenir (engine_settlement.py). LIVE trade'ler
    # risk_manager streak'ine dokunmaz. Bu yüzden "Paper" etiketli ve
    # PAPER bloğunda gösterilir — LIVE TRADER bloğunda DEĞİL.
    if streak >= streak_max:
        streak_line = (
            f"  ⚠️ Paper loss-streak: 🔴 <b>{streak}/{streak_max}</b> "
            f"(risk-gate sınırı)\n"
        )
    elif streak >= max(1, streak_max - 2):
        streak_line = f"  ⚠️ Paper loss-streak: {streak}/{streak_max}\n"
    elif streak > 0:
        streak_line = f"  Paper loss-streak: {streak}/{streak_max}\n"
    else:
        streak_line = ""
    _halt_line = "  🛑 <b>RISK HALT AKTİF</b> (paper risk-manager)\n" if risk_halted else ""
    _open_str = "📌 1 açık" if st.get("open") else "— yok"

    text = (
        format_banner() + "🎯 <b>POLYPAPER — LIVE TRADE İSTASYONU</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{status_text}{pm_age}\n\n"
        f"💵 <b>CÜZDAN</b> (Polymarket — gerçek pUSD)\n"
        f"  Bakiye: <b>{pm_balance}</b>  ·  Açık NAV: {pm_nav}\n"
        f"  Allowance: {pm_allowance}\n\n"
        f"🤖 <b>BOT LIVE TRADER</b>  ·  📅 {_days} gün LIVE\n"
        f"  Risk Limit  [{_bar}]\n"
        f"  Kullanılan ${_used_val:.2f} / ${_budget_val:.2f}  "
        f"(kalan ${st.get('remaining', 0):.2f})\n"
        f"  Bugün: <b>${st['daily_pnl']:+.2f}</b>  ·  {st['daily_trades']} trade\n"
        f"  Pozisyon: {_open_str}  ·  Cüzdan: {st['wallet']}\n\n"
        f"{_live_pnl_block(live_pnl)}"
        f"📡 <b>PİYASA</b> (Binance spot · canlı)\n"
        f"{momentum_line}{regime_str}\n"
        f"📋 <b>PAPER</b> (simülasyon — LIVE değil)\n"
        f"  PnL {p_pnl:+.2f} · {p_trades}t · WR %{p_wr:.0f}\n"
        f"{streak_line}{_halt_line}"
        f"🛡️ Kill-switch: {ks_str}\n\n"
        f"<i>BOT LIVE PnL = Polymarket on-chain activity'den hesap (gerçek "
        f"sonuç). Risk Limit = bot harcama tavanı (LIVE_BUDGET). "
        f"Detay → /portfolio · /lg · /rg</i>"
    )

    toggle_btn = "⏸ Duraklat" if active else "▶️ Devam Et"
    if not on:
        toggle_btn = "✅ Live Aç"
    # 2026-05-05 Heddas direktifi (revize): /live ekranı = LIVE MOD.
    # Burada sadece LIVE BUY/SELL var (PAPER ayrı bir ekranda değil).
    # Bot otomatik PAPER trade'leri ayrı bildirim prefix ile gelir ("📋 PAPER ...").
    # Allowance düşükse uyarı + onay tuşu eksik
    allowance_low = False
    try:
        if pm_allowance != "N/A":
            _allow_val = float(str(pm_allowance).replace("$", "").replace(",", ""))
            allowance_low = _allow_val < 1.0
    except (ValueError, TypeError):
        pass

    kb_rows = [
        [InlineKeyboardButton(toggle_btn, callback_data="live_toggle")],
        [
            InlineKeyboardButton("🟢 Market BUY", callback_data="live_market_buy"),
            InlineKeyboardButton("🔴 Market SELL", callback_data="live_market_sell"),
        ],
    ]
    if allowance_low:
        kb_rows.append(
            [
                InlineKeyboardButton(
                    "⚠️ ALLOWANCE EKSİK — Approve",
                    callback_data="live_approve_allowance",
                )
            ]
        )
    # 2026-05-19 Faz 2B/3 — trade istasyonu panel satırları.
    kb_rows.extend(
        [
            [
                InlineKeyboardButton("📡 Piyasa Tara", callback_data="live_scan"),
                InlineKeyboardButton("🛡 Guards", callback_data="live_guards"),
            ],
            [
                InlineKeyboardButton("📈 Performans", callback_data="live_perf"),
                InlineKeyboardButton("⚙️ Risk", callback_data="live_risk"),
            ],
            [
                # 2026-05-18 Heddas: live budget reset (2-tap confirmed callback).
                InlineKeyboardButton("💰 Budget Reset", callback_data="live_budget_reset"),
                InlineKeyboardButton("🔄 Yenile", callback_data="live_main"),
            ],
            # 2026-05-19 tek-kapı: kokpit = LIVE MODE ana ekranı, mod-seçime dön.
            [InlineKeyboardButton("◀️ Mode Seçimi", callback_data="main_dashboard")],
        ]
    )
    kb = InlineKeyboardMarkup(kb_rows)
    return text, kb


async def _build_compare(engine):
    """Side-by-side paper vs real from DB."""
    comp = await engine.live.get_comparison()
    if not comp or comp.get("error"):
        return (
            "📊 <b>Paper vs Real</b>\n\n"
            "<i>Henuz live trade yok veya DB hatasi.\n"
            "Live modu ac ve trade bekle.</i>"
        )

    lt = comp.get("total_trades", 0)
    lp = comp.get("live_pnl", 0)
    pp = comp.get("paper_pnl_equiv", 0)
    wr = comp.get("wr", 0)

    ratio = lp / max(abs(pp), 0.001) if pp != 0 else 0

    text = (
        f"📊 <b>Paper vs Real Karsilastirma</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Live trade: <b>{lt}</b> | WR: {wr:.0f}%\n\n"
        f"{'':8s} {'Paper':>8s}  {'Real':>10s}\n"
        f"{'PnL':8s} {pp:>+8.2f}  {lp:>+10.4f}\n\n"
    )

    if lt > 0:
        text += f"📈 Real/Paper oran: <b>{ratio:.1%}</b>\n"
        if ratio > 0.7:
            text += "✅ Paper sonuclar gercege yakin!\n"
        elif ratio > 0.3:
            text += "⚠️ Paper biraz iyimser — fee/slippage farki\n"
        else:
            text += "🔴 Sapma var — paper modeli kalibre edilmeli\n"

    # Recent trades
    recent = comp.get("recent", [])
    if recent:
        text += "\n<b>Son Trade'ler:</b>\n"
        for t in recent[:5]:
            e = "🟢" if (t.get("live_pnl") or 0) > 0 else "🔴"
            text += (
                f"  {esc(e)} {t.get('strat','')[:18]} {t.get('dir','')[:1].upper()} "
                f"Live:{t.get('live_pnl',0):+.4f} Paper:{t.get('paper_pnl',0):+.2f}\n"
            )

    return text


async def _build_history(engine):
    """Show live trade history from DB."""
    history = await engine.live.load_trade_history()

    text = "📋 <b>Live Trade Gecmisi</b>\n" "━━━━━━━━━━━━━━━━━━━━━\n\n"

    if not history:
        text += "<i>Henuz live trade yok.</i>\n"
    else:
        for t in history[:10]:
            emoji = "🟢" if t.get("pnl", 0) > 0 else ("🔴" if t.get("result") else "⏳")
            text += (
                f"{emoji} {t.get('strategy','?')[:20]}\n"
                f"  {t.get('direction','?').upper()} @{t.get('entry_odds',0):.3f} "
                f"${t.get('amount',0):.2f} → "
                f"Live:{t.get('pnl',0):+.4f} Paper:{t.get('pnl_paper',0):+.2f}\n"
            )

    st = engine.live.get_status()
    # 2026-05-19: `total_pnl` (_total_pnl sayacı) manuel trade'leri kaçırır —
    # yanıltıcı. Gerçek PnL "📈 Performans" panelindeki on-chain blokta.
    text += f"\n💵 Risk limiti kalan: ${st.get('remaining', 0):.2f}"
    return text


async def _build_risk(engine) -> str:
    """⚙️ Risk paneli (Faz 2B 2026-05-19) — RiskManager state + limitler.

    `engine.risk` (RiskManager) `get_status()` canlı snapshot'ını basar.
    Loss-streak PAPER kaynaklıdır (`engine_settlement`'tan beslenir) —
    kokpit ile aynı etiket. Defensive: risk manager yoksa panel yine açılır.
    """
    _risk = getattr(engine, "risk", None)
    head = "⚙️ <b>RİSK YÖNETİCİSİ</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
    if _risk is None or not hasattr(_risk, "get_status"):
        return head + "\n<i>Risk manager bağlı değil (engine.risk yok).</i>"
    try:
        rs = _risk.get_status()
    except Exception as _e:  # noqa: BLE001
        logger.exception(f"_build_risk get_status: {_e}")
        return head + "\n" + _user_error_msg(_e, "risk durumu")

    halted = bool(rs.get("halted", False))
    state_line = (
        f"🛑 <b>HALTED</b> — {esc(str(rs.get('halt_reason', ''))[:80])}"
        if halted
        else "✅ Aktif"
    )
    limits = rs.get("limits", {}) or {}
    streak = int(rs.get("loss_streak", 0))
    streak_max = int(getattr(getattr(_risk, "limits", None), "max_loss_streak", 10))
    total_exp = float(rs.get("total_exposure", 0.0) or 0.0)
    max_exp = float(limits.get("max_exposure", 0.0) or 0.0)
    daily_pnl = float(rs.get("daily_pnl", 0.0) or 0.0)
    max_dl = float(limits.get("max_daily_loss", 0.0) or 0.0)
    exp_bar = _progress_bar(total_exp / max_exp if max_exp > 0 else 0.0)

    text = (
        f"{head}"
        f"Durum: {state_line}\n\n"
        f"📊 <b>Pozisyon &amp; Maruziyet</b>\n"
        f"  Açık pozisyon: {int(rs.get('open_positions', 0))} / "
        f"{int(limits.get('max_positions', 0))}\n"
        f"  Maruziyet [{exp_bar}]\n"
        f"  ${total_exp:.2f} / ${max_exp:.2f}\n\n"
        f"📉 <b>Günlük</b>\n"
        f"  PnL: <b>${daily_pnl:+.2f}</b>  (halt eşiği -${max_dl:.2f})\n"
        f"  Trade: {int(rs.get('daily_trades', 0))}\n\n"
        f"🔥 <b>Loss-streak</b>: {streak} / {streak_max}  "
        f"<i>(paper risk-manager)</i>\n\n"
        f"🎯 <b>Limitler</b>\n"
        f"  Trade başı max: ${float(limits.get('max_position', 0) or 0):.2f}\n"
        f"  Bakiye tabanı: ${float(limits.get('balance_floor', 0) or 0):.2f}\n"
    )
    tiered = rs.get("tiered_limits", {}) or {}
    per_asset = tiered.get("per_asset", {}) or {}
    if per_asset:
        text += "\n🪙 <b>Asset bazlı maruziyet</b>\n"
        for asset, info in sorted(per_asset.items()):
            cur = float((info or {}).get("current", 0) or 0)
            lim = float((info or {}).get("limit", 0) or 0)
            text += f"  {esc(str(asset))}: ${cur:.2f} / ${lim:.2f}\n"
    per_market = (tiered.get("per_market", {}) or {}).get("markets", {}) or {}
    if per_market:
        text += f"\n🏷 Aktif market maruziyeti: {len(per_market)} market\n"
    return text


async def _build_market_scan(engine) -> str:
    """📡 Piyasa Tarama paneli (Faz 2B 2026-05-19) — scanner aktif market'ler.

    `engine.scanner.active_markets` in-memory cache'ini okur (ekstra async
    fetch YOK — scanner job zaten periyodik tarar). Her `{COIN}_{TF}` için
    güncel market + up/down odds. Defensive: scanner yoksa panel yine açılır.
    """
    head = "📡 <b>PİYASA TARAMA</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
    scanner = getattr(engine, "scanner", None)
    if scanner is None:
        return head + "\n<i>Scanner bağlı değil (engine.scanner yok).</i>"

    active = getattr(scanner, "active_markets", {}) or {}
    last_scan = getattr(scanner, "last_scan", None)
    try:
        scan_str = last_scan.strftime("%H:%M:%S UTC") if last_scan else "—"
    except (AttributeError, ValueError):
        scan_str = "—"
    _ws = getattr(scanner, "ws", None)
    ws_str = "🟢 WS" if (_ws and getattr(_ws, "is_connected", False)) else "⚫ REST"
    total = sum(len(v or []) for v in active.values())
    text = f"{head}Son tarama: {scan_str}  ·  {ws_str}  ·  {total} market\n\n"

    if not active or total == 0:
        return text + "<i>Aktif market yok — scanner henüz tarama yapmadı.</i>"

    # Doğal timeframe sırası (5m→4h) — alfabetik "1h<5m" yerine.
    _tf_order = {"5m": 0, "15m": 1, "30m": 2, "1h": 3, "4h": 4}
    keys_sorted = sorted(
        active,
        key=lambda k: (
            str(k).rpartition("_")[0],
            _tf_order.get(str(k).rpartition("_")[2], 9),
        ),
    )
    last_coin = None
    shown = 0
    for key in keys_sorted:
        mkts = active.get(key) or []
        if not mkts or shown >= 30:
            continue
        coin, _, tf = str(key).rpartition("_")
        if not coin:  # key has no underscore — skip malformed
            continue
        if coin != last_coin:
            text += f"<b>{esc(coin)}</b>\n"
            last_coin = coin
        m = mkts[0] or {}
        slug = str(m.get("slug", ""))
        odds = None
        try:
            if hasattr(scanner, "get_current_odds"):
                odds = scanner.get_current_odds(slug)
        except Exception:  # noqa: BLE001
            odds = None
        # 2026-05-19 Heddas: kompakt fiyat-hareketi — 24s ortalama |hareket| +
        # son pencere yön oku. Detaylı blok market BUY onay ekranında.
        _dl = await _fetch_price_deltas(engine, coin, tf)
        _dtxt = ""
        if _dl and int(_dl.get("n", 0)) > 0:
            _aa = float((_dl.get("avg_abs_pct") or {}).get("all", 0))
            _ar = {"up": "▲", "down": "▼"}.get(str(_dl.get("last_dir", "")), "▬")
            _dtxt = f"  ·  {_ar} 24s|Δ| %{_aa:.3f}"
        if odds:
            up = float(odds.get("up_odds", 0) or 0)
            dn = float(odds.get("down_odds", 0) or 0)
            text += f"  {tf:>3}  Up <b>{up:.2f}</b> / Down <b>{dn:.2f}</b>{_dtxt}\n"
        else:
            text += f"  {tf:>3}  <i>odds yok</i>{_dtxt}\n"
        shown += 1
    text += "\n<i>Δ = pencere açılış→kapanış fiyat farkı · |Δ| = mutlak " "ortalama (volatilite). Detay → market BUY onay ekranı.</i>"
    return text.rstrip() + "\n"


async def _build_performance(engine, db) -> str:
    """📈 Performans paneli (Faz 3 + 2026-05-19 veri-zenginliği) — bot
    performansı tek ekranda.

    3 katman: (1) aggregate BOT LIVE PnL (`_live_pnl_block`), (2) on-chain
    İŞLEM DÖKÜMÜ — per-market detay (`_live_pnl_detail_block`: nerede/ne
    zaman/ne olmuş/ne kadar), (3) paper↔real kalibrasyon (`_build_compare`).

    Eski `_build_history` (DB `live_trades` kaynaklı) kaldırıldı — tarihsel
    trade'ler orada yoktu, "9 trade" üstte / "geçmiş yok" altta çelişkisi
    yaratıyordu (D1 audit). On-chain işlem dökümü gerçek geçmiştir.
    """
    live_pnl = None
    try:
        from data.polymarket_portfolio import compute_live_pnl, read_cached_snapshot

        snap = await read_cached_snapshot(db)
        if snap and snap.get("activity"):
            live_pnl = compute_live_pnl(
                snap["activity"], int(_live_start_dt().timestamp())
            )
    except Exception as _e:  # noqa: BLE001
        logger.debug(f"_build_performance pnl: {_e}")

    try:
        compare = await _build_compare(engine)
    except Exception as _e:  # noqa: BLE001
        logger.debug(f"_build_performance compare: {_e}")
        compare = "<i>Karşılaştırma alınamadı.</i>"

    detail = _live_pnl_detail_block(
        live_pnl.get("per_market", []) if isinstance(live_pnl, dict) else []
    )
    return (
        "📈 <b>PERFORMANS</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{_live_pnl_block(live_pnl)}"
        f"{detail}"
        "<i>⬇️ Paper↔Real kalibrasyon — DB live_trades kaynaklı (otomatik-"
        "mirror trade'ler). Manuel trade'ler yukarıdaki on-chain döküm.</i>\n\n"
        f"{compare}"
    )


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster H — merged from ws_handler.py
# ═══════════════════════════════════════════════════════════════════════


async def ws_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ws = context.bot_data.get("ws_client")
    if not ws:
        return await update.message.reply_text(
            "🔌 <b>WebSocket</b>\n\nStatus: ⚫ REST-only mode\n"
            "Install: <code>pip install websockets</code>",
            parse_mode="HTML",
        )

    st = ws.get_status()
    e = "🟢" if st["connected"] else "🔴"
    age = f"{st.get('last_msg_age')}s ago" if st.get("last_msg_age") else "Never"

    text = (
        f"🔌 <b>WebSocket Status</b>\n\n"
        f"Connection: {esc(e)} {'Connected' if st['connected'] else 'Disconnected'}\n"
        f"Subscribed: {st.get('subscribed', 0)} tokens\n"
        f"Messages: {st.get('messages', 0)}\n"
        f"Errors: {st.get('errors', 0)}\n"
        f"Last message: {age}\n"
        f"Reconnects: {st.get('reconnects', 0)}\n"
        f"Cached prices: {st.get('cached_prices', 0)}\n"
    )

    if st["connected"]:
        text += "\nReal-time data active."
    else:
        text += "\nFalling back to REST polling."

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Refresh", callback_data="show_ws")],
            [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
        ]
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def ws_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ws = context.bot_data.get("ws_client")
    if not ws:
        return await q.message.reply_text("🔌 REST-only mode.")

    st = ws.get_status()
    e = "🟢" if st["connected"] else "🔴"
    age = f"{st.get('last_msg_age', '?')}s" if st.get("last_msg_age") else "-"

    text = (
        f"🔌 {esc(e)} | Tokens: {st.get('subscribed',0)} | "
        f"Msgs: {st.get('messages',0)} | Last: {age}"
    )

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Refresh", callback_data="show_ws")],
            [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
        ]
    )
    await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster H — merged from daily_handler.py
# ═══════════════════════════════════════════════════════════════════════
from core.auto_optimizer import AutoOptimizer as _DailyAutoOptimizer  # noqa: E402
from db.database import Database as _DailyDatabase  # noqa: E402


async def _build_daily(db, engine, user_id):
    optimizer = _DailyAutoOptimizer(db, engine)
    text = await optimizer.generate_daily_summary(user_id)
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Analytics", callback_data="show_analytics")],
            [InlineKeyboardButton("🎯 Strategy Stats", callback_data="strategy_stats")],
            [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
        ]
    )
    return text, kb


async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: _DailyDatabase = context.bot_data["db"]
    engine = context.bot_data.get("engine")
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Önce /start komutunu kullanın.")
    text, kb = await _build_daily(db, engine, user.id)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def daily_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db: _DailyDatabase = context.bot_data["db"]
    engine = context.bot_data.get("engine")
    user = await db.get_user_by_telegram_id(q.from_user.id)
    if not user:
        return await q.message.reply_text("Önce /start komutunu kullanın.")
    text, kb = await _build_daily(db, engine, user.id)
    await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
