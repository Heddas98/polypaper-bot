"""
PolyPaper Bot - /backtest LAB
==============================
2026-05-20 (Heddas direktifi): backtest modülü çok-fonksiyonel
"trade istasyonu". /backtest tek kapı, 4 panel:

    🚀 Hızlı Test       — preset config'lerle replay backtest (köprü)
    🛠 Strateji Kurucu  — Faz 4: JSON paste + listele/sil interaktif
    🆚 Karşılaştır      — multi-strategy /compare (köprü)
    🎯 Kalibrasyon      — live vs paper reality gap

Eski /backtest_v2 + /backtest_replay legacy alias olarak yaşar.

Mimari notlar:
- Callback prefix `lab_*`. Parametresiz: lab_main/quick/builder/compare/
  calibrate/legacy/refresh/help_save + lab_pw/pw_sec/pw_price/pw_hour
  (preset sihirbazı menüleri). Parametreli (Faz 4 + 4b):
  lab_show:<name>, lab_del_ask:<name>, lab_del_confirm:<name>,
  lab_pw_sec_save:<window>:<dir>, lab_pw_price_dir:<dir>,
  lab_pw_price_save:<dir>:<cents>, lab_pw_hour_pick:<hh>,
  lab_pw_hour_save:<hh>:<dir>. Ad regex-validated, state'siz tasarım
  (tüm seçim callback_data'da encoded — `context.user_data` kullanmaz).
- Her panel `_safe_edit` ile yenilenir (B1 audit doktrini —
  "message not modified" sessizce yutulur).
- Reality-gap mini-block her panelin ÜSTÜNDE — kullanıcı ne yaparsa
  yapsın paper'ın gerçeğe ne kadar yaklaştığını görür.
- /lab_save: JSON paste flow — komutun ardından gelen JSON parse +
  validate + data_store/bt_strategies/{name}.json olarak yazılır.
- Faz 4b preset sihirbazları: 🧙 Preset Sihirbazı menüsünden 3 şablon —
  ⏱ Saniye Aralığı (window + direction → save), 📈 Fiyat ≥ X
  (direction → threshold → save), 🕒 Saat X'te (hour → direction →
  save). Auto-naming (sec_30_60_up, price_above_55c_up, hour_22_down).
  2-3 tıkla kayıt — geliştirici JSON yazma derdinden kurtulur.
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
    """Mode-select ekranı keyboard'u — paneller + legacy köprü."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚀 Hızlı Test (tick)", callback_data="lab_quick")],
            [InlineKeyboardButton("🎲 Candle / Martingale", callback_data="lab_candle")],
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

    # Kullanıcı ruleset'i sayısını ayrı bilelim — hazır stratejiler ayrı kaynak
    user_rs_n = 0
    try:
        from backtest.strategies.rule_based import list_rulesets

        user_rs_n = len(list_rulesets())
    except Exception as e:  # noqa: BLE001
        logger.debug("_build_main rulesets count failed: %s", e)

    text = (
        "🧪 <b>BACKTEST LAB</b>\n"
        "<i>Gerçek L2 orderbook üzerinde backtest</i>\n\n"
        f"{gap}"
        "📦 <b>Veri kaynağı:</b>\n"
        f"  ob_snapshots: {ob}\n"
        f"  📋 Kendi kuralların: <b>{user_rs_n}</b> kayıtlı ruleset\n"
        f"  📚 Hazır referans: <b>{strat_n}</b> Python stratejisi\n\n"
        "Hangi panele girmek istersin?\n\n"
        "🚀 <b>Hızlı Test</b> — kendi kuralın veya hazır strateji ile koş\n"
        "🛠 <b>Strateji Kurucu</b> — no-code kural yaz (sihirbaz veya JSON)\n"
        "🆚 <b>Karşılaştır</b> — iki+ kural/strateji yan yana\n"
        "🎯 <b>Kalibrasyon</b> — paper × MULT vs live drift\n"
    )
    return text, _main_kb()


async def _build_quick(db) -> tuple[str, InlineKeyboardMarkup]:
    """🚀 Hızlı Test paneli — kullanıcının kendi kuralları öncelikli.

    Heddas direktifi 2026-05-21: "ben hazır assetleri kullanmak
    istemiyorum". Bu yüzden:
      • Kullanıcı ruleset'leri ÖN PLANDA — her biri için tek-tık komut
      • Hazır Python stratejileri (hour_edge, taker_flow, ...) ARKA
        PLANDA — opsiyonel, referans/karşılaştırma için duruyor
      • Hiç ruleset yoksa: Kurucu'ya net yönlendirme
    """
    gap = await _reality_gap_block(db)
    ob = await _ob_snapshots_summary(db)

    # Kullanıcı ruleset'leri (data_store/bt_strategies/)
    user_rulesets: list[dict] = []
    try:
        from backtest.strategies.rule_based import list_rulesets

        user_rulesets = list_rulesets()
    except Exception as e:  # noqa: BLE001
        logger.debug("_build_quick rulesets list failed: %s", e)

    lines = [
        "🚀 <b>HIZLI TEST</b>",
        "<i>Gerçek L2 ob_snapshots üzerinde replay backtest</i>",
        "",
        gap.rstrip(),
        "",
        f"📦 Veri: {ob}",
        "",
    ]

    if user_rulesets:
        lines.append("<b>📋 Kendi kuralların</b> — koşturmak için kopyala-yapıştır:")
        for rs in user_rulesets[:8]:
            nm = rs.get("name", "?")
            direction = rs.get("direction", "?")
            cond_n = len(rs.get("entry", {}).get("conditions", []))
            lines.append(
                f"  • <code>{esc(nm)}</code> "
                f"({esc(direction)}, {cond_n} kural)\n"
                f"    <code>/backtest_replay rule_based BTC 5m</code>"
            )
        if len(user_rulesets) > 8:
            lines.append(f"  <i>...+{len(user_rulesets) - 8} daha</i>")
        lines.append("")
        lines.append(
            "<i>Not: rule_based stratejisi en son kaydettiğin "
            "ruleset'i auto-load eder. Birden fazla ruleset için "
            "şimdilik tek tek koş.</i>"
        )
    else:
        lines.append(
            "<b>📋 Kendi kuralların:</b> <i>henüz yok</i>\n\n"
            "🛠 <b>Strateji Kurucu</b>'ya geç — 2-3 tıkla preset "
            "sihirbazı veya JSON paste ile kendi kuralını yaz."
        )

    lines.append("")
    lines.append(
        "<b>📚 Hazır referans stratejileri</b> "
        "<i>(opsiyonel, Heddas kullanmıyor)</i>"
    )
    lines.append(
        "  Eski Python class'ları (hour_edge, taker_flow, composite, "
        "streak_reversal...) — referans/karşılaştırma için duruyor. "
        "Kullanmak istemezsen yok say."
    )
    lines.append(
        "  Liste için: <code>/strategies</code> · "
        "Koşturmak için: <code>/backtest_replay &lt;ad&gt; BTC 5m</code>"
    )

    text = "\n".join(lines)

    extra = [
        [InlineKeyboardButton("🛠 Strateji Kurucu", callback_data="lab_builder")],
        [InlineKeyboardButton("🆚 Karşılaştır", callback_data="lab_compare")],
    ]
    return text, _panel_nav_kb(extra_rows=extra)


