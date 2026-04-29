"""
PolyPaper Bot - Main Bot Class (v9 — Phase 33 Adaptive Intelligence)
Thompson Sampling + Regime Detection + Drift Monitor + Full UI refresh.

T11.8-B (2026-04-24): every catch in this module is annotated `# noqa:
BLE001`. bot.py is the boot orchestrator — it touches: handler imports
(40+ optional modules), JobQueue registration (each job has its own
ImportError surface), engine wiring (engine/risk/scanner attribute
chain), Telegram Application startup (httpx + websockets + ssl), DB
init + migration, and admin command setup. Wide catches at the boot
layer are intentional — a single missing module or schema mismatch
should not crash bootstrap; the bot logs the failure and continues
with degraded functionality. Anything tighter risks "first user message
crashes silently because handler X failed to register" type bugs.
T11.6 render policy is preserved (no exception text reaches users from
this file; user-facing error reporting happens in handler modules).
"""
import logging
import os
from telegram import BotCommand, Update, BotCommandScopeChat, BotCommandScopeDefault
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, filters,
)
from config.settings import Settings
from db.database import Database
from telegram_bot.version import BOT_VERSION, BOT_CODENAME

from telegram_bot.handlers.start import (
    start_command, accept_terms_callback, deposit_instructions_callback)
from telegram_bot.handlers.dashboard import (  # Phase 51 P51-03 Faz-2 Cluster J
    dashboard_command, dashboard_callback, refresh_dashboard_callback, add_funds_command,
    journal_command,  # merged from journal.py
    info_callback,  # merged from info_handler.py
    alert_set_cmd, alerts_list_cmd, alert_delete_cmd, price_alert_job,  # merged from price_alert_handler.py
)
from telegram_bot.handlers.strategies import (  # Phase 51 P51-03 Faz-2 Cluster E
    strategies_command, strategies_callback,
    start_strategy_callback, stop_strategy_callback,
    delete_strategy_callback, start_all_callback, stop_all_callback,
    quick_strategy_command, quick_strategy_wizard_callback,  # Phase 52 BUG #2
    edit_command, clone_command,
    autopilot_command, autopilot_callback,  # merged from autopilot_handler.py
    optimize_command, optimize_deploy_callback,  # merged from optimize_handler.py
    # Phase 82a hotfix: pagination + slash commands for /start_all /stop_all
    strategies_page_callback, start_all_command, stop_all_command,
)
from telegram_bot.handlers.strategy_builder import get_strategy_builder_handler
from telegram_bot.handlers.start import (  # Phase 51 P51-03 Faz-2 — wallets merged
    wallets_command, wallets_callback, new_wallet_callback,
    # withdraw merged
    withdraw_command, withdraw_callback, withdraw_funds_command,
    # referrals merged
    referrals_command, referrals_callback,
)
from telegram_bot.handlers.stats import (
    stats_command, stats_callback, stats_by_market_callback,
    strategy_stats_command, strategy_stats_callback,
    trades_command, trades_page_callback,  # Phase 52: /trades with pagination
    # Phase 51 P51-03 — stats_hub merged into stats.py
    stats_hub_command, stats_hub_callback,
    # Phase 51 P51-03 Faz-2 — stats_chart + performance + analytics merged
    stats_chart_command, performance_command,
    analytics_command, analytics_callback)
from telegram_bot.handlers.settings_handler import (  # Phase 51 P51-03 Faz-2 Cluster G
    settings_command, settings_callback, toggle_notification_callback,
    plugins_command, plugin_set_command,  # merged from plugins_handler.py
    canary_command, promote_command, demote_command,  # merged from promote.py
)
from telegram_bot.handlers.markets import (  # Phase 51 P51-03 Faz-2 Cluster C
    markets_command, refresh_markets_callback,
    candles_command, candle_refresh_callback,  # merged from candle_handler.py
    signals_command, signals_callback,  # merged from signals_handler.py
)
from telegram_bot.handlers.backtest_v2 import (  # Phase 51 P51-03 Faz-2 Cluster F
    backtest_v2_cmd, compare_cmd, backtest_v2_callback,
    backtest_v2_config_callback, handle_limit_input,
    backtest_replay_command, replay_callback,  # merged from backtest_replay.py
    # Becker commands removed 2026-04-28 (Heddas direktifi: tam kaldırma)
    cancel_operation_callback,  # Phase 79 S1-12: Cancel for backtest/compare
)
from telegram_bot.handlers.strategy_tester import (  # Phase 79 S2-1: Test user strategies
    get_test_strategy_handlers,
)
from telegram_bot.handlers.strategy_report import (  # Phase 79 S3-09: Strategy lifecycle report
    report_command, report_refresh_callback,
)
# Phase 51 P51-03 Faz-2 Cluster I — kelly/maker/micro/recorder merged into strategies.py
from telegram_bot.handlers.strategies import (
    kelly_command, kelly_toggle_command, analyze_command, analyze_optimize_command,
    maker_stats_command, micro_command,
    recorder_command, recorder_refresh_callback,
)
from telegram_bot.handlers.positions import positions_command, positions_callback
from telegram_bot.handlers.risk_handler import (
    kill_command, resume_command, streak_reset_command, risk_command, risk_callback,
    risk_set_command, risk_field_edit_callback, handle_risk_input,
    force_exit_toggle_callback, force_exit_edit_callback,  # Phase 53b
    # Phase 51 P51-03 — risk_hub merged into risk_handler.py
    risk_hub_command, risk_hub_callback)
from telegram_bot.handlers.diagnose_handler import (  # Phase 62+: trade pipeline diagnostics
    diagnose_command, diagnose_callback)
from telegram_bot.handlers.live_guards_handler import (  # Epic 11 T11.2 [D]: 6-guard live snapshot
    live_guards_command)
from telegram_bot.handlers.rest_timing_handler import (  # Epic 4 T4.8: REST RTT telemetry summary
    dump_rest_timing_command)
from telegram_bot.handlers.force_settle_handler import (  # Sprint 5 HOTFIX v4: manual oracle settle
    force_settle_command)
from telegram_bot.handlers.env_toggle import (  # Phase 82e Sprint 6: hot-tune runtime env
    env_toggle_command)
from telegram_bot.handlers.changelog_handler import (  # Phase 82e Sprint A: /analyze verification
    changelog_command)
from telegram_bot.handlers.archive_info_handler import (  # Phase 82e Sprint B.2: archive reader diag
    archive_info_command)
from core.bg_task import (  # Phase 82e Sprint 2.1: bg task exception guard
    set_notify_handler, make_telegram_notify_handler)
from telegram_bot.handlers.brier_handler import brier_command  # Phase 66
from telegram_bot.handlers.filters_handler import (  # Phase 66: filter toggle panel
    filters_command, filters_callback, _load_persisted_filters)
