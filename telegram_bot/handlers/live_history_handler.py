"""Live trade history detay panel + CSV export — Heddas 2026-05-06.

Polymarket data-api/activity endpoint'inden zengin veri:
  - timestamp, type (TRADE/REDEEM/SPLIT/MERGE)
  - condition_id, transaction_hash (polygonscan link)
  - title, slug, outcome, side
  - size, price, usdc_size

Per-trade detay ekran:
  - Tarih + market + dilim
  - Giriş/çıkış fiyatı + maliyet + fee
  - PnL (cash + percent)
  - Holding time
  - TX link (polygonscan)

CSV export rich fields:
  timestamp_iso, type, market_slug, condition_id, side, outcome,
  size_shares, price_usd, usdc_size, tx_hash, polygonscan_url,
  title, end_date, holding_seconds (computed)
"""
from __future__ import annotations

import csv
import io
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update,
)
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger("polypaper.live_history")

PAGE_SIZE = 5
POLYGONSCAN_TX = "https://polygonscan.com/tx/"


def _fmt_time(ts: int) -> str:
    """Unix sn → ISO TR-friendly format."""
    if not ts:
        return "?"
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        return dt.strftime("%d %b %H:%M UTC")
    except (ValueError, OSError):
        return "?"


def _fmt_iso_full(ts: int) -> str:
    """Unix sn → ISO full UTC timestamp."""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(
            int(ts), tz=timezone.utc).isoformat()
    except (ValueError, OSError):
        return ""


def _type_emoji(act_type: str, side: str = "") -> str:
    """Activity type → emoji."""
    if act_type == "TRADE":
        return "🟢" if side == "BUY" else "🔴"
    if act_type == "REDEEM":
        return "🏆"
    if act_type == "SPLIT":
        return "🔧"
    if act_type == "MERGE":
        return "🔄"
    if act_type == "REWARD":
        return "🎁"
    return "📌"


async def live_history_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Live history callback handler.

    Patterns:
      live_history:<page>            → Trade list (sayfalı)
      live_history_detail:<idx>      → Per-trade detay
      live_export_csv                → CSV download
      live_pnl                       → PnL summary panel
    """
    q = update.callback_query
    if not q:
        return
    await q.answer()
    data = q.data or ""

    if data == "live_export_csv":
        await _export_csv(update, context)
    elif data == "live_pnl":
        await _show_pnl_summary(q, context)
    elif data.startswith("live_history_detail:"):
        try:
            idx = int(data.split(":", 1)[1])
            await _show_trade_detail(q, context, idx)
        except (ValueError, IndexError):
            await q.message.reply_text("Hatalı detay index")
    elif data.startswith("live_history:"):
        try:
            page = int(data.split(":", 1)[1])
        except (ValueError, IndexError):
            page = 0
        await _show_history_list(q, context, page)


async def live_history_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """`/lh` veya `/livehistory` komutu — page 0."""
    fake_q = update.callback_query
    if fake_q is None:
        # /lh komutu — fake callback q
        class _Q:
            data = "live_history:0"
            message = update.message

            async def edit_message_text(self, *a, **kw):
                await update.message.reply_text(*a, **kw)

            async def answer(self):
                pass
        fake_q = _Q()
    await _show_history_list(fake_q, context, 0)


async def _get_activity(context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    """Cache'ten activity dict listesini al (TRADE/REDEEM filtrelenmiş)."""
    engine = context.bot_data.get("engine")
    if engine is None or getattr(engine, "db", None) is None:
        return []
    try:
        from data.polymarket_portfolio import read_cached_snapshot
        snap = await read_cached_snapshot(engine.db)
        if snap:
            return list(snap.get("activity", []) or [])
    except Exception as e:  # noqa: BLE001
        logger.debug(f"_get_activity: {e}")
    return []