async def _build_builder(db) -> tuple[str, InlineKeyboardMarkup]:
    """🛠 Strateji Kurucu paneli — Faz 4: interaktif liste + JSON paste.

    Listede her kullanıcı ruleset'i için "🔍 Detay" butonu (sil flow'una
    açılır). Yeni strateji eklemek için `/lab_save` komutu — JSON paste
    flow (wizard'sız, geliştirici için en hızlı). Faz 4b'de preset
    sihirbazı (saniye aralığı, fiyat trigger gibi hazır şablonlar).
    """
    gap = await _reality_gap_block(db)
    strat_n, strat_sample = _strategy_count()
    sample_txt = "\n".join(f"  • <code>{esc(s)}</code>" for s in strat_sample[:6])
    if strat_n > 6:
        sample_txt += f"\n  • <i>...+{strat_n - 6} daha</i>"

    rs_list: list[dict] = []
    try:
        from backtest.strategies.rule_based import list_rulesets

        rs_list = list_rulesets()
    except Exception as e:  # noqa: BLE001
        logger.debug("_build_builder rulesets list failed: %s", e)

    if rs_list:
        lines = []
        for rs in rs_list[:10]:
            nm = rs.get("name", "?")
            cond_n = len(rs.get("entry", {}).get("conditions", []))
            lines.append(
                f"  • <code>{esc(nm)}</code> "
                f"({esc(rs.get('direction', '?'))}, {cond_n} kural)"
            )
        if len(rs_list) > 10:
            lines.append(f"  • <i>...+{len(rs_list) - 10} daha</i>")
        user_rulesets_txt = "\n".join(lines)
    else:
        user_rulesets_txt = "<i>Henüz no-code strateji yok.</i>"

    text = (
        "🛠 <b>STRATEJİ KURUCU</b>\n"
        "<i>No-code rule builder — sihirbaz veya JSON</i>\n\n"
        f"{gap}"
        "<b>📋 Kendi kuralların</b> "
        "(<code>data_store/bt_strategies/</code>):\n"
        f"{user_rulesets_txt}\n\n"
        "<b>📚 Hazır Python stratejileri</b> "
        "<i>(referans için — plugin değil, repo'nun kendi class'ları)</i>:\n"
        f"{sample_txt}\n"
        "<i>Bunlar geliştiricinin yazdığı sabit Python sınıfları (örn. "
        "hour_edge = saatlik bias, taker_flow = hacim sinyali). "
        "Sen kendi kurallarını yapıyorsan kullanmana gerek yok.</i>\n\n"
        "<b>Yeni kural</b> — 2 yol:\n"
        "  🧙 <b>Preset Sihirbazı</b> (en hızlı — 2-3 tık)\n"
        "  📥 Manuel JSON: <code>/lab_save</code> komutu\n\n"
        "<b>JSON formatı</b>:\n"
        "<pre>/lab_save\n"
        "{\n"
        '  "name": "30_50_buy_up",\n'
        '  "direction": "up",\n'
        '  "confidence": 0.7,\n'
        '  "entry": {\n'
        '    "logic": "AND",\n'
        '    "conditions": [\n'
        '      {"field": "elapsed_seconds", "op": "&gt;=", "value": 30},\n'
        '      {"field": "elapsed_seconds", "op": "&lt;=", "value": 50},\n'
        '      {"field": "up_best_ask",     "op": "&gt;=", "value": 0.55}\n'
        "    ]\n"
        "  }\n"
        "}</pre>\n"
        "Field'lar: elapsed_seconds, elapsed_pct, up_best_bid/ask, "
        "down_best_bid/ask, spread, binance_price, binance_price_change, "
        "hour_utc, market_type, coin\n"
        "Op'lar: <code>== != &lt; &lt;= &gt; &gt;= in not_in</code>"
    )

    # Her ruleset için detay butonu (sil flow'u oradan)
    detail_rows: list[list[InlineKeyboardButton]] = []
    for rs in rs_list[:8]:  # Telegram callback inline-data ≤64 byte, 8 buton güvenli
        nm = rs.get("name", "")
        if nm:
            detail_rows.append(
                [InlineKeyboardButton(f"🔍 {nm}", callback_data=f"lab_show:{nm}")]
            )

    extra = [
        *detail_rows,
        [InlineKeyboardButton("🧙 Preset Sihirbazı", callback_data="lab_pw")],
        [InlineKeyboardButton("📥 /lab_save yardım", callback_data="lab_help_save")],
        [InlineKeyboardButton("🚀 Hızlı Test", callback_data="lab_quick")],
    ]
    return text, _panel_nav_kb(extra_rows=extra)


async def _build_show_ruleset(name: str) -> tuple[str, InlineKeyboardMarkup]:
    """Tek bir ruleset'in detayını göster + sil/geri butonları.

    `lab_show:<name>` callback'inden çağrılır. Bilinmeyen ad → builder'a
    nazik dönüş + bilgilendirme.
    """
    from backtest.strategies.rule_based import _NAME_RX, list_rulesets

    if not _NAME_RX.match(name or ""):
        return (
            "⚠️ <b>Geçersiz ruleset adı.</b>\nBuilder paneline dön.",
            _panel_nav_kb(
                extra_rows=[
                    [InlineKeyboardButton("◀️ Kurucu", callback_data="lab_builder")]
                ]
            ),
        )

    rs = None
    for r in list_rulesets():
        if r.get("name") == name:
            rs = r
            break
    if rs is None:
        return (
            f"⚠️ Ruleset bulunamadı: <code>{esc(name)}</code>",
            _panel_nav_kb(
                extra_rows=[
                    [InlineKeyboardButton("◀️ Kurucu", callback_data="lab_builder")]
                ]
            ),
        )

    import json as _json

    pretty = _json.dumps(rs, indent=2, ensure_ascii=False)
    # Telegram <pre> limit ~4096; pretty likely small ama yine de kırp
    if len(pretty) > 3000:
        pretty = pretty[:3000] + "\n... <kesildi>"

    # Kuralları insan-okunur özetle (JSON yerine)
    cond_lines = _humanize_conditions(rs)
    direction = rs.get("direction", "?")
    dir_emoji = "⬆️" if direction == "up" else "⬇️" if direction == "down" else "↕️"

    text = (
        f"🔍 <b>{esc(name)}</b>\n"
        f"{dir_emoji} Yön: <b>{esc(direction.upper())}</b> · "
        f"güven: {rs.get('confidence', 0.7)}\n\n"
        "<b>Kurallar:</b>\n"
        f"{cond_lines}\n\n"
        "<b>🚀 Backtest et</b> — market seç (tek tık, komut yok):"
    )
    # Market backtest butonları — 2026-05-21 Heddas: komut yazma yok, tuş
    extra = [
        [
            InlineKeyboardButton("🟠 BTC 5m", callback_data=f"lab_bt:{name}:BTC:5m"),
            InlineKeyboardButton("🟠 BTC 15m", callback_data=f"lab_bt:{name}:BTC:15m"),
        ],
        [
            InlineKeyboardButton("🟠 BTC 1h", callback_data=f"lab_bt:{name}:BTC:1h"),
            InlineKeyboardButton("🔵 ETH 15m", callback_data=f"lab_bt:{name}:ETH:15m"),
        ],
        [InlineKeyboardButton("🗑 Sil", callback_data=f"lab_del_ask:{name}")],
        [InlineKeyboardButton("◀️ Kurucu", callback_data="lab_builder")],
    ]
    return text, _panel_nav_kb(extra_rows=extra)


def _humanize_conditions(rs: dict) -> str:
    """RuleSet conditions'ı insan-okunur satırlara çevir (JSON yerine).

    Heddas direktifi 2026-05-21: "isimleri daha açıklayıcı olsun" —
    {"field":"elapsed_seconds","op":">=","value":30} yerine
    "⏱ Market açılışından 30sn sonra" gibi.
    """
    entry = rs.get("entry", {})
    conds = entry.get("conditions", [])
    if not conds:
        return "  <i>(koşul yok — strateji ateşlemez)</i>"
    logic = entry.get("logic", "AND")
    # Alan → insan-okunur etiket
    field_labels = {
        "elapsed_seconds": "⏱ Market saniyesi",
        "elapsed_pct": "⏱ Market ilerleme %",
        "up_best_ask": "📈 UP alış fiyatı",
        "up_best_bid": "📈 UP satış fiyatı",
        "down_best_ask": "📉 DOWN alış fiyatı",
        "down_best_bid": "📉 DOWN satış fiyatı",
        "spread": "↔️ Spread",
        "binance_price": "💲 Binance fiyatı",
        "binance_price_change": "📊 Binance değişim %",
        "hour_utc": "🕐 UTC saat",
        "market_type": "📐 Market türü",
        "coin": "🪙 Coin",
    }
    op_labels = {
        ">=": "≥", "<=": "≤", ">": ">", "<": "<",
        "==": "=", "!=": "≠", "in": "içinde", "not_in": "dışında",
    }
    lines = []
    for c in conds:
        if not isinstance(c, dict):
            continue
        fld = field_labels.get(c.get("field", ""), c.get("field", "?"))
        op = op_labels.get(c.get("op", ""), c.get("op", "?"))
        val = c.get("value", "?")
        lines.append(f"  • {fld} {op} <b>{esc(str(val))}</b>")
    joiner = "\n  <i>VE</i>\n" if logic == "AND" else "\n  <i>VEYA</i>\n"
    return joiner.join(lines) if len(lines) > 1 else "\n".join(lines)