from telegram_bot.handlers.live_handler import (  # Phase 51 P51-03 Faz-2 Cluster H
    live_command, live_callback,
    ws_command, ws_callback,  # merged from ws_handler.py
    daily_command, daily_callback,  # merged from daily_handler.py
)
from telegram_bot.handlers.menu_handler import (
    menu_command, menu_dashboard_callback, menu_strategies_callback,
    menu_brain_callback, menu_backtest_callback, menu_positions_callback,
    menu_stats_callback, menu_risk_callback, menu_market_callback,
    menu_candles_callback, menu_settings_callback, menu_live_callback,
    menu_help_callback, menu_refresh_callback,
    menu_bt_replay_callback, menu_bt_v2_callback, menu_bt_compare_callback,
    menu_learning_callback, menu_experiment_callback,
    menu_health_callback, menu_advanced_callback,
    menu_cmd_mistakes_callback,
    # T1.3 Commit 4: markov/capital callback imports silindi
    # T1.3 Commit 6: breed/vote/whale callback imports silindi (roadmap_handler ghost)
)
# Phase 51 P51-04/P51-05 — natural language intent parser
from telegram_bot.handlers.ai_handler import (  # Phase 51 P51-03 Faz-2 Cluster D
    ai_command, ai_confirm_callback, ai_approval_callback, analyze_apply_callback, analyze_brain_callback, suggest_callback,  # Sprint 3 S3-04 + Phase 79b
    brain_command, brain_toggle_callback,  # merged from brain_handler.py
    regime_command, ts_command, drift_command, monitor_command,  # merged from intelligence_handler.py (T1.3 Commit 3: validate_command ghost removed)
)
# Phase 47f.7+ in-bot shadow report job (replaces broken sandbox scheduled task)
from telegram_bot.jobs.shadow_report_job import shadow_report_job
from telegram_bot.jobs.shadow_vs_paper_job import shadow_vs_paper_job  # Phase 47f.10 P5#22
from telegram_bot.jobs.pnl_divergence_job import pnl_divergence_job  # Phase 66
# Tournament job removed 2026-04-28 (Heddas direktifi: Hyperopt tam silme,
# tournament_job ana işi hyperopt subprocess çalıştırmaktı).
# Hyperopt handler removed 2026-04-28 (Heddas direktifi: tam silme)
# Phase 67/82b/82e ait /hyperopt /hyperopt_all /hyperopt_status /hyperopt_abort
# /mc_kelly komutları + cancel + apply callback'leri kaldırıldı.
from telegram_bot.handlers.roadmap_handler import (  # Phase 70-73
    # T1.3 Commit 5 (2026-04-20): breed/vote/drift_check/whale/market_quality/
    # correlation_check import'ları silindi (ghost modüller).
    ev_stats_command, metrics_command, surface_command, latency_command,
)
from telegram_bot.handlers.lifecycle_handler import lifecycle_command  # Phase 74b
# 2026-04-29 Polymarket Portfolio (Aşama 1): gerçek Proxy cüzdan view
from telegram_bot.handlers.portfolio_handler import (
    portfolio_command, portfolio_callback,
)
from telegram_bot.jobs.polymarket_portfolio_job import polymarket_portfolio_job
# 2026-04-29 Aşama 3.B: top-level mode toggle (Paper vs Real)
from telegram_bot.handlers.mode_handler import mode_command, mode_callback
# Becker recal handler removed 2026-04-28 (Heddas direktifi)
# T1.3 Commit 4 (2026-04-20): phase76_handler silindi —
# markov_command + capital_command ghost modüllere (core.markov_estimator,
# core.capital_allocator) bağlıydı, Phase 76 ayağı Phase 79b sonrası ölü kalmıştı.
from telegram_bot.handlers.phase77_handler import (  # Phase 77
    why_command, why_callback,
    mistakes_command, patterns_command, patterns_callback,
    health_command, health_callback,
    experiment_command, experiment_apply_command, experiment_discard_command,
)
from telegram_bot.jobs.db_retention_job import db_retention_job
from telegram_bot.jobs.auto_promote_job import auto_promote_job  # Phase 48
from telegram_bot.jobs.db_archive_job import db_archive_job  # Phase 59 DB-01b
# Becker rolling recal job removed 2026-04-28 (Heddas direktifi)
from telegram_bot.jobs.maintenance_jobs import (
    daily_db_snapshot_job, heartbeat_job, wal_checkpoint_job,
)

logger = logging.getLogger("polypaper.bot")


