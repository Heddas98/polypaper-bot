"""Polymarket Portfolio handler — gerçek cüzdan view (Aşama 1).

Telegram komutları:
  /portfolio  (alias: /pf)  — Polymarket Proxy cüzdan ana paneli

Inline button tabs:
  💰 Bakiye        — pUSD balance + Exchange allowance + admin uyarı
  📊 Pozisyonlar   — Açık her market'te shares + cost basis + PnL
  📜 Trade History — Son 20 settled trade
  💼 Özet (NAV)    — Toplam portfolio değer + diagnostic + refresh

Veri kaynağı: ``polymarket_portfolio_cache`` table (60s job tarafından
güncellenir). Stale cache (> 5 dk) durumunda inline "🔄 Refresh" butonu
zorla fresh fetch tetikler.

Admin-only komut. ``settings.is_admin(telegram_id)`` ile gate.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.portfolio")

STALE_THRESHOLD_SEC = 300  # 5 dk üstü cache "stale"


def _is_admin(context, telegram_id: int) -> bool:
    settings = context.bot_data.get("settings")
    if not settings:
        return False
    return settings.is_admin(telegram_id)


async def _read_cache(db) -> tuple[Optional[dict], Optional[str]]:
    """Read latest snapshot from DB cache. Returns (snapshot_dict, fetched_at_iso)."""
    try:
        async with db.conn.execute(
            "SELECT snapshot_json, fetched_at FROM polymarket_portfolio_cache "
            "WHERE id=1"
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None, None
        snap = json.loads(row[0])
        return snap, row[1]
    except (aiosqlite.Error, json.JSONDecodeError, TypeError) as e:
        logger.debug(f"portfolio cache read: {e}")
        return None, None


def _is_stale(fetched_at_iso: Optional[str]) -> bool:
    if not fetched_at_iso:
        return True
    try:
        fetched = datetime.fromisoformat(fetched_at_iso.replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - fetched).total_seconds()
        return delta > STALE_THRESHOLD_SEC
    except (ValueError, TypeError):
        return True


def _age_human(fetched_at_iso: Optional[str]) -> str:
    if not fetched_at_iso:
        return "n/a"
    try:
        fetched = datetime.fromisoformat(fetched_at_iso.replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        delta = int((datetime.now(timezone.utc) - fetched).total_seconds())
        if delta < 60:
            return f"{delta}s önce"
        if delta < 3600:
            return f"{delta // 60}dk önce"
        return f"{delta // 3600}h önce"
    except (ValueError, TypeError):
        return "n/a"


def _build_keyboard(active_tab: str = "summary") -> InlineKeyboardMarkup:
    """4-tab navigation + refresh button."""
    def _btn(label: str, tab: str) -> InlineKeyboardButton:
        prefix = "▸ " if tab == active_tab else "  "
        return InlineKeyboardButton(prefix + label, callback_data=f"pf_tab_{tab}")

    return InlineKeyboardMarkup([
        [_btn("💼 Özet", "summary"), _btn("💰 Bakiye", "balance")],
        [_btn("📊 Pozisyonlar", "positions"), _btn("📜 Trades", "trades")],
        [InlineKeyboardButton("🔄 Yenile", callback_data="pf_refresh")],
    ])


def _render_summary(snap: dict, fetched_at: str) -> str:
    """Compact overview tab — main metrics + diagnostics."""
    user = snap.get("user_address", "")
    bal = float(snap.get("pusd_balance", 0))
    nav = float(snap.get("portfolio_value_usd", 0))
    pos_n = int(snap.get("positions_count", 0))
    errors = snap.get("fetch_errors", []) or []
    latency = int(snap.get("fetch_latency_ms", 0))
    age = _age_human(fetched_at)

    lines = [
        "<b>💼 Polymarket Cüzdan</b>",
        "",
        f"<b>Adres:</b> <code>{esc(user[:8] + '...' + user[-6:] if len(user) > 14 else user)}</code>",
        f"<b>pUSD Bakiye:</b> ${bal:,.2f}",
        f"<b>Açık Pozisyon NAV:</b> ${nav:,.2f}",
        f"<b>Toplam Değer:</b> ${bal + nav:,.2f}",
        f"<b>Pozisyon Sayısı:</b> {pos_n}",
        "",
        f"<i>Veri yaşı:</i> {esc(age)} (latency {latency}ms)",
    ]
    if errors:
        lines.append("")
        lines.append(f"⚠ <i>{len(errors)} fetch hatası</i> — detay: <code>{esc(str(errors[0])[:80])}</code>")
    return "\n".join(lines)


def _render_balance(snap: dict, fetched_at: str) -> str:
    """Balance + allowance detail."""
    bal = float(snap.get("pusd_balance", 0))
    allow = float(snap.get("pusd_allowance", 0))
    age = _age_human(fetched_at)

    lines = [
        "<b>💰 Bakiye & Allowance</b>",
        "",
        f"<b>pUSD Bakiye:</b> ${bal:,.4f}",
        f"<b>Exchange Allowance:</b> ${allow:,.4f}",
        "",
    ]
    if allow < bal * 0.5:
        lines.append("⚠ <b>Allowance düşük</b>")
        lines.append("Polymarket UI → Settings → Approve Exchange yap.")
    elif allow < 1.0:
        lines.append("ℹ Allowance &lt; $1 — order place edilirse reject olur.")
    else:
        lines.append("✅ Allowance yeterli — order place edilebilir.")
    lines.append("")
    lines.append(f"<i>Veri yaşı:</i> {esc(age)}")
    return "\n".join(lines)


def _render_positions(snap: dict, fetched_at: str) -> str:
    """Open positions list."""
    positions = snap.get("positions", []) or []
    age = _age_human(fetched_at)

    if not positions:
        return (
            "<b>📊 Açık Pozisyonlar</b>\n\n"
            "<i>Açık pozisyon yok.</i>\n\n"
            "Bot live trading açıkken (LIVE_ENABLED=true) ve uygun "
            "signal bulduğunda burada görünür.\n\n"
            f"<i>Veri yaşı:</i> {esc(age)}"
        )

    lines = [
        "<b>📊 Açık Pozisyonlar</b>",
        "",
    ]
    total_pnl = 0.0
    for i, p in enumerate(positions[:10], 1):
        slug = str(p.get("market_slug", ""))[:32]
        outcome = str(p.get("outcome", ""))[:8]
        shares = float(p.get("shares", 0))
        avg = float(p.get("avg_price", 0))
        cur = float(p.get("cur_price", 0))
        pnl = float(p.get("pnl_usd", 0))
        pnl_pct = float(p.get("pnl_pct", 0))
        total_pnl += pnl
        emoji = "🟢" if pnl >= 0 else "🔴"
        lines.append(
            f"{emoji} <b>{esc(slug)}</b> [{esc(outcome)}]\n"
            f"  {shares:.1f} shares @ ${avg:.4f} → ${cur:.4f} | "
            f"PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)"
        )
    if len(positions) > 10:
        lines.append(f"\n<i>... ve {len(positions)-10} daha</i>")
    lines.append("")
    sign = "+" if total_pnl >= 0 else ""
    lines.append(f"<b>Toplam Unrealized PnL:</b> {sign}${total_pnl:.2f}")
    lines.append(f"<i>Veri yaşı:</i> {esc(age)}")
    return "\n".join(lines)


def _render_trades(snap: dict, fetched_at: str) -> str:
    """Recent trades list."""
    trades = snap.get("recent_trades", []) or []
    age = _age_human(fetched_at)

    if not trades:
        return (
            "<b>📜 Son Trade'ler</b>\n\n"
            "<i>Settled trade yok.</i>\n\n"
            "Bot ilk gerçek trade'i geçirdiğinde burada görünür.\n\n"
            f"<i>Veri yaşı:</i> {esc(age)}"
        )

    lines = [
        "<b>📜 Son Trade'ler</b>",
        "",
    ]
    for t in trades[:10]:
        slug = str(t.get("market_slug", ""))[:24]
        side = str(t.get("side", ""))
        role = str(t.get("role", ""))
        price = float(t.get("price", 0))
        shares = float(t.get("shares", 0))
        fee = float(t.get("fee_usd", 0))
        status = str(t.get("status", ""))
        emoji = {"BUY": "📈", "SELL": "📉"}.get(side, "•")
        status_icon = {"CONFIRMED": "✅", "MINED": "⏳", "RETRYING": "🔄",
                       "FAILED": "❌"}.get(status, "❓")
        lines.append(
            f"{emoji} <b>{esc(side)}</b> {esc(role)} "
            f"{shares:.1f}@${price:.3f} fee=${fee:.3f} {status_icon}\n"
            f"  <code>{esc(slug)}</code>"
        )
    if len(trades) > 10:
        lines.append(f"\n<i>... {len(trades)-10} daha</i>")
    lines.append("")
    lines.append(f"<i>Veri yaşı:</i> {esc(age)}")
    return "\n".join(lines)


_RENDERERS = {
    "summary": _render_summary,
    "balance": _render_balance,
    "positions": _render_positions,
    "trades": _render_trades,
}


async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point — /portfolio veya /pf."""
    user_id = update.effective_user.id
    if not _is_admin(context, user_id):
        await update.message.reply_text("⛔ Admin only.")
        return

    db = context.bot_data.get("db")
    if db is None:
        await update.message.reply_text("❌ DB bağlantısı yok.")
        return

    snap, fetched_at = await _read_cache(db)
    if snap is None:
        await update.message.reply_text(
            "<b>💼 Polymarket Cüzdan</b>\n\n"
            "<i>Cache henüz oluşturulmadı.</i>\n\n"
            "Bot başlatıldıktan ~60 saniye sonra ilk snapshot alınır. "
            "Eğer 2 dakikadır boş ise:\n"
            "1. <code>POLYGON_WALLET</code> + <code>POLYGON_PRIVATE_KEY</code> .env'de set mi?\n"
            "2. <code>PORTFOLIO_REFRESH_ENABLED=true</code> mu?\n"
            "3. Bot log'una bak: <code>portfolio_job</code> hata var mı?",
            parse_mode="HTML",
        )
        return

    text = _render_summary(snap, fetched_at)
    if _is_stale(fetched_at):
        text = (
            "⚠ <i>Cache stale (> 5 dk). Yenile butonuna basın.</i>\n\n" + text
        )
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=_build_keyboard("summary")
    )