async def _run_inline_backtest(name: str, asset: str, tf: str, db) -> tuple[str, InlineKeyboardMarkup]:
    """Ruleset'i inline backtest et — komut yok, tek tık (Heddas 2026-05-21).

    `lab_bt:<name>:<asset>:<tf>` callback'inden çağrılır. Ruleset yüklenir,
    BacktestRunner ile koşulur, zengin sonuç tablosu döner.
    """
    from backtest.strategies.rule_based import _NAME_RX, list_rulesets

    back_kb = _panel_nav_kb(
        extra_rows=[
            [InlineKeyboardButton("◀️ Strateji detayı", callback_data=f"lab_show:{name}")],
            [InlineKeyboardButton("🛠 Kurucu", callback_data="lab_builder")],
        ]
    )

    if not _NAME_RX.match(name or "") or asset not in ("BTC", "ETH", "SOL", "XRP"):
        return "⚠️ Geçersiz backtest parametresi.", back_kb

    rs = None
    for r in list_rulesets():
        if r.get("name") == name:
            rs = r
            break
    if rs is None:
        return f"⚠️ Ruleset bulunamadı: <code>{esc(name)}</code>", back_kb

    if db is None or getattr(db, "conn", None) is None:
        return "⚠️ DB bağlantısı yok — backtest yapılamadı.", back_kb

    try:
        from backtest.runner import BacktestRunner, RunConfig

        cfg = RunConfig(
            asset=asset, timeframe=tf, strategy_name="rule_based",
            strategy_params=rs, last_n=200,
        )
        summary = await BacktestRunner(db).run(cfg)
    except Exception as e:  # noqa: BLE001
        logger.exception("_run_inline_backtest failed: %s", e)
        return (
            f"⚠️ Backtest hatası: <i>{esc(type(e).__name__)}</i> — log'da detay.",
            back_kb,
        )

    pnl_icon = "🟢" if summary.total_pnl > 0 else "🔴" if summary.total_pnl < 0 else "⚪"
    dir_lbl = rs.get("direction", "?").upper()

    if summary.n_markets_discovered == 0:
        body = (
            f"<i>{esc(summary.note or 'Bu market/TF için yeterli veri yok.')}</i>\n\n"
            "Bot daha çok veri toplamalı (1-2 saat) ya da başka market/TF dene."
        )
    else:
        body = (
            "📊 <b>Performans</b>\n"
            f"  {pnl_icon} PnL: <b>${summary.total_pnl:+.2f}</b>\n"
            f"  🎯 Win Rate: <b>{summary.win_rate:.1f}%</b>\n"
            f"  📈 Trade: {summary.n_trades} "
            f"({summary.wins}W / {summary.losses}L)\n"
            f"  💰 Ort. PnL/trade: ${summary.avg_pnl:+.4f}\n"
            f"  💸 Toplam fee: ${summary.fees_total:.4f}\n\n"
            "📦 <b>Kapsam</b>\n"
            f"  Taranan market: {summary.n_markets_discovered}\n"
            f"  İşlenen: {summary.n_markets_processed} "
            f"(+{summary.n_markets_skipped} atlandı)\n"
            f"  Final bakiye: ${summary.final_balance:.2f}"
        )

    text = (
        f"🚀 <b>Backtest Sonucu</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Strateji: <code>{esc(name)}</code> ({esc(dir_lbl)})\n"
        f"🎯 Market: <b>{esc(asset)} {esc(tf)}</b> · son {cfg.last_n}\n\n"
        f"{body}"
    )
    return text, back_kb


async def _build_del_confirm(name: str) -> tuple[str, InlineKeyboardMarkup]:
    """Silmeden önce 2-tık onayı (live_handler doktrini — destructive actions)."""
    from backtest.strategies.rule_based import _NAME_RX

    if not _NAME_RX.match(name or ""):
        return (
            "⚠️ Geçersiz ruleset adı.",
            _panel_nav_kb(
                extra_rows=[[InlineKeyboardButton("◀️ Kurucu", callback_data="lab_builder")]]
            ),
        )
    text = (
        f"🗑 <b>Silmek istediğine emin misin?</b>\n\n"
        f"<code>{esc(name)}.json</code>\n\n"
        "<i>Bu işlem geri alınamaz (dosya kalıcı silinir).</i>"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ EVET, sil", callback_data=f"lab_del_confirm:{name}"
                ),
                InlineKeyboardButton(
                    "❌ İptal", callback_data=f"lab_show:{name}"
                ),
            ],
            [InlineKeyboardButton("◀️ Kurucu", callback_data="lab_builder")],
        ]
    )
    return text, kb


async def _build_del_done(name: str) -> tuple[str, InlineKeyboardMarkup]:
    """Silme işlemini yürüt + sonucu göster. Bilgi panel — Kurucu'ya köprü."""
    from backtest.strategies.rule_based import delete_ruleset

    ok = delete_ruleset(name)
    if ok:
        text = (
            f"✅ <b>Silindi</b>: <code>{esc(name)}.json</code>\n\n"
            "Kurucu paneline geri dön."
        )
    else:
        text = (
            f"⚠️ Silinemedi: <code>{esc(name)}</code> "
            "(zaten yok veya OS hatası — log'da detay).\n\n"
            "Kurucu paneline geri dön."
        )
    return text, _panel_nav_kb(
        extra_rows=[[InlineKeyboardButton("◀️ Kurucu", callback_data="lab_builder")]]
    )


# ── Faz 4b — Preset sihirbazları (state'siz, callback'te encoded) ───


# Preset pencereler ve eşikler — kompakt seçenek listesi
_SEC_WINDOWS = [
    ("10_30", "10-30sn"),
    ("30_50", "30-50sn"),
    ("30_60", "30-60sn"),
    ("60_120", "60-120sn"),
    ("120_180", "120-180sn"),
    ("180_240", "180-240sn"),
    ("240_290", "240-290sn"),
]

_PRICE_THRESHOLDS = [
    ("25", "0.25"),
    ("35", "0.35"),
    ("45", "0.45"),
    ("55", "0.55"),
    ("65", "0.65"),
    ("75", "0.75"),
    ("85", "0.85"),
]

_HOUR_PICKS = [
    ("00", "00:00 UTC"),
    ("06", "06:00 UTC"),
    ("12", "12:00 UTC"),
    ("14", "14:00 UTC"),
    ("18", "18:00 UTC"),
    ("22", "22:00 UTC"),
]

# Faz 5b: limit preset için fiyat + expire seçenekleri (callback_data ≤64 byte)
_LIMIT_PRICES = [
    ("25", "0.25"),
    ("35", "0.35"),
    ("45", "0.45"),
    ("55", "0.55"),
    ("65", "0.65"),
    ("75", "0.75"),
]
_LIMIT_EXPIRES = [
    ("0", "Açık (market_close)"),
    ("30", "30 sn"),
    ("60", "60 sn"),
    ("120", "120 sn"),
    ("240", "240 sn"),
]


def _save_preset_ruleset(ruleset: dict) -> tuple[str, str]:
    """save_ruleset wrapper — başarı/başarısızlık dönüş metni döndürür.

    Returns: (status_text_html, name)
    """
    from backtest.strategies.rule_based import RuleSetError, save_ruleset

    name = ruleset.get("name", "?")
    try:
        target = save_ruleset(ruleset)
        return (
            f"✅ <b>Kaydedildi</b>: <code>{esc(name)}</code>\n"
            f"📁 <code>{esc(str(target))}</code>\n\n"
            f"Backtest çalıştır:\n"
            f"<code>/backtest_replay rule_based BTC 5m</code>"
        ), name
    except RuleSetError as e:
        return f"❌ Geçersiz ruleset: {esc(str(e))}", name
    except Exception as e:  # noqa: BLE001
        logger.exception("_save_preset_ruleset failed: %s", e)
        return "⚠️ Kaydedilemedi — log'da detay var.", name


def _done_kb() -> InlineKeyboardMarkup:
    """Preset save sonrası dönüş butonları."""
    return _panel_nav_kb(
        extra_rows=[
            [InlineKeyboardButton("◀️ Kurucu", callback_data="lab_builder")],
            [InlineKeyboardButton("🧙 Başka preset", callback_data="lab_pw")],
        ]
    )


async def _build_wiz_menu() -> tuple[str, InlineKeyboardMarkup]:
    """🧙 Preset sihirbaz menüsü — 4 şablon."""
    text = (
        "🧙 <b>PRESET SİHİRBAZI</b>\n"
        "<i>Hazır şablon → 2-3 tıkla kayıtlı listeye eklenir</i>\n\n"
        "<b>Şablonlar:</b>\n\n"
        "⏱ <b>Saniye Aralığı Al</b>\n"
        "    \"30-50 saniye arası UP al\" gibi pencere-bazlı\n\n"
        "📈 <b>Fiyat ≥ X Al</b>\n"
        "    \"UP fiyat 0.55'i geçince al\" gibi trigger\n\n"
        "🕒 <b>Saat X'te Al</b>\n"
        "    \"Saat 22 UTC marketinde UP al\" gibi schedule\n\n"
        "📋 <b>Limit @ X Al</b>\n"
        "    GTC limit emir: ask fiyatı X'e düşünce fill, expire sn yoksa"
        " market_close'a kadar bekler.\n"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⏱ Saniye Aralığı", callback_data="lab_pw_sec")],
            [InlineKeyboardButton("📈 Fiyat ≥ X", callback_data="lab_pw_price")],
            [InlineKeyboardButton("🕒 Saat X'te", callback_data="lab_pw_hour")],
            [InlineKeyboardButton("📋 Limit @ X", callback_data="lab_pw_limit")],
            [InlineKeyboardButton("◀️ Kurucu", callback_data="lab_builder")],
        ]
    )
    return text, kb


async def _build_wiz_sec() -> tuple[str, InlineKeyboardMarkup]:
    """⏱ Saniye-aralığı: pencere + yön (her satırda UP/DOWN buton çifti)."""
    text = (
        "⏱ <b>Saniye Aralığı Al</b>\n\n"
        "Hangi pencere ve yönde sinyal istiyorsun?\n"
        "<i>(Sinyal yalnız bu pencerede izinli — pencere dışında strateji"
        " hareket etmez.)</i>\n"
    )
    rows = []
    for window, label in _SEC_WINDOWS:
        rows.append(
            [
                InlineKeyboardButton(
                    f"⬆ UP {label}", callback_data=f"lab_pw_sec_save:{window}:up"
                ),
                InlineKeyboardButton(
                    "⬇ DOWN", callback_data=f"lab_pw_sec_save:{window}:down"
                ),
            ]
        )
    rows.append([InlineKeyboardButton("◀️ Sihirbaz", callback_data="lab_pw")])
    return text, InlineKeyboardMarkup(rows)


