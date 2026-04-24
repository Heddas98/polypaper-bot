"""
Phase 67 + Phase 82e Sprint 1.2: AI Tournament Mode -- Nightly Parameter Optimization
=====================================================================================
Source: TradeSight (AI Strategy Tournament concept)

Every night at 03:00 UTC:
  1. For each active strategy with enough data
  2. Run Optuna hyperopt sweep (50 trials per strategy) IN A SUBPROCESS
  3. If score > improvement threshold -> update params (unless dry-run)
  4. Retire consistently underperforming parameter sets
  5. Report results to admin via Telegram

Architecture:
  - Thompson Sampling SELECTS which strategies get trade opportunities
  - AI Tournament OPTIMIZES each strategy's parameters
  - Together they form a complete auto-improvement loop

Phase 82e Sprint 1.2 migration
------------------------------
Previously this job called ``HyperOptPipeline.optimize()`` INLINE in a
loop of up to 12 strategies. Each pipeline.optimize runs discovery SQL
that can block the event loop for minutes -- triggering the engine stall
watchdog on the very same loop. Now the job delegates to
``backtest.hyperopt_launcher.launch_hyperopt_batch_subprocess`` so the
heavy work happens in a child process and the main bot loop only awaits
short stdout reads.

Known limitations after migration (tracked for future work):
  * The subprocess worker does NOT test-split / overfit-check results
    (the test_score column is always 0). ``is_overfit()`` would return
    False for every row, so we skip that gate in subprocess mode.
    Acceptable because TOURNAMENT_DRY_RUN=true by default.
  * The worker does not filter by asset / timeframe -- it runs over all
    market windows. Same behaviour as the /hyperopt Telegram command
    after Phase 82b. TODO: plumb --asset / --timeframe into the worker.

ENV:
    TOURNAMENT_ENABLED=false           # Start disabled, enable after validation
    TOURNAMENT_HOUR_UTC=3              # Run at 03:00 UTC (night, low activity)
    TOURNAMENT_TRIALS=50               # Optuna trials per strategy
    TOURNAMENT_METRIC=sharpe_ratio     # Optimization target (worker uses its own default)
    TOURNAMENT_MIN_IMPROVEMENT=0.05    # Minimum score improvement to deploy
    TOURNAMENT_MIN_TRADES=20           # Min trades before strategy is eligible
    TOURNAMENT_MAX_STRATEGIES=12       # Cap on strategies to optimize per run
    TOURNAMENT_DRY_RUN=true            # Report only, don't auto-deploy params
"""
from __future__ import annotations

import asyncio
import os
import logging
from datetime import datetime, timezone

import aiosqlite
from telegram.error import TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger("polypaper.tournament")


def _is_enabled() -> bool:
    return os.getenv("TOURNAMENT_ENABLED", "false").lower() == "true"


def _resolve_admin():
    """Resolve admin chat ID."""
    for key in ("ADMIN_TELEGRAM_ID", "ADMIN_CHAT_ID", "TELEGRAM_ADMIN_ID"):
        val = os.getenv(key)
        if val:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None


