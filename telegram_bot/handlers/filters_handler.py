"""
PolyPaper Bot — /filters Handler (Phase 66)
Inline-button toggle UI for all trade-blocking filters.
Each toggle updates os.environ at runtime (no restart needed).
Persistent: writes changes to bot_settings DB table.
"""

import logging
import os

import aiosqlite
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from db.database import Database
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.filters")

# ─── Filter Registry ────────────────────────────────────────────
# Each entry: (key, label, env_var, type, default, description)
# type: "bool" → on/off toggle, "float" → value cycle, "int" → value cycle
# For value-cycle types, "options" lists the cycle values.
FILTER_REGISTRY = [
    # ── Boolean toggles (ON/OFF) ──
    {
        "key": "parity_gate",
        "label": "🔗 Oracle Parity",
        "env": "PARITY_GATE_ENABLED",
        "type": "bool",
        "default": "true",
        "desc": "Chainlink↔Poly fiyat uyumu kontrolü",
        "risk": "low",
    },
    {
        "key": "slippage_gate",
        "label": "📉 Slippage Gate",
        "env": "SLIPPAGE_GATE_ENABLED",
        "type": "bool",
        "default": "false",
        "desc": "Giriş fiyatı kayma kontrolü",
        "risk": "low",
    },
    {
        "key": "conviction",
        "label": "💪 Conviction Filter",
        "env": "CONVICTION_ENABLED",
        "type": "bool",
        "default": "true",
        "desc": "Minimum ikna skoru filtresi",
        "risk": "medium",
    },
    # Becker Calibration filter dropped 2026-05-20 (cleanup):
    # core.becker_calibration silindi (2026-04-29), BECKER_CALIB_ENABLED
    # env'i .env.example'da "kaldırıldı" notuyla işaretli, engine_signals.py
    # ona bakan dead code da temizlendi → UI toggle'ı zombie idi (hiçbir
    # davranis degisikligi tetiklemiyordu).
    {
        "key": "optimism_tax",
        "label": "🏷 Optimism Tax",
        "env": "OPTIMISM_TAX_ENABLED",
        "type": "bool",
        "default": "true",
        "desc": "NO-side maker ekstra tick maliyeti",
        "risk": "low",
    },
    {
        "key": "maker_rebate",
        "label": "💰 Maker Rebate",
        "env": "MAKER_REBATE_ENABLED",
        "type": "bool",
        "default": "true",
        "desc": "Settle'da maker iade hesabı",
        "risk": "low",
    },
    {
        "key": "self_trade",
        "label": "🔄 Self-Trade Prevention",
        "env": "SELF_TRADE_PREVENTION",
        "type": "bool",
        "default": "true",
        "desc": "Aynı token'da karşıt emir engeli",
        "risk": "medium",
    },
    {
        "key": "smart_exit",
        "label": "🧠 Smart Exit",
        "env": "SMART_EXIT_ENABLED",
        "type": "bool",
        "default": "true",
        "desc": "Olasılık-bazlı otomatik çıkış (edge + stoploss)",
        "risk": "high",
    },
    # ── Value-cycle filters ──
    {
        "key": "thompson_pct",
        "label": "🎰 Thompson Top %",
        "env": "THOMPSON_TOP_PCT",
        "type": "cycle",
        "options": ["0.30", "0.40", "0.50", "0.60", "0.80", "1.00"],
        "default": "0.40",
        "desc": "Strateji seçici: üst yüzde dilimi",
        "risk": "medium",
    },
    {
        "key": "edge_zone",
        "label": "🎯 Edge Zone 50-65c",
        "env": "EDGE_ZONE_5065_MIN",
        "type": "cycle",
        "options": ["0.15", "0.20", "0.25", "0.30", "0.35", "0.45"],
        "default": "0.30",
        "desc": "50-65¢ bölgesi minimum sinyal eşiği",
        "risk": "medium",
    },
    {
        "key": "conviction_min",
        "label": "📏 Conviction Min",
        "env": "CONVICTION_MIN",
        "type": "cycle",
        "options": ["0.05", "0.10", "0.15", "0.20", "0.30"],
        "default": "0.15",
        "desc": "Minimum ikna skoru değeri",
        "risk": "medium",
    },
    {
        "key": "canary_mult",
        "label": "🕯 Canary Size Mult",
        "env": "CANARY_SIZE_MULT",
        "type": "cycle",
        "options": ["0.25", "0.50", "0.75", "1.00"],
        "default": "0.25",
        "desc": "Canary stratejiler boyut çarpanı",
        "risk": "low",
    },
    {
        "key": "parity_bps",
        "label": "📐 Parity BPS",
        "env": "CHAINLINK_PARITY_BPS",
        "type": "cycle",
        "options": ["20", "40", "100", "200", "500"],
        "default": "200",
        "desc": "Oracle fark eşiği (basis points)",
        "risk": "medium",
    },
    {
        "key": "fee_tail_low",
        "label": "⬇️ Fee Tail Low",
        "env": "FEE_TAIL_LOW",
        "type": "cycle",
        "options": ["0.03", "0.05", "0.08", "0.10"],
        "default": "0.08",
        "desc": "Düşük fiyat fee bölgesi alt sınırı",
        "risk": "low",
    },
    {
        "key": "fee_tail_high",
        "label": "⬆️ Fee Tail High",
        "env": "FEE_TAIL_HIGH",
        "type": "cycle",
        "options": ["0.90", "0.93", "0.95", "0.97"],
        "default": "0.93",
        "desc": "Yüksek fiyat fee bölgesi üst sınırı",
        "risk": "low",
    },
    {
        "key": "remaining_edge",
        "label": "🎚 Remaining Edge Min",
        "env": "REMAINING_EDGE_MIN",
        "type": "cycle",
        "options": ["0.02", "0.05", "0.08", "0.10", "0.15"],
        "default": "0.05",
        "desc": "Smart-exit edge tükenme eşiği (δ<bu→çıkış)",
        "risk": "high",
    },
    {
        "key": "smart_grace",
        "label": "⏱ Smart Exit Grace",
        "env": "SMART_EXIT_GRACE_SEC",
        "type": "cycle",
        "options": ["0", "30", "60", "90", "120"],
        "default": "60",
        "desc": "Fill sonrası smart-exit bekleme süresi (sn)",
        "risk": "medium",
    },
]