async def _build_wiz_sec_save(arg: str) -> tuple[str, InlineKeyboardMarkup]:
    """⏱ Saniye preset'i kaydet. `arg` = "<min>_<max>:<dir>"."""
    try:
        window, direction = arg.split(":", 1)
        min_s, max_s = window.split("_", 1)
        min_i = int(min_s)
        max_i = int(max_s)
        if direction not in ("up", "down") or min_i < 0 or max_i <= min_i:
            raise ValueError("invalid args")
    except (ValueError, AttributeError):
        return "❌ Geçersiz sihirbaz parametresi.", _done_kb()

    name = f"sec_{min_i}_{max_i}_{direction}"
    ruleset = {
        "name": name,
        "version": "1.0",
        "direction": direction,
        "confidence": 0.7,
        "description": f"Preset: {direction.upper()} al, saniye {min_i}-{max_i}",
        "entry": {
            "logic": "AND",
            "conditions": [
                {"field": "elapsed_seconds", "op": ">=", "value": min_i},
                {"field": "elapsed_seconds", "op": "<=", "value": max_i},
            ],
        },
    }
    status, _ = _save_preset_ruleset(ruleset)
    return status, _done_kb()


async def _build_wiz_price() -> tuple[str, InlineKeyboardMarkup]:
    """📈 Fiyat ≥ X: önce yön seç (sonraki adım eşik seçimi)."""
    text = (
        "📈 <b>Fiyat ≥ X Al</b>\n\n"
        "Hangi yöne al? <i>(UP fiyat referans alınır — up_best_ask ≥ eşik"
        " olunca sinyal.)</i>\n"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬆ UP", callback_data="lab_pw_price_dir:up"),
                InlineKeyboardButton("⬇ DOWN", callback_data="lab_pw_price_dir:down"),
            ],
            [InlineKeyboardButton("◀️ Sihirbaz", callback_data="lab_pw")],
        ]
    )
    return text, kb


async def _build_wiz_price_dir(direction: str) -> tuple[str, InlineKeyboardMarkup]:
    """📈 Fiyat eşiği seç."""
    if direction not in ("up", "down"):
        return "❌ Geçersiz yön.", _done_kb()
    text = (
        f"📈 <b>Fiyat ≥ X Al</b> — yön <b>{direction.upper()}</b>\n\n"
        "Eşik fiyatı seç (UP token referans):\n"
    )
    rows = []
    for cents, label in _PRICE_THRESHOLDS:
        rows.append(
            [
                InlineKeyboardButton(
                    f"≥ {label}",
                    callback_data=f"lab_pw_price_save:{direction}:{cents}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("◀️ Yön seç", callback_data="lab_pw_price")])
    return text, InlineKeyboardMarkup(rows)


async def _build_wiz_price_save(arg: str) -> tuple[str, InlineKeyboardMarkup]:
    """📈 Fiyat preset'i kaydet. `arg` = "<dir>:<cents>"."""
    try:
        direction, cents = arg.split(":", 1)
        cents_i = int(cents)
        if direction not in ("up", "down") or not (1 <= cents_i <= 99):
            raise ValueError("invalid args")
    except (ValueError, AttributeError):
        return "❌ Geçersiz sihirbaz parametresi.", _done_kb()

    threshold = cents_i / 100.0
    name = f"price_above_{cents_i}c_{direction}"
    ruleset = {
        "name": name,
        "version": "1.0",
        "direction": direction,
        "confidence": 0.7,
        "description": (
            f"Preset: {direction.upper()} al, up_best_ask ≥ {threshold:.2f}"
        ),
        "entry": {
            "logic": "AND",
            "conditions": [
                {"field": "up_best_ask", "op": ">=", "value": threshold},
            ],
        },
    }
    status, _ = _save_preset_ruleset(ruleset)
    return status, _done_kb()


async def _build_wiz_hour() -> tuple[str, InlineKeyboardMarkup]:
    """🕒 Saat X'te: önce saat seç (sonra yön)."""
    text = (
        "🕒 <b>Saat X'te Al</b>\n\n"
        "Hangi UTC saatte sinyal çıksın?\n"
        "<i>(market.hour_utc bu saate eşitse strateji aktif.)</i>\n"
    )
    rows = []
    pairs = [_HOUR_PICKS[i : i + 3] for i in range(0, len(_HOUR_PICKS), 3)]
    for triple in pairs:
        rows.append(
            [
                InlineKeyboardButton(
                    label, callback_data=f"lab_pw_hour_pick:{hh}"
                )
                for hh, label in triple
            ]
        )
    rows.append([InlineKeyboardButton("◀️ Sihirbaz", callback_data="lab_pw")])
    return text, InlineKeyboardMarkup(rows)


async def _build_wiz_hour_pick(hh: str) -> tuple[str, InlineKeyboardMarkup]:
    """🕒 Saat seçildi → yön sor."""
    try:
        hour_i = int(hh)
        if not (0 <= hour_i <= 23):
            raise ValueError()
    except ValueError:
        return "❌ Geçersiz saat.", _done_kb()
    text = (
        f"🕒 <b>Saat {hour_i:02d}:00 UTC marketleri</b>\n\n"
        "Yön?\n"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬆ UP", callback_data=f"lab_pw_hour_save:{hh}:up"
                ),
                InlineKeyboardButton(
                    "⬇ DOWN", callback_data=f"lab_pw_hour_save:{hh}:down"
                ),
            ],
            [InlineKeyboardButton("◀️ Saat seç", callback_data="lab_pw_hour")],
        ]
    )
    return text, kb


# ── Faz 5b — Limit Al preset (3 adım: yön → fiyat → expire) ────


async def _build_wiz_limit() -> tuple[str, InlineKeyboardMarkup]:
    """📋 Limit @ X: önce yön seç."""
    text = (
        "📋 <b>Limit @ X Al</b> — GTC limit order preset\n\n"
        "Hangi yöne emir? <i>(seçtiğin tarafın ask fiyatı, sonraki adımda"
        " seçeceğin limit'in altına düşünce fill — yoksa expire/market_close.)</i>\n"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬆ UP", callback_data="lab_pw_limit_dir:up"),
                InlineKeyboardButton("⬇ DOWN", callback_data="lab_pw_limit_dir:down"),
            ],
            [InlineKeyboardButton("◀️ Sihirbaz", callback_data="lab_pw")],
        ]
    )
    return text, kb