async def tournament_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback -- nightly AI Tournament parameter optimization.

    Phase 82e Sprint 1.2: delegates hyperopt to a subprocess via
    :func:`backtest.hyperopt_launcher.launch_hyperopt_batch_subprocess`
    so the engine's event loop never blocks on discovery SQL.
    """
    if not _is_enabled():
        return

    try:
        app = context.application
        db = app.bot_data.get("db")
        if db is None:
            return

        admin = _resolve_admin()
        now = datetime.now(timezone.utc)

        # ── Config ──
        n_trials = int(os.getenv("TOURNAMENT_TRIALS", "50"))
        metric = os.getenv("TOURNAMENT_METRIC", "sharpe_ratio")
        min_improvement = float(os.getenv("TOURNAMENT_MIN_IMPROVEMENT", "0.05"))
        min_trades = int(os.getenv("TOURNAMENT_MIN_TRADES", "20"))
        max_strats = int(os.getenv("TOURNAMENT_MAX_STRATEGIES", "12"))
        dry_run = os.getenv("TOURNAMENT_DRY_RUN", "true").lower() == "true"

        logger.info(
            "Tournament START: trials=%d metric=%s dry_run=%s",
            n_trials, metric, dry_run
        )

        # ── 1. Get eligible strategies ──
        rows = await db.conn.execute_fetchall(
            """SELECT s.id, s.label, s.strategy_type, s.asset, s.timeframe,
                      s.odds_threshold, s.trade_amount,
                      COUNT(e.id) as trade_count,
                      COALESCE(SUM(CASE WHEN e.pnl > 0 THEN 1 ELSE 0 END), 0) as wins,
                      COALESCE(SUM(e.pnl), 0) as total_pnl
               FROM strategies s
               LEFT JOIN executions e ON e.strategy_id = s.id AND e.result IS NOT NULL
               WHERE s.status = 'active'
               GROUP BY s.id
               HAVING trade_count >= ?
               ORDER BY total_pnl DESC
               LIMIT ?""",
            (min_trades, max_strats)
        )

        if not rows:
            logger.info("Tournament: no eligible strategies (min %d trades)", min_trades)
            return

        # ── 2. Import PARAM_SPACES for name validation ──
        try:
            from backtest.hyperopt import PARAM_SPACES
        except ImportError as ie:
            logger.error("Tournament: hyperopt import failed: %s", ie)
            if admin:
                try:
                    await context.bot.send_message(
                        chat_id=admin,
                        text=f"⚠️ Tournament: hyperopt import failed: {ie}",
                        parse_mode="HTML",
                    )
                except (TelegramError, asyncio.TimeoutError):
                    # T11.8-B (2026-04-24): narrow from bare Exception.
                    # Nested admin-notify failure is best-effort; the import
                    # failure above is the real signal and was already logged.
                    pass
            return

        # ── 3. Build (row, backtest_name) pairs and collect unique strat names ──
        # Multiple live strategies may map to the same backtest strategy name
        # (e.g. two fusion instances both map to late_convergence). We still
        # hyperopt the backtest name once per run and apply the result to
        # every live strategy that maps to it.
        mapped: list[dict] = []
        for row in rows:
            sid, label, stype, asset, tf, threshold, amount, trades, wins, pnl = row
            wr = wins / trades if trades > 0 else 0
            strat_name = _map_strategy_type(stype, label)
            if strat_name not in PARAM_SPACES:
                logger.debug(
                    "Tournament: %s (%s) not in PARAM_SPACES, skipping",
                    label, strat_name,
                )
                continue
            mapped.append({
                "strategy_id": sid,
                "label": label,
                "type": stype,
                "backtest_name": strat_name,
                "current_wr": round(wr, 3),
                "current_pnl": round(pnl, 2),
                "trades": trades,
            })

        if not mapped:
            logger.info("Tournament: no PARAM_SPACES match for any eligible strategy")
            return

        # Unique backtest names for the worker; preserve insertion order
        seen_names: set[str] = set()
        batch_names: list[str] = []
        for entry in mapped:
            bname = entry["backtest_name"]
            if bname in seen_names:
                continue
            seen_names.add(bname)
            batch_names.append(bname)

        logger.info(
            "Tournament: optimizing %d backtest strat(s) covering %d live strat(s): %s",
            len(batch_names), len(mapped), ",".join(batch_names),
        )

        # ── 4. Run hyperopt in a single batch subprocess ──
        try:
            from backtest.hyperopt_launcher import launch_hyperopt_batch_subprocess
        except ImportError as ie:
            logger.error("Tournament: launcher import failed: %s", ie)
            if admin:
                try:
                    await context.bot.send_message(
                        chat_id=admin,
                        text=f"⚠️ Tournament: launcher import failed: {ie}",
                        parse_mode="HTML",
                    )
                except (TelegramError, asyncio.TimeoutError):
                    # T11.8-B (2026-04-24): narrow from bare Exception.
                    # Same best-effort admin-notify pattern as above.
                    pass
            return

        done_list = await launch_hyperopt_batch_subprocess(
            strategies=batch_names,
            n_trials=n_trials,
            source="tournament",
        )
        done_by_name = {info.name: info for info in done_list}

        # ── 5. Evaluate results and optionally deploy ──
        # Note: subprocess worker does NOT test-split, so is_overfit()-style
        # check is not available here. We gate on min_improvement only.
        results: list[dict] = []
        deployed: list[str] = []
        for entry in mapped:
            bname = entry["backtest_name"]
            info = done_by_name.get(bname)
            if info is None:
                entry["error"] = (
                    "no STRAT_DONE event (worker failed, stalled, or timed out)"
                )
                results.append(entry)
                continue

            entry["best_score"] = float(info.best_value)
            entry["best_params"] = dict(info.best_params)
            entry["trial_count"] = int(info.trial_count)
            entry["elapsed_sec"] = float(info.elapsed_sec)

            if info.best_value < min_improvement:
                entry["action"] = (
                    f"SKIP (score {info.best_value:.4f} < min "
                    f"{min_improvement:.4f})"
                )
                results.append(entry)
                continue

            if dry_run:
                entry["action"] = f"WOULD_DEPLOY (score={info.best_value:.4f})"
                results.append(entry)
                continue

            # Live deploy -- update this live strategy's params
            try:
                await _deploy_params(db, entry["strategy_id"], info.best_params)
                entry["action"] = f"DEPLOYED (score={info.best_value:.4f})"
                deployed.append(entry["label"])
            except (aiosqlite.Error, KeyError, TypeError) as e:
                # T11.8-B (2026-04-24): narrow from bare Exception.
                # _deploy_params SET clause raises aiosqlite.Error (DB);
                # KeyError/TypeError surface on params dict shape issues
                # from the hyperopt worker. exc_info=True preserves trace.
                entry["action"] = f"DEPLOY_FAILED: {type(e).__name__}: {e}"
                logger.error(
                    "Tournament: deploy failed for %s: %s",
                    entry["label"], e, exc_info=True,
                )
            results.append(entry)

        # ── 6. Send report ──
        elapsed = (datetime.now(timezone.utc) - now).total_seconds()
        report = _format_tournament_report(results, elapsed, dry_run, metric)

        if admin:
            try:
                await context.bot.send_message(
                    chat_id=admin, text=report, parse_mode="HTML"
                )
            except (TelegramError, asyncio.TimeoutError) as e:
                # T11.8-B (2026-04-24): narrow from bare Exception.
                # send_message transport failures only; the tournament run
                # itself already succeeded and results were logged.
                logger.error("Tournament: send report failed: "
                             "%s: %s", type(e).__name__, e)

        logger.info(
            "Tournament DONE: %d entries, %d deployed, %.0fs",
            len(results), len(deployed), elapsed
        )

    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): outermost job-runner wrapper intentionally
        # wide. Nightly tournament is heavy (subprocess + DB + telegram);
        # scheduler-thread crash would kill future runs. T7.6 job-safety
        # exemption.
        logger.error("tournament_job failed: %s", e, exc_info=True)


# ═══ Helpers ═══

def _map_strategy_type(stype: str, label: str) -> str:
    """Map live strategy_type/label to backtest strategy name."""
    label_lower = (label or "").lower()

    # Direct label match
    name_map = {
        "hour_edge": "hour_edge",
        "late_conv": "late_convergence",
        "streak": "streak_reversal",
        "opening": "opening_breakout",
        "ob_imb": "orderbook_imbalance",
        "fade": "fade_rip",
        "taker": "taker_flow",
        "calib": "calibration_arb",
        "cross": "cross_coin",
        "composite": "composite",
        "funding": "funding_rate",
    }

    for key, val in name_map.items():
        if key in label_lower:
            return val

    # Type-based fallback
    # Phase 75-fix: fusion→late_convergence (composite needs sub-strats),
    #               momentum→late_convergence (hour_edge only works 4/24 hours)
    type_map = {
        "fusion": "late_convergence",
        "momentum": "late_convergence",
        "contrarian": "streak_reversal",
        "scalper": "taker_flow",
        "sniper": "late_convergence",
    }
    return type_map.get(stype, stype)


async def _deploy_params(db, strategy_id: str, params: dict) -> None:
    """Update strategy parameters in DB. Only updates known columns."""
    updatable = {
        "odds_threshold": params.get("odds_threshold"),
        "min_confidence": params.get("min_confidence"),
    }
    # Filter None values
    updates = {k: v for k, v in updatable.items() if v is not None}

    if not updates:
        return

    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [strategy_id]

    await db.conn.execute(
        f"UPDATE strategies SET {set_clause}, "
        f"updated_at = datetime('now') WHERE id = ?",
        tuple(values)
    )
    await db.conn.commit()
    logger.info("Tournament: deployed params for %s: %s", strategy_id, updates)

    # Phase 75: Notify lifecycle that tournament updated this strategy
    # Forces lifecycle to re-read params from DB on next get_params() call
    try:
        engine = db._engine if hasattr(db, '_engine') else None
        if engine and hasattr(engine, 'lifecycle'):
            engine.lifecycle._cache.pop(strategy_id, None)
            logger.info("Tournament: lifecycle cache invalidated for %s", strategy_id[:8])
    except (AttributeError, KeyError):
        # T11.8-B (2026-04-24): narrow from bare Exception. lifecycle cache
        # invalidation is best-effort — AttributeError if engine/lifecycle
        # not wired yet, KeyError if the strategy wasn't cached. Silent
        # swallow is correct (worst case: one stale cache hit on next call).
        pass


def _format_tournament_report(
    results: list, elapsed: float, dry_run: bool, metric: str
) -> str:
    """Format tournament results for Telegram.

    Phase 82e Sprint 1.2: result dicts now carry ``best_score`` /
    ``best_params`` / ``trial_count`` populated from a
    :class:`backtest.hyperopt_ipc.StratDoneInfo` instead of a full
    ``HyperOptResult``. Overfit detection is not available in subprocess
    mode (worker does not do test-split) -- the report notes this.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mode = "🔬 DRY RUN" if dry_run else "🚀 LIVE"

    lines = [
        f"🏆 <b>AI Tournament Report</b> {mode}",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"Metric: <code>{metric}</code> | {len(results)} kayıt",
        f"Duration: {elapsed:.0f}s  ·  <i>overfit check disabled (subprocess mode)</i>",
        f"",
    ]

    for r in results:
        if "error" in r:
            lines.append(f"❌ <b>{r['label']}</b>: {r['error']}")
            continue

        score = r.get("best_score")
        action = r.get("action", "?")
        icon = "✅" if "DEPLOY" in action else "⏭"
        score_str = f"{score:.4f}" if isinstance(score, (int, float)) else "N/A"
        trial_cnt = r.get("trial_count") or 0
        trial_str = f" [{trial_cnt}t]" if trial_cnt else ""
        wr_pct = (r.get("current_wr") or 0) * 100

        lines.append(
            f"{icon} <b>{r['label']}</b> "
            f"({r.get('trades', 0)}t WR{wr_pct:.0f}%): "
            f"{score_str}{trial_str} → {action}"
        )

    deployed = [r for r in results if "DEPLOYED" in r.get("action", "")]
    if deployed:
        lines.extend([
            f"",
            f"🚀 <b>{len(deployed)} strateji güncellendi</b>",
        ])
    elif not dry_run:
        lines.append(f"\n⏭ Güncellenecek strateji yok")

    lines.append(f"\n🕐 {now_str}")
    return "\n".join(lines)