# Risk-level emojis
RISK_EMOJI = {"low": "🟢", "medium": "🟡", "high": "🔴"}


def _get_current_value(f: dict) -> str:
    """Get current runtime value for a filter (env > db > default)."""
    return os.environ.get(f["env"], f["default"])


def _is_on(f: dict) -> bool:
    """Check if a boolean filter is currently ON."""
    return _get_current_value(f).lower() in ("true", "1", "yes")


def _build_keyboard(page: int = 0) -> InlineKeyboardMarkup:
    """Build inline keyboard with toggle/cycle buttons.
    8 filters per page."""
    PAGE_SIZE = 8
    filters_list = FILTER_REGISTRY
    total_pages = (len(filters_list) + PAGE_SIZE - 1) // PAGE_SIZE
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(filters_list))
    page_filters = filters_list[start:end]

    buttons = []
    for f in page_filters:
        risk_icon = RISK_EMOJI.get(f["risk"], "⚪")
        if f["type"] == "bool":
            is_on = _is_on(f)
            status = "✅ ON" if is_on else "❌ OFF"
            btn_text = f"{risk_icon} {f['label']}: {status}"
            callback = f"flt:toggle:{f['key']}"
        else:
            val = _get_current_value(f)
            btn_text = f"{risk_icon} {f['label']}: {val}"
            callback = f"flt:cycle:{f['key']}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=callback)])

    # Navigation row
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Önceki", callback_data=f"flt:page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Sonraki ▶️", callback_data=f"flt:page:{page + 1}"))
    if nav:
        buttons.append(nav)

    # Quick presets row
    buttons.append(
        [
            InlineKeyboardButton("🟢 Agresif", callback_data="flt:preset:aggressive"),
            InlineKeyboardButton("🟡 Normal", callback_data="flt:preset:normal"),
            InlineKeyboardButton("🔴 Güvenli", callback_data="flt:preset:safe"),
        ]
    )

    # Page indicator
    buttons.append(
        [InlineKeyboardButton(f"📄 {page + 1}/{total_pages} | /filters", callback_data="flt:noop")]
    )

    return InlineKeyboardMarkup(buttons)