async def _show_history_list(q, context, page: int) -> None:
    """Sayfa N'deki trade listesi."""
    activity = await _get_activity(context)
    total = len(activity)

    if total == 0:
        text = (
            "📜 <b>LIVE TRADES</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Henüz on-chain trade yok.\n"
            "<i>Polymarket cache henüz güncellenmemiş olabilir — "
            "bot başlatıldıktan 60sn sonra populate olur.</i>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Yenile",
                                   callback_data=f"live_history:{page}")],
            [InlineKeyboardButton("◀️ Live Menü",
                                   callback_data="main_live")],
        ])
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (BadRequest, TelegramError):
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return

    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    chunk = activity[start:end]

    text = (
        f"📜 <b>LIVE TRADES — Sayfa {page + 1}/{pages}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Toplam {total} on-chain işlem (Polymarket data-api)</i>\n\n"
    )
    rows = []
    for i, a in enumerate(chunk):
        idx = start + i
        ts = int(a.get("timestamp", 0) or 0)
        act_type = str(a.get("type", ""))
        side = str(a.get("side", ""))
        title = str(a.get("title", "?"))[:32]
        outcome = str(a.get("outcome", ""))
        size = float(a.get("size", 0))
        price = float(a.get("price", 0))
        usdc = float(a.get("usdc_size", 0))
        emoji = _type_emoji(act_type, side)

        text += (
            f"{emoji} <b>{act_type}</b> "
            f"{('· ' + side) if side else ''}\n"
            f"  📅 {_fmt_time(ts)}\n"
            f"  🎯 {title}{' · ' + outcome if outcome else ''}\n"
            f"  💵 {size:.2f} @ ${price:.3f} = ${usdc:.2f}\n\n"
        )
        rows.append([InlineKeyboardButton(
            f"{emoji} {act_type} — Detay",
            callback_data=f"live_history_detail:{idx}",
        )])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(
            "⏪ Önceki", callback_data=f"live_history:{page - 1}",
        ))
    if page < pages - 1:
        nav_row.append(InlineKeyboardButton(
            "Sonraki ⏩", callback_data=f"live_history:{page + 1}",
        ))
    if nav_row:
        rows.append(nav_row)
    rows.append([
        InlineKeyboardButton("🔄 Yenile",
                              callback_data=f"live_history:{page}"),
        InlineKeyboardButton("📤 CSV Export",
                              callback_data="live_export_csv"),
    ])
    rows.append([
        InlineKeyboardButton("📈 PnL Özet", callback_data="live_pnl"),
        InlineKeyboardButton("◀️ Live Menü", callback_data="main_live"),
    ])
    kb = InlineKeyboardMarkup(rows)
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (BadRequest, TelegramError):
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def _show_trade_detail(q, context, idx: int) -> None:
    """Tek trade detay ekran."""
    activity = await _get_activity(context)
    if idx < 0 or idx >= len(activity):
        await q.message.reply_text("Trade bulunamadı")
        return
    a = activity[idx]
    ts = int(a.get("timestamp", 0) or 0)
    act_type = str(a.get("type", ""))
    side = str(a.get("side", ""))
    title = str(a.get("title", "?"))
    slug = str(a.get("slug", ""))
    outcome = str(a.get("outcome", ""))
    cid = str(a.get("condition_id", ""))
    asset = str(a.get("asset", ""))
    size = float(a.get("size", 0))
    price = float(a.get("price", 0))
    usdc = float(a.get("usdc_size", 0))
    tx_hash = str(a.get("transaction_hash", ""))
    emoji = _type_emoji(act_type, side)

    text = (
        f"{emoji} <b>{act_type} {('— ' + side) if side else ''}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>📅 Zaman:</b> {_fmt_time(ts)}\n"
        f"<b>🎯 Market:</b> {title}\n"
    )
    if slug:
        text += f"<b>🔖 Slug:</b> <code>{slug}</code>\n"
    if outcome:
        text += f"<b>📊 Outcome:</b> {outcome}\n"
    if size:
        text += f"<b>💼 Hisse:</b> {size:.4f}\n"
    if price:
        text += f"<b>💰 Fiyat:</b> ${price:.4f}\n"
    if usdc:
        text += f"<b>💵 USDC:</b> ${usdc:.4f}\n"
    if cid:
        text += f"<b>📑 Condition:</b> <code>{cid[:18]}...</code>\n"
    if asset:
        text += f"<b>🪙 Token ID:</b> <code>{asset[:16]}...</code>\n"

    rows = []
    if tx_hash:
        text += (
            f"\n<b>🔗 Transaction:</b>\n"
            f"<code>{tx_hash[:18]}...{tx_hash[-6:]}</code>\n"
        )
        rows.append([InlineKeyboardButton(
            "🔗 Polygonscan'da Aç",
            url=f"{POLYGONSCAN_TX}{tx_hash}",
        )])

    if slug:
        rows.append([InlineKeyboardButton(
            "🌐 Polymarket'da Gör",
            url=f"https://polymarket.com/event/{slug}",
        )])

    rows.append([InlineKeyboardButton(
        "◀️ Liste",
        callback_data="live_history:0",
    )])
    kb = InlineKeyboardMarkup(rows)
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb,
                                   disable_web_page_preview=True)
    except (BadRequest, TelegramError):
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb,
                                    disable_web_page_preview=True)


