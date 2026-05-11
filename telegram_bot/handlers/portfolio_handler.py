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
from datetime import UTC, datetime
from typing import Optional

import aiosqlite
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
            "SELECT snapshot_json, fetched_at FROM polymarket_portfolio_cache " "WHERE id=1"
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
            fetched = fetched.replace(tzinfo=UTC)
        delta = (datetime.now(UTC) - fetched).total_seconds()
        return delta > STALE_THRESHOLD_SEC
    except (ValueError, TypeError):
        return True


def _age_human(fetched_at_iso: Optional[str]) -> str:
    if not fetched_at_iso:
        return "n/a"
    try:
        fetched = datetime.fromisoformat(fetched_at_iso.replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=UTC)
        delta = int((datetime.now(UTC) - fetched).total_seconds())
        if delta < 60:
            return f"{delta}s önce"
        if delta < 3600:
            return f"{delta // 60}dk önce"
        return f"{delta // 3600}h önce"
    except (ValueError, TypeError):
        return "n/a"


def _build_keyboard(active_tab: str = "summary") -> InlineKeyboardMarkup:
    """6-tab navigation (4 view + 2 actions row) + refresh button."""

    def _btn(label: str, tab: str) -> InlineKeyboardButton:
        prefix = "▸ " if tab == active_tab else "  "
        return InlineKeyboardButton(prefix + label, callback_data=f"pf_tab_{tab}")

    return InlineKeyboardMarkup(
        [
            # Row 1-2: Read-only tabs
            [_btn("💼 Özet", "summary"), _btn("💰 Bakiye", "balance")],
            [_btn("📊 Pozisyonlar", "positions"), _btn("📜 Trades", "trades")],
            # Row 3-4: Actions (Aşama 2)
            [
                InlineKeyboardButton("📥 Yatır", callback_data="pf_act_deposit"),
                InlineKeyboardButton("📤 Çek", callback_data="pf_act_withdraw"),
            ],
            [
                InlineKeyboardButton("🔓 Allowance", callback_data="pf_act_approve"),
                InlineKeyboardButton("📂 Wallet Yönet", callback_data="pf_act_wallet"),
            ],
            # Row 5: Refresh
            # P0-03 (2026-05-08): "🔑 PK Export" button removed — exposed full
            # private key over Telegram. PK access now via OS keychain (P0-02).
            [
                InlineKeyboardButton("🔄 Yenile", callback_data="pf_refresh"),
            ],
        ]
    )


def _render_summary(snap: dict, fetched_at: str) -> str:
    """Compact overview tab — main metrics + diagnostics."""
    user = snap.get("user_address", "")
    bal = float(snap.get("pusd_balance", 0))
    nav = float(snap.get("portfolio_value_usd", 0))
    pos_n = int(snap.get("positions_count", 0))
    errors = snap.get("fetch_errors", []) or []
    latency = int(snap.get("fetch_latency_ms", 0))
    age = _age_human(fetched_at)

    # 2026-04-29 Aşama 3.B: mode banner
    from telegram_bot.templates.mode_banner import format_banner

    lines = [
        format_banner().rstrip(),
        "",
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
        lines.append(
            f"⚠ <i>{len(errors)} fetch hatası</i> — detay: <code>{esc(str(errors[0])[:80])}</code>"
        )
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
        status_icon = {"CONFIRMED": "✅", "MINED": "⏳", "RETRYING": "🔄", "FAILED": "❌"}.get(
            status, "❓"
        )
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
        text = "⚠ <i>Cache stale (> 5 dk). Yenile butonuna basın.</i>\n\n" + text
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
                    (
                        snap_obj.user_address,
                        json.dumps(snap, ensure_ascii=False, default=str),
                        snap_obj.fetched_at,
                        snap_obj.fetch_latency_ms,
                        len(snap_obj.fetch_errors),
                    ),
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
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=_build_keyboard("summary"))
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
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=_build_keyboard(tab))
        return

    # 2026-04-29 Aşama 2 Actions
    if data.startswith("pf_act_"):
        action = data.replace("pf_act_", "")
        await _handle_action(q, context, action)
        return