def _build_text(page: int = 0) -> str:
    """Build filter status text."""
    lines = [
        "⚙️ <b>Trade Filtre Paneli</b>",
        "",
        "Filtreler runtime'da değişir (restart gerekmez).",
        "🟢 Düşük risk | 🟡 Orta risk | 🔴 Yüksek risk",
        "",
    ]

    PAGE_SIZE = 8
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(FILTER_REGISTRY))

    for f in FILTER_REGISTRY[start:end]:
        risk_icon = RISK_EMOJI.get(f["risk"], "⚪")
        if f["type"] == "bool":
            status = "✅" if _is_on(f) else "❌"
        else:
            status = f"<code>{_get_current_value(f)}</code>"
        lines.append(f"{risk_icon} <b>{esc(f['label'])}</b>: {status}")
        lines.append(f"   <i>{esc(f['desc'])}</i>")

    return "\n".join(lines)


# ─── Preset Configurations ──────────────────────────────────────
PRESETS = {
    "aggressive": {
        "PARITY_GATE_ENABLED": "false",
        "SLIPPAGE_GATE_ENABLED": "false",
        "CONVICTION_ENABLED": "false",
        "OPTIMISM_TAX_ENABLED": "false",
        "SMART_EXIT_ENABLED": "false",
        "THOMPSON_TOP_PCT": "1.00",
        "EDGE_ZONE_5065_MIN": "0.15",
        "CONVICTION_MIN": "0.05",
        "CANARY_SIZE_MULT": "1.00",
        "CHAINLINK_PARITY_BPS": "500",
        "FEE_TAIL_LOW": "0.03",
        "FEE_TAIL_HIGH": "0.97",
        "REMAINING_EDGE_MIN": "0.02",
        "SMART_EXIT_GRACE_SEC": "120",
    },
    "normal": {
        "PARITY_GATE_ENABLED": "true",
        "SLIPPAGE_GATE_ENABLED": "false",
        "CONVICTION_ENABLED": "true",
        "OPTIMISM_TAX_ENABLED": "true",
        "SMART_EXIT_ENABLED": "true",
        "THOMPSON_TOP_PCT": "0.40",
        "EDGE_ZONE_5065_MIN": "0.30",
        "CONVICTION_MIN": "0.15",
        "CANARY_SIZE_MULT": "0.25",
        "CHAINLINK_PARITY_BPS": "200",
        "FEE_TAIL_LOW": "0.08",
        "FEE_TAIL_HIGH": "0.93",
        "REMAINING_EDGE_MIN": "0.05",
        "SMART_EXIT_GRACE_SEC": "60",
    },
    "safe": {
        "PARITY_GATE_ENABLED": "true",
        "SLIPPAGE_GATE_ENABLED": "true",
        "CONVICTION_ENABLED": "true",
        "OPTIMISM_TAX_ENABLED": "true",
        "SMART_EXIT_ENABLED": "true",
        "THOMPSON_TOP_PCT": "0.30",
        "EDGE_ZONE_5065_MIN": "0.45",
        "CONVICTION_MIN": "0.30",
        "CANARY_SIZE_MULT": "0.25",
        "CHAINLINK_PARITY_BPS": "40",
        "FEE_TAIL_LOW": "0.10",
        "FEE_TAIL_HIGH": "0.90",
        "REMAINING_EDGE_MIN": "0.10",
        "SMART_EXIT_GRACE_SEC": "90",
    },
}