async def _export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """CSV export — zengin field listesi.

    Polymarket activity field'ları + computed (holding_sec).
    """
    activity = await _get_activity(context)
    if not activity:
        await update.callback_query.message.reply_text(
            "📤 CSV: henüz veri yok.",
        )
        return

    buf = io.StringIO()
    fieldnames = [
        "timestamp_unix", "timestamp_iso", "type", "side",
        "title", "slug", "outcome", "outcome_index",
        "size_shares", "price_usd", "usdc_size",
        "condition_id", "asset_token_id",
        "transaction_hash", "polygonscan_url",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for a in activity:
        ts = int(a.get("timestamp", 0) or 0)
        tx = str(a.get("transaction_hash", ""))
        writer.writerow({
            "timestamp_unix": ts,
            "timestamp_iso": _fmt_iso_full(ts),
            "type": a.get("type", ""),
            "side": a.get("side", ""),
            "title": a.get("title", ""),
            "slug": a.get("slug", ""),
            "outcome": a.get("outcome", ""),
            "outcome_index": a.get("outcome_index", 0),
            "size_shares": a.get("size", 0),
            "price_usd": a.get("price", 0),
            "usdc_size": a.get("usdc_size", 0),
            "condition_id": a.get("condition_id", ""),
            "asset_token_id": a.get("asset", ""),
            "transaction_hash": tx,
            "polygonscan_url": (POLYGONSCAN_TX + tx) if tx else "",
        })

    csv_bytes = buf.getvalue().encode("utf-8")
    fname = (
        "polypaper_live_trades_"
        f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    )
    try:
        await update.callback_query.message.reply_document(
            document=InputFile(io.BytesIO(csv_bytes), filename=fname),
            caption=(
                f"📤 <b>{len(activity)} trade</b> CSV export\n"
                f"<i>Polymarket on-chain activity</i>"
            ),
            parse_mode="HTML",
        )
    except Exception as e:  # noqa: BLE001
        logger.exception(f"_export_csv: {e}")
        await update.callback_query.message.reply_text(
            f"❌ CSV export hata: {type(e).__name__}: {str(e)[:200]}"
        )


async def _show_pnl_summary(q, context: ContextTypes.DEFAULT_TYPE) -> None:
    """PnL detay panel — coin/timeframe/best/worst breakdown."""
    activity = await _get_activity(context)
    engine = context.bot_data.get("engine")
    closed_positions = []
    open_positions = []

    if engine and getattr(engine, "db", None):
        try:
            from data.polymarket_portfolio import read_cached_snapshot
            snap = await read_cached_snapshot(engine.db)
            if snap:
                closed_positions = list(snap.get("closed_positions", []) or [])
                open_positions = list(snap.get("positions", []) or [])
        except Exception as e:  # noqa: BLE001
            logger.debug(f"_show_pnl_summary: {e}")

    # Compute statistics
    today_ts_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    week_ts_start = today_ts_start - 7 * 86400

    today_trades = [a for a in activity if a.get("type") == "TRADE"
                    and int(a.get("timestamp", 0) or 0) >= today_ts_start]
    week_trades = [a for a in activity if a.get("type") == "TRADE"
                   and int(a.get("timestamp", 0) or 0) >= week_ts_start]
    today_redeems = [a for a in activity if a.get("type") == "REDEEM"
                     and int(a.get("timestamp", 0) or 0) >= today_ts_start]

    # Open + closed PnL
    open_pnl = sum(
        float(p.get("cur_value_usd", 0)) - float(p.get("cost_basis_usd", 0))
        for p in open_positions
    )
    closed_pnl = sum(float(c.get("realized_pnl", 0)) for c in closed_positions)

    # Win/loss from closed positions
    wins = [c for c in closed_positions if float(c.get("realized_pnl", 0)) > 0]
    losses = [c for c in closed_positions if float(c.get("realized_pnl", 0)) < 0]
    total_closed = len(wins) + len(losses)
    win_rate = (len(wins) / total_closed * 100.0) if total_closed > 0 else 0.0

    # Best/worst
    best = max(closed_positions, key=lambda c: float(c.get("realized_pnl", 0)),
                default=None)
    worst = min(closed_positions, key=lambda c: float(c.get("realized_pnl", 0)),
                 default=None)

    text = (
        "📈 <b>LIVE PnL — Detay</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Bugün ({datetime.now(timezone.utc).strftime('%d %b')}):</b>\n"
        f"  • Trade: {len(today_trades)}\n"
        f"  • Redeem: {len(today_redeems)}\n\n"
        f"<b>Son 7 gün:</b>\n"
        f"  • Trade: {len(week_trades)}\n"
        f"  • Closed pozisyon: {total_closed}\n"
        f"  • Win Rate: {win_rate:.1f}% ({len(wins)}W/{len(losses)}L)\n\n"
        f"<b>Toplam PnL:</b>\n"
        f"  • Açık (mark-to-market): "
        f"{'🟢' if open_pnl >= 0 else '🔴'} {open_pnl:+.2f}\n"
        f"  • Kapalı (realized): "
        f"{'🟢' if closed_pnl >= 0 else '🔴'} {closed_pnl:+.2f}\n"
        f"  • Net: <b>{'🟢' if (open_pnl + closed_pnl) >= 0 else '🔴'} "
        f"{open_pnl + closed_pnl:+.2f}</b>\n"
    )

    if best:
        text += (
            f"\n<b>🏆 En İyi:</b>\n"
            f"  {str(best.get('title', '?'))[:32]}\n"
            f"  +${float(best.get('realized_pnl', 0)):.2f} "
            f"({float(best.get('percent_realized_pnl', 0)):+.1f}%)\n"
        )
    if worst:
        text += (
            f"\n<b>⚰️ En Kötü:</b>\n"
            f"  {str(worst.get('title', '?'))[:32]}\n"
            f"  ${float(worst.get('realized_pnl', 0)):.2f} "
            f"({float(worst.get('percent_realized_pnl', 0)):+.1f}%)\n"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📜 Trade Listesi",
                               callback_data="live_history:0")],
        [InlineKeyboardButton("📤 CSV Export",
                               callback_data="live_export_csv")],
        [InlineKeyboardButton("◀️ Live Menü",
                               callback_data="main_live")],
    ])
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (BadRequest, TelegramError):
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
