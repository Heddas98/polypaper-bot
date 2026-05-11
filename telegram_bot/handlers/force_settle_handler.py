"""
PolyPaper Bot - /force_settle Handler (Sprint 5 HOTFIX v4)
==========================================================
Admin-only manual settlement for stuck positions. When the oracle is silent
longer than expected, ops can inspect and settle without waiting for the
7200s (or 900s for short-TF) UMA force-settle deadline.

Commands:
    /force_settle               → list stuck positions (oldest close > 60s)
    /force_settle list          → same as no-arg
    /force_settle <exec_prefix> → settle a single execution by id prefix (>=6 chars)
    /force_settle all_stuck     → settle every stuck position found

The command runs a "fresh oracle pass" per position before manually settling:
    1. Gamma outcomePrices (check_market_resolved)
    2. CLOB last-trade (get_resolution_price, unclamped 0.0/1.0)
    3. Scanner last-known odds (extreme >0.85 or <0.15)
    4. Last odds_history row
    5. Fallback: entry side (lose on null oracle)

This mirrors _check() in engine_monitor.py but forces execution regardless
of the force-settle deadline — operator gets a clean summary + trade receipt.
"""
from __future__ import annotations

import asyncio
import logging

from core.slug_utils import infer_tf_from_slug, infer_asset_from_slug
from datetime import datetime, timezone

import aiosqlite
import httpx
from telegram import Update
from telegram.ext import ContextTypes

from config.settings import Settings
from core.engine_support import _slug_end
from data.polymarket_client import safe_float
from telegram_bot.templates.safe_html import esc, fmt_usd

logger = logging.getLogger("polypaper.handlers.force_settle")


def _is_admin(context, telegram_id: int) -> bool:
    settings: Settings = context.bot_data.get("settings")
    if not settings:
        logger.warning(
            f"⚠️ force_settle _is_admin: settings missing, denying {telegram_id}")
        return False
    return settings.is_admin(telegram_id)


async def _fetch_open_rows(db):
    rows = []
    async with db.conn.execute(
            "SELECT * FROM executions WHERE status='bet_placed' "
            "ORDER BY created_at ASC") as c:
        async for row in c:
            rows.append(dict(row))
    return rows


def _elapsed_since_close(slug: str, now: datetime) -> float | None:
    end = _slug_end(slug)
    if not end:
        return None
    return (now - end).total_seconds()


async def _resolve_oracle(engine, row) -> tuple[str | None, float | None, str]:
    """Run the full resolution chain and return (winner, price, source)."""
    slug = row["event_slug"]
    direction = row["direction"]

    # 1) Gamma outcomePrices
    try:
        resolved = await engine.client.check_market_resolved(slug)
    except (httpx.HTTPError, asyncio.TimeoutError, AttributeError) as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. check_market_
        # resolved gamma fetch — httpx + JSON parse errors. None falls
        # through to next oracle layer.
        logger.debug(f"force_settle gamma err {slug[:30]}: "
                     f"{type(e).__name__}: {e}")
        resolved = None
    if resolved:
        return resolved, None, "gamma"

    # 2) CLOB last-trade unclamped (0.0/1.0 allowed)
    token_id = row.get("market_token_id")
    if token_id:
        try:
            cur_p = await engine.client.get_resolution_price(token_id)
        except (httpx.HTTPError, asyncio.TimeoutError, AttributeError) as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. CLOB price
            # fetch — same surface as gamma above.
            logger.debug(f"force_settle clob err {slug[:30]}: "
                         f"{type(e).__name__}: {e}")
            cur_p = None
        if cur_p is not None and (cur_p >= 0.95 or cur_p <= 0.05):
            if cur_p >= 0.95:
                return direction, cur_p, "clob"
            opp = "down" if direction == "up" else "up"
            return opp, cur_p, "clob"

    # 3) Scanner extreme odds
    last = None
    try:
        last = engine.scanner.get_last_known_odds(slug)
    except (AttributeError, KeyError):
        # T11.8-B (2026-04-24): narrow from bare Exception. Scanner not yet
        # initialized (AttributeError) or slug not cached (KeyError). Falls
        # through to next oracle layer.
        pass
    if last:
        lu = safe_float(last.get("up_odds"))
        if lu is not None and (lu > 0.85 or lu < 0.15):
            return ("up" if lu > 0.5 else "down"), lu, "scanner"

    # 4) odds_history last row
    try:
        async with engine.db.conn.execute(
                "SELECT up_odds FROM odds_history WHERE event_slug=? "
                "ORDER BY timestamp DESC LIMIT 1", (slug,)) as c:
            r = await c.fetchone()
        if r and r["up_odds"] is not None:
            lu = float(r["up_odds"])
            return ("up" if lu > 0.5 else "down"), lu, "history"
    except (aiosqlite.Error, KeyError, IndexError, TypeError, ValueError):
        # T11.8-B (2026-04-24): narrow from bare Exception. odds_history
        # query + row[col] + float() coercion. Falls through to entry-side
        # fallback (5th oracle).
        pass

    # 5) Fallback: entry side → this nearly always locks in loss
    entry = safe_float(row["execution_price"]) or 0.5
    return ("up" if entry > 0.5 else "down"), entry, "fallback"


async def _settle_one(engine, row) -> dict:
    entry = safe_float(row["execution_price"]) or 0.5
    shares = row["trade_amount"] / entry if entry > 0 else 0
    winner, price, src = await _resolve_oracle(engine, row)
    try:
        await engine._settle(row, winner, shares, price)
        return {
            "ok": True, "exec_id": row["id"], "slug": row["event_slug"],
            "winner": winner, "price": price, "source": src,
            "direction": row["direction"], "trade_amount": row["trade_amount"],
        }
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): _settle outer wrapper intentionally wide.
        # Multi-step settlement: oracle resolution + fee math + DB update +
        # journal write — heterogeneous failure surface. Result dict with
        # ok=False signals failure to caller.
        logger.exception(f"force_settle _settle fail {row['id'][:8]}: {e}")
        return {
            "ok": False, "exec_id": row["id"], "slug": row["event_slug"],
            "error": str(e),
        }