async def _persist_filter(db: Database, env_key: str, value: str):
    """Save filter value to bot_settings DB (survives restart)."""
    try:
        await db.conn.execute(
            """INSERT INTO bot_settings(key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (f"filter.{env_key}", value),
        )
        await db.conn.commit()
    except aiosqlite.Error as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. INSERT ON CONFLICT
        # surface aiosqlite.Error only. Persistence failure is non-fatal —
        # os.environ already updated for current session.
        logger.warning(f"persist filter {env_key}: " f"{type(e).__name__}: {e}")


async def _load_persisted_filters(db: Database):
    """Load all persisted filter overrides from DB into os.environ on startup."""
    try:
        async with db.conn.execute(
            "SELECT key, value FROM bot_settings WHERE key LIKE 'filter.%'"
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            env_key = row["key"].replace("filter.", "", 1)
            os.environ[env_key] = row["value"]
            logger.info(f"  filter restored: {env_key}={row['value']}")
        if rows:
            logger.info(f"Loaded {len(rows)} persisted filter overrides")
    except (aiosqlite.Error, KeyError, TypeError) as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. SELECT + row[key]
        # access. Failure to load persisted filters means defaults stay.
        logger.warning(f"load persisted filters: " f"{type(e).__name__}: {e}")


# ─── Telegram Handlers ──────────────────────────────────────────


async def filters_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /filters command — show filter toggle panel."""
    context.bot_data["db"]
    admin_id = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_CHAT_ID")
    if admin_id and str(update.effective_user.id) != str(admin_id):
        await update.message.reply_text("⛔ Bu komut sadece admin için.")
        return

    text = _build_text(0)
    kb = _build_keyboard(0)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def filters_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all flt:* callback queries.

    Epic 10 T10.2 (C1): admin gate — callback mirrors filters_command's
    admin check (L368-371). Without this, any Telegram user who reaches
    a callback_data="flt:*" payload can mutate runtime filter state
    (os.environ + bot_settings DB).
    """
    query = update.callback_query
    admin_id = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_CHAT_ID")
    if admin_id and str(update.effective_user.id) != str(admin_id):
        await query.answer("⛔ Admin only", show_alert=True)
        return
    await query.answer()
    data = query.data  # e.g. "flt:toggle:parity_gate"

    if data == "flt:noop":
        return

    db: Database = context.bot_data["db"]
    parts = data.split(":")

    if len(parts) < 3:
        return

    action = parts[1]
    target = parts[2]

    if action == "page":
        page = int(target)
        text = _build_text(page)
        kb = _build_keyboard(page)
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            # T11.8-B (2026-04-24): narrow from bare Exception. edit_message
            # BadRequest "not modified" — toggle UI tolerates no-op edits.
            pass
        return

    if action == "preset":
        preset = PRESETS.get(target)
        if not preset:
            return
        for env_key, value in preset.items():
            os.environ[env_key] = value
            await _persist_filter(db, env_key, value)
        preset_names = {"aggressive": "🟢 Agresif", "normal": "🟡 Normal", "safe": "🔴 Güvenli"}
        logger.warning(f"FILTERS: preset applied → {target}")
        text = _build_text(0)
        text += f"\n\n✅ <b>{preset_names.get(target, target)}</b> preset uygulandı!"
        kb = _build_keyboard(0)
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (TimeoutError, BadRequest, TelegramError):
            # T11.8-B (2026-04-24): narrow from bare Exception. edit_message
            # BadRequest "not modified" — toggle UI tolerates no-op edits.
            pass
        return

    if action == "toggle":
        # Find filter by key
        filt = next((f for f in FILTER_REGISTRY if f["key"] == target), None)
        if not filt or filt["type"] != "bool":
            return
        current = _is_on(filt)
        new_val = "false" if current else "true"
        os.environ[filt["env"]] = new_val
        await _persist_filter(db, filt["env"], new_val)
        status = "ON" if not current else "OFF"
        logger.warning(f"FILTERS: {filt['env']} → {new_val} ({status})")

    elif action == "cycle":
        filt = next((f for f in FILTER_REGISTRY if f["key"] == target), None)
        if not filt or filt["type"] != "cycle":
            return
        options = filt["options"]
        current = _get_current_value(filt)
        try:
            idx = options.index(current)
            new_idx = (idx + 1) % len(options)
        except ValueError:
            new_idx = 0
        new_val = options[new_idx]
        os.environ[filt["env"]] = new_val
        await _persist_filter(db, filt["env"], new_val)
        logger.warning(f"FILTERS: {filt['env']} → {new_val}")

    # Determine which page this filter is on
    filt_idx = next((i for i, f in enumerate(FILTER_REGISTRY) if f["key"] == target), 0)
    page = filt_idx // 8

    text = _build_text(page)
    kb = _build_keyboard(page)
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (TimeoutError, BadRequest, TelegramError):
        # T11.8-B (2026-04-24): narrow from bare Exception. Same edit_message
        # no-op-tolerant pattern as above.
        pass
