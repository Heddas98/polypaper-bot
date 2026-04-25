"""
PolyPaper Bot - /diagnose Handler (Phase 62+)
=============================================
Admin diagnostic command showing full trade filtering pipeline with skip counts.
Displays signal evaluation stages, risk state, env thresholds, strategy health, and WS status.

ADMIN ONLY — shows sensitive engine diagnostics.
"""
import logging
import os
import time
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config.settings import Settings
from telegram_bot.templates.safe_html import esc, fmt_usd

logger = logging.getLogger("polypaper.handlers.diagnose")


def _is_admin(context, telegram_id: int) -> bool:
    """Phase 17: Check admin access. Phase 54: never fallback to True."""
    settings: Settings = context.bot_data.get("settings")
    if not settings:
        logger.warning(f"⚠️ _is_admin: settings missing, denying user {telegram_id}")
        return False
    return settings.is_admin(telegram_id)


def _build_bg_tasks_section() -> str:
    """Phase 82e Sprint 2.1 — bg_task registry snapshot block.

    Extracted from diagnose_command so diagnose_callback can render the
    same block without duplicating code.
    """
    try:
        # Epic 7 B6 (2026-04-22): get_live_task_count() surfaces the
        # strong-ref set size — distinct from `snap` length which counts
        # metadata-registered tasks (including completed/failed). `live`
        # = tasks currently held by _BG_TASK_OBJECTS + GC-protected.
        from core.bg_task import (
            get_registry_snapshot,
            get_recent_errors,
            get_live_task_count,
        )
        snap = get_registry_snapshot()
        errs = get_recent_errors(5)
        live = get_live_task_count()

        out = (
            f"<b>Background Tasks</b> ({len(snap)} tracked · "
            f"{live} live strong-ref)\n"
        )
        if snap:
            failed = [(n, i) for n, i in snap.items() if i.get("state") == "failed"]
            running = [(n, i) for n, i in snap.items() if i.get("state") == "running"]
            cancelled = [(n, i) for n, i in snap.items() if i.get("state") == "cancelled"]
            completed = [(n, i) for n, i in snap.items() if i.get("state") == "completed"]

            for n, i in failed:
                ec = i.get("error_count", 0)
                err = (i.get("last_error") or "")[:60]
                out += f"  ❌ <code>{esc(n)}</code> x{ec}: {esc(err)}\n"
            for n, i in running:
                out += f"  ✅ <code>{esc(n)}</code>\n"
            if cancelled:
                out += f"  ⏸ {len(cancelled)} cancelled\n"
            if completed:
                out += f"  🏁 {len(completed)} completed\n"
        else:
            out += "  (no tasks registered yet)\n"

        if errs:
            out += f"\n<b>Last {len(errs)} bg_task errors</b>\n"
            for e in errs[:5]:
                nm = esc(e.get("name", "?"))
                er = esc((e.get("error") or "")[:70])
                ago = max(0, int(time.time() - e.get("ts", 0)))
                out += f"  • <code>{nm}</code> ({ago}s ago): {er}\n"
        return out + "\n"
    except Exception as _bg_err:  # noqa: BLE001
        # T11.8-B (2026-04-24): bg_task registry import + getter calls — wide
        # catch lets /diagnose render even if registry module changes shape.
        # Admin-only diagnostic so 80-char truncated exc str is acceptable.
        return (
            f"<b>Background Tasks</b>: &lt;registry unavailable: "
            f"{esc(str(_bg_err)[:80])}&gt;\n\n"
        )


