"""PolyPaper Bot - /live_guards Handler (Epic 11 T11.2 [D])
============================================================
Admin diagnostic command rendering the 6 live trading guards in a
single view so the operator can see the state AND the current
thresholds without having to remember each ENV name or climb through
``/risk``, ``/diagnose`` and ``/ws``.

Guards covered
--------------
G1  Kill Switch        — ``core.kill_switch.KillSwitch``
G2  Live Budget        — ``LIVE_BUDGET`` env (lifetime cap) +
                          ``_total_spent`` cumulative
G3  Daily Loss         — ``LIVE_MAX_DAILY_LOSS`` env +
                          ``_daily_pnl`` running tally
G4  PnL Divergence     — ``PNL_DIVERGENCE_ENABLED`` / _WINDOW_H /
                          _ALERT_PCT / _MIN_TRADES env
G5  Rolling WR Kill    — ``ROLLING_WR_WINDOW`` + ``ROLLING_WR_KILL``
                          env (auto_optimizer helpers)
G6  WS Stale           — ``WS_STALE_THRESHOLD`` env + live WS age

Every threshold is re-read **on every command invocation** so a fresh
``/envt LIVE_BUDGET 5.00`` takes effect without a restart. This is the
T6.1 runtime-read doctrine mirrored at the UI layer — the command
renders ``_get_*()`` helper return values, not frozen module-level
constants.

ADMIN ONLY — leaks budget + daily loss + kill-reason text.
"""

from __future__ import annotations

import logging
import os
import time

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import Settings
from telegram_bot.templates.safe_html import esc, fmt_usd

logger = logging.getLogger("polypaper.handlers.live_guards")


def _is_admin(context, telegram_id: int) -> bool:
    """T10.2 admin-gate parity — mirrors ``diagnose_handler._is_admin``.

    Never falls through to ``True``; if ``settings`` is missing from
    ``bot_data`` the caller is denied. This is a defensive invariant:
    a misconfigured boot must not open an info-leak side-door.
    """
    settings: Settings = context.bot_data.get("settings")
    if not settings:
        logger.warning(f"live_guards _is_admin: settings missing, denying user {telegram_id}")
        return False
    return settings.is_admin(telegram_id)


def _fmt_bool(flag: bool, true_label: str = "ON", false_label: str = "OFF") -> str:
    return true_label if flag else false_label


