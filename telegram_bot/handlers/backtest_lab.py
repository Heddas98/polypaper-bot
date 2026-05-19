"""
PolyPaper Bot - /backtest LAB (Faz 1: mode-first iskelet)
==========================================================
2026-05-20 (Heddas direktifi): backtest modülü çok-fonksiyonel
"trade istasyonu"na evrilsin. /backtest tek kapı, 4 panel:

    🚀 Hızlı Test       — preset config'lerle replay backtest
    🛠 Strateji Kurucu  — no-code rule builder (Faz 3-4'te dolar)
    🆚 Karşılaştır      — multi-strategy /compare
    🎯 Kalibrasyon      — live vs paper reality gap

Eski /backtest_v2 + /backtest_replay legacy alias olarak yaşar.

Faz 1 KAPSAMI: panel navigation iskeleti, reality-gap mini-block,
mevcut motora köprüler. YENİ filter/strateji özellikleri Faz 2-6.
Geri uyumluluk: tüm eski komutlar çalışmaya devam eder.

Mimari notlar:
- Callback prefix `lab_*` (lab_main, lab_quick, lab_builder,
  lab_compare, lab_calibrate, lab_refresh).
- Her panel `_safe_edit` ile yenilenir (B1 audit doktrini —
  "message not modified" sessizce yutulur).
- Reality-gap mini-block her panelin ÜSTÜNDE — kullanıcı ne
  yaparsa yapsın paper'ın gerçeğe ne kadar yaklaştığını görür.
- Quick Test paneli Faz 1'de mevcut /backtest_replay'a yönlendirir;
  Faz 2'de yeni filtrelerle inline button akışı.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.backtest_lab")


# ── Helpers ──────────────────────────────────────────────────


def _panel_nav_kb(extra_rows: list[list[InlineKeyboardButton]] | None = None) -> InlineKeyboardMarkup:
    """Alt-panel navigasyonu — Ana Panel + Yenile (live_handler pattern).

    `extra_rows` panel-spesifik buton satırlarını üstte tutar; en alta
    ortak "Ana Panel · Yenile" satırı eklenir.
    """
    rows: list[list[InlineKeyboardButton]] = list(extra_rows or [])
    rows.append(
        [
            InlineKeyboardButton("◀️ Ana Panel", callback_data="lab_main"),
            InlineKeyboardButton("🔄 Yenile", callback_data="lab_refresh"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _main_kb() -> InlineKeyboardMarkup:
    """Mode-select ekranı keyboard'u — 4 panel + legacy köprü."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚀 Hızlı Test", callback_data="lab_quick")],
            [InlineKeyboardButton("🛠 Strateji Kurucu", callback_data="lab_builder")],
            [InlineKeyboardButton("🆚 Karşılaştır", callback_data="lab_compare")],
            [InlineKeyboardButton("🎯 Kalibrasyon", callback_data="lab_calibrate")],
            [InlineKeyboardButton("📚 Eski paneller (/bt2)", callback_data="lab_legacy")],
        ]
    )


async def _safe_edit(q, text: str, kb: InlineKeyboardMarkup | None) -> None:
    """live_handler._safe_edit aynası — "not modified" sessizce yut."""
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    except (TimeoutError, TelegramError):
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


# ── Reality-gap mini-block ──────────────────────────────────