async def portfolio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline button callbacks: pf_tab_<name>, pf_refresh."""
    q = update.callback_query
    user_id = q.from_user.id
    if not _is_admin(context, user_id):
        await q.answer("Admin only", show_alert=True)
        return

    db = context.bot_data.get("db")
    if db is None:
        await q.answer("DB yok", show_alert=True)
        return

    data = q.data or ""

    if data == "pf_refresh":
        # Force fresh fetch
        await q.answer("Yenileniyor...")
        try:
            from data.polymarket_portfolio import build_snapshot
            snap_obj = await build_snapshot()
            snap = snap_obj.to_dict()
            fetched_at = snap_obj.fetched_at
            # Update DB cache too
            try:
                await db.conn.execute(
                    "INSERT INTO polymarket_portfolio_cache "
                    "(id, user_address, snapshot_json, fetched_at, "
                    "fetch_latency_ms, error_count) VALUES (1,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "user_address=excluded.user_address, "
                    "snapshot_json=excluded.snapshot_json, "
                    "fetched_at=excluded.fetched_at, "
                    "fetch_latency_ms=excluded.fetch_latency_ms, "
                    "error_count=excluded.error_count",
                    (snap_obj.user_address,
                     json.dumps(snap, ensure_ascii=False, default=str),
                     snap_obj.fetched_at, snap_obj.fetch_latency_ms,
                     len(snap_obj.fetch_errors)),
                )
                await db.conn.commit()
            except aiosqlite.Error as e:
                logger.debug(f"refresh cache write fail: {e}")
        except Exception as e:  # noqa: BLE001
            logger.exception(f"portfolio refresh failed: {e}")
            await q.edit_message_text(
                f"❌ Refresh hatası: <code>{esc(str(e)[:200])}</code>",
                parse_mode="HTML",
            )
            return
        text = _render_summary(snap, fetched_at)
        await q.edit_message_text(
            text, parse_mode="HTML", reply_markup=_build_keyboard("summary")
        )
        return

    # Tab switch
    if data.startswith("pf_tab_"):
        tab = data.replace("pf_tab_", "")
        if tab not in _RENDERERS:
            await q.answer("Bilinmeyen tab", show_alert=True)
            return
        snap, fetched_at = await _read_cache(db)
        if snap is None:
            await q.answer("Cache yok — Yenile butonuna basın", show_alert=True)
            return
        await q.answer()
        text = _RENDERERS[tab](snap, fetched_at)
        await q.edit_message_text(
            text, parse_mode="HTML", reply_markup=_build_keyboard(tab)
        )
