"""
Phase 67 / Phase 82b / Phase 82e Sprint 1.3:
  /hyperopt, /hyperopt_all, /hyperopt_status, /mc_kelly
===========================================================================

/hyperopt [strategy] [trials]  — Optuna hyperopt for a single strategy
/hyperopt_all [trials]         — Sweep all strategies
/hyperopt_status               — Show current hyperopt worker status
/mc_kelly [wr] [price]         — Monte Carlo Kelly validation

Usage:
    /hyperopt hour_edge 50
    /hyperopt_all 30
    /mc_kelly 0.57 0.65
    /mc_kelly               → uses current bot stats

Phase 82b: /hyperopt and /hyperopt_all now launch a subprocess worker
(backtest.hyperopt_worker) instead of running in the main event loop. This
protects the Telegram loop from nest_asyncio/thread-dance crashes and from
memory pressure. Progress is streamed back over stdout as JSON IPC events
(backtest.hyperopt_ipc.IPCEvent) and rendered via live message edits.

Phase 82e Sprint 1.3: all subprocess kill paths now route through
``backtest.hyperopt_launcher._terminate_subprocess`` (SIGTERM → grace →
SIGKILL escalation). The run loop is additionally wrapped in an outer
``asyncio.wait_for`` guard (``study_timeout * strat_count + 300s``) so a
pathological worker that streams IPC forever without STRAT_DONE can no
longer hold the Telegram handler open indefinitely.

T11.8-B (2026-04-24): Every catch in this module is annotated `# noqa:
BLE001`. Hyperopt subprocess management touches: subprocess (returncode,
SIGTERM/KILL), JSON IPC parsing (malformed STRAT_DONE/PROGRESS payloads),
asyncio.wait_for timeouts, telegram edit_message no-op, DB persist of
best_params, and Optuna study restoration — heterogeneous failure surface
across 5+ libraries. Wide catch + admin-only diagnostic acceptable per
T11.6 policy.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from backtest.hyperopt_ipc import (
    EventType,
    IPCEvent,
    PidFileLock,
    HyperoptProgressState,
    StratDoneInfo,
    format_eta_hybrid,
)
# Phase 82e Sprint 1.3: shared graceful-kill helper
from backtest.hyperopt_launcher import _terminate_subprocess
# Epic 10 T10.6 (2026-04-22): admin gate helpers for state-mutating callbacks.
# hyperopt_apply_callback UPDATE'i yaptığı için T10.2 pattern'ine tabi.
from telegram_bot.handlers.strategies import _is_admin_call, _deny_callback

logger = logging.getLogger("polypaper.handler.hyperopt")

# Phase 79 S1-12: Cancel mechanism for heavy commands
# Maps chat_id -> asyncio.Event to signal cancellation
_cancel_events: dict[int, asyncio.Event] = {}

# Sprint 3 S3-05: Pending hyperopt results waiting for "Apply" button
_pending_hyperopt: dict[int, dict] = {}  # chat_id -> {strategy_name, best_params, best_score}

# ══════════════════════════════════════════════════════════════════════
# Phase 82b singletons
# ══════════════════════════════════════════════════════════════════════

# Progress state used by /hyperopt, /hyperopt_all, /hyperopt_status.
_progress_state = HyperoptProgressState()

# In-process mutex — prevents two concurrent subprocess launches from the
# same bot process. Complements the cross-process PidFileLock.
_inproc_lock = asyncio.Lock()

# Subprocess tunables (ENV-overridable)
_SUBPROCESS_STALL_SEC = int(os.getenv("HYPEROPT_SUBPROCESS_STALL_SEC", "120"))
_PROGRESS_EDIT_COOLDOWN = float(os.getenv("HYPEROPT_EDIT_COOLDOWN_SEC", "1.0"))

# Phase 82e Sprint 1.3: per-strategy study budget + outer-guard slack.
# The outer wait_for wrapper uses ``study_timeout * strat_count + slack``
# so a worker that emits IPC forever without STRAT_DONE cannot hold the
# Telegram handler open indefinitely.
_STUDY_TIMEOUT_SEC = int(os.getenv("HYPEROPT_STUDY_TIMEOUT_SEC", "3600"))
_OUTER_GUARD_SLACK_SEC = int(os.getenv("HYPEROPT_OUTER_GUARD_BUFFER_SEC", "300"))

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOCK_PATH = str(_PROJECT_ROOT / ".hyperopt.lock")


# ══════════════════════════════════════════════════════════════════════
# Phase 82b.4 — subprocess stderr pump
# ══════════════════════════════════════════════════════════════════════
# The hyperopt worker writes all Python logging (INFO/WARNING/ERROR) to
# stderr because stdout is reserved for IPC JSON events. Prior to 82b.4
# stderr was piped but never read, so subprocess log messages (e.g.
# "HyperOpt: discovered N market windows in X.Xs" from the window cache)
# never reached polypaper.log. This pump drains stderr line-by-line and
# re-emits through the parent's logger so we can actually see what the
# worker is doing.
async def _pump_subprocess_stderr(proc, pid: int) -> None:
    """Drain subprocess stderr to polypaper.log. Runs until EOF."""
    try:
        while True:
            raw = await proc.stderr.readline()
            if not raw:
                break  # EOF — subprocess exited
            text = raw.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            # Classify by the worker log format prefix to pick the right level.
            if "[worker:ERROR]" in text or " ERROR " in text:
                logger.error("hyperopt stderr [pid=%s]: %s", pid, text)
            elif "[worker:WARNING]" in text or " WARNING " in text:
                logger.warning("hyperopt stderr [pid=%s]: %s", pid, text)
            else:
                logger.info("hyperopt stderr [pid=%s]: %s", pid, text)
    except Exception as e:  # noqa: BLE001
        logger.debug("hyperopt stderr pump exited: %s", e)


def _parse_hyperopt_args(args: list[str]) -> dict:
    """Phase 81: Parse /hyperopt args including --last, --from, --random flags.

    Phase 82e Sprint 5 (FINAL): also parses --asset / --tf (alias: --timeframe)
    for Fusion×29 granular apply. If both are set, the worker optimizes on
    markets matching (asset, timeframe) and the DB row tags this so the
    apply callback can target every matching live strategy.

    Usage:
        /hyperopt hour_edge 50 --last 30
        /hyperopt momentum 100 --from 2026-04-10
        /hyperopt fusion 50 --asset BTC --tf 5m
        /hyperopt fusion 50 --random 80 --asset ETH --timeframe 15m
    """
    result = {"strategy": "late_convergence", "n_trials": 50,
              "last_n": 0, "from_date": "", "random_n": 0,
              "asset": "", "timeframe": ""}
    positional = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--last" and i + 1 < len(args):
            result["last_n"] = int(args[i + 1])
            i += 2
        elif a == "--from" and i + 1 < len(args):
            result["from_date"] = args[i + 1]
            i += 2
        elif a == "--random" and i + 1 < len(args):
            result["random_n"] = int(args[i + 1])
            i += 2
        elif a == "--asset" and i + 1 < len(args):
            result["asset"] = args[i + 1].strip().upper()
            i += 2
        elif a in ("--tf", "--timeframe") and i + 1 < len(args):
            result["timeframe"] = args[i + 1].strip()
            i += 2
        else:
            positional.append(a)
            i += 1
    if len(positional) > 0:
        result["strategy"] = positional[0]
    if len(positional) > 1:
        try:
            result["n_trials"] = int(positional[1])
        except ValueError:
            pass
    return result


async def _run_hyperopt_worker(
    update: Update,
    mode: str,
    strategy: Optional[str],
    strategies: Optional[list[str]],
    n_trials: int,
    filter_info: str = "",
    asset: str = "",
    timeframe: str = "",
) -> list[StratDoneInfo]:
    """Phase 82b: launch the hyperopt subprocess worker and stream IPC events.

    Returns the list of STRAT_DONE events collected (empty on failure / abort).

    Phase 82e Sprint 5 (FINAL): asset / timeframe flow into worker CLI so
    that Fusion×29-style per-slice optimizations save tagged rows that the
    apply callback can target precisely.
    """
    chat = update.effective_chat
    msg = update.message

    # ── Build subprocess command ──
    python_exe = sys.executable or "python"
    cmd = [python_exe, "-u", "-m", "backtest.hyperopt_worker",
           "--mode", mode, "--n-trials", str(n_trials),
           "--lock-path", _LOCK_PATH]
    if mode == "single" and strategy:
        cmd += ["--strategy", strategy]
    elif mode == "batch" and strategies:
        cmd += ["--strategies", ",".join(strategies)]
    # Phase 82e Sprint 5: forward slice filters if the caller set them.
    _asset = (asset or "").strip().upper()
    _tf = (timeframe or "").strip()
    if _asset:
        cmd += ["--asset", _asset]
    if _tf:
        cmd += ["--timeframe", _tf]

    # ── Header message ──
    if mode == "single":
        header = (f"🔬 HyperOpt başlıyor: <b>{strategy}</b> "
                  f"({n_trials} trial){filter_info}\n"
                  f"Subprocess PID alınıyor...")
    else:
        strat_count = len(strategies) if strategies else "tümü"
        header = (f"🔬 Batch HyperOpt başlıyor ({strat_count} strateji × {n_trials} trial)\n"
                  f"Subprocess PID alınıyor...")
    cancel_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ İptal", callback_data="cancel_hyperopt")
    ]])
    header_msg = await msg.reply_text(header, parse_mode="HTML", reply_markup=cancel_kb)
    _cancel_events[chat.id] = asyncio.Event()

    # ── Spawn subprocess ──
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(_PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("hyperopt subprocess spawn failed: %s", e, exc_info=True)
        await header_msg.edit_text(
            f"❌ HyperOpt subprocess başlatılamadı: <code>{e}</code>",
            parse_mode="HTML")
        _cancel_events.pop(chat.id, None)
        return []

    logger.info("hyperopt subprocess launched: pid=%s mode=%s", proc.pid, mode)

    # Phase 82b.4: start stderr pump so worker's Python logging reaches
    # polypaper.log. Before this, stderr was piped but silently discarded.
    _stderr_pump_task = asyncio.create_task(
        _pump_subprocess_stderr(proc, proc.pid))

    if mode == "single":
        _strats_total = 1
    else:
        _strats_total = len(strategies) if strategies else 0  # STARTED event will update
    _progress_state.start(
        mode=mode,
        strats_total=max(1, _strats_total),
        pid=proc.pid,
    )

    # ── Event loop: read IPC lines from stdout ──
    done_info: list[StratDoneInfo] = []
    last_event_time = time.monotonic()
    header_updated_pid = False

    # Phase 82e Sprint 1.3: outer budget = per-strat study * count + slack
    _strat_count_guard = max(1, _strats_total)
    _outer_budget_sec = _STUDY_TIMEOUT_SEC * _strat_count_guard + _OUTER_GUARD_SLACK_SEC
    _loop_start_ts = time.monotonic()

    async def _ipc_event_loop() -> None:
        """Inner loop: pumps IPC events. Used by outer wait_for guard.

        Uses ``nonlocal`` for mutating closure state because Python doesn't
        allow mutating captures from an inner async function directly.
        """
        nonlocal last_event_time, header_updated_pid, done_info
        while True:
            # User cancelled?
            cancel_evt = _cancel_events.get(chat.id)
            if cancel_evt and cancel_evt.is_set():
                logger.warning(
                    "hyperopt cancelled by user — escalating kill pid=%s",
                    proc.pid,
                )
                # Sprint 1.3: graceful → hard kill escalation
                await _terminate_subprocess(
                    proc, "telegram", reason="user_cancel"
                )
                await header_msg.edit_text(
                    "❌ HyperOpt iptal edildi.", parse_mode="HTML")
                return

            # Wait for next line with stall timeout
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=_SUBPROCESS_STALL_SEC,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "hyperopt subprocess stalled (no IPC for %ds) — "
                    "escalating kill pid=%s",
                    _SUBPROCESS_STALL_SEC, proc.pid,
                )
                # Sprint 1.3: graceful → hard kill escalation
                await _terminate_subprocess(
                    proc,
                    "telegram",
                    reason=f"stall_{_SUBPROCESS_STALL_SEC}s",
                )
                await msg.reply_text(
                    f"⚠️ HyperOpt subprocess {_SUBPROCESS_STALL_SEC}s boyunca "
                    f"cevap vermedi, öldürüldü (pid={proc.pid}).",
                    parse_mode="HTML")
                return

            if not line:
                return  # EOF — subprocess exited (Sprint 1.3: was break)

            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue

            evt = IPCEvent.parse(raw)
            if evt is None:
                # Non-IPC output (e.g. stray logger line) — log and continue
                logger.debug("hyperopt non-IPC stdout: %s", raw[:200])
                continue

            last_event_time = time.monotonic()
            _progress_state.update(evt)

            # Update header with pid once we see STARTED
            if evt.event == EventType.STARTED.value and not header_updated_pid:
                header_updated_pid = True
                try:
                    mode_label = "Tek strateji" if mode == "single" else "Batch"
                    strat_label = strategy or f"{len(strategies or [])} strateji"
                    await header_msg.edit_text(
                        f"🔬 HyperOpt çalışıyor: <b>{strat_label}</b> "
                        f"({n_trials} trial/strat) · pid=<code>{proc.pid}</code>\n"
                        f"Mod: {mode_label}{filter_info}",
                        parse_mode="HTML", reply_markup=cancel_kb)
                except Exception:  # noqa: BLE001
                    pass

            # Live-edit progress on TRIAL_DONE (cooldown-throttled)
            if evt.event == EventType.TRIAL_DONE.value:
                if _progress_state.can_push("trial_edit", int(_PROGRESS_EDIT_COOLDOWN)):
                    try:
                        pct = _progress_state.progress_pct
                        eta = _progress_state.eta_sec
                        strat_now = _progress_state.current_strat or ""
                        body = (
                            f"⏳ <b>{strat_now or mode}</b>\n"
                            f"Trial: {_progress_state.current_trial}/{_progress_state.current_total}"
                            f"  ·  Strat: {len(_progress_state.strats_done)}/{_progress_state.strats_total}\n"
                            f"ETA: {format_eta_hybrid(eta, pct)}"
                        )
                        await header_msg.edit_text(
                            body, parse_mode="HTML", reply_markup=cancel_kb)
                        _progress_state.mark_pushed("trial_edit")
                    except Exception as edit_err:  # noqa: BLE001
                        logger.debug("progress edit skipped: %s", edit_err)

            # Per-strategy done: send a separate summary message
            elif evt.event == EventType.STRAT_DONE.value:
                info = StratDoneInfo(
                    name=getattr(evt, "strat", "") or "?",
                    best_value=float(getattr(evt, "best_value", 0.0) or 0.0),
                    best_params=dict(getattr(evt, "best_params", {}) or {}),
                    elapsed_sec=float(getattr(evt, "elapsed_sec", 0.0) or 0.0),
                    trial_count=int(getattr(evt, "trial", 0) or 0),
                )
                done_info.append(info)
                try:
                    params_str = "\n".join(
                        f"  • <code>{k}</code>: {v}"
                        for k, v in list(info.best_params.items())[:8]
                    ) or "  (boş)"
                    await msg.reply_text(
                        f"✅ <b>{info.name}</b> tamamlandı\n"
                        f"Skor: <b>{info.best_value:.4f}</b>  ·  "
                        f"Trial: {info.trial_count}  ·  "
                        f"{info.elapsed_sec:.0f}s\n\n"
                        f"Best params:\n{params_str}",
                        parse_mode="HTML")
                except Exception:  # noqa: BLE001
                    pass

            elif evt.event == EventType.MEMORY_WARNING.value:
                try:
                    await msg.reply_text(
                        f"⚠️ Bellek uyarısı: "
                        f"<code>{getattr(evt, 'message', '')}</code>",
                        parse_mode="HTML")
                except Exception:  # noqa: BLE001
                    pass

            elif evt.event == EventType.MEMORY_CRITICAL.value:
                try:
                    await msg.reply_text(
                        f"🔥 Bellek kritik — gc tetiklendi: "
                        f"<code>{getattr(evt, 'message', '')}</code>",
                        parse_mode="HTML")
                except Exception:  # noqa: BLE001
                    pass

            elif evt.event == EventType.MEMORY_ABORT.value:
                try:
                    await msg.reply_text(
                        f"🛑 Bellek sınırı aşıldı — subprocess abort: "
                        f"<code>{getattr(evt, 'message', '')}</code>",
                        parse_mode="HTML")
                except Exception:  # noqa: BLE001
                    pass

            elif evt.event == EventType.TIMEOUT.value:
                logger.warning("worker timeout event: %s", getattr(evt, "message", ""))

            elif evt.event == EventType.STATUS.value:
                # Phase 82b.3: log worker config proof line to polypaper.log
                # so we can verify LAST_N / timeouts actually reached the
                # subprocess (stderr is piped but never read).
                logger.info(
                    "worker status [%s]: %s",
                    getattr(evt, "strat", "-") or "-",
                    getattr(evt, "message", "") or "",
                )
                # Sprint 2.3 — mirror into progress state so /diagnose shows
                # the last worker heartbeat (mode=idle would skip but active
                # run sets last_status/last_status_at).
                _progress_state.update(evt)

            elif evt.event == EventType.ERROR.value:
                try:
                    await msg.reply_text(
                        f"❌ Worker error: <code>{getattr(evt, 'message', '')}</code>",
                        parse_mode="HTML")
                except Exception:  # noqa: BLE001
                    pass

            elif evt.event == EventType.BATCH_DONE.value:
                # Worker will exit shortly after this; finish the loop on EOF.
                pass
        # end of _ipc_event_loop (exits on return / EOF / stall / cancel)

    # Phase 82e Sprint 1.3: outer guard -- even if IPC keeps flowing, the
    # handler cannot be held open indefinitely. Budget scales with strat
    # count so /hyperopt_all has enough room for legitimate long batches.
    try:
        try:
            await asyncio.wait_for(_ipc_event_loop(), timeout=_outer_budget_sec)
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - _loop_start_ts
            logger.error(
                "hyperopt outer budget %ds exhausted (elapsed=%.1fs) -- "
                "escalating kill pid=%s",
                _outer_budget_sec,
                elapsed,
                proc.pid,
            )
            await _terminate_subprocess(
                proc,
                "telegram",
                reason=f"outer_budget_{_outer_budget_sec}s",
            )
            try:
                await msg.reply_text(
                    f"⚠️ HyperOpt outer budget ({_outer_budget_sec}s) aşıldı, "
                    f"subprocess durduruldu (pid={proc.pid}).",
                    parse_mode="HTML",
                )
            except Exception:  # noqa: BLE001
                pass

        # ── Wait for clean exit (Sprint 1.3: helper handles escalation) ──
        try:
            await asyncio.wait_for(proc.wait(), timeout=15)
        except asyncio.TimeoutError:
            logger.warning(
                "hyperopt subprocess did not exit within 15s after EOF -- "
                "escalating kill pid=%s",
                proc.pid,
            )
            await _terminate_subprocess(
                proc, "telegram", reason="post_consume_wait_timeout"
            )

    finally:
        _progress_state.finalize()
        _cancel_events.pop(chat.id, None)
        # Phase 82b.4: ensure stderr pump is awaited or cancelled so the
        # task doesn't leak. Pump exits on its own when proc.stderr hits
        # EOF; this is belt-and-suspenders for abort paths.
        try:
            if not _stderr_pump_task.done():
                try:
                    await asyncio.wait_for(_stderr_pump_task, timeout=2.0)
                except asyncio.TimeoutError:
                    _stderr_pump_task.cancel()
        except Exception:  # noqa: BLE001
            pass

    return done_info


async def hyperopt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hyperopt — single-strategy hyperopt via subprocess worker (Phase 82b).

    Usage:
        /hyperopt hour_edge 50
        /hyperopt momentum 100 --last 30
        /hyperopt fusion 50 --from 2026-04-10
        /hyperopt contrarian 80 --random 60
    """
    try:
        db = context.application.bot_data.get("db")
        if not db:
            await update.message.reply_text("DB unavailable")
            return

        # In-process + cross-process lock check
        if _inproc_lock.locked():
            await update.message.reply_text(
                "⚠️ HyperOpt zaten çalışıyor. "
                "Durumu görmek için /hyperopt_status kullan.",
                parse_mode="HTML")
            return
        busy_info = PidFileLock(_LOCK_PATH).status()
        if busy_info and not busy_info.get("is_stale"):
            await update.message.reply_text(
                f"⚠️ HyperOpt başka bir süreçte çalışıyor "
                f"(pid=<code>{busy_info.get('pid')}</code>, "
                f"mode=<code>{busy_info.get('mode')}</code>). "
                f"/hyperopt_status ile takip edebilirsin.",
                parse_mode="HTML")
            return

        args = context.args or []
        parsed = _parse_hyperopt_args(args)
        strategy = parsed["strategy"]
        n_trials = parsed["n_trials"]

        # Build filter info line (note: filters --last/--from/--random are not
        # yet plumbed into the subprocess worker — Phase 82b scope)
        filter_parts = []
        if parsed["last_n"]:
            filter_parts.append(f"son {parsed['last_n']} market")
        if parsed["from_date"]:
            filter_parts.append(f"{parsed['from_date']}'den itibaren")
        if parsed["random_n"]:
            filter_parts.append(f"rastgele {parsed['random_n']} market")
        # Phase 82e Sprint 5 (FINAL): asset / tf ARE piped to worker
        if parsed.get("asset"):
            filter_parts.append(f"asset={parsed['asset']}")
        if parsed.get("timeframe"):
            filter_parts.append(f"tf={parsed['timeframe']}")
        filter_info = ("\nFiltre: " + ", ".join(filter_parts)) if filter_parts else ""

        async with _inproc_lock:
            done_info = await _run_hyperopt_worker(
                update, mode="single",
                strategy=strategy, strategies=None,
                n_trials=n_trials, filter_info=filter_info,
                asset=parsed.get("asset", ""),
                timeframe=parsed.get("timeframe", ""),
            )

        if not done_info:
            await update.message.reply_text(
                "ℹ️ HyperOpt hiç sonuç üretmedi. "
                "Log'a bak veya /hyperopt_status ile kontrol et.",
                parse_mode="HTML")
            return

        # Apply button for the first (only) strategy
        info = done_info[0]
        if info.best_params:
            chat_id = update.effective_chat.id
            _pending_hyperopt[chat_id] = {
                "strategy_name": info.name,
                "best_params": info.best_params,
                "best_score": info.best_value,
                "row_id": None,  # saved by subprocess, id not available here
                # Phase 82e Sprint 5 (FINAL): slice filter carries into
                # apply callback so UPDATE targets the correct instance(s).
                "asset": parsed.get("asset", ""),
                "timeframe": parsed.get("timeframe", ""),
            }
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Uygula", callback_data="hyperopt_apply"),
                InlineKeyboardButton("❌ Reddet", callback_data="hyperopt_reject"),
            ]])
            await update.message.reply_text(
                f"📥 <b>{info.name}</b> parametrelerini stratejiye uygulayalım mı?",
                parse_mode="HTML", reply_markup=kb)

    except ImportError as ie:
        await update.message.reply_text(f"⚠️ optuna yüklü değil: {ie}")
    except Exception as e:  # noqa: BLE001
        logger.error("/hyperopt failed: %s", e, exc_info=True)
        await update.message.reply_text(f"HyperOpt error: {e}")