def _build_hyperopt_section() -> str:
    """Phase 82e Sprint 2.3 — HyperOpt worker status block.

    Renders the parent-side HyperoptProgressState: active run details
    (mode/strat/trial/elapsed/ETA/memory/last_status) OR last run summary
    OR 'idle — no run recorded' fallback. Never raises.
    """
    try:
        from telegram_bot.handlers.hyperopt_handler import _progress_state
        from backtest.hyperopt_ipc import format_eta_hybrid

        state = _progress_state
        out = "<b>HyperOpt Status</b>\n"

        if state.active:
            mode_label = {
                "single": "Single",
                "batch": "Batch",
            }.get(state.mode, state.mode)
            strat = state.current_strat or "-"
            trial = state.current_trial or 0
            total = state.current_total or 0
            elapsed_s = int(state.elapsed_sec)
            pct = state.progress_pct
            eta = format_eta_hybrid(state.eta_sec, pct)

            out += f"  Mode: {esc(mode_label)} 🟢\n"
            out += f"  Strat: <code>{esc(strat)}</code>\n"
            if total > 0:
                out += f"  Trial: {trial}/{total}\n"
            out += f"  Elapsed: {elapsed_s}s\n"
            out += f"  ETA: {esc(eta)}\n"
            if state.memory_mb > 0:
                out += (
                    f"  Memory: {state.memory_mb:.0f}MB "
                    f"(sys {state.memory_sys_pct:.0f}%)\n"
                )

            if state.last_status and state.last_status_at:
                ago = max(
                    0,
                    int((datetime.utcnow() - state.last_status_at).total_seconds()),
                )
                out += (
                    f"  Last: <code>{esc(state.last_status[:120])}</code> "
                    f"({ago}s ago)\n"
                )

            if state.warnings:
                last_warn = state.warnings[-1]
                out += f"  ⚠️ {esc(last_warn[:80])}\n"

        elif state.last_run_summary:
            s = state.last_run_summary
            elapsed = int(s.get("elapsed_sec") or 0)
            done = s.get("strats_done", 0)
            total = s.get("strats_total", 0)
            err = s.get("error")
            icon = "❌" if err else "🏁"
            out += f"  {icon} last run ({s.get('mode', '?')}): "
            out += f"{done}/{total} strats in {elapsed}s\n"
            top = s.get("top_strats") or []
            if top:
                t0 = top[0]
                out += (
                    f"  Best: <code>{esc(t0.get('name', '?'))}</code> "
                    f"score={t0.get('best_value', 0.0):.4f}\n"
                )
            if err:
                out += f"  Error: {esc(str(err)[:80])}\n"
        else:
            out += "  (idle — no run recorded this session)\n"

        return out + "\n"
    except Exception as _ho_err:  # noqa: BLE001
        # T11.8-B (2026-04-24): hyperopt_launcher import + worker registry —
        # subprocess + IPC + JSON parsing layers. Wide catch keeps /diagnose
        # rendering when worker silently dies. Admin-only.
        return (
            f"<b>HyperOpt Status</b>: &lt;unavailable: "
            f"{esc(str(_ho_err)[:80])}&gt;\n\n"
        )