def _format_stuck_list(rows, now: datetime) -> str:
    if not rows:
        return "✅ <b>Stuck pozisyon yok</b>\n\nTüm açık pozisyonlar aktif veya oracle bekleme normal aralıkta."
    lines = ["🕓 <b>Stuck Positions</b>\n"]
    for r in rows:
        elapsed = _elapsed_since_close(r["event_slug"], now)
        if elapsed is None:
            elapsed_s = "n/a"
        elif elapsed < 0:
            elapsed_s = f"{-elapsed:.0f}s until close"
        else:
            elapsed_s = f"{elapsed/60:.1f}m past close"
        parts = r["event_slug"].split("-")
        # P0-08-D (2026-05-08): slug_utils — 4 TF aware
        asset = infer_asset_from_slug(slug)
        tf = infer_tf_from_slug(slug)
        lines.append(
            f"• <code>{esc(r['id'][:8])}</code> {esc(asset)} {esc(tf)} "
            f"{r['direction'].upper()} @ {fmt_usd(r['trade_amount'])} "
            f"— <i>{esc(elapsed_s)}</i>")
    lines.append("")
    lines.append(
        "Kullanım:\n"
        "• <code>/force_settle &lt;exec_prefix&gt;</code> — tek pozisyon\n"
        "• <code>/force_settle all_stuck</code> — hepsini settle et")
    return "\n".join(lines)


def _format_settle_result(res: dict) -> str:
    if not res.get("ok"):
        return (f"❌ <b>Force-settle FAILED</b>\n"
                f"exec: <code>{esc(res.get('exec_id', '?')[:8])}</code>\n"
                f"slug: <code>{esc(res.get('slug', '?'))}</code>\n"
                f"err: <code>{esc(res.get('error', '?'))[:200]}</code>")
    parts = res.get("slug", "").split("-")
    # P0-08-D (2026-05-08): slug_utils — 4 TF aware
    asset = infer_asset_from_slug(slug)
    tf = infer_tf_from_slug(slug)
    price_str = f" @ {res['price']:.3f}" if res.get("price") is not None else ""
    return (f"⚖️ <b>Force-settled</b>\n"
            f"exec: <code>{esc(res['exec_id'][:8])}</code>\n"
            f"market: {esc(asset)} {esc(tf)} {res['direction'].upper()}\n"
            f"amount: {fmt_usd(res['trade_amount'])}\n"
            f"winner: <b>{esc(res['winner'])}</b>{price_str}\n"
            f"source: <code>{esc(res['source'])}</code>")


async def force_settle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/force_settle — admin manual oracle settle for stuck positions."""
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine çalışmıyor.")
    db = context.bot_data.get("db")
    if not db:
        return await update.message.reply_text("DB bağlanmadı.")

    args = context.args or []
    mode = args[0].lower() if args else "list"

    try:
        rows = await _fetch_open_rows(db)
    except aiosqlite.Error as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. _fetch_open_rows
        # SELECT surfaces aiosqlite.Error only. T10.7 render policy preserved.
        # Epic 10 T10.7 (2026-04-22): exception detail loglara yazılır,
        # kullanıcıya generic mesaj döner — DB şeması / tablo isimleri /
        # SQL parçaları Telegram'a sızmasın.
        logger.exception(f"force_settle fetch_open_rows: {e}")
        return await update.message.reply_text(
            "❌ Açık pozisyonlar sorgulanamadı. Detay loglarda.",
            parse_mode="HTML")

    now = datetime.now(timezone.utc)

    # Stuck = closed for > 60s and still bet_placed
    stuck = [
        r for r in rows
        if (_elapsed_since_close(r["event_slug"], now) or -1) > 60
    ]

    if mode in ("list",):
        text = _format_stuck_list(stuck, now)
        return await update.message.reply_text(text, parse_mode="HTML")

    if mode == "all_stuck":
        if not stuck:
            return await update.message.reply_text(
                "✅ Stuck pozisyon yok — hiçbir şey yapılmadı.", parse_mode="HTML")
        results = []
        for r in stuck:
            results.append(await _settle_one(engine, r))
        ok = sum(1 for x in results if x.get("ok"))
        fail = len(results) - ok
        summary = [f"⚖️ <b>Toplu force-settle</b>\n{ok} OK, {fail} FAIL\n"]
        for res in results:
            summary.append(_format_settle_result(res))
        return await update.message.reply_text(
            "\n\n".join(summary), parse_mode="HTML")

    # Treat args[0] as exec_id prefix
    prefix = args[0].strip()
    if len(prefix) < 6:
        return await update.message.reply_text(
            "❌ exec_id prefix en az 6 karakter olmalı.\n"
            "Liste: <code>/force_settle</code>", parse_mode="HTML")

    matches = [r for r in rows if r["id"].startswith(prefix)]
    if not matches:
        return await update.message.reply_text(
            f"❌ <code>{esc(prefix)}</code> ile eşleşen açık pozisyon yok.",
            parse_mode="HTML")
    if len(matches) > 1:
        ids = ", ".join(esc(r["id"][:12]) for r in matches)
        return await update.message.reply_text(
            f"❌ Prefix birden fazla pozisyonla eşleşiyor: {ids}\n"
            "Daha uzun bir prefix ver.", parse_mode="HTML")

    res = await _settle_one(engine, matches[0])
    return await update.message.reply_text(
        _format_settle_result(res), parse_mode="HTML")