async def hyperopt_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hyperopt_all — batch hyperopt all strategies via subprocess (Phase 82b)."""
    try:
        db = context.application.bot_data.get("db")
        if not db:
            await update.message.reply_text("DB unavailable")
            return

        if _inproc_lock.locked():
            await update.message.reply_text(
                "⚠️ HyperOpt zaten çalışıyor. /hyperopt_status ile kontrol et.",
                parse_mode="HTML")
            return
        busy_info = PidFileLock(_LOCK_PATH).status()
        if busy_info and not busy_info.get("is_stale"):
            await update.message.reply_text(
                f"⚠️ HyperOpt başka bir süreçte çalışıyor "
                f"(pid=<code>{busy_info.get('pid')}</code>, "
                f"mode=<code>{busy_info.get('mode')}</code>).",
                parse_mode="HTML")
            return

        args = context.args or []
        n_trials = int(args[0]) if len(args) > 0 else int(
            os.getenv("HYPEROPT_BATCH_TRIALS", "15"))

        async with _inproc_lock:
            done_info = await _run_hyperopt_worker(
                update, mode="batch",
                strategy=None, strategies=None,
                n_trials=n_trials,
            )

        # Final batch summary
        if done_info:
            rows = []
            for info in sorted(done_info, key=lambda x: x.best_value, reverse=True):
                rows.append(
                    f"• <b>{info.name}</b>  skor={info.best_value:.4f}  "
                    f"({info.trial_count}t · {info.elapsed_sec:.0f}s)"
                )
            total_elapsed = sum(i.elapsed_sec for i in done_info)
            summary = (
                f"📊 <b>Batch tamamlandı</b> · {len(done_info)} strateji · "
                f"toplam {total_elapsed:.0f}s\n\n" + "\n".join(rows)
            )
            try:
                await update.message.reply_text(summary, parse_mode="HTML")
            except Exception:  # noqa: BLE001
                # Long message; split
                await update.message.reply_text(
                    f"📊 Batch tamamlandı ({len(done_info)} strateji).",
                    parse_mode="HTML")
        else:
            await update.message.reply_text(
                "ℹ️ Batch HyperOpt hiç sonuç üretmedi.",
                parse_mode="HTML")

    except ImportError as ie:
        await update.message.reply_text(f"⚠️ optuna yüklü değil: {ie}")
    except Exception as e:  # noqa: BLE001
        logger.error("/hyperopt_all failed: %s", e, exc_info=True)
        await update.message.reply_text(f"Batch HyperOpt error: {e}")


async def hyperopt_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hyperopt_status — show current worker state (Phase 82b)."""
    try:
        lock_info = PidFileLock(_LOCK_PATH).status()
        state = _progress_state

        # Case A: active subprocess (lock held, not stale)
        if lock_info and not lock_info.get("is_stale") and state.active:
            pct = state.progress_pct
            eta = state.eta_sec
            strat = state.current_strat or "-"
            text = (
                f"🔬 <b>HyperOpt aktif</b>\n"
                f"Mod: <code>{state.mode}</code>  ·  "
                f"pid: <code>{lock_info.get('pid')}</code>\n"
                f"Strateji: <b>{strat}</b>\n"
                f"Trial: {state.current_trial}/{state.current_total}\n"
                f"Strat ilerleme: {len(state.strats_done)}/{state.strats_total}\n"
                f"Süre: {int(state.elapsed_sec)}s\n"
                f"ETA: {format_eta_hybrid(eta, pct)}"
            )
            await update.message.reply_text(text, parse_mode="HTML")
            return

        # Case B: lock held but progress state is clean → recent subprocess
        if lock_info and not lock_info.get("is_stale"):
            text = (
                f"🔬 HyperOpt lock tutuluyor ama ilerleme yayını yok.\n"
                f"pid=<code>{lock_info.get('pid')}</code>  "
                f"mode=<code>{lock_info.get('mode')}</code>\n"
                f"started=<code>{lock_info.get('started_at')}</code>"
            )
            await update.message.reply_text(text, parse_mode="HTML")
            return

        # Case C: stale lock
        if lock_info and lock_info.get("is_stale"):
            text = (
                f"⚠️ Eski (stale) lock bulundu: "
                f"pid=<code>{lock_info.get('pid')}</code>. "
                f"Bir sonraki /hyperopt çağrısı temizleyecek."
            )
            await update.message.reply_text(text, parse_mode="HTML")
            return

        # Case D: idle — show last summary if any
        if state.last_run_summary:
            s = state.last_run_summary
            top = s.get("top_strats", []) or []
            top_lines = "\n".join(
                f"  • {t.get('name')} — skor {t.get('best_value', 0):.4f} "
                f"({t.get('trial_count', 0)}t)"
                for t in top[:5]
            ) or "  (yok)"
            text = (
                f"ℹ️ HyperOpt <b>aktif değil</b>.\n\n"
                f"Son çalışma özeti:\n"
                f"• Mod: <code>{s.get('mode', '-')}</code>\n"
                f"• Tamamlanan strat: {s.get('strats_done', 0)}/{s.get('strats_total', 0)}\n"
                f"• Süre: {int(s.get('elapsed_sec', 0))}s\n"
                f"• Bitiş: <code>{s.get('ended_at', '-')}</code>\n\n"
                f"En iyi sonuçlar:\n{top_lines}"
            )
            await update.message.reply_text(text, parse_mode="HTML")
            return

        await update.message.reply_text(
            "ℹ️ HyperOpt <b>aktif değil</b>. /hyperopt veya /hyperopt_all ile başlat.",
            parse_mode="HTML")

    except Exception as e:  # noqa: BLE001
        logger.error("/hyperopt_status failed: %s", e, exc_info=True)
        await update.message.reply_text(f"status error: {e}")