async def _build_wiz_limit_dir(direction: str) -> tuple[str, InlineKeyboardMarkup]:
    """📋 Limit fiyatı seç (yön belli)."""
    if direction not in ("up", "down"):
        return "❌ Geçersiz yön.", _done_kb()
    text = (
        f"📋 <b>Limit @ X Al</b> — yön <b>{direction.upper()}</b>\n\n"
        f"Hangi fiyatın altına düşünce fill?\n"
        "<i>Yön UP ise up_best_ask, DOWN ise down_best_ask referansı.</i>\n"
    )
    rows = []
    for cents, label in _LIMIT_PRICES:
        rows.append(
            [
                InlineKeyboardButton(
                    f"@ {label}",
                    callback_data=f"lab_pw_limit_price:{direction}:{cents}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("◀️ Yön seç", callback_data="lab_pw_limit")])
    return text, InlineKeyboardMarkup(rows)


async def _build_wiz_limit_price(arg: str) -> tuple[str, InlineKeyboardMarkup]:
    """📋 Limit expire seçimi. `arg` = "<dir>:<cents>"."""
    try:
        direction, cents = arg.split(":", 1)
        cents_i = int(cents)
        if direction not in ("up", "down") or not (1 <= cents_i <= 99):
            raise ValueError("invalid")
    except (ValueError, AttributeError):
        return "❌ Geçersiz argüman.", _done_kb()

    threshold = cents_i / 100.0
    text = (
        f"📋 <b>Limit @ X Al</b> — {direction.upper()} @ <b>{threshold:.2f}</b>\n\n"
        "Limit ne kadar süre açık kalsın?\n"
        "<i>0 = market_close'a kadar açık (en sabırlı). N sn = sinyalden bu kadar"
        " sn sonra dolmazsa iptal — trade açılmaz.</i>\n"
    )
    rows = []
    for expire, label in _LIMIT_EXPIRES:
        rows.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"lab_pw_limit_save:{direction}:{cents}:{expire}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton("◀️ Fiyat seç", callback_data=f"lab_pw_limit_dir:{direction}")]
    )
    return text, InlineKeyboardMarkup(rows)


async def _build_wiz_limit_save(arg: str) -> tuple[str, InlineKeyboardMarkup]:
    """📋 Limit preset'i kaydet. `arg` = "<dir>:<cents>:<expire>"."""
    try:
        parts = arg.split(":")
        if len(parts) != 3:
            raise ValueError("3-part expected")
        direction, cents, expire = parts
        cents_i = int(cents)
        expire_i = int(expire)
        if direction not in ("up", "down") or not (1 <= cents_i <= 99) or expire_i < 0:
            raise ValueError("range")
    except (ValueError, AttributeError):
        return "❌ Geçersiz argüman.", _done_kb()

    threshold = cents_i / 100.0
    expire_tag = "open" if expire_i == 0 else f"{expire_i}s"
    name = f"limit_{cents_i}c_{direction}_{expire_tag}"

    ruleset: dict = {
        "name": name,
        "version": "1.0",
        "direction": direction,
        "confidence": 0.7,
        "description": (
            f"Preset: GTC limit @ {threshold:.2f} {direction.upper()}, "
            f"expire={'market_close' if expire_i == 0 else f'{expire_i}s'}"
        ),
        # Limit emirler genelde herhangi bir snap'te yakalanmalı — basit koşul
        # (ilk snap'te sinyal atar, sonraki snap'lerde limit fill bekler).
        # Strateji default'unda "her snap"'te ateşleme olmaz, bu yüzden minimal
        # ama her zaman geçen bir entry condition kullanıyoruz.
        "entry": {
            "logic": "AND",
            "conditions": [
                {"field": "elapsed_seconds", "op": ">=", "value": 0},
            ],
        },
        "entry_limit_price": threshold,
    }
    if expire_i > 0:
        ruleset["entry_limit_expire_seconds"] = expire_i

    status, _ = _save_preset_ruleset(ruleset)
    return status, _done_kb()


async def _build_wiz_hour_save(arg: str) -> tuple[str, InlineKeyboardMarkup]:
    """🕒 Saat preset'i kaydet. `arg` = "<hh>:<dir>"."""
    try:
        hh, direction = arg.split(":", 1)
        hour_i = int(hh)
        if direction not in ("up", "down") or not (0 <= hour_i <= 23):
            raise ValueError("invalid args")
    except (ValueError, AttributeError):
        return "❌ Geçersiz sihirbaz parametresi.", _done_kb()

    name = f"hour_{hour_i:02d}_{direction}"
    ruleset = {
        "name": name,
        "version": "1.0",
        "direction": direction,
        "confidence": 0.7,
        "description": (
            f"Preset: {direction.upper()} al, market saat {hour_i:02d}:00 UTC"
        ),
        "entry": {
            "logic": "AND",
            "conditions": [
                {"field": "hour_utc", "op": "==", "value": hour_i},
            ],
        },
    }
    status, _ = _save_preset_ruleset(ruleset)
    return status, _done_kb()


async def _build_help_save() -> tuple[str, InlineKeyboardMarkup]:
    """`/lab_save` komutunun nasıl kullanılacağı — örnek + kural ipuçları."""
    text = (
        "📥 <b>/lab_save — JSON paste flow</b>\n\n"
        "<b>Kullanım</b>: Komutun ARDINDAN bir satır boşluk bırak ve "
        "geçerli JSON ruleset yapıştır.\n\n"
        "<b>Basit örnek</b> — sadece saniye 30-50 al:\n"
        "<pre>/lab_save\n"
        "{\n"
        '  "name": "test_30_50",\n'
        '  "direction": "up",\n'
        '  "entry": {\n'
        '    "conditions": [\n'
        '      {"field": "elapsed_seconds", "op": "&gt;=", "value": 30},\n'
        '      {"field": "elapsed_seconds", "op": "&lt;=", "value": 50}\n'
        "    ]\n"
        "  }\n"
        "}</pre>\n"
        "<b>Stop-loss notu</b>: fiyat eşiğinde exit (örn. 0.30'a düşünce sat) "
        "strateji-bazında değil, ReplayConfig knob'undadır — "
        "<code>exit_yes_price_below</code> alanı (motor seviyesinde).\n\n"
        "<b>Doğrulama kuralları</b>:\n"
        "  • name: 1-64 karakter <code>[A-Za-z0-9_-]</code> (dosya güvenli)\n"
        "  • direction: <code>up</code> veya <code>down</code>\n"
        "  • confidence: 0.0 - 1.0 (default 0.7)\n"
        "  • entry.logic: <code>AND</code> veya <code>OR</code> (default AND)\n"
        "  • entry.conditions: en az 1 koşul\n\n"
        "Geçerli koşul → <code>data_store/bt_strategies/{name}.json</code>"
        " olarak kaydedilir, sonra Kurucu listesinde görünür."
    )
    return text, _panel_nav_kb(
        extra_rows=[[InlineKeyboardButton("◀️ Kurucu", callback_data="lab_builder")]]
    )


async def _build_compare(db) -> tuple[str, InlineKeyboardMarkup]:
    """🆚 Karşılaştır paneli — /compare köprüsü.

    Heddas direktifi 2026-05-21: hazır asset kullanmıyor, kendi kuralları
    var. Örnekler de kullanıcı ruleset'lerinden çekilsin (varsa).
    """
    gap = await _reality_gap_block(db)

    # Kullanıcı ruleset adları — örnek komutta hazır class ismi yerine onları öner
    user_rs_names: list[str] = []
    try:
        from backtest.strategies.rule_based import list_rulesets

        user_rs_names = [rs.get("name", "") for rs in list_rulesets()[:3] if rs.get("name")]
    except Exception as e:  # noqa: BLE001
        logger.debug("_build_compare rulesets list failed: %s", e)

    if len(user_rs_names) >= 2:
        # Kullanıcının kendi ruleset'leri ile örnek (rule_based kuralları aynı
        # ad'a takar — şu an /compare ile multi-ruleset koşturmak için
        # rule_based stratejisi her ruleset için ayrı kayıtlı olmalı.
        # Bu mevcut UX limiti — örnek yine de gerçek isimler.)
        usage_example = (
            "  <code>/compare rule_based rule_based</code>\n"
            "  <i>(her ikisi de en son kayıtlı kuralı yükler — multi-ruleset"
            " karşılaştırma backlog'da; şimdilik birer-birer çalıştır)</i>"
        )
    else:
        usage_example = (
            "  <code>/compare ad1 ad2 ad3</code> "
            "<i>(kendi ruleset adların — önce Kurucu'da kaydet)</i>"
        )

    text = (
        "🆚 <b>KARŞILAŞTIR</b>\n"
        "<i>2+ kuralı/stratejiyi aynı veri üzerinde yan yana</i>\n\n"
        f"{gap}"
        f"📋 Kendi kuralın: <b>{len(user_rs_names)}+</b> kayıtlı\n\n"
        "<b>Kullanım</b>:\n"
        f"{usage_example}\n\n"
        "Train/test 70/30 bölmesi için sonuna <code>split</code> ekle.\n\n"
        "Çıktı: her strateji için WR, PnL, Sharpe, max drawdown + "
        "ortak veri üzerinde sıralanmış tablo.\n\n"
        "<i>Hazır referans Python stratejileri ile karşılaştırmak istersen "
        "<code>/strategies</code> listesinden adlarını al.</i>"
    )
    extra = [
        [InlineKeyboardButton("🚀 Hızlı Test", callback_data="lab_quick")],
        [InlineKeyboardButton("🎯 Kalibrasyon", callback_data="lab_calibrate")],
    ]
    return text, _panel_nav_kb(extra_rows=extra)


async def _per_strategy_drift_block(db, mult: float, top_n: int = 5) -> str:
    """Faz 6b (2026-05-20): live_trades GROUP BY strategy_label — top N drift.

    Heddas direktifi devam: hangi stratejinin paper'a uymayan live PnL ürettiğini
    panel-üstünde gör. Pencere 7g (24h'i Faz 1 mini-block zaten gösteriyor).
    Sadece settled trade'ler dahil.

    Hata olursa boş satır (sessiz fallback).
    """
    if db is None or getattr(db, "conn", None) is None:
        return ""
    try:
        from datetime import UTC, datetime, timedelta

        since = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        async with db.conn.execute(
            """SELECT strategy_label,
                      COUNT(*) AS n,
                      COALESCE(SUM(paper_pnl), 0) AS paper,
                      COALESCE(SUM(pnl), 0) AS live
               FROM live_trades
               WHERE settled_at IS NOT NULL AND settled_at >= ?
               GROUP BY strategy_label
               ORDER BY n DESC
               LIMIT ?""",
            (since, top_n),
        ) as cur:
            rows = await cur.fetchall()
    except Exception as e:  # noqa: BLE001
        logger.debug("_per_strategy_drift_block failed: %s", e)
        return ""

    if not rows:
        return ""

    # Defansif: 4-element olmayan row'lar atlanır (mock/legacy data güvenliği)
    valid_rows = [r for r in rows if r is not None and len(r) >= 4]
    if not valid_rows:
        return ""

    lines = ["<b>Strateji bazında drift (7g)</b>:"]
    for row in valid_rows:
        label = str(row[0] or "?")
        n = int(row[1] or 0)
        paper = float(row[2] or 0.0)
        live = float(row[3] or 0.0)
        expected = paper * mult
        if abs(expected) > 0.01:
            drift_pct = (live - expected) / abs(expected) * 100.0
            pct_str = f"{drift_pct:+.0f}%"
        else:
            pct_str = "n/a"
        # Renk: ±10% eşiği aşmış mı?
        try:
            alert_pct = float(os.getenv("REALITY_GAP_ALERT_PCT", "10.0"))
        except (TypeError, ValueError):
            alert_pct = 10.0
        if abs(expected) > 0.01 and abs((live - expected) / abs(expected) * 100.0) > alert_pct:
            icon = "🟡"
        elif n == 0:
            icon = "⚪"
        else:
            icon = "🟢"
        lines.append(
            f"  {icon} <code>{esc(label)}</code> "
            f"({n}t) "
            f"exp <code>${expected:+.2f}</code> "
            f"vs <code>${live:+.2f}</code> "
            f"({pct_str})"
        )
    return "\n".join(lines)


async def _build_calibrate(db) -> tuple[str, InlineKeyboardMarkup]:
    """🎯 Kalibrasyon paneli — reality_gap detayı + ek metrikler.

    Üstteki mini-block + nightly rapor referansı + manuel komut + Faz 6b'de
    eklenen per-strateji drift breakdown.
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

    # Faz 6: Polymarket constants block — drift status (test pin'ler kanıt)
    constants_block = "<i>Sabitler bloğu okunamadı</i>"
    try:
        from core.fees_v2 import CATEGORY_FEES, TAIL_HIGH, TAIL_LOW

        crypto = CATEGORY_FEES.get("crypto", {})
        constants_block = (
            "<b>Polymarket sabitleri</b> "
            "<i>(son docs doğrulaması: 2026-05-20)</i>\n"
            f"  crypto taker: <code>{crypto.get('taker_rate', '?')}</code> · "
            f"exp <code>{crypto.get('taker_exp', '?')}</code> · "
            f"maker rebate <code>{crypto.get('maker_rebate_pct', 0) * 100:.0f}%</code>\n"
            f"  tail zones: <code>{TAIL_LOW}</code>..<code>{TAIL_HIGH}</code>\n"
            "  drift check: <code>py scripts/check_polymarket_drift.py</code>\n"
            "  pin testi: <code>pytest tests/unit/test_polymarket_constants_drift.py</code>"
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("_build_calibrate constants block failed: %s", e)

    # Faz 6b: per-strateji drift breakdown (live_trades GROUP BY strategy_label)
    per_strat = await _per_strategy_drift_block(db, mult)
    per_strat_block = f"{per_strat}\n\n" if per_strat else ""

    text = (
        "🎯 <b>KALİBRASYON</b>\n"
        "<i>Backtest sonuçları gerçeğe ne kadar yakın?</i>\n\n"
        f"{gap}"
        f"{long_block}\n\n"
        f"{per_strat_block}"
        f"{constants_block}\n\n"
        "<b>Detay komutları</b>:\n"
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
        f"<code>{os.getenv('REALITY_GAP_WINDOW_H', '168')}</code>h"
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


# ── 🎲 Candle / Martingale (market-level, candles_ext) ──────


async def _build_candle_menu(db) -> tuple[str, InlineKeyboardMarkup]:
    """🎲 Candle/Martingale menü — hazır kombinasyonlar (tek tık).

    Heddas direktifi: "candle'ları topluyoruz, kullanalım" + martingale.
    candles_ext (Binance gerçek yön) 2600+ market — tick'ten çok zengin.
    """
    text = (
        "🎲 <b>CANDLE / MARTINGALE BACKTEST</b>\n"
        "<i>candles_ext (Binance gerçek yön) — market-level</i>\n\n"
        "Her candle = bir market (close&gt;open → UP kazandı). 2600+ BTC 5m "
        "market — tick backtest'ten (7 market) çok daha zengin.\n\n"
        "🎲 <b>Martingale</b>: kaybedince 2× katla, kazanınca reset.\n"
        "⚠️ <i>50c'de matematiksel negatif EV — sayılarla göreceksin "
        "(max streak, bust, max bet). Kumar tuzağını ölç.</i>\n\n"
        "Bir kombinasyon seç:"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 BTC 5m — Flat UP (streak analizi)", callback_data="lab_cb:BTC:5m:up:flat")],
            [InlineKeyboardButton("🎲 BTC 5m — Martingale ×6", callback_data="lab_cb:BTC:5m:up:m6")],
            [InlineKeyboardButton("🎲 BTC 5m — Martingale ×4 (güvenli)", callback_data="lab_cb:BTC:5m:up:m4")],
            [InlineKeyboardButton("🎲 BTC 1h — Martingale ×6", callback_data="lab_cb:BTC:1h:up:m6")],
            [InlineKeyboardButton("🎲 BTC 15m — Martingale ×6", callback_data="lab_cb:BTC:15m:up:m6")],
            [InlineKeyboardButton("⚙️ Özel Martingale Kurucu", callback_data="lab_mw:BTC:5m:up:m6:50:0")],
            [InlineKeyboardButton("◀️ Ana Panel", callback_data="lab_main")],
        ]
    )
    return text, kb


# ── ⚙️ Martingale Kurucu (tek panel, cycle butonlar) ────────

# Cycle değer listeleri — buton her basışta bir sonrakine döner
_MW_ASSETS = ["BTC", "ETH", "SOL", "XRP"]
_MW_TFS = ["5m", "15m", "1h"]
_MW_DIRS = ["up", "down", "follow_trend", "fade_trend"]
_MW_MODES = ["flat", "m4", "m6", "m8", "mUL"]
_MW_ENTRIES = [45, 50, 55, 60]  # cents
_MW_STOPS = [0, 5, 7, 9]  # 0 = kapalı


def _cycle(lst: list, cur):
    """Listede cur'dan bir sonraki değere dön (sona gelince başa)."""
    try:
        i = lst.index(cur)
        return lst[(i + 1) % len(lst)]
    except ValueError:
        return lst[0]


_DIR_LABEL = {
    "up": "UP", "down": "DOWN", "follow_trend": "Trend takip", "fade_trend": "Trend ters",
}
_MODE_LABEL = {
    "flat": "Flat (sabit)", "m4": "Martingale ×4", "m6": "Martingale ×6",
    "m8": "Martingale ×8", "mUL": "Martingale ∞",
}


async def _build_mart_config(
    asset: str, tf: str, direction: str, mode: str, entry_cents: int, stop: int
) -> tuple[str, InlineKeyboardMarkup]:
    """⚙️ Martingale kurucu — tek panel, tüm parametreler cycle buton.

    Heddas direktifi: "tek menüden ayarlayalım, neye bastığımda ne değişir
    anlayabileyim." Her buton ilgili parametreyi bir sonraki değere döndürür
    + paneli yeniden çizer (state callback_data'da encoded — state'siz).
    """
    entry = entry_cents / 100.0
    stop_lbl = "kapalı" if stop == 0 else f"{stop} ardışıkta dur"
    mart = mode != "flat"

    text = (
        "⚙️ <b>MARTINGALE KURUCU</b>\n"
        "<i>Her butona bas → değer değişir. Hazırsan ÇALIŞTIR.</i>\n\n"
        "<b>Mevcut ayar:</b>\n"
        f"  🪙 Asset: <b>{esc(asset)}</b>\n"
        f"  ⏱ Zaman dilimi: <b>{esc(tf)}</b>\n"
        f"  🧭 Yön: <b>{esc(_DIR_LABEL.get(direction, direction))}</b>\n"
        f"  🎲 Mod: <b>{esc(_MODE_LABEL.get(mode, mode))}</b>\n"
        f"  💵 Giriş fiyatı: <b>{entry:.2f}</b> "
        "<i>(50c'den giremezsin — gerçek giriş farklı; farklı fiyat = farklı sonuç)</i>\n"
        f"  🛑 Trend-stop: <b>{esc(stop_lbl)}</b>"
    )
    if mart:
        text += (
            "\n\n<i>🎲 Martingale: kaybedince 2× katla, kazanınca 1$'a reset. "
            "Trend-stop: N ardışık aynı yön sonrası bahis durur (uzun trend "
            "martingale'i iflas ettirir — koruma).</i>"
        )

    def _st(a, t, d, m, e, s):
        return f"lab_mw:{a}:{t}:{d}:{m}:{e}:{s}"

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"🪙 {asset} ▸", callback_data=_st(_cycle(_MW_ASSETS, asset), tf, direction, mode, entry_cents, stop)
                ),
                InlineKeyboardButton(
                    f"⏱ {tf} ▸", callback_data=_st(asset, _cycle(_MW_TFS, tf), direction, mode, entry_cents, stop)
                ),
            ],
            [
                InlineKeyboardButton(
                    f"🧭 {_DIR_LABEL.get(direction, direction)} ▸",
                    callback_data=_st(asset, tf, _cycle(_MW_DIRS, direction), mode, entry_cents, stop),
                ),
                InlineKeyboardButton(
                    f"🎲 {_MODE_LABEL.get(mode, mode)} ▸",
                    callback_data=_st(asset, tf, direction, _cycle(_MW_MODES, mode), entry_cents, stop),
                ),
            ],
            [
                InlineKeyboardButton(
                    f"💵 {entry:.2f} ▸", callback_data=_st(asset, tf, direction, mode, _cycle(_MW_ENTRIES, entry_cents), stop)
                ),
                InlineKeyboardButton(
                    f"🛑 {stop_lbl} ▸", callback_data=_st(asset, tf, direction, mode, entry_cents, _cycle(_MW_STOPS, stop))
                ),
            ],
            [InlineKeyboardButton("🚀 ÇALIŞTIR", callback_data=f"lab_mwrun:{asset}:{tf}:{direction}:{mode}:{entry_cents}:{stop}")],
            [InlineKeyboardButton("◀️ Candle menü", callback_data="lab_candle")],
        ]
    )
    return text, kb