async def _handle_action(q, context: ContextTypes.DEFAULT_TYPE, action: str) -> None:
    """A1-A5 inline action handler."""
    from data.polymarket_actions import (
        approve_allowance,
        deposit_info,
        wallet_import_steps,
        withdraw_info,
    )

    # A2 — Deposit info
    if action == "deposit":
        await q.answer("Deposit bilgileri yükleniyor...")
        info = deposit_info()
        if info.get("error"):
            await q.edit_message_text(
                f"❌ Deposit hatası: {esc(info['error'])}",
                parse_mode="HTML",
                reply_markup=_back_keyboard(),
            )
            return
        text = (
            "<b>📥 Polymarket Deposit Rehberi</b>\n\n"
            f"<b>Ağ:</b> {esc(info['chain'])}\n"
            f"<b>Token:</b> USDC.e (auto → pUSD)\n"
            f"<b>Min:</b> {esc(info['min_deposit'])}\n\n"
            f"<b>Deposit Adresi:</b>\n<code>{esc(info['address'])}</code>\n\n"
            f"<a href=\"{info['polymarket_ui']}\">🔗 Polymarket UI'dan deposit</a>\n"
            f"<a href=\"{info['polygonscan']}\">🔗 Polygonscan'da gör</a>\n\n"
            "<i>QR kod indirildi — Telegram resim olarak gönderiyor.</i>\n\n"
            "<b>EIP-681 URI:</b>\n"
            f"<code>{esc(info['eip681_uri'])}</code>\n\n"
            "Rabby/MetaMask → Send → Polygon → bu adres → USDC tutar gönder."
        )
        try:
            await q.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=_back_keyboard(),
                disable_web_page_preview=True,
            )
            # Send QR image as separate photo
            await context.bot.send_photo(
                chat_id=q.message.chat_id,
                photo=info["qr_image_url"],
                caption=f"QR: {info['address']}",
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"deposit QR send fail: {e}")
        return

    # A3 — Withdraw info (Polymarket UI)
    if action == "withdraw":
        await q.answer()
        info = withdraw_info()
        text = (
            "<b>📤 Polymarket Withdraw</b>\n\n"
            f"<b>Yöntem:</b> {esc(info['method'])}\n"
            f"<b>Fee:</b> {esc(info['fee'])}\n"
            f"<b>Min:</b> {esc(info['min_withdraw'])}\n\n"
            f"<a href=\"{info['ui_url']}\">🔗 Polymarket Withdraw Sayfası</a>\n"
            f"<a href=\"{info['polygonscan']}\">🔗 Polygonscan: Proxy işlemleri</a>\n\n"
            f"<i>{esc(info['note'])}</i>"
        )
        await q.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=_back_keyboard(),
            disable_web_page_preview=True,
        )
        return

    # A1 — Allowance approve
    if action == "approve":
        await q.answer("Allowance approve gönderiliyor...")
        await q.edit_message_text(
            "<b>🔓 Allowance Approve</b>\n\n"
            "<i>Polygon network'te transaction imzalanıyor + gönderiliyor "
            "(~10-30 saniye)...</i>",
            parse_mode="HTML",
        )
        ok, msg = await approve_allowance()
        emoji = "✅" if ok else "❌"
        await q.edit_message_text(
            f"<b>{emoji} Allowance Approve</b>\n\n{esc(msg)}\n\n"
            "<i>Sonuç /portfolio Bakiye sekmesinde 60s içinde görünür.</i>",
            parse_mode="HTML",
            reply_markup=_back_keyboard(),
        )
        return

    # A4 — Wallet import rehberi
    if action == "wallet":
        await q.answer()
        steps = wallet_import_steps()
        text = (
            "<b>📂 Yeni Wallet Import / Switch</b>\n\n"
            f"<b>1.</b> {esc(steps['step_1'])}\n\n"
            f"<b>2.</b> {esc(steps['step_2'])}\n\n"
            f"<b>3.</b> <pre>{esc(steps['step_3'])}</pre>\n\n"
            f"<b>4.</b> {esc(steps['step_4'])}\n\n"
            f"{esc(steps['warning'])}"
        )
        await q.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=_back_keyboard(),
        )
        return

    # A5 — PK Export REMOVED 2026-05-08 (P0-03 security fix). If a stale
    # callback arrives ("pf_act_pk"), respond with a polite error so old
    # message keyboards in chat history don't crash silently.
    if action == "pk":
        await q.answer(
            "Private Key export güvenlik nedeniyle kaldırıldı (P0-03).",
            show_alert=True,
        )
        logger.warning(
            "PK_EXPORT_BLOCKED: user_id=%s username=%s — feature deleted",
            q.from_user.id,
            q.from_user.username,
        )
        return

    await q.answer(f"Bilinmeyen aksiyon: {action}", show_alert=True)


def _back_keyboard() -> InlineKeyboardMarkup:
    """Single 'Geri' button leading to portfolio summary tab."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("◂ Geri", callback_data="pf_tab_summary")],
        ]
    )