# ══════════════════════════════════════════════════════════════════════
# Phase 82e Sprint 3.2 — /hyperopt_abort (admin lock override)
# ══════════════════════════════════════════════════════════════════════

def _is_admin(context, telegram_id: int) -> bool:
    """Mirror of diagnose_handler._is_admin for lock-override gating."""
    try:
        settings = context.bot_data.get("settings")
    except (AttributeError, KeyError):
        # T11.8-B (2026-04-24): narrow from bare Exception. bot_data is a
        # dict — KeyError if missing; AttributeError if bot not initialized.
        return False
    if not settings:
        return False
    try:
        return settings.is_admin(telegram_id)
    except (AttributeError, TypeError):
        # T11.8-B (2026-04-24): narrow from bare Exception. is_admin missing
        # on settings (older config) — deny by default.
        return False


async def hyperopt_abort_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hyperopt_abort — force-release a stuck hyperopt lock (admin only).

    Sprint 3.2: complements the stale_sec timeout fallback. If a worker
    was SIGKILLed (e.g. OOM) it can't run its cleanup — the lock file is
    orphaned until stale_sec (default 3600s) elapses. This command deletes
    the lock file unconditionally so the operator can relaunch immediately.

    Does NOT kill a running subprocess — use the /hyperopt inline "İptal"
    button for that. This only clears the lock file.
    """
    try:
        if not _is_admin(context, update.effective_user.id):
            await update.message.reply_text("⛔ Sadece admin komutu.")
            return

        lock = PidFileLock(_LOCK_PATH)
        status = lock.status()

        if status is None:
            await update.message.reply_text(
                "ℹ️ HyperOpt lock zaten yok — temizlenecek bir şey yok.",
                parse_mode="HTML")
            return

        pid = status.get("pid")
        mode = status.get("mode", "?")
        is_stale = status.get("is_stale", False)

        # Check whether the PID is actually alive before we force-release.
        # If it's live, warn the admin they're about to orphan a running
        # worker and make them re-issue with --force.
        args = context.args or []
        force = "--force" in args or "-f" in args

        if not is_stale and not force:
            await update.message.reply_text(
                f"⚠️ <b>Live HyperOpt bulundu</b>\n"
                f"pid=<code>{pid}</code>  mode=<code>{mode}</code>\n"
                f"Subprocess'i kesmek istiyorsan önce /hyperopt çağrısında "
                f"<b>❌ İptal</b> butonuna bas.\n\n"
                f"Gerçekten sadece lock dosyasını silmek istiyorsan:\n"
                f"<code>/hyperopt_abort --force</code>",
                parse_mode="HTML")
            return

        released = lock.force_release(reason=f"tg_abort_admin_{update.effective_user.id}")
        if released:
            # Also reset parent's in-memory state so /hyperopt_status reads clean.
            try:
                if _progress_state.active:
                    _progress_state.finalize(error="aborted_by_admin")
            except Exception as _fe:  # noqa: BLE001
                logger.debug("progress_state.finalize failed: %s", _fe)

            await update.message.reply_text(
                f"✅ Lock serbest bırakıldı.\n"
                f"eski pid=<code>{pid}</code>  mode=<code>{mode}</code>\n"
                f"stale={'yes' if is_stale else 'no'}  forced={'yes' if force else 'no'}",
                parse_mode="HTML")
        else:
            await update.message.reply_text(
                "❌ Lock silinemedi — dosya izinlerini veya disk durumunu kontrol et.",
                parse_mode="HTML")

    except Exception as e:  # noqa: BLE001
        logger.error("/hyperopt_abort failed: %s", e, exc_info=True)
        await update.message.reply_text(f"abort error: {e}")


async def mc_kelly_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mc_kelly command — Monte Carlo Kelly validation."""
    try:
        db = context.application.bot_data.get("db")
        args = context.args or []

        # Parse args or use defaults from bot stats
        if len(args) >= 2:
            win_rate = float(args[0])
            avg_price = float(args[1])
            bankroll = float(args[2]) if len(args) > 2 else 10000.0
        elif db:
            # Auto-detect from bot stats
            try:
                row = await db.conn.execute_fetchall(
                    """SELECT COUNT(*),
                              COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0),
                              COALESCE(AVG(execution_price), 0.65)
                       FROM executions WHERE result IS NOT NULL"""
                )
                total = row[0][0] if row else 0
                wins = row[0][1] if row else 0
                avg_price = row[0][2] if row else 0.65
                win_rate = wins / total if total > 0 else 0.57

                bal_row = await db.conn.execute_fetchall(
                    "SELECT balance FROM paper_wallet ORDER BY updated_at DESC LIMIT 1"
                )
                bankroll = bal_row[0][0] if bal_row else 10000.0
            except Exception:  # noqa: BLE001
                win_rate, avg_price, bankroll = 0.57, 0.65, 10000.0
        else:
            win_rate, avg_price, bankroll = 0.57, 0.65, 10000.0

        await update.message.reply_text(
            f"🎲 MC Kelly simülasyonu başlıyor...\n"
            f"WR: {win_rate:.1%} | Price: {avg_price:.2f} | "
            f"Bankroll: ${bankroll:,.0f}\n"
            f"Arka planda çalışıyor.",
            parse_mode="HTML",
        )

        from utils.mc_simulation import MonteCarloKelly
        mc = MonteCarloKelly(
            win_rate=win_rate,
            avg_entry_price=avg_price,
            initial_bankroll=bankroll,
        )

        # CPU-yoğun simulate() işini ayrı thread'te çalıştır
        result = await asyncio.to_thread(mc.simulate)
        text = result.format_telegram()
        await update.message.reply_text(text, parse_mode="HTML")

    except ImportError as ie:
        await update.message.reply_text(f"⚠️ numpy yüklü değil: {ie}")
    except Exception as e:  # noqa: BLE001
        logger.error("/mc_kelly failed: %s", e, exc_info=True)
        await update.message.reply_text(f"MC Kelly error: {e}")