async def _run_mart_config(
    asset: str, tf: str, direction: str, mode: str, entry_cents: int, stop: int, db
) -> tuple[str, InlineKeyboardMarkup]:
    """⚙️ Kurucu ayarlarıyla candle backtest çalıştır → zengin sonuç.

    `lab_mwrun:<asset>:<tf>:<dir>:<mode>:<entry>:<stop>` callback'inden.
    Sonuç ekranında "◀️ Ayarlara dön" ile aynı state'e geri dönülür
    (parametre ince-ayar döngüsü).
    """
    back_kb = _panel_nav_kb(
        extra_rows=[
            [InlineKeyboardButton("◀️ Ayarlara dön", callback_data=f"lab_mw:{asset}:{tf}:{direction}:{mode}:{entry_cents}:{stop}")],
            [InlineKeyboardButton("🎲 Candle menü", callback_data="lab_candle")],
        ]
    )
    if asset not in _MW_ASSETS or tf not in _MW_TFS or db is None or getattr(db, "conn", None) is None:
        return "⚠️ Geçersiz parametre veya DB yok.", back_kb

    martingale = mode != "flat"
    max_levels = {"m4": 4, "m6": 6, "m8": 8, "mUL": 0}.get(mode, 6)
    entry = entry_cents / 100.0

    try:
        from backtest.candle_runner import CandleBacktestRunner, CandleRunConfig

        cfg = CandleRunConfig(
            asset=asset, timeframe=tf, bet_direction=direction,
            entry_price=entry, martingale=martingale, max_levels=max_levels,
            stop_after_streak=stop, last_n=500,
        )
        s = await CandleBacktestRunner(db).run(cfg)
    except Exception as e:  # noqa: BLE001
        logger.exception("_run_mart_config failed: %s", e)
        return f"⚠️ Hata: <i>{esc(type(e).__name__)}</i>", back_kb

    if s.n_markets == 0:
        return f"<i>{esc(s.note)}</i>", back_kb

    pnl_icon = "🟢" if s.total_pnl > 0 else "🔴" if s.total_pnl < 0 else "⚪"
    mode_lbl = _MODE_LABEL.get(mode, mode)
    stop_lbl = "kapalı" if stop == 0 else f"{stop} ardışık"

    mart_block = ""
    if martingale:
        mart_block = (
            "\n🎲 <b>Martingale</b>\n"
            f"  Max bet: <b>${s.max_bet:.0f}</b> · en derin: {s.max_level_reached} · "
            f"bust: {s.busts}×\n"
        )
        if s.busts > 0:
            mart_block += f"  ⚠️ <i>{s.busts} bust = gerçekte {s.busts} sermaye iflası</i>\n"

    text = (
        "🚀 <b>Martingale Backtest Sonucu</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ {esc(asset)} {esc(tf)} · {esc(_DIR_LABEL.get(direction, direction))} · "
        f"{esc(mode_lbl)} · giriş {entry:.2f} · stop {esc(stop_lbl)}\n"
        f"📦 {s.n_markets} market · UP %{s.up_pct:.1f}\n\n"
        "📊 <b>Sonuç</b>\n"
        f"  {pnl_icon} PnL: <b>${s.total_pnl:+.2f}</b>\n"
        f"  🎯 WR: {s.win_rate:.1f}% · {s.n_trades} trade ({s.wins}W/{s.losses}L)\n"
        f"{mart_block}\n"
        f"🔢 Üstüste aynı yön: max <b>{s.max_streak}</b>\n\n"
        "<i>Parametreyi değiştirip tekrar dene → 'Ayarlara dön'.</i>"
    )
    return text, back_kb