def build_guards_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    """6-guard live trading snapshot metnini üretir (saf render).

    2026-05-19 (`/live` Faz 2B): `live_guards_command` içinden ayrıldı —
    artık `/lg` komutu VE `/live` kokpitinin "🛡 Guards" paneli aynı
    builder'ı kullanır (tek kaynak, UI drift yok). Runtime ENV her
    çağrıda yeniden okunur (T6.3 ghost-flag doktrini).

    Returns: Telegram HTML metni (4096 limitine karşı truncate'li).
    """
    lines: list[str] = ["🛡️ <b>Live Guards — Runtime Snapshot</b>\n"]

    # ── Master switch ─────────────────────────────────────────────
    live_enabled = os.getenv("LIVE_ENABLED", "false").lower() == "true"
    master_emoji = "🟢" if live_enabled else "🟡"
    lines.append(
        f"<b>Master</b>\n"
        f"  {master_emoji} LIVE_ENABLED = "
        f"<code>{_fmt_bool(live_enabled, 'true', 'false')}</code>\n"
    )

    engine = context.bot_data.get("engine")

    # ── G1 — Kill Switch ──────────────────────────────────────────
    # Channels: 1) file @ data_store/polypaper.stop, 2) in-memory,
    # 3) Telegram /kill /resume. Renders get_status() which exposes
    # all three channels so the operator can diagnose which source
    # tripped. When engine is not booted (sandbox /test), falls back
    # to an ephemeral KillSwitch instance so file-based channel is
    # still readable.
    try:
        if engine and hasattr(engine, "kill_switch"):
            ks = engine.kill_switch.get_status()
        else:
            from core.kill_switch import KillSwitch

            ks = KillSwitch().get_status()
        kill_emoji = "🛑" if ks.get("killed") else "✅"
        kill_reason = ks.get("reason") or "Inactive"
        file_emoji = "📄" if ks.get("file_exists") else "∅"
        mem_emoji = "🧠" if ks.get("memory_flag") else "∅"
        lines.append(
            f"<b>G1 Kill Switch</b>\n"
            f"  State: {kill_emoji} {esc(kill_reason[:60])}\n"
            f"  File:  {file_emoji} <code>{esc(str(ks.get('file_path', '?')))}</code>\n"
            f"  Mem:   {mem_emoji}\n"
        )
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): per-guard render block intentionally wide.
        # Each guard touches different engine internals (kill_switch attrs,
        # file paths, mem state); wide catch ensures one failure doesn't
        # blank the whole /live_guards snapshot. Admin-only diagnostic.
        logger.exception("live_guards: G1 render failed")
        lines.append(f"<b>G1 Kill Switch</b>\n  ⚠️ render error: {esc(str(e)[:60])}\n")

    # ── G2 — Live Budget ──────────────────────────────────────────
    # ``_get_live_budget()`` re-reads LIVE_BUDGET per call (T11.2 [B]).
    # ``_total_spent`` is the cumulative live mirror spend persisted
    # via LiveTrader._save_state. ``remaining`` is rendered directly
    # from get_status() so we show the same arithmetic the budget gate
    # uses at ``maybe_mirror`` time.
    try:
        from core.live_trader import _get_live_budget

        budget = _get_live_budget()
        if engine and hasattr(engine, "live"):
            ls = engine.live.get_status()
            spent = float(ls.get("total_spent", 0.0))
            remaining = float(ls.get("remaining", budget - spent))
        else:
            spent = 0.0
            remaining = budget
        pct_used = (spent / budget * 100.0) if budget > 0 else 0.0
        budget_emoji = "🟢" if pct_used < 80 else ("🟡" if pct_used < 100 else "🛑")
        lines.append(
            f"<b>G2 Live Budget</b>\n"
            f"  {budget_emoji} LIVE_BUDGET = {fmt_usd(budget, decimals=2)}\n"
            f"  Spent:     {fmt_usd(spent, decimals=2)} "
            f"({pct_used:.0f}%)\n"
            f"  Remaining: {fmt_usd(remaining, decimals=2)}\n"
        )
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): per-guard render — see G1 for rationale.
        logger.exception("live_guards: G2 render failed")
        lines.append(f"<b>G2 Live Budget</b>\n  ⚠️ render error: {esc(str(e)[:60])}\n")

    # ── G3 — Daily Loss ───────────────────────────────────────────
    # ``_get_max_daily_loss()`` re-reads LIVE_MAX_DAILY_LOSS.
    # ``_daily_pnl`` is rolling within the calendar day (reset at UTC
    # midnight by LiveTrader._rollover_daily). When _daily_pnl <=
    # -LIVE_MAX_DAILY_LOSS, ``maybe_mirror`` halts (not pause).
    try:
        from core.live_trader import _get_max_daily_loss

        max_daily_loss = _get_max_daily_loss()
        if engine and hasattr(engine, "live"):
            ls = engine.live.get_status()
            daily_pnl = float(ls.get("daily_pnl", 0.0))
            daily_trades = int(ls.get("daily_trades", 0))
        else:
            daily_pnl = 0.0
            daily_trades = 0
        # Halt trigger: daily_pnl <= -max_daily_loss
        halted = daily_pnl <= -max_daily_loss and max_daily_loss > 0
        dl_emoji = "🛑" if halted else ("📉" if daily_pnl < 0 else "📈")
        lines.append(
            f"<b>G3 Daily Loss</b>\n"
            f"  {dl_emoji} LIVE_MAX_DAILY_LOSS = "
            f"{fmt_usd(max_daily_loss, decimals=2)}\n"
            f"  Today PnL:    {fmt_usd(daily_pnl, decimals=4, sign=True)}\n"
            f"  Today trades: {daily_trades}\n"
        )
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): per-guard render — see G1 for rationale.
        logger.exception("live_guards: G3 render failed")
        lines.append(f"<b>G3 Daily Loss</b>\n  ⚠️ render error: {esc(str(e)[:60])}\n")

    # ── G4 — PnL Divergence (Paper ↔ Shadow) ──────────────────────
    # Job lives at ``telegram_bot/jobs/pnl_divergence_job.py``; ENV is
    # read inside the JobQueue callback on every tick. We render the
    # exact same env reads here so the operator can confirm a /envt
    # patch took effect before waiting for the next tick.
    pnl_div_enabled = os.getenv("PNL_DIVERGENCE_ENABLED", "true").lower() == "true"
    pnl_div_window_h = float(os.getenv("PNL_DIVERGENCE_WINDOW_H", "24"))
    pnl_div_alert_pct = float(os.getenv("PNL_DIVERGENCE_ALERT_PCT", "5.0"))
    pnl_div_min_trades = int(os.getenv("PNL_DIVERGENCE_MIN_TRADES", "5"))
    pnl_div_emoji = "🟢" if pnl_div_enabled else "⚫"
    lines.append(
        f"<b>G4 PnL Divergence</b>\n"
        f"  {pnl_div_emoji} Enabled = "
        f"<code>{_fmt_bool(pnl_div_enabled, 'true', 'false')}</code>\n"
        f"  Window:     {pnl_div_window_h:.1f}h\n"
        f"  Alert ≥:    {pnl_div_alert_pct:.2f}%\n"
        f"  Min trades: {pnl_div_min_trades}\n"
    )

    # ── G5 — Rolling WR Kill ──────────────────────────────────────
    # Helpers from ``core.auto_optimizer`` (T6.4 runtime-read). These
    # values drive ``_check_rolling_wr`` every optimizer tick. Protected
    # strategies (classic type) bypass this guard by design.
    try:
        from core.auto_optimizer import (
            _get_rolling_wr_kill_threshold,
            _get_rolling_wr_window,
        )

        wr_window = _get_rolling_wr_window()
        wr_kill = _get_rolling_wr_kill_threshold()
        protected = os.getenv("PROTECTED_STRATEGY_TYPES", "classic")
        lines.append(
            f"<b>G5 Rolling WR Kill</b>\n"
            f"  Window:    last {wr_window} trades\n"
            f"  Kill if WR &lt; {wr_kill:.1f}%\n"
            f"  Protected: <code>{esc(protected)}</code>\n"
        )
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): per-guard render — see G1 for rationale.
        logger.exception("live_guards: G5 render failed")
        lines.append(f"<b>G5 Rolling WR Kill</b>\n  ⚠️ render error: {esc(str(e)[:60])}\n")

    # ── G6 — WebSocket Staleness ──────────────────────────────────
    # Threshold is env-driven. Current age comes from engine scanner
    # WS ``_last_msg_ts`` (same source as ``_is_ws_fresh``). If engine
    # absent (sandbox), only the threshold is rendered.
    try:
        ws_threshold = float(os.getenv("WS_STALE_THRESHOLD", "60.0"))
        ws_age = None
        ws_fresh = None
        if engine and hasattr(engine, "scanner"):
            ws = getattr(engine.scanner, "ws", None)
            if ws and hasattr(ws, "_last_msg_ts") and ws._last_msg_ts:
                ws_age = time.time() - ws._last_msg_ts
                ws_fresh = ws_age < ws_threshold
        if ws_fresh is True:
            ws_emoji = "🟢"
        elif ws_fresh is False:
            ws_emoji = "🛑"
        else:
            ws_emoji = "⚫"  # unknown / not booted
        age_str = f"{ws_age:.1f}s" if ws_age is not None else "n/a"
        lines.append(
            f"<b>G6 WS Stale</b>\n"
            f"  {ws_emoji} WS_STALE_THRESHOLD = {ws_threshold:.1f}s\n"
            f"  Last tick age: {age_str}\n"
        )
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): per-guard render — see G1 for rationale.
        logger.exception("live_guards: G6 render failed")
        lines.append(f"<b>G6 WS Stale</b>\n  ⚠️ render error: {esc(str(e)[:60])}\n")

    # ── Hint footer ───────────────────────────────────────────────
    # Note: use square brackets instead of &lt;KEY&gt;. Telegram HTML parse
    # sometimes mangles `<KEY>` even after entity-escape (treats as unknown
    # tag), eating the preceding "/e" of "/envt". [KEY]/[VALUE] renders
    # cleanly in every client.
    lines.append("\n<i>Tune any threshold at runtime with <code>/envt [KEY] [VALUE]</code>.</i>")

    full_text = "\n".join(lines)
    # Telegram hard limit 4096 — truncate defensively
    if len(full_text) > 3950:
        full_text = full_text[:3900] + "\n\n... (truncated)"
    return full_text


async def live_guards_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """``/live_guards`` (alias ``/lg``) — 6-guard live trading snapshot.

    Renders runtime values for all 6 Epic 11 T11.2 guards + the
    ``LIVE_ENABLED`` master switch. Uses the same runtime helpers
    that the guard sites themselves use so UI and engine can never
    drift (T6.3 ghost-flag doctrine).
    """
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    await update.message.reply_text(build_guards_text(context), parse_mode="HTML")