# Phase 79 S1-12: Cancel callback handler for hyperopt operations
async def cancel_hyperopt_callback(update: Update,
                                  context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle cancel button for /hyperopt and /hyperopt_all operations."""
    chat_id = update.effective_chat.id
    evt = _cancel_events.get(chat_id)
    if evt:
        evt.set()
        await update.callback_query.answer("İptal işlemi başlatılıyor...")
        await update.callback_query.edit_message_text(
            "❌ <b>HyperOpt iptal edildi.</b>",
            parse_mode="HTML")
    else:
        await update.callback_query.answer("Aktif işlem yok.")


# ═══ Sprint 3 S3-05: HyperOpt Apply/Reject Callback ═══

# Allowed strategy params that can be updated from hyperopt
_ALLOWED_PARAMS = {
    "odds_threshold", "trade_amount", "stop_loss_percent", "stop_loss_odds",
    "take_profit_percent", "take_profit_odds",
    "minutes_before_end", "minutes_after_start", "price_difference",
}


# ═══ Phase 82d: HyperOpt Param Classifier ═══════════════════════
# HyperOpt üretimi paramlar 4 kategoriye ayrılır:
#   - db_column:    strategies tablosunda birebir kolon var (_ALLOWED_PARAMS)
#   - plugin_param: core/strategy_plugins.py CONFIGURABLE[stype] altında
#   - engine_gate:  underscore-prefix'li engine-level common param
#                   (şu an sadece _min_confidence canlı kullanımda)
#   - ignore:       _odds_threshold (dead param) veya bilinmeyen
# Bu sınıflandırıcı apply callback'te yönlendirme için kullanılır.

def _classify_param(param: str, strategy_type: str, registry) -> str:
    """Decide where a hyperopt-produced param should land.

    Returns one of: "db_column", "plugin_param", "engine_gate", "ignore".
    """
    # 1. Core DB column (existing _ALLOWED_PARAMS mechanism)
    if param in _ALLOWED_PARAMS:
        return "db_column"
    # 2. Plugin-specific param declared in CONFIGURABLE
    try:
        plugin_schema = registry.CONFIGURABLE.get(strategy_type, {})
        if param in plugin_schema:
            return "plugin_param"
    except Exception:  # noqa: BLE001
        pass
    # 3. Known engine-level gate (Karar B1)
    if param == "_min_confidence":
        return "engine_gate"
    # 4. Dead (Karar A1 → _odds_threshold) or unknown
    return "ignore"


async def hyperopt_apply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle hyperopt_apply / hyperopt_reject inline buttons.

    Epic 10 T10.6 (2026-04-22): admin gate eklendi. Bu callback,
    `strategies` tablosunu ve `hyperopt_results` tablosunu UPDATE eder
    (best_params apply + applied=2 reject). Gate yok iken herhangi bir
    Telegram user callback_data crafting ile state mutasyonu
    tetikleyebilirdi. T10.2 pattern'iyle (C3 strategy callbacks) aynı
    _is_admin_call()+_deny_callback() helper'ı kullanılıyor.
    """
    if not _is_admin_call(update):
        return await _deny_callback(update)
    q = update.callback_query
    await q.answer()
    chat_id = update.effective_chat.id
    pending = _pending_hyperopt.pop(chat_id, None)

    if not pending:
        try:
            await q.edit_message_text("⌛ HyperOpt sonucu süresi doldu.")
        except Exception:  # noqa: BLE001
            pass
        return

    if q.data == "hyperopt_reject":
        # Phase 80: Mark as rejected in DB
        row_id = pending.get("row_id")
        if row_id:
            try:
                db = context.application.bot_data.get("db")
                if db:
                    await db.conn.execute(
                        "UPDATE hyperopt_results SET applied=2 WHERE id=?", (row_id,))
                    await db.conn.commit()
            except Exception:  # noqa: BLE001
                pass
        try:
            await q.edit_message_text("❌ HyperOpt sonucu reddedildi.")
        except Exception:  # noqa: BLE001
            pass
        return

    # ═══ Phase 82d: Apply best_params via 4-way classifier ═══════════
    # HyperOpt paramları 4 kategoriye dağıtılır (bkz. _classify_param):
    #   db_column     → strategies tablosunda kolon UPDATE
    #   plugin_param  → registry.set_config (runtime) + strategy_params
    #                   JSON "plugin_params" anahtarı (persist)
    #   engine_gate   → strategy_params JSON "engine_gates" anahtarı (persist)
    #   ignore        → dead/bilinmeyen; yalnız log + Telegram ⏭ satırı
    # strategy_params JSON MERGE edilir: lifecycle top-level alanları
    # (phase, min_composite, ...) korunur.
    db = context.application.bot_data.get("db")
    if not db:
        await q.edit_message_text("⚠️ DB bulunamadi.")
        return

    # Registry (plugin runtime) — engine warm-up olmamışsa None olabilir
    engine = context.application.bot_data.get("engine")
    registry = getattr(engine, "plugins", None) if engine else None

    strategy_name = pending["strategy_name"]
    best_params = pending["best_params"]
    # Phase 82e Sprint 5 (FINAL): slice filter from /hyperopt --asset/--tf
    pending_asset = (pending.get("asset") or "").strip().upper()
    pending_tf = (pending.get("timeframe") or "").strip()

    # 4 bucket — Telegram özet + DB yazım akışı
    db_updates: list[tuple[str, object]] = []   # (param, value)
    plugin_updates: dict[str, object] = {}
    gate_updates: dict[str, object] = {}
    ignored: list[str] = []
    plugin_set_failures: list[str] = []

    try:
        # Phase 82e Sprint 5 (FINAL): granular matching.
        # Legacy: match label/strategy_type LIKE, take rows[0] → only one
        # fusion strategy updated. Now: match strategy_type exactly AND if
        # pending_asset/pending_tf set, further constrain; UPDATE ALL.
        # Fallback: if no rows match with filter, retry without asset/tf
        # so /hyperopt without --asset/--tf still works on legacy rows.
        if pending_asset and pending_tf:
            rows = await db.conn.execute_fetchall(
                "SELECT id, label, strategy_type, strategy_params, asset, timeframe "
                "FROM strategies "
                "WHERE strategy_type = ? AND asset = ? AND timeframe = ? "
                "AND status='active'",
                (strategy_name, pending_asset, pending_tf))
            match_scope = f"type={strategy_name} asset={pending_asset} tf={pending_tf}"
        else:
            rows = await db.conn.execute_fetchall(
                "SELECT id, label, strategy_type, strategy_params, asset, timeframe "
                "FROM strategies "
                "WHERE (strategy_type = ? OR label LIKE ?) AND status='active'",
                (strategy_name, f"%{strategy_name}%"))
            match_scope = f"type/label~={strategy_name}"

        if not rows:
            # Fallback: maybe the strategy doesn't have asset/tf set; try broader
            rows = await db.conn.execute_fetchall(
                "SELECT id, label, strategy_type, strategy_params, asset, timeframe "
                "FROM strategies "
                "WHERE (label LIKE ? OR strategy_type LIKE ?)",
                (f"%{strategy_name}%", f"%{strategy_name}%"))

        if not rows:
            await q.edit_message_text(
                f"⚠️ Strateji bulunamadi: {strategy_name}\n"
                f"Eşleşme: <code>{match_scope}</code>",
                parse_mode="HTML")
            return

        # Resolve stype from first row (all matches share stype by construction
        # when pending filter is set).
        stype = rows[0][2] or ""
        labels_applied = [r[1] for r in rows]

        # Classify every produced param (stype from first row — all matches
        # share it when asset/tf filter is active).
        for param, value in best_params.items():
            cat = _classify_param(param, stype, registry)
            if cat == "db_column":
                db_updates.append((param, value))
            elif cat == "plugin_param":
                plugin_updates[param] = value
            elif cat == "engine_gate":
                gate_updates[param] = value
            else:  # "ignore"
                ignored.append(param)

        # 1) DB column updates — apply to EVERY matching strategy row
        for sid_row in rows:
            sid_i = sid_row[0]
            for param, value in db_updates:
                await db.conn.execute(
                    f"UPDATE strategies SET {param}=? WHERE id=?",
                    (value, sid_i))

        # 2) Plugin runtime updates via registry.set_config (single call; the
        # registry holds shared class-level config for all instances of stype)
        if plugin_updates and registry is not None and stype:
            for param, value in list(plugin_updates.items()):
                try:
                    ok = registry.set_config(stype, param, value)
                    if not ok:
                        plugin_set_failures.append(param)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"HyperOpt set_config {stype}.{param} failed: {e}")
                    plugin_set_failures.append(param)
        elif plugin_updates and registry is None:
            logger.warning(
                "HyperOpt: registry unavailable — plugin params only "
                "persisted to DB, will apply on next bot startup")

        # 3) Persist plugin_params + engine_gates to strategy_params JSON
        #    for EACH matching strategy (MERGE per-row so lifecycle state
        #    is preserved independently).
        if plugin_updates or gate_updates:
            for sid_row in rows:
                sid_i = sid_row[0]
                existing_sp_raw = sid_row[3] or "{}"
                try:
                    existing_sp = json.loads(existing_sp_raw)
                    if not isinstance(existing_sp, dict):
                        existing_sp = {}
                except (json.JSONDecodeError, TypeError):
                    # T11.8-B (2026-04-24): narrow from bare Exception. Stored
                    # strategy_params raw JSON parse — empty dict fallback.
                    existing_sp = {}
                if plugin_updates:
                    existing_sp["plugin_params"] = {
                        **existing_sp.get("plugin_params", {}),
                        **plugin_updates,
                    }
                if gate_updates:
                    existing_sp["engine_gates"] = {
                        **existing_sp.get("engine_gates", {}),
                        **gate_updates,
                    }
                await db.conn.execute(
                    "UPDATE strategies SET strategy_params=? WHERE id=?",
                    (json.dumps(existing_sp), sid_i))

        total_applied = len(db_updates) + len(plugin_updates) + len(gate_updates)

        if total_applied > 0:
            await db.conn.commit()

            # ══ Phase 82d: Invalidate lifecycle cache ══ (per matched sid)
            # Apply Callback DB'ye ham JSON yazdi; lifecycle._cache elinde
            # eski StrategyParams objesi tutuyor olabilir. Bir sonraki
            # get_params() çağrısının DB'den fresh okumasını garantile.
            try:
                if engine is not None and getattr(engine, "lifecycle", None):
                    for sid_row in rows:
                        engine.lifecycle._cache.pop(sid_row[0], None)
            except (AttributeError, KeyError) as _ci:
                # T11.8-B (2026-04-24): narrow from bare Exception. Cache
                # invalidate best-effort — see tournament_job for same doctrine.
                logger.debug(f"lifecycle cache invalidate failed: "
                             f"{type(_ci).__name__}: {_ci}")

            # Phase 80: Mark as applied in DB + changelog
            row_id = pending.get("row_id")
            if row_id:
                try:
                    await db.conn.execute(
                        "UPDATE hyperopt_results SET applied=1 WHERE id=?",
                        (row_id,))
                    await db.conn.commit()
                except Exception:  # noqa: BLE001
                    pass
            try:
                from core.changelog import log_change
                applied_dict: dict = {}
                for p, v in db_updates:
                    applied_dict[p] = v
                for p, v in plugin_updates.items():
                    applied_dict[p] = v
                for p, v in gate_updates.items():
                    applied_dict[p] = v
                # Changelog: one entry per matched strategy
                for sid_row in rows:
                    await log_change(
                        db, sid_row[0], "HYPEROPT_APPLY", "hyperopt",
                        old=None, new=applied_dict,
                        reason=f"score={pending['best_score']:.4f} "
                               f"matched={len(rows)}",
                        label=sid_row[1])
            except Exception:  # noqa: BLE001
                pass

            # Build Telegram summary (HTML; Markdown YOK)
            lines = [
                "✅ <b>HyperOpt Uygulandi</b>",
                f"Tip: <code>{stype}</code>",
                f"Skor: {pending['best_score']:.4f}",
                f"Eşleşme: <code>{match_scope}</code>",
                f"Güncellenen strateji sayısı: <b>{len(rows)}</b>",
                "",
            ]
            # Show up to 5 labels to avoid spamming Telegram for 29-fusion cases
            if len(labels_applied) <= 5:
                lines.append("Stratejiler: " + ", ".join(labels_applied))
            else:
                lines.append(
                    f"Stratejiler: {', '.join(labels_applied[:5])}"
                    f" ... (+{len(labels_applied)-5})")
            lines.append("")
            if db_updates:
                lines.append(f"📊 DB kolonu ({len(db_updates)}):")
                for p, v in db_updates:
                    lines.append(f"  • {p}: {v}")
            if plugin_updates:
                lines.append(f"🧩 Plugin param ({len(plugin_updates)}):")
                for p, v in plugin_updates.items():
                    tag = "" if p not in plugin_set_failures \
                        else "  ⚠️ runtime set_config failed"
                    lines.append(f"  • {p}: {v}{tag}")
            if gate_updates:
                lines.append(f"🚪 Engine gate ({len(gate_updates)}):")
                for p, v in gate_updates.items():
                    lines.append(f"  • {p}: {v}")
            if ignored:
                lines.append(
                    f"⏭ Atlandi ({len(ignored)}): {', '.join(ignored)}")
            if registry is None and plugin_updates:
                lines.append("")
                lines.append(
                    "ℹ️ Engine warm-up'ta değildi: plugin paramları DB'ye "
                    "yazildi, runtime'a bot restart sonrasi yansir.")
            text = "\n".join(lines)
            logger.info(
                f"HyperOpt applied to {len(rows)} strategy(ies) "
                f"[{stype}] scope={match_scope}: "
                f"db={len(db_updates)} plugin={len(plugin_updates)} "
                f"gate={len(gate_updates)} ignored={len(ignored)} | "
                f"best_params={best_params}")
        else:
            text = (
                f"⚠️ Uygulanacak parametre bulunamadi.\n"
                f"Strateji tip: <code>{stype}</code>\n"
                f"Atlanan: {', '.join(ignored) or 'yok'}")

        try:
            await q.edit_message_text(text, parse_mode="HTML")
        except Exception:  # noqa: BLE001
            await q.message.reply_text(text, parse_mode="HTML")

    except Exception as e:  # noqa: BLE001
        logger.error(f"HyperOpt apply error: {e}", exc_info=True)
        try:
            await q.edit_message_text(f"❌ Uygulama hatasi: {str(e)[:100]}")
        except Exception:  # noqa: BLE001
            pass