async def _run_candle_backtest(
    asset: str, tf: str, direction: str, mode: str, db
) -> tuple[str, InlineKeyboardMarkup]:
    """`lab_cb:<asset>:<tf>:<dir>:<mode>` — candle backtest çalıştır.

    mode: flat | m4 | m6 | m8 | mUL (martingale tavan katlama).
    """
    back_kb = _panel_nav_kb(
        extra_rows=[[InlineKeyboardButton("◀️ Candle menü", callback_data="lab_candle")]]
    )
    if asset not in ("BTC", "ETH", "SOL", "XRP") or tf not in ("5m", "15m", "1h"):
        return "⚠️ Geçersiz parametre.", back_kb
    if db is None or getattr(db, "conn", None) is None:
        return "⚠️ DB bağlantısı yok.", back_kb

    martingale = mode != "flat"
    max_levels = {"m4": 4, "m6": 6, "m8": 8, "mUL": 0}.get(mode, 6)

    try:
        from backtest.candle_runner import CandleBacktestRunner, CandleRunConfig

        cfg = CandleRunConfig(
            asset=asset, timeframe=tf, bet_direction=direction,
            martingale=martingale, max_levels=max_levels, last_n=500,
        )
        s = await CandleBacktestRunner(db).run(cfg)
    except Exception as e:  # noqa: BLE001
        logger.exception("_run_candle_backtest failed: %s", e)
        return f"⚠️ Hata: <i>{esc(type(e).__name__)}</i>", back_kb

    if s.n_markets == 0:
        return f"<i>{esc(s.note)}</i>", back_kb

    pnl_icon = "🟢" if s.total_pnl > 0 else "🔴" if s.total_pnl < 0 else "⚪"

    # Streak dağılımı (Heddas: "üstüste kaç market")
    streak_lines = []
    for k in sorted(s.streak_dist):
        streak_lines.append(f"  {k} ardışık: {s.streak_dist[k]}×")
    streak_block = "\n".join(streak_lines[-8:]) if streak_lines else "  <i>yok</i>"

    mode_lbl = "Flat (sabit bahis)" if not martingale else (
        f"Martingale ×{max_levels}" if max_levels > 0 else "Martingale ∞"
    )

    mart_block = ""
    if martingale:
        mart_block = (
            "\n🎲 <b>Martingale</b>\n"
            f"  Max bet ulaşılan: <b>${s.max_bet:.0f}</b>\n"
            f"  En derin katlama: {s.max_level_reached}\n"
            f"  Bust (tavana çarpma): {s.busts}×\n"
        )
        if s.busts > 0:
            mart_block += (
                f"  ⚠️ <i>{s.busts} kez tavana çarptı = {s.busts} büyük kayıp. "
                "Gerçekte bu sermaye iflası demek.</i>\n"
            )

    text = (
        f"🎲 <b>Candle Backtest</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 {esc(asset)} {esc(tf)} · yön {esc(direction.upper())} · {esc(mode_lbl)}\n"
        f"📦 {s.n_markets} market (son {cfg.last_n}) · UP %{s.up_pct:.1f}\n\n"
        "📊 <b>Sonuç</b>\n"
        f"  {pnl_icon} PnL: <b>${s.total_pnl:+.2f}</b>\n"
        f"  🎯 WR: {s.win_rate:.1f}% · {s.n_trades} trade ({s.wins}W/{s.losses}L)\n"
        f"{mart_block}\n"
        f"🔢 <b>Üstüste aynı yön (max {s.max_streak})</b>\n"
        f"{streak_block}\n\n"
        "<i>candles_ext Binance gerçek yön — Polymarket up/down Chainlink/"
        "Binance fiyatına settle olur.</i>"
    )
    return text, back_kb


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
    "lab_candle": _build_candle_menu,  # 2026-05-21 Heddas: candle/martingale
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

    Faz 4: Parametreli callback'ler — `lab_show:<name>`, `lab_del_ask:<name>`,
    `lab_del_confirm:<name>`, `lab_help_save`. İsim regex-validated (path
    traversal koruması).
    """
    q = update.callback_query
    if q is None:
        return
    try:
        await q.answer()
    except TelegramError:
        pass

    data = q.data or ""
    db = context.bot_data.get("db")

    # 1) Parametresiz panel callback'leri (lab_main, lab_quick, ...)
    builder = _BUILDERS.get(data)
    if builder is not None:
        try:
            text, kb = await builder(db)
            await _safe_edit(q, text, kb)
        except Exception as e:  # noqa: BLE001
            logger.exception("backtest_lab_callback build failed: %s", e)
            await _safe_edit(
                q, "⚠️ Panel üretilemedi — log'da detay var.", _main_kb()
            )
        return

    # 2) Faz 4 — yardım panelleri + Faz 4b/5b preset sihirbazı (parametresiz)
    _PARAMETERLESS_BUILDERS = {
        "lab_help_save": _build_help_save,
        "lab_pw": _build_wiz_menu,
        "lab_pw_sec": _build_wiz_sec,
        "lab_pw_price": _build_wiz_price,
        "lab_pw_hour": _build_wiz_hour,
        "lab_pw_limit": _build_wiz_limit,  # Faz 5b
    }
    pl_builder = _PARAMETERLESS_BUILDERS.get(data)
    if pl_builder is not None:
        try:
            text, kb = await pl_builder()
            await _safe_edit(q, text, kb)
        except Exception as e:  # noqa: BLE001
            logger.exception("backtest_lab_callback parametersiz %s failed: %s", data, e)
            await _safe_edit(q, "⚠️ Panel açılamadı.", _main_kb())
        return

    # 3) Faz 4/4b — parametreli callback'ler (`prefix:arg` formatı)
    if ":" not in data:
        logger.warning("backtest_lab_callback: bilinmeyen data=%r", data)
        return
    action, _, arg = data.partition(":")
    try:
        if action == "lab_bt":
            # lab_bt:<name>:<asset>:<tf> — inline backtest (db gerek)
            parts = arg.split(":")
            if len(parts) != 3:
                text, kb = "⚠️ Geçersiz backtest parametresi.", _main_kb()
            else:
                bt_name, bt_asset, bt_tf = parts
                # "Hesaplanıyor" ara-mesajı (uzun sürebilir)
                try:
                    await q.edit_message_text(
                        f"⏳ <b>{esc(bt_asset)} {esc(bt_tf)}</b> backtest "
                        f"çalışıyor...\n<i>Strateji: {esc(bt_name)}</i>",
                        parse_mode="HTML",
                    )
                except (BadRequest, TelegramError):
                    pass
                text, kb = await _run_inline_backtest(bt_name, bt_asset, bt_tf, db)
        elif action == "lab_cb":
            # lab_cb:<asset>:<tf>:<dir>:<mode> — candle/martingale backtest
            parts = arg.split(":")
            if len(parts) != 4:
                text, kb = "⚠️ Geçersiz candle parametresi.", _main_kb()
            else:
                cb_asset, cb_tf, cb_dir, cb_mode = parts
                try:
                    await q.edit_message_text(
                        f"⏳ <b>{esc(cb_asset)} {esc(cb_tf)}</b> candle backtest "
                        "çalışıyor...",
                        parse_mode="HTML",
                    )
                except (BadRequest, TelegramError):
                    pass
                text, kb = await _run_candle_backtest(cb_asset, cb_tf, cb_dir, cb_mode, db)
        elif action == "lab_mw":
            # lab_mw:<asset>:<tf>:<dir>:<mode>:<entry>:<stop> — kurucu cycle panel
            parts = arg.split(":")
            if len(parts) != 6:
                text, kb = "⚠️ Geçersiz kurucu parametresi.", _main_kb()
            else:
                a, t, d, m, e, s = parts
                text, kb = await _build_mart_config(a, t, d, m, int(e), int(s))
        elif action == "lab_mwrun":
            # lab_mwrun:<asset>:<tf>:<dir>:<mode>:<entry>:<stop> — kurucu çalıştır
            parts = arg.split(":")
            if len(parts) != 6:
                text, kb = "⚠️ Geçersiz kurucu parametresi.", _main_kb()
            else:
                a, t, d, m, e, s = parts
                try:
                    await q.edit_message_text(
                        f"⏳ <b>{esc(a)} {esc(t)}</b> martingale backtest çalışıyor...",
                        parse_mode="HTML",
                    )
                except (BadRequest, TelegramError):
                    pass
                text, kb = await _run_mart_config(a, t, d, m, int(e), int(s), db)
        elif action == "lab_show":
            text, kb = await _build_show_ruleset(arg)
        elif action == "lab_del_ask":
            text, kb = await _build_del_confirm(arg)
        elif action == "lab_del_confirm":
            text, kb = await _build_del_done(arg)
        elif action == "lab_pw_sec_save":
            text, kb = await _build_wiz_sec_save(arg)
        elif action == "lab_pw_price_dir":
            text, kb = await _build_wiz_price_dir(arg)
        elif action == "lab_pw_price_save":
            text, kb = await _build_wiz_price_save(arg)
        elif action == "lab_pw_hour_pick":
            text, kb = await _build_wiz_hour_pick(arg)
        elif action == "lab_pw_hour_save":
            text, kb = await _build_wiz_hour_save(arg)
        # Faz 5b — Limit Al preset (3 adim akisi)
        elif action == "lab_pw_limit_dir":
            text, kb = await _build_wiz_limit_dir(arg)
        elif action == "lab_pw_limit_price":
            text, kb = await _build_wiz_limit_price(arg)
        elif action == "lab_pw_limit_save":
            text, kb = await _build_wiz_limit_save(arg)
        else:
            logger.warning("backtest_lab_callback: bilinmeyen action=%r", action)
            return
        await _safe_edit(q, text, kb)
    except Exception as e:  # noqa: BLE001
        logger.exception("backtest_lab_callback action %s failed: %s", action, e)
        await _safe_edit(q, "⚠️ İşlem yapılamadı — log'da detay var.", _main_kb())


# ── /lab_save komutu ────────────────────────────────────────


async def lab_save_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/lab_save — JSON ruleset paste flow (Faz 4).

    Kullanım:
        /lab_save
        {
          "name": "my_strategy",
          "direction": "up",
          "entry": {
            "conditions": [
              {"field": "elapsed_seconds", "op": ">=", "value": 30}
            ]
          }
        }

    Komutu izleyen TÜM metin (newline'larla birlikte) JSON olarak parse
    edilir, doğrulanır, başarılıysa `data_store/bt_strategies/{name}.json`
    olarak kaydedilir. Geçersizse ham hata mesajı (log'da detay).
    """
    import json as _json

    msg = update.message
    if msg is None:
        return

    # Komutu çıkar — sadece ilk satırın ilk kelimesi `/lab_save`
    raw = msg.text or ""
    # `/lab_save` ya da `/lab_save@botname` — ikisini de yakala
    parts = raw.split("\n", 1)
    if not parts:
        await msg.reply_text(
            "📥 <code>/lab_save</code> komutunu kullanmak için /lab → "
            "Strateji Kurucu → 📥 yardım'a bak.",
            parse_mode="HTML",
        )
        return
    first = parts[0].strip()
    payload = parts[1] if len(parts) > 1 else ""

    # Aynı satırda inline JSON da olabilir: `/lab_save {...}`
    if not payload:
        # `/lab_save` veya `/lab_save@bot` sonrasını al
        after = first.split(maxsplit=1)
        if len(after) >= 2:
            payload = after[1]

    payload = (payload or "").strip()
    if not payload:
        await msg.reply_text(
            "📥 <b>JSON eksik.</b>\n\n"
            "<code>/lab_save</code> komutunu izleyen satırlara geçerli "
            "ruleset JSON yapıştır. Örnek için /lab → 🛠 Strateji Kurucu "
            "→ 📥 /lab_save yardım.",
            parse_mode="HTML",
        )
        return

    try:
        parsed = _json.loads(payload)
    except _json.JSONDecodeError as e:
        # noqa: T11.6-OK reason=admin-diagnostic — kullanici JSON'i kendi
        # yazdi/yapistirdi, parse hatasini gormesi gerek (line/column info
        # debugging icin sart). admin-only context, info disclosure yok.
        await msg.reply_text(
            f"❌ <b>JSON parse hatası</b>: {esc(str(e))}\n\n"  # noqa: T11.6-OK
            "<i>İpucu: braket/virgül eksiklerini kontrol et.</i>",
            parse_mode="HTML",
        )
        return

    try:
        from backtest.strategies.rule_based import save_ruleset

        target = save_ruleset(parsed)
    except Exception as e:  # noqa: BLE001
        try:
            from backtest.strategies.rule_based import RuleSetError as _RSE

            if isinstance(e, _RSE):
                # noqa: T11.6-OK reason=admin-diagnostic — ruleset validation
                # mesaji "entry_limit_price 0..1 araliginda olmali" gibi tarif
                # — kullanicinin schema'sini duzeltmesi icin sart.
                await msg.reply_text(
                    f"❌ <b>Ruleset geçersiz</b>: {esc(str(e))}",  # noqa: T11.6-OK
                    parse_mode="HTML",
                )
                return
        except ImportError:
            pass
        logger.exception("lab_save_command save failed: %s", e)
        await msg.reply_text(
            "⚠️ Ruleset kaydedilemedi — log'da detay var.",
            parse_mode="HTML",
        )
        return

    name = parsed.get("name", "?")
    await msg.reply_text(
        f"✅ <b>Kaydedildi</b>: <code>{esc(name)}</code>\n"
        f"📁 <code>{esc(str(target))}</code>\n\n"
        f"Backtest çalıştır: <code>/backtest_replay rule_based BTC 5m</code>\n"
        f"Listele: <code>/lab</code> → 🛠 Strateji Kurucu",
        parse_mode="HTML",
    )