class PolyPaperBot:
    def __init__(self, settings: Settings, db: Database,
                 scanner=None, engine=None, odds_feed=None, poly_client=None,
                 ws_client=None):
        self.settings = settings
        self.db = db
        self.scanner = scanner
        self.engine = engine
        self.odds_feed = odds_feed
        self.poly_client = poly_client
        self._ws_client = ws_client
        self.app: Application = None

    async def run(self):
        errors = self.settings.validate()
        if errors:
            for e in errors:
                logger.error(f"Config: {e}")
            raise ValueError("Config errors")

        self.app = Application.builder().token(self.settings.TELEGRAM_BOT_TOKEN).build()
        self.app.bot_data.update({
            "db": self.db, "settings": self.settings,
            "scanner": self.scanner, "engine": self.engine,
            "odds_feed": self.odds_feed, "poly_client": self.poly_client,
            "ws_client": getattr(self, '_ws_client', None),
            # Phase 51 P51-04 — mount bot instance so /ai router can
            # dispatch bot-class methods (_health_check, _risk_status,
            # _db_health, _shadow_report_now).
            "bot": self,
        })
        if self.engine:
            self.engine.bot_app = self.app

        # Phase 66: load persisted filter overrides from DB
        await _load_persisted_filters(self.db)

        # 1. Conversation handlers FIRST
        self.app.add_handler(get_strategy_builder_handler())

        # 2. All commands with shortcuts
        cmds = [
            ("start", start_command), ("dashboard", dashboard_command), ("d", dashboard_command),
            ("menu", menu_command),
            ("strategies", strategies_command), ("s", strategies_command),
            ("quick_strategy", quick_strategy_command),
            # Phase 82a hotfix: bulk slash commands (fallback when Telegram
            # truncates inline bulk buttons due to 100-button keyboard cap).
            ("start_all", start_all_command),
            ("stop_all", stop_all_command),
            ("positions", positions_command), ("pos", positions_command),
            ("markets", markets_command),
            # AI
            ("autopilot", autopilot_command), ("ap", autopilot_command),
            ("analyze", analyze_command), ("optimize_ai", analyze_optimize_command),
            # Phase 33: Adaptive Intelligence
            ("regime", regime_command),
            ("ts", ts_command), ("thompson", ts_command),  # Phase 47f.9: readable alias
            ("drift", drift_command), ("regime_drift", drift_command),  # Phase 47f.9: readable alias
            # T1.3 Commit 3: ("validate", validate_command) kaldırıldı — wf_validator ghost
            ("monitor", monitor_command), ("m", monitor_command),
            ("brain", brain_command),
            ("candles", candles_command),
            ("recorder", recorder_command),
            ("backtest_replay", backtest_replay_command),
            ("live", live_command),
            # Analytics
            ("stats", stats_command), ("stats_chart", stats_chart_command), ("strategy_stats", strategy_stats_command), ("ss", strategy_stats_command),
            ("canary", canary_command), ("promote", promote_command), ("demote", demote_command),
            ("stats_hub", stats_hub_command), ("risk_hub", risk_hub_command),
            ("performance", performance_command), ("perf", performance_command),
            ("daily", daily_command), ("analytics", analytics_command),
            ("journal", journal_command), ("kelly", kelly_command),
            ("maker_stats", maker_stats_command),  # Phase 43c
            ("micro", micro_command),  # Phase 46d
            ("microstructure", micro_command),  # Phase 47f.9: readable alias
            # Becker commands removed 2026-04-28 (Heddas direktifi):
            #   /becker_status /calibration_status /becker_build /calibration_build
            #   /becker_replay /becker_zones /becker_deep
            # Phase 50 (Suggestion 12.3) — price alerts
            ("alert", alert_set_cmd),
            ("alerts", alerts_list_cmd),
            ("alert_del", alert_delete_cmd),
            # Backtest (Phase 38a: legacy v1 removed; replay + v2 only)
            # Phase 50 P1-05: backtest_legacy alias removed — use /backtest_v2 or /bt2
            ("backtest_v2", backtest_v2_cmd), ("bt2", backtest_v2_cmd),
            ("compare", compare_cmd),
            # Risk
            ("risk", risk_command), ("risk_set", risk_set_command),
            ("kill", kill_command), ("resume", resume_command), ("streak_reset", streak_reset_command),
            # Account
            ("wallets", wallets_command), ("add_funds", add_funds_command),
            ("withdraw", withdraw_command), ("withdraw_funds", withdraw_funds_command),
            # Settings
            ("settings", settings_command), ("signals", signals_command),
            ("plugins", plugins_command), ("ws", ws_command),
            ("kelly_toggle", kelly_toggle_command), ("optimize", optimize_command),
            ("edit", edit_command), ("clone", clone_command),
            ("plugin_set", plugin_set_command), ("referrals", referrals_command),
            ("help", self._help),
            # Phase 47f.7+ manual shadow report trigger
            ("shadow_report", self._shadow_report_now),
            ("shadow", self._shadow_report_now),
            ("sr", self._shadow_report_now),
            # Phase 52: /trades dedicated handler with pagination
            ("trades", trades_command),
            # Phase 47f.7+ ops commands (P0-3 / P1-5)
            ("db_health", self._db_health),
            ("dbh", self._db_health),
            ("db_cleanup", self._db_cleanup),
            ("dbc", self._db_cleanup),
            ("db_archive", self._db_archive),
            ("dba", self._db_archive),
            ("risk_status", self._risk_status),
            ("rs", self._risk_status),
            ("health_check", self._health_check),
            ("hc", self._health_check),
            # Phase 51 P51-04 — natural language intent parser
            ("ai", ai_command),
            ("nl", ai_command),  # short alias
            # Phase 62+: trade pipeline diagnostics
            ("diagnose", diagnose_command),
            # Epic 11 T11.2 [D]: 6-guard live snapshot (admin)
            ("live_guards", live_guards_command),
            ("lg", live_guards_command),
            # Epic 4 T4.8: REST RTT telemetry summary (admin)
            ("dump_rest_timing", dump_rest_timing_command),
            ("drt", dump_rest_timing_command),
            # Sprint 5 HOTFIX v4: manual oracle settle for stuck positions
            ("force_settle", force_settle_command),
            ("fs", force_settle_command),
            # Phase 82e Sprint 6: hot-tune runtime env knobs (admin)
            ("env_toggle", env_toggle_command),
            ("envt", env_toggle_command),
            # Phase 82e Sprint A: strategy changelog — verifies /analyze executions
            ("changelog", changelog_command), ("cl", changelog_command),
            # Phase 82e Sprint B.2: archive reader diag (hot+cold tier)
            ("archive_info", archive_info_command), ("ainfo", archive_info_command),
            # Phase 66: filter toggle panel
            ("filters", filters_command), ("f", filters_command),
            # Phase 66: Brier Score calibration report
            ("brier", brier_command),
            # Hyperopt commands removed 2026-04-28 (Heddas direktifi):
            #   /hyperopt /hyperopt_all /hyperopt_status /hyperopt_abort /mc_kelly
            # MC Kelly hyperopt_handler.py içinde tanımlıydı; ayrı bir dosyaya
            # taşımak istersek Aşama 2'de basit ~50 satır rewrite.
            # Phase 70-73: Roadmap commands (T1.3 Commit 5: breed/vote/drift_check/
            # whale/market_quality/correlation_check ghost silindi)
            ("ev_stats", ev_stats_command),
            ("metrics", metrics_command),
            ("surface", surface_command),
            ("latency", latency_command),
            # Becker recal commands removed 2026-04-28 (Heddas direktifi)
            # 2026-04-29 Polymarket gerçek cüzdan view (Aşama 1)
            ("portfolio", portfolio_command), ("pf", portfolio_command),
            # 2026-04-29 Aşama 3.B: top-level mode toggle (Paper/Real)
            ("mode", mode_command), ("m", mode_command),
            # Phase 74b: Per-strategy lifecycle
            ("lifecycle", lifecycle_command), ("lc", lifecycle_command),
            # T1.3 Commit 4 (2026-04-20): Phase 76 markov + capital
            # registration'ları silindi (phase76_handler.py ghost modül).
            # Phase 77: Learning + Explainer + Health + Experiment
            ("why", why_command),
            ("mistakes", mistakes_command),
            ("patterns", patterns_command),
            ("health", health_command), ("h", health_command),
            ("experiment", experiment_command), ("exp", experiment_command),
            ("experiment_apply", experiment_apply_command),
            ("experiment_discard", experiment_discard_command),
        ]
        for name, handler in cmds:
            self.app.add_handler(CommandHandler(name, handler))

        # 3. Exact callbacks
        for pattern, handler in [
            ("accept_terms", accept_terms_callback),
            ("deposit_instructions", deposit_instructions_callback),
            ("show_dashboard", dashboard_callback),
            ("refresh_dashboard", refresh_dashboard_callback),
            ("show_strategies", strategies_callback),
            ("start_all_strats", start_all_callback),
            ("stop_all_strats", stop_all_callback),
            ("strats_noop", strategies_page_callback),  # Phase 82a: pagination label click no-op
            ("show_wallets", wallets_callback),
            ("new_wallet", new_wallet_callback),
            ("show_stats", stats_callback),
            ("show_settings", settings_callback),
            ("refresh_markets", refresh_markets_callback),
            ("show_withdraw", withdraw_callback),
            ("show_referrals", referrals_callback),
            ("show_positions", positions_callback),
            ("show_autopilot", self._ap_redirect),
        ]:
            self.app.add_handler(CallbackQueryHandler(handler, pattern=f"^{pattern}$"))

        # Phase 77: Learning + Explainer + Health callbacks
        for pattern, handler in [
            ("why_refresh", why_callback),
            ("patterns_refresh", patterns_callback),
            ("health_refresh", health_callback),
        ]:
            self.app.add_handler(CallbackQueryHandler(handler, pattern=f"^{pattern}$"))

        # Phase 35: Brain toggles
        for pattern in ["brain_toggle", "brain_refresh"]:
            self.app.add_handler(CallbackQueryHandler(brain_toggle_callback, pattern=f"^{pattern}"))

        # Phase 35: Candle refresh
        self.app.add_handler(CallbackQueryHandler(candle_refresh_callback, pattern="^candle_refresh$"))

        # Phase 36: Market recorder
        self.app.add_handler(CallbackQueryHandler(recorder_refresh_callback, pattern="^recorder_refresh$"))
        self.app.add_handler(CallbackQueryHandler(replay_callback, pattern="^replay_"))

        # Phase 34: Live trader buttons (+ Phase 52 ÖNERİ #6 confirm/cancel)
        for pattern in ["live_toggle", "live_toggle_confirm", "live_toggle_cancel",
                        "live_main", "live_compare", "live_history"]:
            self.app.add_handler(CallbackQueryHandler(live_callback, pattern=f"^{pattern}$"))

        # Phase 52 BUG #2 — /quick_strategy wizard callbacks (qs_*)
        self.app.add_handler(CallbackQueryHandler(quick_strategy_wizard_callback, pattern="^qs_"))

        # Phase 82a hotfix — /strategies pagination callbacks (strats_page_N)
        self.app.add_handler(CallbackQueryHandler(strategies_page_callback, pattern="^strats_page_"))

        async def _monitor_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Phase 58: Run monitor inline instead of 'type /monitor' redirect."""
            q = update.callback_query
            await q.answer()
            try:
                # monitor_command uses update.message — patch it for callback
                update.message = q.message
                await monitor_command(update, context)
            except Exception:  # noqa: BLE001
                await q.message.reply_text("💡 /monitor veya /m yazın.", parse_mode="HTML")
        self.app.add_handler(CallbackQueryHandler(_monitor_cb, pattern="^show_monitor$"))

        # Phase 47f.10 P2#8/P2#9 — Stats/Risk hub tab routing
        self.app.add_handler(CallbackQueryHandler(stats_hub_callback, pattern="^hub:"))
        self.app.add_handler(CallbackQueryHandler(risk_hub_callback, pattern="^rhub:"))

        # Phase 51 P51-04 — /ai suggestion confirm buttons
        self.app.add_handler(CallbackQueryHandler(ai_confirm_callback, pattern="^ai_(run|cancel)$"))
        # Sprint 3 S3-04 — AI Brain low-confidence approval buttons
        self.app.add_handler(CallbackQueryHandler(ai_approval_callback, pattern="^ai_(approve|reject)$"))
        # Phase 79b — /analyze action execution buttons + brain cycle fallback
        self.app.add_handler(CallbackQueryHandler(analyze_apply_callback, pattern="^analyze_(apply|skip)$"))
        self.app.add_handler(CallbackQueryHandler(analyze_brain_callback, pattern="^analyze_brain$"))
        # Phase 79b — Strategy Suggester approve/reject
        self.app.add_handler(CallbackQueryHandler(suggest_callback, pattern="^suggest_(approve|reject)$"))

        # Phase 66: filter toggle panel callbacks
        self.app.add_handler(CallbackQueryHandler(filters_callback, pattern="^flt:"))

        # Hub menu callbacks
        for pattern, handler in [
            ("menu_dashboard", menu_dashboard_callback),
            ("menu_strategies", menu_strategies_callback),
            ("menu_brain", menu_brain_callback),
            ("menu_backtest", menu_backtest_callback),
            ("menu_positions", menu_positions_callback),
            ("menu_stats", menu_stats_callback),
            ("menu_risk", menu_risk_callback),
            ("menu_market", menu_market_callback),
            ("menu_candles", menu_candles_callback),
            ("menu_settings", menu_settings_callback),
            ("menu_live", menu_live_callback),
            ("menu_help", menu_help_callback),
            ("menu_refresh", menu_refresh_callback),
            ("menu_bt_replay", menu_bt_replay_callback),
            ("menu_bt_v2", menu_bt_v2_callback),
            ("menu_bt_compare", menu_bt_compare_callback),
            # Phase 77: Learning row callbacks
            ("menu_learning", menu_learning_callback),
            ("menu_experiment", menu_experiment_callback),
            ("menu_health", menu_health_callback),
            ("menu_advanced", menu_advanced_callback),
            ("menu_cmd_mistakes", menu_cmd_mistakes_callback),
            # T1.3 Commit 4 (2026-04-20): menu_cmd_markov + menu_cmd_capital
            # callback handler'ları silindi (phase76_handler ghost).
            # T1.3 Commit 6 (2026-04-20): menu_cmd_breed/vote/whale silindi
            # (roadmap_handler ghost).
        ]:
            self.app.add_handler(CallbackQueryHandler(handler, pattern=f"^{pattern}$"))

        # 4. Prefix callbacks
        for prefix, handler in [
            ("start_strat_", start_strategy_callback),
            ("stop_strat_", stop_strategy_callback),
            ("delete_strat_", delete_strategy_callback),
            ("toggle_notify_", toggle_notification_callback),
        ]:
            self.app.add_handler(CallbackQueryHandler(handler, pattern=f"^{prefix}"))

        for prefix in ["re_pos", "re_open", "re_exp", "re_loss",
                        "re_trades", "re_streak", "re_floor", "re_market"]:
            self.app.add_handler(CallbackQueryHandler(risk_field_edit_callback, pattern=f"^{prefix}$"))
        # Phase 53b: Force Exit toggle + edit
        self.app.add_handler(CallbackQueryHandler(force_exit_toggle_callback, pattern="^fe_toggle$"))
        self.app.add_handler(CallbackQueryHandler(force_exit_edit_callback, pattern="^fe_edit$"))

        self.app.add_handler(CallbackQueryHandler(stats_by_market_callback, pattern="^stats_by_market$"))
        self.app.add_handler(CallbackQueryHandler(strategy_stats_callback, pattern="^strategy_stats$"))
        self.app.add_handler(CallbackQueryHandler(trades_page_callback, pattern="^trades_page_"))  # Phase 52: /trades pagination
        self.app.add_handler(CallbackQueryHandler(risk_callback, pattern="^show_risk$"))
        self.app.add_handler(CallbackQueryHandler(diagnose_callback, pattern="^show_diagnose$"))  # Phase 62+: /diagnose refresh
        self.app.add_handler(CallbackQueryHandler(ws_callback, pattern="^show_ws$"))
        self.app.add_handler(CallbackQueryHandler(signals_callback, pattern="^show_signals$"))
        self.app.add_handler(CallbackQueryHandler(daily_callback, pattern="^show_daily$"))
        self.app.add_handler(CallbackQueryHandler(analytics_callback, pattern="^show_analytics$"))
        self.app.add_handler(CallbackQueryHandler(optimize_deploy_callback, pattern="^opt_deploy_"))
        self.app.add_handler(CallbackQueryHandler(autopilot_callback, pattern="^ap_"))
        self.app.add_handler(CallbackQueryHandler(backtest_v2_callback, pattern="^bt2_"))
        self.app.add_handler(CallbackQueryHandler(backtest_v2_config_callback, pattern="^bt2c_"))

        # Phase 79 S2-1: Strategy tester handlers
        for handler in get_test_strategy_handlers():
            self.app.add_handler(handler)

        # Phase 79 S3-09: Strategy lifecycle report
        self.app.add_handler(CommandHandler("report", report_command))
        self.app.add_handler(CallbackQueryHandler(report_refresh_callback, pattern="^report_refresh_"))

        # Phase 79 S1-12: Cancel handlers for heavy operations
        self.app.add_handler(CallbackQueryHandler(cancel_operation_callback, pattern="^cancel_backtest$"))
        # Hyperopt cancel + apply callback handlers removed 2026-04-28 (Heddas direktifi)

        # 2026-04-29 Polymarket Portfolio inline callbacks (Aşama 1+2)
        # tab_<name>, refresh, act_<deposit|withdraw|approve|wallet|pk>
        self.app.add_handler(CallbackQueryHandler(portfolio_callback, pattern="^pf_(tab_|refresh|act_)"))

        # 2026-04-29 Aşama 3.B: mode toggle inline callbacks
        # mode_set_<paper|real>, mode_refresh, mode_nav_<live|portfolio>
        self.app.add_handler(CallbackQueryHandler(mode_callback, pattern="^mode_(set_|refresh|nav_)"))

        for pat in ["show_api", "share_pnl", "import_wallet", "wallet_info_",
                     "wallet_key_", "wallet_delete_", "select_wallet_"]:
            self.app.add_handler(CallbackQueryHandler(self._ph(pat), pattern=f"^{pat}"))

        # Parameter info callbacks
        self.app.add_handler(CallbackQueryHandler(info_callback, pattern="^info_"))

        async def _risk_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if context.user_data.get("risk_editing"):
                return await handle_risk_input(update, context)
            if context.user_data.get("bt2_editing_limit"):
                return await handle_limit_input(update, context)
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _risk_text), group=5)

        # Phase 52 ÖNERİ #2 — double-slash typo recovery. Telegram Web's
        # autocomplete occasionally inserts "//dashboard" instead of the
        # intended "/dashboard" (the leading slash plus the picked hint
        # compound). CommandHandler ignores those, so the user sees no
        # response at all. Here we intercept the malformed input and
        # either route it to the real command or hint the correct form.
        async def _double_slash_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            msg = getattr(update, "message", None)
            if msg is None or not getattr(msg, "text", None):
                return
            text = msg.text.strip()
            # Must start with exactly two or more slashes.
            if not text.startswith("//"):
                return
            stripped = text.lstrip("/")
            if not stripped:
                return
            suggested = "/" + stripped.split()[0]
            from telegram_bot.templates.safe_html import esc as _esc
            await msg.reply_text(
                f"⚠️ Komut başında çift slash var.\n"
                f"<code>{_esc(suggested)}</code> mu demek istedin?",
                parse_mode="HTML",
            )
        self.app.add_handler(
            MessageHandler(filters.TEXT & filters.Regex(r"^/{2,}"),
                           _double_slash_handler),
            group=4,
        )

        # ── Phase 59b: Telegram Komut Menüsü ──────────────────────────
        # Telegram max 100 commands per scope. BotCommandScopeChat
        # OVERRIDES BotCommandScopeDefault, so admin gets ALL commands
        # (public + admin) in one combined list.
        #
        # Aliases (/d /s /m /ap /bt2 /h /rs /ss /pos /perf /sr /dba /dbh)
        # still work via CommandHandler but are NOT in the menu.

        # Phase 79 — Consolidated Help Menu: 20 core commands only
        # Aliases still work: /d /s /m /h /test /pos /perf /ss /rs /ap /cap /exp /lc
        # Advanced commands (50+) redirectable via /ai or moved to /help full text
        public_commands = [
            # ── Genel (3) ──
            BotCommand("dashboard", "Ana panel (alias: /d)"),
            BotCommand("menu", "Hub menu"),
            BotCommand("help", "Komutlar + aciklamalar"),
            # ── Strateji (5) ──
            BotCommand("strategies", "Strateji listesi (alias: /s)"),
            BotCommand("quick_strategy", "Hizli strateji olustur"),
            BotCommand("report", "Strateji yasam dongusu"),
            # Phase 82a hotfix: bulk actions (inline buttons truncate at 100-button cap)
            BotCommand("start_all", "Tum stratejileri baslat"),
            BotCommand("stop_all", "Tum stratejileri durdur"),
            # ── Test & Backtest (3) ──
            BotCommand("test_strategy", "Gercek veri test et (/test)"),
            BotCommand("backtest_v2", "Backtest v2 (alias: /bt2)"),
            # /hyperopt + /mc_kelly removed 2026-04-28 (Heddas direktifi)
            # ── Istatistik (3) ──
            BotCommand("stats_hub", "Tum istatistikler (tab menu)"),
            BotCommand("daily", "Gunluk ozet"),
            BotCommand("trades", "Son trade listesi"),
            BotCommand("portfolio", "Polymarket gercek cuzdan (alias: /pf)"),
            BotCommand("mode", "Paper/Real mode toggle (alias: /m)"),
            # ── Risk & Kontrol (3) ──
            BotCommand("risk_hub", "Risk yonetimi (tab menu)"),
            BotCommand("kill", "Acil durdur"),
            BotCommand("resume", "Devam et"),
            # ── AI & Analiz (2) ──
            BotCommand("brain", "AI Brain paneli"),
            BotCommand("analyze", "AI analiz baslat"),
            # ── Sistem (2) ──
            BotCommand("health", "Modul sagligi (/h)"),
            BotCommand("ws", "WebSocket durumu"),
        ]

        # Admin-only commands (appended to public for admin scope)
        admin_extra_commands = [
            BotCommand("shadow_report", "Shadow monitor raporu (alias: /sr)"),
            BotCommand("db_health", "DB saglik + tablo boyutlari (/dbh)"),
            BotCommand("db_cleanup", "DB cleanup (manuel, /dbc)"),
            BotCommand("db_archive", "OB arsiv (nightly, /dba)"),
            BotCommand("health_check", "Eski health check (job durumu, /hc)"),
            BotCommand("canary", "Canary stage yonetimi"),
            BotCommand("promote", "Stage yukselt"),
            BotCommand("demote", "Stage geri al"),
            BotCommand("filters", "Trade filtre paneli (on/off, alias: /f)"),
            BotCommand("diagnose", "Trade pipeline tani raporu"),
            # Becker BotCommand entries removed 2026-04-28 (Heddas direktifi)
            BotCommand("experiment_apply", "Experiment sonucunu uygula"),
            BotCommand("experiment_discard", "Experiment sonucunu iptal et"),
            # /hyperopt_all + /hyperopt_status removed 2026-04-28 (Heddas direktifi)
        ]

        # Set public commands for all users
        await self.app.bot.set_my_commands(
            public_commands,
            scope=BotCommandScopeDefault()
        )

        # Admin gets ALL commands (public + admin extras combined)
        admin_id = os.getenv("ADMIN_TELEGRAM_ID")
        if admin_id:
            try:
                combined = public_commands + admin_extra_commands
                await self.app.bot.set_my_commands(
                    combined,
                    scope=BotCommandScopeChat(chat_id=int(admin_id))
                )
                logger.info(f"✅ Admin commands set ({len(combined)} total) for chat_id={admin_id}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"⚠️ Could not set admin commands: {e}")
        else:
            logger.warning("⚠️ ADMIN_TELEGRAM_ID not set — admin-only commands hidden. Send /sr to capture.")

        self.app.add_error_handler(self._err)

        # Phase 53b: Restore persisted force_exit_seconds from DB
        try:
            saved_fe = await self.db.get_setting("risk.force_exit_seconds")
            if saved_fe is not None:
                import core.engine_monitor as _em
                _em.FORCE_EXIT_SECONDS = int(saved_fe)
                logger.info(f"✅ force_exit_seconds restored from DB: {saved_fe}s")
        except Exception as _fe_err:  # noqa: BLE001
            logger.warning(f"force_exit_seconds restore failed: {_fe_err}")

        # Phase 47f.7+ in-bot shadow report (replaces sandbox scheduled task)
        try:
            # Seed ADMIN_TELEGRAM_ID from persisted file if env is missing/0,
            # so JobQueue auto-runs work even before any /sr is sent.
            try:
                from telegram_bot.jobs.shadow_report_job import resolve_admin_chat_id
                _admin = resolve_admin_chat_id()
                if _admin:
                    os.environ["ADMIN_TELEGRAM_ID"] = str(_admin)
                    logger.info(f"✅ admin_chat_id resolved → {_admin}")
                else:
                    logger.warning("⚠️ admin_chat_id not set — send /sr in Telegram once to capture it")
            except Exception as _ae:  # noqa: BLE001
                logger.warning(f"admin_chat resolve failed: {_ae}")

            jq = self.app.job_queue
            if jq is not None:
                interval = int(os.getenv("SHADOW_REPORT_INTERVAL_SEC", "1800"))  # 30 min
                first = int(os.getenv("SHADOW_REPORT_FIRST_SEC", "60"))
                jq.run_repeating(shadow_report_job, interval=interval, first=first,
                                 name="shadow_report")
                logger.info(f"✅ shadow_report job scheduled (every {interval}s, first in {first}s)")

                # P3-11: heartbeat ping (every 10 min) + daily DB snapshot (every 24h)
                hb_interval = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "600"))
                jq.run_repeating(heartbeat_job, interval=hb_interval, first=120,
                                 name="heartbeat")
                logger.info(f"✅ heartbeat job scheduled (every {hb_interval}s)")

                snap_interval = int(os.getenv("DB_SNAPSHOT_INTERVAL_SEC", "86400"))
                snap_first = int(os.getenv("DB_SNAPSHOT_FIRST_SEC", "300"))
                jq.run_repeating(daily_db_snapshot_job, interval=snap_interval,
                                 first=snap_first, name="daily_db_snapshot")
                logger.info(f"✅ daily_db_snapshot job scheduled (every {snap_interval}s)")

                # Epic 5 T5.5 (2026-04-21): periodic WAL TRUNCATE checkpoint
                # prevents WAL bloat when long-read connections (backup job,
                # ro_connect) block autocheckpoint. Default 6h — at 8.8GB DB
                # with ~20MB/hr write pressure, 6h keeps WAL < 200 MB.
                wal_ckpt_hours = int(os.getenv("WAL_CHECKPOINT_INTERVAL_HOURS", "6"))
                wal_ckpt_interval = wal_ckpt_hours * 3600
                wal_ckpt_first = int(os.getenv("WAL_CHECKPOINT_FIRST_SEC", "1200"))  # 20 min after boot
                jq.run_repeating(wal_checkpoint_job, interval=wal_ckpt_interval,
                                 first=wal_ckpt_first, name="wal_checkpoint")
                logger.info(
                    f"✅ wal_checkpoint job scheduled (every {wal_ckpt_hours}h, "
                    f"first in {wal_ckpt_first}s)")

                # Phase 47f.8: DB retention — prune old ob_snapshots/candles nightly
                ret_interval = int(os.getenv("DB_RETENTION_INTERVAL_SEC", "86400"))
                ret_first = int(os.getenv("DB_RETENTION_FIRST_SEC", "900"))
                jq.run_repeating(db_retention_job, interval=ret_interval,
                                 first=ret_first, name="db_retention")
                logger.info(f"✅ db_retention job scheduled (every {ret_interval}s, first in {ret_first}s)")

                # Phase 47f.10 P5#22: hourly shadow vs paper anomaly compare
                svp_interval = int(os.getenv("SHADOW_COMPARE_INTERVAL_SEC", "3600"))
                svp_first = int(os.getenv("SHADOW_COMPARE_FIRST_SEC", "1800"))
                jq.run_repeating(shadow_vs_paper_job, interval=svp_interval,
                                 first=svp_first, name="shadow_vs_paper")
                logger.info(f"✅ shadow_vs_paper job scheduled (every {svp_interval}s, first in {svp_first}s)")

                # 2026-04-29 Polymarket Portfolio refresh (Aşama 1)
                if os.getenv("PORTFOLIO_REFRESH_ENABLED", "true").lower() == "true":
                    pf_interval = int(os.getenv("PORTFOLIO_REFRESH_SEC", "60"))
                    pf_first = int(os.getenv("PORTFOLIO_REFRESH_FIRST_SEC", "30"))
                    jq.run_repeating(polymarket_portfolio_job, interval=pf_interval,
                                     first=pf_first, name="polymarket_portfolio")
                    logger.info(
                        f"✅ polymarket_portfolio job scheduled "
                        f"(every {pf_interval}s, first in {pf_first}s)"
                    )

                # Phase 66: Daily PnL divergence alert (paper vs live aggregate)
                if os.getenv("PNL_DIVERGENCE_ENABLED", "true").lower() == "true":
                    pnl_div_interval = int(os.getenv("PNL_DIVERGENCE_INTERVAL_SEC", "86400"))  # daily
                    pnl_div_first = int(os.getenv("PNL_DIVERGENCE_FIRST_SEC", "3600"))  # 1h after boot
                    jq.run_repeating(pnl_divergence_job, interval=pnl_div_interval,
                                     first=pnl_div_first, name="pnl_divergence")
                    logger.info(f"✅ pnl_divergence job scheduled (every {pnl_div_interval}s)")

                # Tournament job removed 2026-04-28 (Heddas direktifi: Hyperopt
                # tam silme — tournament_job hyperopt subprocess'e dayalıydı).

                # Phase 50 (Suggestion 12.3) — price alert watcher
                if os.getenv("PRICE_ALERT_ENABLED", "1") == "1":
                    pa_interval = int(os.getenv("PRICE_ALERT_INTERVAL_SEC", "30"))
                    self.app.bot_data["odds_feed"] = self.odds_feed
                    jq.run_repeating(price_alert_job, interval=pa_interval,
                                     first=pa_interval, name="price_alert")
                    logger.info(f"✅ price_alert job scheduled (every {pa_interval}s)")

                # Phase 48: daily auto-promote canary → promoted
                if os.getenv("AUTO_PROMOTE_ENABLED", "1") == "1":
                    ap_interval = int(os.getenv("AUTO_PROMOTE_INTERVAL_SEC", "86400"))
                    ap_first = int(os.getenv("AUTO_PROMOTE_FIRST_SEC", "1200"))
                    jq.run_repeating(auto_promote_job, interval=ap_interval,
                                     first=ap_first, name="auto_promote")
                    logger.info(f"✅ auto_promote job scheduled (every {ap_interval}s, first in {ap_first}s)")

                # Phase 59: Weekly pattern discovery
                if os.getenv("PATTERN_DISCOVERY_ENABLED", "1") == "1":
                    from telegram_bot.jobs.pattern_discovery_job import pattern_discovery_callback
                    pd_interval = int(os.getenv("PATTERN_DISCOVERY_INTERVAL_SEC", "604800"))  # weekly
                    pd_first = int(os.getenv("PATTERN_DISCOVERY_FIRST_SEC", "3600"))  # 1h after startup
                    jq.run_repeating(pattern_discovery_callback, interval=pd_interval,
                                     first=pd_first, name="pattern_discovery")
                    logger.info(f"✅ pattern_discovery job scheduled (every {pd_interval}s)")

                # Phase 59 DB-01b: nightly OB archive to parquet
                if os.getenv("DB_ARCHIVE_ENABLED", "1") == "1":
                    ar_interval = int(os.getenv("DB_ARCHIVE_INTERVAL_SEC", "86400"))  # daily
                    ar_first = int(os.getenv("DB_ARCHIVE_FIRST_SEC", "600"))  # 10 min after startup
                    jq.run_repeating(db_archive_job, interval=ar_interval,
                                    first=ar_first, name="db_archive")
                    logger.info(f"✅ db_archive job scheduled (every {ar_interval}s, first in {ar_first}s)")

                # Phase 79b: Strategy Suggester (every 4h, Claude finds niche edges)
                if os.getenv("STRATEGY_SUGGESTER_ENABLED", "true").lower() == "true":
                    try:
                        from core.strategy_suggester import StrategySuggester, SUGGEST_INTERVAL
                        _suggester = StrategySuggester(self.db, self.engine, self.app)
                        self.app.bot_data["strategy_suggester"] = _suggester

                        async def _suggest_job(context):
                            s = context.application.bot_data.get("strategy_suggester")
                            if s:
                                await s.run()

                        sg_interval = int(os.getenv("AI_STRATEGY_SUGGEST_INTERVAL", str(SUGGEST_INTERVAL)))
                        sg_first = int(os.getenv("AI_STRATEGY_SUGGEST_FIRST", "1800"))  # 30min after startup
                        jq.run_repeating(_suggest_job, interval=sg_interval,
                                         first=sg_first, name="strategy_suggester")
                        logger.info(f"✅ strategy_suggester job scheduled (every {sg_interval}s, first in {sg_first}s)")
                    except Exception as _sg_e:  # noqa: BLE001
                        logger.warning(f"Strategy Suggester schedule failed: {_sg_e}")

                # Becker rolling recalibration job removed 2026-04-28 (Heddas direktifi)
            else:
                logger.warning("JobQueue is None — shadow_report disabled. "
                               "Install python-telegram-bot[job-queue]")
        except Exception as _je:  # noqa: BLE001
            logger.exception(f"Failed to schedule shadow_report: {_je}")

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

        # Phase 82e Sprint 2.1: register bg_task notify handler so any
        # safe_create_task() failure alerts admin on Telegram. Cooldown +
        # rate limit are internal to bg_task module (BG_TASK_NOTIFY_COOLDOWN_SEC).
        try:
            admin_id = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_CHAT_ID")
            if admin_id:
                handler = make_telegram_notify_handler(self.app, int(admin_id))
                set_notify_handler(handler)
                logger.info(
                    f"🛡️ bg_task notify handler registered → chat {admin_id}")
            else:
                logger.warning(
                    "bg_task notify disabled (ADMIN_TELEGRAM_ID / ADMIN_CHAT_ID unset)")
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"bg_task notify handler setup failed: {_e}")

        logger.info(f"✅ PolyPaper Bot {BOT_VERSION} — {BOT_CODENAME} is live!")

        import asyncio
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    def _ph(self, name):
        async def h(update: Update, context: ContextTypes.DEFAULT_TYPE):
            await update.callback_query.answer(f"🚧 {name} - Yakinda!", show_alert=True)
        return h

    async def _ap_redirect(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        engine = context.bot_data.get("engine")
        if not engine or not engine.autopilot:
            return await update.callback_query.message.reply_text("AutoPilot aktif degil.")
        await update.callback_query.message.reply_text("🤖 Analiz ediliyor...")
        actions = await engine.autopilot.generate_actions()
        if not actions:
            return await update.callback_query.message.reply_text(
                "🤖 <b>AutoPilot</b>\n\n✅ Oneri yok.", parse_mode="HTML")
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        for action in actions:
            aid = await engine.autopilot.store_pending(action)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Onayla", callback_data=f"ap_yes_{aid}"),
                InlineKeyboardButton("❌ Reddet", callback_data=f"ap_no_{aid}")]])
            await update.callback_query.message.reply_text(
                f"{action['emoji']} <b>{action['desc']}</b>\n{action['reason']}",
                parse_mode="HTML", reply_markup=kb)

    async def _help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Phase 79: Consolidated /help — 20 core commands only + alias pointer
        is_admin = False
        admin_id = os.getenv("ADMIN_TELEGRAM_ID")
        if admin_id and update.effective_user:
            is_admin = str(update.effective_user.id) == str(admin_id)

        core_help = (
            # T0.5 2026-04-20: drop hardcoded "Phase 79" — follow BOT_CODENAME
            f"📋 <b>PolyPaper Bot {BOT_VERSION} — {BOT_CODENAME}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"

            "<b>🏠 Genel</b>\n"
            "/dashboard — Ana panel <i>(/d)</i>\n"
            "/menu — Hub menu\n"
            "/help — Bu ekran\n\n"

            "<b>🎯 Strateji</b>\n"
            "/strategies — Strateji listesi <i>(/s)</i>\n"
            "/quick_strategy — Hizli strateji olustur\n"
            "/report — Strateji raporu\n\n"

            "<b>🧪 Test &amp; Backtest</b>\n"
            "/test_strategy — Gercek veri ile test <i>(/test)</i>\n"
            "/backtest_v2 — Backtest v2 <i>(/bt2)</i>\n\n"

            "<b>📊 Istatistik</b>\n"
            "/stats_hub — Tum istatistikler (tab menu)\n"
            "/daily — Gunluk ozet\n"
            "/trades — Son trade listesi\n\n"

            "<b>🛡 Risk &amp; Kontrol</b>\n"
            "/risk_hub — Risk yonetimi (tab menu)\n"
            "/kill — Acil durdur\n"
            "/resume — Devam et\n\n"

            "<b>🧠 AI &amp; Analiz</b>\n"
            "/brain — AI Brain kontrol paneli\n"
            "/analyze — AI analiz baslat\n\n"

            "<b>⚙️ Sistem</b>\n"
            "/health — Modul sagligi <i>(/h)</i>\n"
            "/ws — WebSocket durumu\n\n"
        )

        # Aliases reference
        alias_text = (
            "<b>📌 Diger Komutlar (Alias)</b>\n"
            "Hala calisir ama menu'de gozukmez:\n"
            "<code>/lifecycle (/lc), /risks (/rs), /kelly, /pattern, /markov, "
            "/capital (/cap), /experiment (/exp)</code>\n\n"
        )

        # Admin section
        admin_section = ""
        if is_admin:
            admin_section = (
                "<b>🔒 Admin Komutlari</b>\n"
                "/shadow_report <i>(/sr)</i> | /db_health <i>(/dbh)</i> | "
                "/db_cleanup <i>(/dbc)</i> | /diagnose | /filters <i>(/f)</i>\n\n"
            )

        footer = (
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "💡 <i>50+ ileri komut var. /ai ile Turkce arayabilirsin!</i>\n"
            "<i>Ornek: /ai son 3 gunun ozeti</i>"
        )

        full_text = core_help + alias_text + admin_section + footer

        await update.message.reply_text(full_text, parse_mode="HTML")

    async def _shadow_report_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manuel olarak shadow monitor raporunu tetikle (admin)."""
        try:
            from telegram_bot.jobs.shadow_report_job import (
                shadow_report_job,
                save_admin_chat_id,
                resolve_admin_chat_id,
            )

            # Capture caller's chat_id and persist it so JobQueue auto-runs work too.
            caller_chat_id = update.effective_chat.id if update.effective_chat else None
            if caller_chat_id:
                save_admin_chat_id(caller_chat_id)
                # Also seed env so any other code path picks it up immediately.
                os.environ["ADMIN_TELEGRAM_ID"] = str(caller_chat_id)

            await update.message.reply_text(
                f"🔄 Shadow report tetikleniyor... (chat_id=<code>{caller_chat_id}</code> kaydedildi)",
                parse_mode="HTML",
            )

            pushed = await shadow_report_job(
                context, force=True, override_chat_id=caller_chat_id
            )
            if pushed and pushed > 0:
                await update.message.reply_text(
                    f"✅ Shadow report tamamlandı — {pushed} mesaj gönderildi."
                )
            else:
                await update.message.reply_text(
                    "⚠️ Shadow report çalıştı ama hiç mesaj gönderilmedi.\n"
                    "(DB'de hiç strateji yok veya quiet hours.)"
                )
        except Exception as e:  # noqa: BLE001
            logger.exception(f"manual shadow_report failed: {e}")
            await update.message.reply_text(f"❌ Hata: {type(e).__name__}: {e}")

    async def _db_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Top-N table sizes + DB file size + WAL size + total executions count."""
        try:
            db = context.application.bot_data.get("db")
            if db is None:
                await update.message.reply_text("❌ DB bağlı değil.")
                return
            cur = await db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            tables = [r[0] for r in await cur.fetchall()]
            rows = []
            for t in tables:
                try:
                    c = await db.conn.execute(f"SELECT COUNT(*) FROM {t}")
                    n = (await c.fetchone())[0]
                    rows.append((t, n))
                except Exception:  # noqa: BLE001
                    pass
            rows.sort(key=lambda r: r[1], reverse=True)
            top = rows[:15]
            from pathlib import Path
            db_path = Path("data_store/polypaper.db")
            wal_path = Path("data_store/polypaper.db-wal")
            db_mb = db_path.stat().st_size / (1024 * 1024) if db_path.exists() else 0
            wal_mb = wal_path.stat().st_size / (1024 * 1024) if wal_path.exists() else 0
            lines = [
                "<b>📦 DB Health</b>",
                f"polypaper.db = <code>{db_mb:.1f} MB</code>",
                f"polypaper.db-wal = <code>{wal_mb:.1f} MB</code>",
                f"tables: <code>{len(tables)}</code>",
                "",
                "<b>Top 15 by row count:</b>",
            ]
            for name, n in top:
                lines.append(f"  <code>{name}</code> — <code>{n:,}</code> rows")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:  # noqa: BLE001
            logger.exception(f"db_health failed: {e}")
            await update.message.reply_text(f"❌ Hata: {type(e).__name__}: {e}")

    async def _db_cleanup(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manuel DB retention tetikleyici — /db_cleanup veya /dbc."""
        try:
            from telegram_bot.jobs.db_retention_job import db_retention_job
            await update.message.reply_text(
                "🧹 DB retention başlatılıyor... (büyük DB'lerde 1-2 dk sürebilir)",
                parse_mode="HTML",
            )
            summary = await db_retention_job(context, force_notify=True)
            total = sum(summary.values())
            await update.message.reply_text(
                f"✅ Tamam — toplam <code>{total:,}</code> satır silindi.",
                parse_mode="HTML",
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(f"db_cleanup failed: {e}")
            await update.message.reply_text(f"❌ Hata: {type(e).__name__}: {e}")

    async def _db_archive(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Phase 59 DB-01b: Manuel OB archive tetikleyici — /db_archive veya /dba.

        Moves old ob_snapshots to parquet, optionally VACUUMs DB.
        """
        try:
            from telegram_bot.jobs.db_archive_job import db_archive_command
            await db_archive_command(update, context)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"db_archive failed: {e}")
            await update.message.reply_text(f"❌ Hata: {type(e).__name__}: {e}")

    async def _risk_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Risk + halt + son 10 trade WR snapshot."""
        try:
            db = context.application.bot_data.get("db")
            engine = context.application.bot_data.get("engine")
            risk = getattr(engine, "risk", None) if engine else None
            state = getattr(risk, "state", None) if risk is not None else None
            lines = ["<b>🛡️ Risk Status</b>"]
            if state is not None:
                halted = getattr(state, "halted", False)
                halt_reason = getattr(state, "halt_reason", "")
                daily_pnl = getattr(state, "daily_pnl", 0.0)
                cons_losses = getattr(state, "consecutive_losses", 0)
                daily_trades = getattr(state, "daily_trade_count", 0)
                open_pos = getattr(state, "open_position_count", 0)
                exposure = getattr(state, "total_exposure", 0.0)
                lines.append(f"halted: <code>{halted}</code>" + (f" ({halt_reason})" if halt_reason else ""))
                lines.append(f"daily_pnl: <code>{daily_pnl:+.2f}</code>")
                lines.append(f"consecutive_losses: <code>{cons_losses}</code>")
                lines.append(f"daily_trades: <code>{daily_trades}</code>")
                lines.append(f"open_positions: <code>{open_pos}</code>")
                lines.append(f"total_exposure: <code>${exposure:.2f}</code>")
            else:
                lines.append("<i>risk module unavailable</i>")
            if db is not None:
                cur = await db.conn.execute(
                    "SELECT pnl FROM executions WHERE status IN ('closed','settled','won','lost') "
                    "ORDER BY created_at DESC LIMIT 10"
                )
                last10 = [r[0] or 0 for r in await cur.fetchall()]
                if last10:
                    wins = sum(1 for p in last10 if p > 0)
                    lines.append("")
                    lines.append("<b>Son 10 trade</b>")
                    lines.append(f"WR=<code>{wins/len(last10)*100:.0f}%</code> "
                                 f"PnL=<code>{sum(last10):+.2f}</code>")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:  # noqa: BLE001
            logger.exception(f"risk_status failed: {e}")
            await update.message.reply_text(f"❌ Hata: {type(e).__name__}: {e}")

    async def _health_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Bot heartbeat + scheduler + DB ping."""
        try:
            from datetime import datetime
            db = context.application.bot_data.get("db")
            engine = context.application.bot_data.get("engine")
            jq = context.application.job_queue
            jobs = [j.name for j in jq.jobs()] if jq else []
            db_ok = "OK"
            if db is not None:
                try:
                    await db.conn.execute("SELECT 1")
                except Exception as e:  # noqa: BLE001
                    db_ok = f"ERR: {e}"
            # Phase 53b: force exit counter
            fe_count = getattr(engine, '_force_exits_today', 0) if engine else 0
            from core.engine_monitor import FORCE_EXIT_SECONDS
            fe_cfg = f"{FORCE_EXIT_SECONDS}s" if FORCE_EXIT_SECONDS > 0 else "OFF"
            lines = [
                "<b>❤️ Health</b>",
                f"time: <code>{datetime.utcnow().strftime('%H:%M:%S')}</code> UTC",
                f"db: <code>{db_ok}</code>",
                f"engine: <code>{'OK' if engine else 'NULL'}</code>",
                f"jobs ({len(jobs)}): <code>{', '.join(jobs) or 'none'}</code>",
                f"force_exit: <code>{fe_cfg}</code> (today: {fe_count})",
            ]
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:  # noqa: BLE001
            logger.exception(f"health failed: {e}")
            await update.message.reply_text(f"❌ Hata: {type(e).__name__}: {e}")

    async def _err(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Error: {context.error}", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text("⚠️ Hata. /start dene")
            except Exception:  # noqa: BLE001
                pass