async def _reality_gap_block(db) -> str:
    """Her panelin ÜSTÜNDE 1-4 satırlık özet.

    `live_trades` son 24h penceresinden: trade sayısı, paper × MULT
    karşı live, drift %. Aktif live_trades yoksa "veri yok" satırı.
    REALITY_GAP_MULT env (default 0.66) — `reality_gap_handler`'la aynı.

    Hata olursa boş satır döner (UI patlamasın).
    """
    if db is None or getattr(db, "conn", None) is None:
        return "🎯 <b>Gerçeklik:</b> <i>DB yok</i>\n\n"
    try:
        mult = float(os.getenv("REALITY_GAP_MULT", "0.66"))
    except (TypeError, ValueError):
        mult = 0.66
    try:
        since = (datetime.now(UTC) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        async with db.conn.execute(
            """SELECT COUNT(*),
                      COALESCE(SUM(paper_pnl), 0),
                      COALESCE(SUM(pnl), 0)
               FROM live_trades
               WHERE settled_at IS NOT NULL AND settled_at >= ?""",
            (since,),
        ) as cur:
            row = await cur.fetchone()
        n = int(row[0] or 0) if row else 0
        paper = float(row[1] or 0.0) if row else 0.0
        live = float(row[2] or 0.0) if row else 0.0
    except Exception as e:  # noqa: BLE001
        logger.debug("_reality_gap_block query failed: %s", e)
        return "🎯 <b>Gerçeklik:</b> <i>okuma hatası</i>\n\n"

    if n == 0:
        return (
            "🎯 <b>Gerçeklik (24h):</b> <i>henüz settled live trade yok</i>\n"
            f"  beklenen ölçek: paper × <code>{mult}</code>\n\n"
        )

    expected = paper * mult
    drift = live - expected
    denom = abs(expected) if abs(expected) > 0.01 else 0.01
    drift_pct = (drift / denom) * 100.0

    # Renk emojisi: ±10% sapma alert eşiği (REALITY_GAP_ALERT_PCT default)
    try:
        alert_pct = float(os.getenv("REALITY_GAP_ALERT_PCT", "10.0"))
    except (TypeError, ValueError):
        alert_pct = 10.0
    icon = "🟢" if abs(drift_pct) <= alert_pct else "🟡"

    return (
        f"🎯 <b>Gerçeklik (24h, {n} trade):</b>\n"
        f"  paper × {mult} → <code>${expected:+.2f}</code>  "
        f"vs live <code>${live:+.2f}</code>  {icon} "
        f"(<code>{drift_pct:+.1f}%</code>)\n\n"
    )


# ── Strategy & motor durum özetleri ─────────────────────────


def _strategy_count() -> tuple[int, list[str]]:
    """Mevcut backtest stratejilerinin sayısı + ilk birkaçı."""
    try:
        from backtest.strategies.base import StrategyRegistryV2

        names = StrategyRegistryV2.list_all()
        return len(names), names[:6]
    except Exception as e:  # noqa: BLE001
        logger.debug("_strategy_count: registry probe failed: %s", e)
        return 0, []


async def _ob_snapshots_summary(db) -> str:
    """ReplayEngine'in çekebileceği veri penceresi — 1 satırlık özet.

    `ob_snapshots` tablosundan: kayıt sayısı + en yeni-en eski ms farkı.
    Quick Test panelinde "bu kadar veriniz var" hissi verir.
    """
    if db is None or getattr(db, "conn", None) is None:
        return "<i>ob_snapshots: DB yok</i>"
    try:
        async with db.conn.execute(
            "SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM ob_snapshots"
        ) as cur:
            row = await cur.fetchone()
        n = int(row[0] or 0) if row else 0
        if n == 0:
            return "<i>ob_snapshots: kayıt yok</i>"
        min_ms = int(row[1] or 0)
        max_ms = int(row[2] or 0)
        span_h = (max_ms - min_ms) / 3_600_000 if max_ms > min_ms else 0
        return f"<code>{n:,}</code> snapshot · <code>{span_h:.1f}h</code> pencere"
    except Exception as e:  # noqa: BLE001
        logger.debug("_ob_snapshots_summary failed: %s", e)
        return "<i>ob_snapshots: okuma hatası</i>"


# ── Panel builders ──────────────────────────────────────────


async def _build_main(db) -> tuple[str, InlineKeyboardMarkup]:
    """Mode-select ekran — 4 panel + reality-gap + meta özet."""
    gap = await _reality_gap_block(db)
    strat_n, strat_sample = _strategy_count()
    ob = await _ob_snapshots_summary(db)

    sample_txt = ", ".join(esc(s) for s in strat_sample[:5]) or "<i>yok</i>"
    if strat_n > 5:
        sample_txt += f", …+{strat_n - 5}"

    text = (
        "🧪 <b>BACKTEST LAB</b>\n"
        "<i>Tek kapı — mode-first paradigma</i>\n\n"
        f"{gap}"
        "📦 <b>Veri kaynağı:</b>\n"
        f"  ob_snapshots: {ob}\n"
        f"  strateji: <code>{strat_n}</code> kayıtlı ({sample_txt})\n\n"
        "Hangi panele girmek istersin?\n\n"
        "🚀 <b>Hızlı Test</b> — son N market, preset config\n"
        "🛠 <b>Strateji Kurucu</b> — kural-bazlı no-code "
        "<i>(Faz 3-4)</i>\n"
        "🆚 <b>Karşılaştır</b> — iki+ stratejiyi yan yana\n"
        "🎯 <b>Kalibrasyon</b> — paper × MULT vs live drift\n"
    )
    return text, _main_kb()


async def _build_quick(db) -> tuple[str, InlineKeyboardMarkup]:
    """🚀 Hızlı Test paneli — preset config'lerle replay backtest.

    Faz 1: mevcut /backtest_replay komutuna köprü. Her preset için
    çalıştırılacak komut metnini gösterir (kullanıcı kopya-yapıştır
    edebilir veya butonla doğrudan komut çağrısı — Faz 2'de inline).
    """
    gap = await _reality_gap_block(db)
    ob = await _ob_snapshots_summary(db)
    strat_n, _ = _strategy_count()

    text = (
        "🚀 <b>HIZLI TEST</b>\n"
        "<i>Preset config ile gerçek L2 replay backtest</i>\n\n"
        f"{gap}"
        f"📦 Veri: {ob} · strateji: <code>{strat_n}</code>\n\n"
        "<b>Önerilen presetler</b> (kopya/yapıştır veya buton):\n\n"
        "1️⃣  Son 100 BTC 5m market — varsayılan strateji\n"
        "    <code>/backtest_replay hour_edge BTC 5m</code>\n\n"
        "2️⃣  Son 200 ETH 5m market\n"
        "    <code>/backtest_replay hour_edge ETH 5m</code>\n\n"
        "3️⃣  Son 100 BTC 15m market\n"
        "    <code>/backtest_replay hour_edge BTC 15m</code>\n\n"
        "4️⃣  Tüm stratejileri karşılaştır (son 100 BTC 5m)\n"
        "    <code>/compare</code>\n\n"
        "<i>Faz 2'de bu paneller inline butonlarla doğrudan koşar,"
        " saniye-aralığı / saat / weekday / price-trigger filtreleri eklenir.</i>"
    )
    extra = [
        [InlineKeyboardButton("🛠 Strateji Kurucu", callback_data="lab_builder")],
        [InlineKeyboardButton("🆚 Karşılaştır", callback_data="lab_compare")],
    ]
    return text, _panel_nav_kb(extra_rows=extra)


async def _build_builder(db) -> tuple[str, InlineKeyboardMarkup]:
    """🛠 Strateji Kurucu paneli — Faz 3-4'te no-code rule builder.

    Faz 1: önizleme — gelecek yetenekleri listele, kullanıcı bekleyişi
    olmadan deneyebileceği mevcut stratejileri göster.
    """
    gap = await _reality_gap_block(db)
    strat_n, strat_sample = _strategy_count()
    sample_txt = "\n".join(f"  • <code>{esc(s)}</code>" for s in strat_sample[:8])
    if strat_n > 8:
        sample_txt += f"\n  • <i>...+{strat_n - 8} daha</i>"

    text = (
        "🛠 <b>STRATEJİ KURUCU</b>\n"
        "<i>No-code rule builder — Faz 3-4'te aktifleşir</i>\n\n"
        f"{gap}"
        "<b>Şu an mevcut</b> — Python-kodlu stratejiler:\n"
        f"{sample_txt}\n\n"
        "<b>Faz 3-4'te gelecek</b> — kural cümleleri ile no-code:\n"
        "  • <i>Coin = BTC · TF = 5m · saniye 30–50'de AL</i>\n"
        "  • <i>Up fiyat ≥ 0.55 → BUY YES, ≥ 0.80 → SELL</i>\n"
        "  • <i>Saat 22:00, UTC Pazartesi → işlem yap</i>\n"
        "  • <i>Limit order @ 0.45, 60sn'de fill olmazsa iptal</i>\n"
        "  • Tüm kurallar AND/OR ile birleştirilir\n"
        "  • JSON'a kaydedilir → strateji listesine eklenir\n\n"
        "<i>Yön: Faz 2'de motor filtreleri (saniye/saat/price-trigger),"
        " Faz 3'te <code>RuleBasedStrategy</code> adaptörü, Faz 4'te"
        " Telegram'dan inline buton akışıyla kural kurma.</i>"
    )
    extra = [
        [InlineKeyboardButton("🚀 Hızlı Test", callback_data="lab_quick")],
    ]
    return text, _panel_nav_kb(extra_rows=extra)


async def _build_compare(db) -> tuple[str, InlineKeyboardMarkup]:
    """🆚 Karşılaştır paneli — mevcut /compare köprüsü."""
    gap = await _reality_gap_block(db)
    strat_n, _ = _strategy_count()

    text = (
        "🆚 <b>KARŞILAŞTIR</b>\n"
        "<i>2+ stratejiyi aynı veri üzerinde yan yana</i>\n\n"
        f"{gap}"
        f"📦 Kayıtlı strateji: <code>{strat_n}</code>\n\n"
        "<b>Kullanım</b>:\n"
        "  <code>/compare hour_edge composite streak_reversal</code>\n\n"
        "Train/test 70/30 bölmesi için sonuna <code>split</code> ekle:\n"
        "  <code>/compare hour_edge composite split</code>\n\n"
        "Çıktı: her strateji için WR, PnL, Sharpe, max drawdown +"
        " ortak veri üzerinde sıralanmış tablo.\n\n"
        "<i>Faz 4'te kurucu-üretimi stratejiler de bu listede çıkar.</i>"
    )
    extra = [
        [InlineKeyboardButton("🚀 Hızlı Test", callback_data="lab_quick")],
        [InlineKeyboardButton("🎯 Kalibrasyon", callback_data="lab_calibrate")],
    ]
    return text, _panel_nav_kb(extra_rows=extra)


async def _build_calibrate(db) -> tuple[str, InlineKeyboardMarkup]:
    """🎯 Kalibrasyon paneli — reality_gap detayı + ek metrikler.

    Üstteki mini-block + nightly rapor referansı + manuel komut.
    """
    gap = await _reality_gap_block(db)

    # Ek pencere — son 7g (24h'in tamamlayıcısı, nightly rapor penceresine yakın)
    long_block = "<i>7g penceresi yok</i>"
    if db is not None and getattr(db, "conn", None) is not None:
        try:
            since_7d = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
            async with db.conn.execute(
                """SELECT COUNT(*),
                          COALESCE(SUM(paper_pnl), 0),
                          COALESCE(SUM(pnl), 0)
                   FROM live_trades
                   WHERE settled_at IS NOT NULL AND settled_at >= ?""",
                (since_7d,),
            ) as cur:
                row = await cur.fetchone()
            n7 = int(row[0] or 0) if row else 0
            paper7 = float(row[1] or 0.0) if row else 0.0
            live7 = float(row[2] or 0.0) if row else 0.0
            try:
                mult = float(os.getenv("REALITY_GAP_MULT", "0.66"))
            except (TypeError, ValueError):
                mult = 0.66
            exp7 = paper7 * mult
            if n7 > 0:
                drift7 = live7 - exp7
                denom = abs(exp7) if abs(exp7) > 0.01 else 0.01
                pct = (drift7 / denom) * 100.0
                long_block = (
                    f"<b>7g pencere ({n7} trade):</b>\n"
                    f"  paper × MULT → <code>${exp7:+.2f}</code>\n"
                    f"  live          → <code>${live7:+.2f}</code>\n"
                    f"  drift         → <code>${drift7:+.2f}</code> "
                    f"(<code>{pct:+.1f}%</code>)"
                )
            else:
                long_block = "<i>7g penceresinde settled live trade yok</i>"
        except Exception as e:  # noqa: BLE001
            logger.debug("_build_calibrate 7d query failed: %s", e)

    text = (
        "🎯 <b>KALİBRASYON</b>\n"
        "<i>Backtest sonuçları gerçeğe ne kadar yakın?</i>\n\n"
        f"{gap}"
        f"{long_block}\n\n"
        "<b>Detay</b>:\n"
        "  • <code>/reality_gap</code> (alias <code>/rg</code>) — "
        "nightly rapor + 24h snapshot\n"
        "  • <code>/ref_audit</code> (alias <code>/ra</code>) — "
        "reference price audit (Binance kline)\n"
        "  • <code>/recon</code> (alias <code>/rc</code>) — pUSD "
        "on-chain vs DB\n\n"
        "<b>Env'ler</b>:\n"
        f"  <code>REALITY_GAP_MULT</code> = "
        f"<code>{os.getenv('REALITY_GAP_MULT', '0.66')}</code>\n"
        f"  <code>REALITY_GAP_ALERT_PCT</code> = "
        f"<code>{os.getenv('REALITY_GAP_ALERT_PCT', '10.0')}</code>%\n"
        f"  <code>REALITY_GAP_WINDOW_H</code> = "
        f"<code>{os.getenv('REALITY_GAP_WINDOW_H', '168')}</code>h\n\n"
        "<i>Faz 6'da her LAB run'ı bittiğinde gap blok'u otomatik gösterilir +"
        " Polymarket docs constant drift CI guard'ı eklenir.</i>"
    )
    extra = [
        [InlineKeyboardButton("🚀 Hızlı Test", callback_data="lab_quick")],
        [InlineKeyboardButton("🆚 Karşılaştır", callback_data="lab_compare")],
    ]
    return text, _panel_nav_kb(extra_rows=extra)


async def _build_legacy(db) -> tuple[str, InlineKeyboardMarkup]:
    """📚 Eski paneller — /bt2 ve eski komutlara köprü (geri uyumluluk)."""
    gap = await _reality_gap_block(db)
    text = (
        "📚 <b>ESKİ PANELLER</b>\n"
        "<i>Geri uyumluluk — eski komutlar çalışmaya devam ediyor</i>\n\n"
        f"{gap}"
        "  • <code>/backtest_v2</code> (alias <code>/bt2</code>) — "
        "PolyCop-style interactive panel\n"
        "  • <code>/backtest_replay</code> — gerçek L2 replay (CLI-style)\n"
        "  • <code>/compare</code> — multi-strategy karşılaştırma\n\n"
        "<i>Yeni LAB tek kapı; eskileri zamanla deprecate edeceğiz.</i>"
    )
    return text, _panel_nav_kb()


# ── Public entry points ─────────────────────────────────────


async def backtest_lab_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/backtest, /bt, /lab — mode-select ekranını aç."""
    db = context.bot_data.get("db")
    try:
        text, kb = await _build_main(db)
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:  # noqa: BLE001
        logger.exception("backtest_lab_command failed: %s", e)
        await update.message.reply_text(
            "⚠️ LAB paneli açılamadı — log'da detay var.",
            parse_mode="HTML",
        )


# Callback data → builder eşlemesi. `lab_main` ve `lab_refresh` aynı
# ana paneli açar — refresh ana panelden tetiklendiğinde anlamlı,
# alt panelden ise (panel kendi refresh data'sını override eder)
# en kötü Main'e döner. Faz 2'de panel-spesifik refresh.
_BUILDERS = {
    "lab_main": _build_main,
    "lab_refresh": _build_main,
    "lab_quick": _build_quick,
    "lab_builder": _build_builder,
    "lab_compare": _build_compare,
    "lab_calibrate": _build_calibrate,
    "lab_legacy": _build_legacy,
}


async def backtest_lab_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`lab_*` callback dispatcher — yalnızca navigasyon (real-money YOK).

    Bu panelin hiçbir butonu trading başlatmaz — sadece backtest/raporlama
    ekranları arası geçiş. Bu yüzden admin gate'i live_handler kadar sıkı
    değil (kötüye kullanım yüzeyi yok); yine de panel render hatasında
    sessizce log + kullanıcıya nazik mesaj.
    """
    q = update.callback_query
    if q is None:
        return
    try:
        await q.answer()
    except TelegramError:
        pass

    data = q.data or ""
    builder = _BUILDERS.get(data)
    if builder is None:
        logger.warning("backtest_lab_callback: bilinmeyen data=%r", data)
        return

    db = context.bot_data.get("db")
    try:
        text, kb = await builder(db)
        await _safe_edit(q, text, kb)
    except Exception as e:  # noqa: BLE001
        logger.exception("backtest_lab_callback build failed: %s", e)
        await _safe_edit(
            q,
            "⚠️ Panel üretilemedi — log'da detay var.",
            _main_kb(),
        )