async def diagnose_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /diagnose — Full trade filtering pipeline visibility.

    Shows:
    1. Signal eval skip counts (MARKET_HALT, NO_LIQ, TOO_EARLY, etc.)
    2. Current risk state (halted?, positions, daily PnL, loss streak)
    3. Env thresholds (MAX_OPEN_POSITIONS, MAX_TOTAL_EXPOSURE, etc.)
    4. Active vs running strategy counts
    5. WebSocket health (fresh?, last tick age, stale threshold)

    ADMIN ONLY.
    """
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine çalışmıyor.")

    # ══ 1. SKIP COUNTER (trade filtering pipeline) ══
    skip_counts = engine.skips._counts.copy() if hasattr(engine.skips, '_counts') else {}
    skip_total = engine.skips._total if hasattr(engine.skips, '_total') else 0

    # Sort by count descending
    skip_items = sorted(skip_counts.items(), key=lambda x: -x[1])

    skip_text = f"<b>Trade Filtering Pipeline (this heartbeat)</b>\n"
    skip_text += f"Total skips: <b>{skip_total}</b>\n"
    if skip_items:
        for reason, count in skip_items:
            skip_text += f"  • {esc(reason)}: {count}\n"
    else:
        skip_text += "  No skips recorded\n"
    skip_text += "\n"

    # ══ 2. RISK STATE ══
    rs = engine.risk.get_status()
    limits = rs.get("limits", {})

    # Risk halt emoji + text
    # T6.3-B (2026-04-24) bug fix: ESKI mantik ters yazilmisti --
    #   halted=True  -> halt_reason  (bu DOGRU, halt aktif)
    #   halted=False -> "Active"     (bu YANLIS! halt yok ama "Active" diyordu, kullanici "halt active" sandi)
    # Yeni: halted=False -> "No halt" (net Turkce: halt yok); halted=True -> halt_reason
    halt_emoji = "🛑" if rs.get("halted", False) else "✅"
    halt_text = rs.get("halt_reason", "Halted") if rs.get("halted") else "No halt"

    # Kill switch status
    ks = engine.kill_switch.get_status()
    kill_emoji = "🛑" if ks.get("killed", False) else "✅"
    kill_text = ks.get("reason", "N/A") if ks.get("killed") else "Inactive"

    # Daily PnL color
    daily_pnl = rs.get("daily_pnl", 0.0)
    pnl_emoji = "📈" if daily_pnl >= 0 else "📉"

    risk_text = (
        f"<b>Risk State</b>\n"
        f"  Kill: {kill_emoji} {esc(kill_text)}\n"
        f"  Halt: {halt_emoji} {esc(halt_text)}\n"
        f"  Open: {rs.get('open_positions', 0)}/{limits.get('max_positions', 0)}\n"
        f"  Exp: {fmt_usd(rs.get('total_exposure', 0.0), decimals=1)} / "
        f"{fmt_usd(limits.get('max_exposure', 0.0), decimals=0)}\n"
        f"  {pnl_emoji} Daily PnL: {fmt_usd(daily_pnl, sign=True)}\n"
        f"  Trades: {rs.get('daily_trades', 0)}\n"
        f"  Loss streak: {rs.get('loss_streak', 0)}\n"
    )
    risk_text += "\n"

    # ══ 3. ENV THRESHOLDS ══
    max_positions = int(os.getenv("MAX_OPEN_POSITIONS", "10"))
    max_exposure = float(os.getenv("MAX_TOTAL_EXPOSURE", "5000"))
    max_daily_loss = float(os.getenv("MAX_DAILY_LOSS", "500"))
    max_loss_streak = int(os.getenv("MAX_LOSS_STREAK", "5"))
    min_order_usd = float(os.getenv("MIN_ORDER_USD", "2.0"))
    min_order_shares = float(os.getenv("MIN_ORDER_SHARES", "1.0"))
    ws_stale_secs = float(os.getenv("WS_STALE_THRESHOLD", "60.0"))

    env_text = (
        f"<b>Environment Thresholds</b>\n"
        f"  MAX_OPEN_POSITIONS: {max_positions}\n"
        f"  MAX_TOTAL_EXPOSURE: {fmt_usd(max_exposure, decimals=0)}\n"
        f"  MAX_DAILY_LOSS: {fmt_usd(max_daily_loss, decimals=0)}\n"
        f"  MAX_LOSS_STREAK: {max_loss_streak}\n"
        f"  MIN_ORDER_USD: {fmt_usd(min_order_usd, decimals=2)}\n"
        f"  MIN_ORDER_SHARES: {min_order_shares}\n"
        f"  WS_STALE_THRESHOLD: {ws_stale_secs:.1f}s\n"
    )
    env_text += "\n"

    # ══ 4. STRATEGY HEALTH ══
    try:
        strats = await engine.db.get_active_strategies()
        active_count = len(strats)

        # Count running (enabled) vs paused
        # Phase 78-fix: Strategy has .status not .enabled
        running_count = sum(1 for s in strats if getattr(s, 'status', '') == 'active')
        paused_count = active_count - running_count

        strat_text = (
            f"<b>Strategy Health</b>\n"
            f"  Active: {active_count}\n"
            f"  Running: {running_count}\n"
            f"  Paused: {paused_count}\n"
        )
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): strategies fetch wrapper. db.get_strategies()
        # may surface aiosqlite.Error + AttributeError on missing engine.db
        # link. Admin-only; truncated exc str is acceptable for operator.
        logger.error(f"Failed to fetch strategies: {e}")
        strat_text = (
            f"<b>Strategy Health</b>\n"
            f"  Error fetching strategies: {esc(str(e)[:50])}\n"
        )
    strat_text += "\n"

    # ══ 5. WEBSOCKET HEALTH ══
    ws_fresh = engine._is_ws_fresh()
    ws_emoji = "🟢" if ws_fresh else "⚫"

    # Calculate WS tick age
    ws_age_secs = 999.0
    if (engine.scanner.ws and
        hasattr(engine.scanner.ws, '_last_msg_ts') and
        engine.scanner.ws._last_msg_ts):
        ws_age_secs = time.time() - engine.scanner.ws._last_msg_ts

    ws_connected = (engine.scanner.ws and
                    hasattr(engine.scanner.ws, 'is_connected') and
                    engine.scanner.ws.is_connected)
    ws_conn_emoji = "✅" if ws_connected else "❌"

    ws_text = (
        f"<b>WebSocket Health</b>\n"
        f"  Connected: {ws_conn_emoji}\n"
        f"  Fresh: {ws_emoji}\n"
        f"  Last tick: {ws_age_secs:.1f}s ago\n"
        f"  Stale threshold: {ws_stale_secs:.1f}s\n"
    )
    ws_text += "\n"

    # ══ 6. ENGINE CYCLE INFO ══
    cycle_text = (
        f"<b>Engine State</b>\n"
        f"  Cycle: {engine._cycle}\n"
        f"  Pending orders: {len(engine._pending)}\n"
        f"  Now (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n"
    )

    # ══ 7. BG TASK REGISTRY (Phase 82e Sprint 2.1) ══
    # Surfaces silent task failures: running/failed/cancelled state per
    # wrapped bg task + last 5 errors (newest first). Only populated after
    # safe_create_task() has been called for each task.
    bg_text = _build_bg_tasks_section()

    # ══ 8. HYPEROPT STATUS (Phase 82e Sprint 2.3) ══
    # Surfaces parent-side HyperoptProgressState so the user can see
    # whether a background hyperopt run is stuck (last_status + age) or
    # finished (last_run_summary) without having to call /hyperopt_status.
    ho_text = _build_hyperopt_section()

    # Combine all sections
    full_text = (
        f"🔧 <b>Trade Pipeline Diagnostics</b>\n\n"
        f"{skip_text}"
        f"{risk_text}"
        f"{env_text}"
        f"{strat_text}"
        f"{ws_text}"
        f"{cycle_text}"
        f"\n{bg_text}"
        f"{ho_text}"
    )
    # Telegram hard limit 4096 — truncate to leave room for markup errors
    if len(full_text) > 3950:
        full_text = full_text[:3900] + "\n\n... (truncated)"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="show_diagnose")],
        [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
    ])

    await update.message.reply_text(
        full_text,
        parse_mode="HTML",
        reply_markup=kb
    )


async def diagnose_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback for /diagnose refresh button.
    Re-evaluate and update the message inline.
    """
    q = update.callback_query
    await q.answer()

    # Re-run the diagnostic command logic via the message
    # Create a mock update that points to the same message
    # (this avoids code duplication)

    engine = context.bot_data.get("engine")
    if not engine:
        return await q.edit_message_text("Engine çalışmıyor.")

    # ══ 1. SKIP COUNTER ══
    skip_counts = engine.skips._counts.copy() if hasattr(engine.skips, '_counts') else {}
    skip_total = engine.skips._total if hasattr(engine.skips, '_total') else 0
    skip_items = sorted(skip_counts.items(), key=lambda x: -x[1])

    skip_text = f"<b>Trade Filtering Pipeline (this heartbeat)</b>\n"
    skip_text += f"Total skips: <b>{skip_total}</b>\n"
    if skip_items:
        for reason, count in skip_items:
            skip_text += f"  • {esc(reason)}: {count}\n"
    else:
        skip_text += "  No skips recorded\n"
    skip_text += "\n"

    # ══ 2. RISK STATE ══
    rs = engine.risk.get_status()
    limits = rs.get("limits", {})

    # T6.3-B halt_text fix -- bkz. _build_diagnose_text yorumu yukarida
    halt_emoji = "🛑" if rs.get("halted", False) else "✅"
    halt_text = rs.get("halt_reason", "Halted") if rs.get("halted") else "No halt"

    ks = engine.kill_switch.get_status()
    kill_emoji = "🛑" if ks.get("killed", False) else "✅"
    kill_text = ks.get("reason", "N/A") if ks.get("killed") else "Inactive"

    daily_pnl = rs.get("daily_pnl", 0.0)
    pnl_emoji = "📈" if daily_pnl >= 0 else "📉"

    risk_text = (
        f"<b>Risk State</b>\n"
        f"  Kill: {kill_emoji} {esc(kill_text)}\n"
        f"  Halt: {halt_emoji} {esc(halt_text)}\n"
        f"  Open: {rs.get('open_positions', 0)}/{limits.get('max_positions', 0)}\n"
        f"  Exp: {fmt_usd(rs.get('total_exposure', 0.0), decimals=1)} / "
        f"{fmt_usd(limits.get('max_exposure', 0.0), decimals=0)}\n"
        f"  {pnl_emoji} Daily PnL: {fmt_usd(daily_pnl, sign=True)}\n"
        f"  Trades: {rs.get('daily_trades', 0)}\n"
        f"  Loss streak: {rs.get('loss_streak', 0)}\n"
    )
    risk_text += "\n"

    # ══ 3. ENV THRESHOLDS ══
    max_positions = int(os.getenv("MAX_OPEN_POSITIONS", "10"))
    max_exposure = float(os.getenv("MAX_TOTAL_EXPOSURE", "5000"))
    max_daily_loss = float(os.getenv("MAX_DAILY_LOSS", "500"))
    max_loss_streak = int(os.getenv("MAX_LOSS_STREAK", "5"))
    min_order_usd = float(os.getenv("MIN_ORDER_USD", "2.0"))
    min_order_shares = float(os.getenv("MIN_ORDER_SHARES", "1.0"))
    ws_stale_secs = float(os.getenv("WS_STALE_THRESHOLD", "60.0"))

    env_text = (
        f"<b>Environment Thresholds</b>\n"
        f"  MAX_OPEN_POSITIONS: {max_positions}\n"
        f"  MAX_TOTAL_EXPOSURE: {fmt_usd(max_exposure, decimals=0)}\n"
        f"  MAX_DAILY_LOSS: {fmt_usd(max_daily_loss, decimals=0)}\n"
        f"  MAX_LOSS_STREAK: {max_loss_streak}\n"
        f"  MIN_ORDER_USD: {fmt_usd(min_order_usd, decimals=2)}\n"
        f"  MIN_ORDER_SHARES: {min_order_shares}\n"
        f"  WS_STALE_THRESHOLD: {ws_stale_secs:.1f}s\n"
    )
    env_text += "\n"

    # ══ 4. STRATEGY HEALTH ══
    try:
        strats = await engine.db.get_active_strategies()
        active_count = len(strats)
        # Phase 78-fix: Strategy has .status not .enabled
        running_count = sum(1 for s in strats if getattr(s, 'status', '') == 'active')
        paused_count = active_count - running_count

        strat_text = (
            f"<b>Strategy Health</b>\n"
            f"  Active: {active_count}\n"
            f"  Running: {running_count}\n"
            f"  Paused: {paused_count}\n"
        )
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): callback strategies fetch — same wide-catch
        # pattern as command path; admin-only diagnostic.
        logger.error(f"Failed to fetch strategies: {e}")
        strat_text = (
            f"<b>Strategy Health</b>\n"
            f"  Error: {esc(str(e)[:50])}\n"
        )
    strat_text += "\n"

    # ══ 5. WEBSOCKET HEALTH ══
    ws_fresh = engine._is_ws_fresh()
    ws_emoji = "🟢" if ws_fresh else "⚫"

    ws_age_secs = 999.0
    if (engine.scanner.ws and
        hasattr(engine.scanner.ws, '_last_msg_ts') and
        engine.scanner.ws._last_msg_ts):
        ws_age_secs = time.time() - engine.scanner.ws._last_msg_ts

    ws_connected = (engine.scanner.ws and
                    hasattr(engine.scanner.ws, 'is_connected') and
                    engine.scanner.ws.is_connected)
    ws_conn_emoji = "✅" if ws_connected else "❌"

    ws_text = (
        f"<b>WebSocket Health</b>\n"
        f"  Connected: {ws_conn_emoji}\n"
        f"  Fresh: {ws_emoji}\n"
        f"  Last tick: {ws_age_secs:.1f}s ago\n"
        f"  Stale threshold: {ws_stale_secs:.1f}s\n"
    )
    ws_text += "\n"

    # ══ 6. ENGINE CYCLE INFO ══
    cycle_text = (
        f"<b>Engine State</b>\n"
        f"  Cycle: {engine._cycle}\n"
        f"  Pending orders: {len(engine._pending)}\n"
        f"  Now (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n"
    )

    # ══ 7. BG TASK REGISTRY (Phase 82e Sprint 2.1) ══
    bg_text = _build_bg_tasks_section()

    # ══ 8. HYPEROPT STATUS (Phase 82e Sprint 2.3) ══
    ho_text = _build_hyperopt_section()

    full_text = (
        f"🔧 <b>Trade Pipeline Diagnostics</b>\n\n"
        f"{skip_text}"
        f"{risk_text}"
        f"{env_text}"
        f"{strat_text}"
        f"{ws_text}"
        f"{cycle_text}"
        f"\n{bg_text}"
        f"{ho_text}"
    )
    if len(full_text) > 3950:
        full_text = full_text[:3900] + "\n\n... (truncated)"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="show_diagnose")],
        [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
    ])

    await q.edit_message_text(
        full_text,
        parse_mode="HTML",
        reply_markup=kb
    )
