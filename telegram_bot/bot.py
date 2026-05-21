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

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config.settings import Settings
from core.bg_task import (  # Phase 82e Sprint 2.1: bg task exception guard
    make_telegram_notify_handler,
    set_notify_handler,
)
from db.database import Database

# Phase 51 P51-04/P51-05 — natural language intent parser
from telegram_bot.handlers.ai_handler import (  # Phase 51 P51-03 Faz-2 Cluster D
    ai_approval_callback,
    ai_command,  # Sprint 3 S3-04 + Phase 79b
    ai_confirm_callback,
    analyze_apply_callback,
    analyze_brain_callback,
    brain_command,  # merged from brain_handler.py
    brain_toggle_callback,
    drift_command,
    monitor_command,
    regime_command,  # merged from intelligence_handler.py (T1.3 Commit 3: validate_command ghost removed)
    suggest_callback,
    ts_command,
)
from telegram_bot.handlers.archive_info_handler import (  # Phase 82e Sprint B.2: archive reader diag
    archive_info_command,
)
from telegram_bot.handlers.backtest_lab import (  # 2026-05-20: /backtest LAB mode-first
    backtest_lab_callback,
    backtest_lab_command,
    lab_save_command,  # Faz 4 — JSON paste flow
)
from telegram_bot.handlers.backtest_v2 import (  # Phase 51 P51-03 Faz-2 Cluster F
    backtest_replay_command,  # merged from backtest_replay.py
    backtest_v2_cmd,  # 2026-05-21: LAB'a yonlendiren deprecation shim
    # Becker commands removed 2026-04-28; engine_v2 yolu removed 2026-05-21.
    # backtest_v2_callback / backtest_v2_config_callback / handle_limit_input
    # silindi — eski PolyCop config panel yolu artik LAB tek kapida.
    cancel_operation_callback,  # Phase 79 S1-12: Cancel for backtest/compare
    compare_cmd,
    replay_callback,
)
from telegram_bot.handlers.brier_handler import brier_command  # Phase 66
from telegram_bot.handlers.changelog_handler import (  # Phase 82e Sprint A: /analyze verification
    changelog_command,
)
from telegram_bot.handlers.dashboard import (  # Phase 51 P51-03 Faz-2 Cluster J
    add_funds_command,
    alert_delete_cmd,
    alert_set_cmd,  # merged from price_alert_handler.py
    alerts_list_cmd,
    dashboard_callback,
    dashboard_command,
    info_callback,  # merged from info_handler.py
    journal_command,  # merged from journal.py
    price_alert_job,
    refresh_dashboard_callback,
)

# P0-08-E7 (2026-05-08): backtest data storage panel
from telegram_bot.handlers.data_status_handler import data_status_command
from telegram_bot.handlers.diagnose_handler import (  # Phase 62+: trade pipeline diagnostics
    diagnose_callback,
    diagnose_command,
)
from telegram_bot.handlers.env_toggle import (  # Phase 82e Sprint 6: hot-tune runtime env
    env_toggle_command,
)
from telegram_bot.handlers.filters_handler import (  # Phase 66: filter toggle panel
    _load_persisted_filters,
    filters_callback,
    filters_command,
)
from telegram_bot.handlers.force_settle_handler import (  # Sprint 5 HOTFIX v4: manual oracle settle
    force_settle_command,
)
from telegram_bot.handlers.lifecycle_handler import lifecycle_command  # Phase 74b
from telegram_bot.handlers.live_guards_handler import (  # Epic 11 T11.2 [D]: 6-guard live snapshot
    live_guards_command,
)
from telegram_bot.handlers.live_handler import (  # Phase 51 P51-03 Faz-2 Cluster H
    allowance_command,
    buy_command,
    daily_callback,
    daily_command,  # merged from daily_handler.py
    live_callback,
    live_command,
    sell_command,
    ws_callback,
    ws_command,  # merged from ws_handler.py
)
from telegram_bot.handlers.markets import (  # Phase 51 P51-03 Faz-2 Cluster C
    candle_refresh_callback,
    candles_command,  # merged from candle_handler.py
    markets_command,
    refresh_markets_callback,
    signals_callback,
    signals_command,  # merged from signals_handler.py
)
from telegram_bot.handlers.menu_handler import (
    menu_advanced_callback,
    menu_backtest_callback,
    menu_brain_callback,
    menu_bt_compare_callback,
    menu_bt_replay_callback,
    menu_bt_v2_callback,
    menu_candles_callback,
    menu_cmd_mistakes_callback,
    # T1.3 Commit 4: markov/capital callback imports silindi
    # T1.3 Commit 6: breed/vote/whale callback imports silindi (roadmap_handler ghost)
    menu_command,
    menu_dashboard_callback,
    menu_experiment_callback,
    menu_health_callback,
    menu_help_callback,
    menu_learning_callback,
    menu_live_callback,
    menu_market_callback,
    menu_positions_callback,
    menu_refresh_callback,
    menu_risk_callback,
    menu_settings_callback,
    menu_stats_callback,
    menu_strategies_callback,
)

# 2026-04-29 Aşama 3.B: top-level mode toggle (Paper vs Real)
from telegram_bot.handlers.mode_handler import mode_callback, mode_command

# Becker recal handler removed 2026-04-28 (Heddas direktifi)
# T1.3 Commit 4 (2026-04-20): phase76_handler silindi —
# markov_command + capital_command ghost modüllere (core.markov_estimator,
# core.capital_allocator) bağlıydı, Phase 76 ayağı Phase 79b sonrası ölü kalmıştı.
from telegram_bot.handlers.phase77_handler import (  # Phase 77
    experiment_apply_command,
    experiment_command,
    experiment_discard_command,
    health_callback,
    health_command,
    mistakes_command,
    patterns_callback,
    patterns_command,
    why_callback,
    why_command,
)

# 2026-04-29 Polymarket Portfolio (Aşama 1): gerçek Proxy cüzdan view
from telegram_bot.handlers.portfolio_handler import (
    portfolio_callback,
    portfolio_command,
)
from telegram_bot.handlers.positions import positions_callback, positions_command

# P1-03-c (2026-05-09): reality gap (paper-vs-live drift) panel
from telegram_bot.handlers.reality_gap_handler import reality_gap_command

# P1-09-c (2026-05-09): on-chain reconciliation status panel
from telegram_bot.handlers.recon_handler import recon_command

# P0-07-f (2026-05-09): reference price audit panel
from telegram_bot.handlers.ref_audit_handler import ref_audit_command
from telegram_bot.handlers.rest_timing_handler import (  # Epic 4 T4.8: REST RTT telemetry summary
    dump_rest_timing_command,
)
from telegram_bot.handlers.risk_handler import (
    force_exit_edit_callback,
    force_exit_toggle_callback,  # Phase 53b
    handle_risk_input,
    kill_command,
    resume_command,
    risk_callback,
    risk_command,
    risk_field_edit_callback,
    risk_hub_callback,
    # Phase 51 P51-03 — risk_hub merged into risk_handler.py
    risk_hub_command,
    risk_set_command,
    streak_reset_command,
)

# Tournament job removed 2026-04-28 (Heddas direktifi: Hyperopt tam silme,
# tournament_job ana işi hyperopt subprocess çalıştırmaktı).
# Hyperopt handler removed 2026-04-28 (Heddas direktifi: tam silme)
# Phase 67/82b/82e ait /hyperopt /hyperopt_all /hyperopt_status /hyperopt_abort
# /mc_kelly komutları + cancel + apply callback'leri kaldırıldı.
from telegram_bot.handlers.roadmap_handler import (  # Phase 70-73
    # T1.3 Commit 5 (2026-04-20): breed/vote/drift_check/whale/market_quality/
    # correlation_check import'ları silindi (ghost modüller).
    ev_stats_command,
    latency_command,
    metrics_command,
    surface_command,
)
from telegram_bot.handlers.settings_handler import (  # Phase 51 P51-03 Faz-2 Cluster G
    canary_command,  # merged from promote.py
    demote_command,
    plugin_set_command,
    plugins_command,  # merged from plugins_handler.py
    promote_command,
    settings_callback,
    settings_command,
    toggle_notification_callback,
)
from telegram_bot.handlers.start import (  # Phase 51 P51-03 Faz-2 — wallets merged
    accept_terms_callback,
    deposit_instructions_callback,
    new_wallet_callback,
    referrals_callback,
    # referrals merged
    referrals_command,
    start_command,
    wallets_callback,
    wallets_command,
    withdraw_callback,
    # withdraw merged
    withdraw_command,
    withdraw_funds_command,
)
from telegram_bot.handlers.stats import (
    analytics_callback,
    analytics_command,
    performance_command,
    stats_by_market_callback,
    stats_callback,
    # Phase 51 P51-03 Faz-2 — stats_chart + performance + analytics merged
    stats_chart_command,
    stats_command,
    stats_hub_callback,
    # Phase 51 P51-03 — stats_hub merged into stats.py
    stats_hub_command,
    strategy_stats_callback,
    strategy_stats_command,
    trades_command,  # Phase 52: /trades with pagination
    trades_page_callback,
)

# Phase 51 P51-03 Faz-2 Cluster I — kelly/maker/micro/recorder merged into strategies.py
from telegram_bot.handlers.strategies import (  # Phase 51 P51-03 Faz-2 Cluster E
    analyze_command,
    analyze_optimize_command,
    autopilot_callback,
    autopilot_command,  # merged from autopilot_handler.py
    clone_command,
    delete_strategy_callback,
    edit_command,
    kelly_command,
    kelly_toggle_command,
    maker_stats_command,
    micro_command,
    optimize_command,  # merged from optimize_handler.py
    optimize_deploy_callback,
    quick_strategy_command,  # Phase 52 BUG #2
    quick_strategy_wizard_callback,
    recorder_command,
    recorder_refresh_callback,
    start_all_callback,
    start_all_command,
    start_strategy_callback,
    stop_all_callback,
    stop_all_command,
    stop_strategy_callback,
    strategies_callback,
    strategies_command,
    # Phase 82a hotfix: pagination + slash commands for /start_all /stop_all
    strategies_page_callback,
)
from telegram_bot.handlers.strategy_builder import get_strategy_builder_handler
from telegram_bot.handlers.strategy_report import (  # Phase 79 S3-09: Strategy lifecycle report
    report_command,
    report_refresh_callback,
)
from telegram_bot.handlers.strategy_tester import (  # Phase 79 S2-1: Test user strategies
    get_test_strategy_handlers,
)
from telegram_bot.jobs.auto_promote_job import auto_promote_job  # Phase 48
from telegram_bot.jobs.db_archive_job import db_archive_job  # Phase 59 DB-01b
from telegram_bot.jobs.db_retention_job import db_retention_job

# Becker rolling recal job removed 2026-04-28 (Heddas direktifi)
from telegram_bot.jobs.maintenance_jobs import (
    daily_db_snapshot_job,
    heartbeat_job,
    wal_checkpoint_job,
)
from telegram_bot.jobs.pnl_divergence_job import pnl_divergence_job  # Phase 66
from telegram_bot.jobs.polymarket_portfolio_job import polymarket_portfolio_job
from telegram_bot.jobs.reality_gap_job import reality_gap_job  # P1-03-b (2026-05-09)

# Phase 47f.7+ in-bot shadow report job (replaces broken sandbox scheduled task)
from telegram_bot.jobs.shadow_report_job import shadow_report_job
from telegram_bot.jobs.shadow_vs_paper_job import shadow_vs_paper_job  # Phase 47f.10 P5#22
from telegram_bot.version import BOT_CODENAME, BOT_VERSION

logger = logging.getLogger("polypaper.bot")


class PolyPaperBot:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        scanner=None,
        engine=None,
        odds_feed=None,
        poly_client=None,
        ws_client=None,
    ):
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
        self.app.bot_data.update(
            {
                "db": self.db,
                "settings": self.settings,
                "scanner": self.scanner,
                "engine": self.engine,
                "odds_feed": self.odds_feed,
                "poly_client": self.poly_client,
                "ws_client": getattr(self, "_ws_client", None),
                # Phase 51 P51-04 — mount bot instance so /ai router can
                # dispatch bot-class methods (_health_check, _risk_status,
                # _db_health, _shadow_report_now).
                "bot": self,
            }
        )
        if self.engine:
            self.engine.bot_app = self.app

        # Phase 66: load persisted filter overrides from DB
        await _load_persisted_filters(self.db)

        # 1. Conversation handlers FIRST
        self.app.add_handler(get_strategy_builder_handler())

        # 2. All commands with shortcuts
        # 2026-05-06 Heddas direktifi: mod-first /start dashboard (paper vs live)
        try:
            from telegram_bot.handlers.live_history_handler import (
                live_history_callback,
                live_history_command,
            )
            from telegram_bot.handlers.main_dashboard import (
                main_callback,
                main_command,
            )

            _MOD_FIRST_OK = True
        except ImportError as _md_err:
            logger.warning(f"main_dashboard import: {_md_err}")
            _MOD_FIRST_OK = False
            main_command = start_command  # fallback
            live_history_command = lambda *a, **kw: None  # noqa: E731

        cmds = [
            ("start", main_command if _MOD_FIRST_OK else start_command),
            ("paper", main_command if _MOD_FIRST_OK else dashboard_command),
            ("legacy_start", start_command),  # eski davranış için fallback
            ("lh", live_history_command),
            ("livehistory", live_history_command),
            # 2026-05-19 Heddas direktifi "tek kapı": /dashboard + /d de
            # mode-seçim ekranını açar — bot'un tek girişi var, oradan
            # paper/live dünyalarına dağılır. Eski detaylı dashboard
            # içeriği PAPER MODE altında gösterilir (dashboard._build).
            ("dashboard", main_command if _MOD_FIRST_OK else dashboard_command),
            ("d", main_command if _MOD_FIRST_OK else dashboard_command),
            ("menu", menu_command),
            ("strategies", strategies_command),
            ("s", strategies_command),
            ("quick_strategy", quick_strategy_command),
            # Phase 82a hotfix: bulk slash commands (fallback when Telegram
            # truncates inline bulk buttons due to 100-button keyboard cap).
            ("start_all", start_all_command),
            ("stop_all", stop_all_command),
            ("positions", positions_command),
            ("pos", positions_command),
            ("markets", markets_command),
            # AI
            ("autopilot", autopilot_command),
            ("ap", autopilot_command),
            ("analyze", analyze_command),
            ("optimize_ai", analyze_optimize_command),
            # Phase 33: Adaptive Intelligence
            ("regime", regime_command),
            ("ts", ts_command),
            ("thompson", ts_command),  # Phase 47f.9: readable alias
            ("drift", drift_command),
            ("regime_drift", drift_command),  # Phase 47f.9: readable alias
            # T1.3 Commit 3: ("validate", validate_command) kaldırıldı — wf_validator ghost
            ("monitor", monitor_command),
            ("m", monitor_command),
            ("brain", brain_command),
            ("candles", candles_command),
            ("recorder", recorder_command),
            ("backtest_replay", backtest_replay_command),
            ("live", live_command),
            # 2026-05-05 Heddas: custom amount manuel BUY/SELL + allowance approve
            ("buy", buy_command),
            ("sell", sell_command),
            ("allowance", allowance_command),
            ("approve", allowance_command),
            # Analytics
            ("stats", stats_command),
            ("stats_chart", stats_chart_command),
            ("strategy_stats", strategy_stats_command),
            ("ss", strategy_stats_command),
            ("canary", canary_command),
            ("promote", promote_command),
            ("demote", demote_command),
            ("stats_hub", stats_hub_command),
            ("risk_hub", risk_hub_command),
            ("performance", performance_command),
            ("perf", performance_command),
            ("daily", daily_command),
            ("analytics", analytics_command),
            ("journal", journal_command),
            ("kelly", kelly_command),
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
            ("backtest_v2", backtest_v2_cmd),
            ("bt2", backtest_v2_cmd),
            ("compare", compare_cmd),
            # 2026-05-20 (Heddas direktifi): /backtest LAB mode-first tek kapı.
            # Eski /backtest_v2 + /backtest_replay legacy alias olarak kalır.
            ("backtest", backtest_lab_command),
            ("bt", backtest_lab_command),
            ("lab", backtest_lab_command),
            ("lab_save", lab_save_command),  # Faz 4 — JSON paste ruleset kaydı
            # Risk
            ("risk", risk_command),
            ("risk_set", risk_set_command),
            ("kill", kill_command),
            ("resume", resume_command),
            ("streak_reset", streak_reset_command),
            # Account
            ("wallets", wallets_command),
            ("add_funds", add_funds_command),
            ("withdraw", withdraw_command),
            ("withdraw_funds", withdraw_funds_command),
            # Settings
            ("settings", settings_command),
            ("signals", signals_command),
            ("plugins", plugins_command),
            ("ws", ws_command),
            ("kelly_toggle", kelly_toggle_command),
            ("optimize", optimize_command),
            ("edit", edit_command),
            ("clone", clone_command),
            ("plugin_set", plugin_set_command),
            ("referrals", referrals_command),
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
            ("changelog", changelog_command),
            ("cl", changelog_command),
            # Phase 82e Sprint B.2: archive reader diag (hot+cold tier)
            ("archive_info", archive_info_command),
            ("ainfo", archive_info_command),
            # Phase 66: filter toggle panel
            ("filters", filters_command),
            ("f", filters_command),
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
            ("portfolio", portfolio_command),
            ("pf", portfolio_command),
            # P0-08-E7 (2026-05-08): backtest data storage panel
            ("data_status", data_status_command),
            ("ds", data_status_command),
            ("ref_audit", ref_audit_command),
            ("ra", ref_audit_command),
            ("recon", recon_command),
            ("rc", recon_command),
            ("reality_gap", reality_gap_command),
            ("rg", reality_gap_command),
            # 2026-04-29 Aşama 3.B: top-level mode toggle (Paper/Real)
            # 2026-05-21 fix: ("m", mode_command) kaldirildi. /m zaten line
            # 433'te monitor_command'a kayitliydi; PTB ayni grupta ilk eslesen
            # handler'i calistirir → mode'un /m alias'i HIC atesleneMIYORDU
            # (sessiz golgeleme). /m = monitor (davranis degismedi). /mode kalir.
            ("mode", mode_command),
            # Phase 74b: Per-strategy lifecycle
            ("lifecycle", lifecycle_command),
            ("lc", lifecycle_command),
            # T1.3 Commit 4 (2026-04-20): Phase 76 markov + capital
            # registration'ları silindi (phase76_handler.py ghost modül).
            # Phase 77: Learning + Explainer + Health + Experiment
            ("why", why_command),
            ("mistakes", mistakes_command),
            ("patterns", patterns_command),
            ("health", health_command),
            ("h", health_command),
            ("experiment", experiment_command),
            ("exp", experiment_command),
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
        # 2026-05-21 fix: button (markets.py:208) emits "candles_refresh"
        # (plural, matches docstring) but registration was "^candle_refresh$"
        # (singular) → /candles "🔄 Refresh" button was dead. Pattern aligned.
        self.app.add_handler(
            CallbackQueryHandler(candle_refresh_callback, pattern="^candles_refresh$")
        )

        # Phase 36: Market recorder
        self.app.add_handler(
            CallbackQueryHandler(recorder_refresh_callback, pattern="^recorder_refresh$")
        )
        self.app.add_handler(CallbackQueryHandler(replay_callback, pattern="^replay_"))

        # Phase 34: Live trader buttons (+ Phase 52 ÖNERİ #6 confirm/cancel)
        for pattern in [
            "live_toggle",
            "live_toggle_confirm",
            "live_toggle_cancel",
            "live_main",
            "live_compare",
            "live_history",
            # 2026-05-18 Heddas: live budget reset (2-tap confirmed)
            "live_budget_reset",
            "live_budget_reset_confirm",
            "live_budget_reset_cancel",
            # 2026-05-19 Faz 2B/3 trade istasyonu panelleri — KRİTİK: bu
            # 4 callback live_callback if/elif zincirinde vardı ama burada
            # kayıtlı DEĞİLdi → butonlar tamamen tepkisizdi (ölü buton).
            "live_scan",
            "live_guards",
            "live_perf",
            "live_risk",
            # 2026-05-20 mode-state fix: PAPER MODE'dan açılan Piyasa Tara
            # paneli — "Ana Panel" PAPER dashboard'a dönsün (LIVE'a değil).
            "live_scan_paper",
        ]:
            self.app.add_handler(CallbackQueryHandler(live_callback, pattern=f"^{pattern}$"))
        # 2026-05-05 Heddas: Market BUY/SELL UI callback'leri
        # live_market_buy / live_market_sell / live_market_asset:* /
        # live_market_amount:* / live_market_exec:* / live_market_tf:*
        # live_approve_allowance
        self.app.add_handler(CallbackQueryHandler(live_callback, pattern="^live_market_"))
        self.app.add_handler(
            CallbackQueryHandler(live_callback, pattern="^live_approve_allowance$")
        )
        # 2026-05-05 Heddas: SELL flow PnL panel + % satış
        self.app.add_handler(CallbackQueryHandler(live_callback, pattern="^live_sell_pct:"))
        # 2026-05-05 Heddas: Redeem winning shares (gasless via Relayer)
        self.app.add_handler(CallbackQueryHandler(live_callback, pattern="^live_redeem:"))
        # 2026-05-06 Heddas: Mod-first dashboard + Live history detay + CSV
        if _MOD_FIRST_OK:
            self.app.add_handler(CallbackQueryHandler(main_callback, pattern="^main_"))
            self.app.add_handler(
                CallbackQueryHandler(live_history_callback, pattern="^live_history")
            )
            self.app.add_handler(
                CallbackQueryHandler(live_history_callback, pattern="^live_export_csv$")
            )
            self.app.add_handler(CallbackQueryHandler(live_history_callback, pattern="^live_pnl$"))

        # Phase 52 BUG #2 — /quick_strategy wizard callbacks (qs_*)
        self.app.add_handler(CallbackQueryHandler(quick_strategy_wizard_callback, pattern="^qs_"))

        # Phase 82a hotfix — /strategies pagination callbacks (strats_page_N)
        self.app.add_handler(
            CallbackQueryHandler(strategies_page_callback, pattern="^strats_page_")
        )

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
        self.app.add_handler(
            CallbackQueryHandler(ai_approval_callback, pattern="^ai_(approve|reject)$")
        )
        # Phase 79b — /analyze action execution buttons + brain cycle fallback
        self.app.add_handler(
            CallbackQueryHandler(analyze_apply_callback, pattern="^analyze_(apply|skip)$")
        )
        self.app.add_handler(
            CallbackQueryHandler(analyze_brain_callback, pattern="^analyze_brain$")
        )
        # Phase 79b — Strategy Suggester approve/reject
        self.app.add_handler(
            CallbackQueryHandler(suggest_callback, pattern="^suggest_(approve|reject)$")
        )

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

        for prefix in [
            "re_pos",
            "re_open",
            "re_exp",
            "re_loss",
            "re_trades",
            "re_streak",
            "re_floor",
            "re_market",
        ]:
            self.app.add_handler(
                CallbackQueryHandler(risk_field_edit_callback, pattern=f"^{prefix}$")
            )
        # Phase 53b: Force Exit toggle + edit
        self.app.add_handler(
            CallbackQueryHandler(force_exit_toggle_callback, pattern="^fe_toggle$")
        )
        self.app.add_handler(CallbackQueryHandler(force_exit_edit_callback, pattern="^fe_edit$"))

        self.app.add_handler(
            CallbackQueryHandler(stats_by_market_callback, pattern="^stats_by_market$")
        )
        self.app.add_handler(
            CallbackQueryHandler(strategy_stats_callback, pattern="^strategy_stats$")
        )
        self.app.add_handler(
            CallbackQueryHandler(trades_page_callback, pattern="^trades_page_")
        )  # Phase 52: /trades pagination
        self.app.add_handler(CallbackQueryHandler(risk_callback, pattern="^show_risk$"))
        self.app.add_handler(
            CallbackQueryHandler(diagnose_callback, pattern="^show_diagnose$")
        )  # Phase 62+: /diagnose refresh
        self.app.add_handler(CallbackQueryHandler(ws_callback, pattern="^show_ws$"))
        self.app.add_handler(CallbackQueryHandler(signals_callback, pattern="^show_signals$"))
        self.app.add_handler(CallbackQueryHandler(daily_callback, pattern="^show_daily$"))
        self.app.add_handler(CallbackQueryHandler(analytics_callback, pattern="^show_analytics$"))
        self.app.add_handler(CallbackQueryHandler(optimize_deploy_callback, pattern="^opt_deploy_"))
        self.app.add_handler(CallbackQueryHandler(autopilot_callback, pattern="^ap_"))
        # bt2_* + bt2c_* callback'leri silindi 2026-05-21 — engine_v2 yolu
        # kaldirildi, eski PolyCop config panel artik yok. /backtest_v2
        # + /bt2 komutlari LAB'a yonlendiren shim.
        # 2026-05-20 (Heddas direktifi): /backtest LAB callback dispatcher.
        # `lab_*` prefix — main/quick/builder/compare/calibrate/legacy/refresh.
        self.app.add_handler(CallbackQueryHandler(backtest_lab_callback, pattern="^lab_"))

        # Phase 79 S2-1: Strategy tester handlers
        for handler in get_test_strategy_handlers():
            self.app.add_handler(handler)

        # Phase 79 S3-09: Strategy lifecycle report
        self.app.add_handler(CommandHandler("report", report_command))
        self.app.add_handler(
            CallbackQueryHandler(report_refresh_callback, pattern="^report_refresh_")
        )

        # Phase 79 S1-12: Cancel handlers for heavy operations
        self.app.add_handler(
            CallbackQueryHandler(cancel_operation_callback, pattern="^cancel_backtest$")
        )
        # Hyperopt cancel + apply callback handlers removed 2026-04-28 (Heddas direktifi)

        # 2026-04-29 Polymarket Portfolio inline callbacks (Aşama 1+2)
        # tab_<name>, refresh, act_<deposit|withdraw|approve|wallet|pk>
        self.app.add_handler(
            CallbackQueryHandler(portfolio_callback, pattern="^pf_(tab_|refresh|act_)")
        )

        # 2026-04-29 Aşama 3.B: mode toggle inline callbacks
        # mode_set_<paper|real>, mode_refresh, mode_nav_<live|portfolio>
        self.app.add_handler(
            CallbackQueryHandler(mode_callback, pattern="^mode_(set_|refresh|nav_)")
        )

        # 2026-05-22: "_ph" placeholder kayitlari (show_api / share_pnl /
        # import_wallet / wallet_info_ / wallet_delete_ / select_wallet_)
        # kaldirildi. Hepsi "Yakinda!" doner butonlardi; start.py'deki
        # butonlar da silindi (bkz _send_wallets). Orphan kayit kalmadi.

        # Parameter info callbacks
        self.app.add_handler(CallbackQueryHandler(info_callback, pattern="^info_"))

        async def _risk_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if context.user_data.get("risk_editing"):
                return await handle_risk_input(update, context)
            # bt2_editing_limit branch silindi 2026-05-21 — engine_v2 config
            # panel kaldirildi, kullanici limit text input akisi yok.

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
            MessageHandler(filters.TEXT & filters.Regex(r"^/{2,}"), _double_slash_handler),
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
            # ── Strateji yuzeyi GIZLENDI (2026-05-22, Heddas #7 "gizle") ──
            # Strateji plugin'leri 2026-05-21'de silinmisti (bos registry,
            # auto-trade kapali). Bu turdaki TUM komutlar (/strategies,
            # /quick_strategy, /report, /start_all, /stop_all, /test_strategy,
            # /canary, /promote, /demote, /brain, /analyze, /experiment_*)
            # BotCommand menusunden cikarildi. Handler'lar dormant kalir
            # (geri uyumluluk + ileride RuleBasedStrategy live-port icin).
            # Kullanici LAB no-code rule_based ile kendi kurallarini yaziyor.
            # ── 🧪 Backtest LAB (tek kapi) ──
            # 2026-05-21 (Heddas direktifi): backtest komutlari tek baslik
            # altinda toplandi. /backtest LAB tek kapi (alias /bt, /lab),
            # /backtest_v2 + /bt2 + /backtest_replay + /compare LAB icine
            # tasindi / yonlendirildi — BotCommand listesinden cikarildi
            # (yine calisirlar ama menude gozukmezler).
            BotCommand("backtest", "Backtest LAB — gercek L2 replay (alias: /bt /lab)"),
            # /hyperopt + /mc_kelly removed 2026-04-28 (Heddas direktifi)
            # ── Istatistik (3) ──
            BotCommand("stats_hub", "Tum istatistikler (tab menu)"),
            BotCommand("daily", "Gunluk ozet"),
            BotCommand("trades", "Son trade listesi"),
            BotCommand("portfolio", "Polymarket gercek cuzdan (alias: /pf)"),
            BotCommand("mode", "Mode secim ekrani (paper/live)"),
            # ── Risk & Kontrol (3) ──
            BotCommand("risk_hub", "Risk yonetimi (tab menu)"),
            BotCommand("kill", "Acil durdur"),
            BotCommand("resume", "Devam et"),
            # ── AI (1) ── /brain + /analyze gizlendi (strateji yuzeyi #7)
            BotCommand("ai", "Turkce dogal-dil komut (alias /nl)"),
            # ── Sistem (2) ──
            BotCommand("health", "Modul sagligi (/h)"),
            BotCommand("ws", "WebSocket durumu"),
        ]

        # Admin-only commands (appended to public for admin scope)
        admin_extra_commands = [
            BotCommand("shadow_report", "Shadow monitor raporu (alias: /sr)"),
            BotCommand("db_health", "DB saglik + tablo boyutlari (/dbh)"),
            BotCommand("data_status", "Backtest data storage paneli (alias /ds)"),
            BotCommand("ref_audit", "Reference price feed audit (alias /ra)"),
            BotCommand("recon", "On-chain reconciliation status (alias /rc)"),
            BotCommand("reality_gap", "Paper-vs-live drift report (alias /rg)"),
            BotCommand("db_cleanup", "DB cleanup (manuel, /dbc)"),
            BotCommand("db_archive", "OB arsiv (nightly, /dba)"),
            BotCommand("health_check", "Eski health check (job durumu, /hc)"),
            BotCommand("filters", "Trade filtre paneli (on/off, alias: /f)"),
            BotCommand("diagnose", "Trade pipeline tani raporu"),
            # Becker BotCommand entries removed 2026-04-28 (Heddas direktifi)
            # experiment_apply/discard gizlendi 2026-05-22 (strateji yuzeyi #7)
            # /hyperopt_all + /hyperopt_status removed 2026-04-28 (Heddas direktifi)
        ]

        # Set public commands for all users
        await self.app.bot.set_my_commands(public_commands, scope=BotCommandScopeDefault())

        # Admin gets ALL commands (public + admin extras combined)
        admin_id = os.getenv("ADMIN_TELEGRAM_ID")
        if admin_id:
            try:
                combined = public_commands + admin_extra_commands
                await self.app.bot.set_my_commands(
                    combined, scope=BotCommandScopeChat(chat_id=int(admin_id))
                )
                logger.info(f"✅ Admin commands set ({len(combined)} total) for chat_id={admin_id}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"⚠️ Could not set admin commands: {e}")
        else:
            logger.warning(
                "⚠️ ADMIN_TELEGRAM_ID not set — admin-only commands hidden. Send /sr to capture."
            )

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
                    logger.warning(
                        "⚠️ admin_chat_id not set — send /sr in Telegram once to capture it"
                    )
            except Exception as _ae:  # noqa: BLE001
                logger.warning(f"admin_chat resolve failed: {_ae}")

            jq = self.app.job_queue
            if jq is not None:
                interval = int(os.getenv("SHADOW_REPORT_INTERVAL_SEC", "1800"))  # 30 min
                first = int(os.getenv("SHADOW_REPORT_FIRST_SEC", "60"))
                jq.run_repeating(
                    shadow_report_job, interval=interval, first=first, name="shadow_report"
                )
                logger.info(
                    f"✅ shadow_report job scheduled (every {interval}s, first in {first}s)"
                )

                # P3-11: heartbeat ping (every 10 min) + daily DB snapshot (every 24h)
                hb_interval = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "600"))
                jq.run_repeating(heartbeat_job, interval=hb_interval, first=120, name="heartbeat")
                logger.info(f"✅ heartbeat job scheduled (every {hb_interval}s)")

                snap_interval = int(os.getenv("DB_SNAPSHOT_INTERVAL_SEC", "86400"))
                snap_first = int(os.getenv("DB_SNAPSHOT_FIRST_SEC", "300"))
                jq.run_repeating(
                    daily_db_snapshot_job,
                    interval=snap_interval,
                    first=snap_first,
                    name="daily_db_snapshot",
                )
                logger.info(f"✅ daily_db_snapshot job scheduled (every {snap_interval}s)")

                # Epic 5 T5.5 (2026-04-21): periodic WAL TRUNCATE checkpoint
                # prevents WAL bloat when long-read connections (backup job,
                # ro_connect) block autocheckpoint. Default 6h — at 8.8GB DB
                # with ~20MB/hr write pressure, 6h keeps WAL < 200 MB.
                wal_ckpt_hours = int(os.getenv("WAL_CHECKPOINT_INTERVAL_HOURS", "6"))
                wal_ckpt_interval = wal_ckpt_hours * 3600
                wal_ckpt_first = int(
                    os.getenv("WAL_CHECKPOINT_FIRST_SEC", "1200")
                )  # 20 min after boot
                jq.run_repeating(
                    wal_checkpoint_job,
                    interval=wal_ckpt_interval,
                    first=wal_ckpt_first,
                    name="wal_checkpoint",
                )
                logger.info(
                    f"✅ wal_checkpoint job scheduled (every {wal_ckpt_hours}h, "
                    f"first in {wal_ckpt_first}s)"
                )

                # Phase 47f.8: DB retention — prune old ob_snapshots/candles nightly
                ret_interval = int(os.getenv("DB_RETENTION_INTERVAL_SEC", "86400"))
                ret_first = int(os.getenv("DB_RETENTION_FIRST_SEC", "900"))
                jq.run_repeating(
                    db_retention_job, interval=ret_interval, first=ret_first, name="db_retention"
                )
                logger.info(
                    f"✅ db_retention job scheduled (every {ret_interval}s, first in {ret_first}s)"
                )

                # Phase 47f.10 P5#22: hourly shadow vs paper anomaly compare
                svp_interval = int(os.getenv("SHADOW_COMPARE_INTERVAL_SEC", "3600"))
                svp_first = int(os.getenv("SHADOW_COMPARE_FIRST_SEC", "1800"))
                jq.run_repeating(
                    shadow_vs_paper_job,
                    interval=svp_interval,
                    first=svp_first,
                    name="shadow_vs_paper",
                )
                logger.info(
                    f"✅ shadow_vs_paper job scheduled (every {svp_interval}s, first in {svp_first}s)"
                )

                # P1-03-b (2026-05-09) + P1-03 Wave 2 (2026-05-11): nightly
                # reality gap. Pinned to a fixed UTC time so the report lands
                # at the same hour every night regardless of bot restarts.
                # Default 03:00 UTC (off-hours, no race with US/EU markets).
                # Manuel `/rg` komutu fresh-on-demand karşılar — boot-kick yok.
                # ENV: REALITY_GAP_ENABLED=true|false, REALITY_GAP_TIME_HHMM=HH:MM (UTC)
                if os.getenv("REALITY_GAP_ENABLED", "true").lower() == "true":
                    from datetime import time as _dtime

                    rg_hhmm = os.getenv("REALITY_GAP_TIME_HHMM", "03:00").strip()
                    try:
                        _h_str, _m_str = rg_hhmm.split(":", 1)
                        _rg_time = _dtime(hour=int(_h_str), minute=int(_m_str))
                    except (ValueError, IndexError):
                        logger.warning(
                            f"reality_gap: invalid REALITY_GAP_TIME_HHMM={rg_hhmm!r}, "
                            f"falling back to 03:00 UTC"
                        )
                        _rg_time = _dtime(hour=3, minute=0)
                    jq.run_daily(
                        reality_gap_job,
                        time=_rg_time,
                        name="reality_gap",
                    )
                    logger.info(
                        f"✅ reality_gap job scheduled "
                        f"(daily at {_rg_time.strftime('%H:%M')} UTC, "
                        f"manuel /rg fresh-on-demand)"
                    )

                # 2026-04-29 Polymarket Portfolio refresh (Aşama 1)
                if os.getenv("PORTFOLIO_REFRESH_ENABLED", "true").lower() == "true":
                    pf_interval = int(os.getenv("PORTFOLIO_REFRESH_SEC", "60"))
                    pf_first = int(os.getenv("PORTFOLIO_REFRESH_FIRST_SEC", "30"))
                    jq.run_repeating(
                        polymarket_portfolio_job,
                        interval=pf_interval,
                        first=pf_first,
                        name="polymarket_portfolio",
                    )
                    logger.info(
                        f"✅ polymarket_portfolio job scheduled "
                        f"(every {pf_interval}s, first in {pf_first}s)"
                    )

                # 2026-05-05 Heddas direktifi: Auto-redeem winning positions
                if os.getenv("AUTO_REDEEM_ENABLED", "false").lower() == "true":
                    try:
                        from telegram_bot.jobs.auto_redeem_job import auto_redeem_job

                        ar_interval = int(os.getenv("AUTO_REDEEM_INTERVAL_SEC", "300"))
                        ar_first = int(os.getenv("AUTO_REDEEM_FIRST_SEC", "120"))
                        jq.run_repeating(
                            auto_redeem_job,
                            interval=ar_interval,
                            first=ar_first,
                            name="auto_redeem",
                        )
                        logger.info(
                            f"✅ auto_redeem job scheduled "
                            f"(every {ar_interval}s, first in {ar_first}s)"
                        )
                    except (ImportError, AttributeError) as _ar_err:
                        logger.warning(f"auto_redeem skip: {_ar_err}")
                else:
                    logger.info("ⓘ auto_redeem job disabled (AUTO_REDEEM_ENABLED=false)")

                # Phase 66: Daily PnL divergence alert (paper vs live aggregate)
                if os.getenv("PNL_DIVERGENCE_ENABLED", "true").lower() == "true":
                    pnl_div_interval = int(
                        os.getenv("PNL_DIVERGENCE_INTERVAL_SEC", "86400")
                    )  # daily
                    pnl_div_first = int(
                        os.getenv("PNL_DIVERGENCE_FIRST_SEC", "3600")
                    )  # 1h after boot
                    jq.run_repeating(
                        pnl_divergence_job,
                        interval=pnl_div_interval,
                        first=pnl_div_first,
                        name="pnl_divergence",
                    )
                    logger.info(f"✅ pnl_divergence job scheduled (every {pnl_div_interval}s)")

                # Tournament job removed 2026-04-28 (Heddas direktifi: Hyperopt
                # tam silme — tournament_job hyperopt subprocess'e dayalıydı).

                # Phase 50 (Suggestion 12.3) — price alert watcher
                if os.getenv("PRICE_ALERT_ENABLED", "1") == "1":
                    pa_interval = int(os.getenv("PRICE_ALERT_INTERVAL_SEC", "30"))
                    self.app.bot_data["odds_feed"] = self.odds_feed
                    jq.run_repeating(
                        price_alert_job, interval=pa_interval, first=pa_interval, name="price_alert"
                    )
                    logger.info(f"✅ price_alert job scheduled (every {pa_interval}s)")

                # Phase 48: daily auto-promote canary → promoted
                if os.getenv("AUTO_PROMOTE_ENABLED", "1") == "1":
                    ap_interval = int(os.getenv("AUTO_PROMOTE_INTERVAL_SEC", "86400"))
                    ap_first = int(os.getenv("AUTO_PROMOTE_FIRST_SEC", "1200"))
                    jq.run_repeating(
                        auto_promote_job, interval=ap_interval, first=ap_first, name="auto_promote"
                    )
                    logger.info(
                        f"✅ auto_promote job scheduled (every {ap_interval}s, first in {ap_first}s)"
                    )

                # Phase 59: Weekly pattern discovery
                if os.getenv("PATTERN_DISCOVERY_ENABLED", "1") == "1":
                    from telegram_bot.jobs.pattern_discovery_job import pattern_discovery_callback

                    pd_interval = int(
                        os.getenv("PATTERN_DISCOVERY_INTERVAL_SEC", "604800")
                    )  # weekly
                    pd_first = int(
                        os.getenv("PATTERN_DISCOVERY_FIRST_SEC", "3600")
                    )  # 1h after startup
                    jq.run_repeating(
                        pattern_discovery_callback,
                        interval=pd_interval,
                        first=pd_first,
                        name="pattern_discovery",
                    )
                    logger.info(f"✅ pattern_discovery job scheduled (every {pd_interval}s)")

                # Phase 59 DB-01b: nightly OB archive to parquet
                if os.getenv("DB_ARCHIVE_ENABLED", "1") == "1":
                    ar_interval = int(os.getenv("DB_ARCHIVE_INTERVAL_SEC", "86400"))  # daily
                    ar_first = int(os.getenv("DB_ARCHIVE_FIRST_SEC", "600"))  # 10 min after startup
                    jq.run_repeating(
                        db_archive_job, interval=ar_interval, first=ar_first, name="db_archive"
                    )
                    logger.info(
                        f"✅ db_archive job scheduled (every {ar_interval}s, first in {ar_first}s)"
                    )

                # 2026-05-05 Heddas direktifi sadeleştirme:
                # Strategy Suggester emekliye ayrıldı.
                # Sebep: AI Brain CREATE eylemi zaten yeni strateji öneriyor,
                # iki LLM çağrısı çakışma + cost israfı. Tek karar verici: AI Brain.
                # Eski default `STRATEGY_SUGGESTER_ENABLED=true` artık `false`.
                if os.getenv("STRATEGY_SUGGESTER_ENABLED", "false").lower() == "true":
                    logger.warning(
                        "⚠ STRATEGY_SUGGESTER_ENABLED=true — Heddas 2026-05-05 "
                        "direktifi gereği bu modül emekliye ayrıldı. "
                        "Devre dışı bırakmak için ENV'den kaldır veya 'false' yap. "
                        "Yeni stratejiler için AI Brain CREATE eylemi kullan."
                    )

                # Becker rolling recalibration job removed 2026-04-28 (Heddas direktifi)
                # Strategy Suggester emekliye ayrıldı 2026-05-05 (Heddas direktifi)
            else:
                logger.warning(
                    "JobQueue is None — shadow_report disabled. "
                    "Install python-telegram-bot[job-queue]"
                )
        except Exception as _je:  # noqa: BLE001
            logger.exception(f"Failed to schedule shadow_report: {_je}")

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES, drop_pending_updates=True
        )

        # Phase 82e Sprint 2.1: register bg_task notify handler so any
        # safe_create_task() failure alerts admin on Telegram. Cooldown +
        # rate limit are internal to bg_task module (BG_TASK_NOTIFY_COOLDOWN_SEC).
        try:
            admin_id = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_CHAT_ID")
            if admin_id:
                handler = make_telegram_notify_handler(self.app, int(admin_id))
                set_notify_handler(handler)
                logger.info(f"🛡️ bg_task notify handler registered → chat {admin_id}")
            else:
                logger.warning("bg_task notify disabled (ADMIN_TELEGRAM_ID / ADMIN_CHAT_ID unset)")
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

    async def _ap_redirect(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.callback_query.answer()
        engine = context.bot_data.get("engine")
        if not engine or not engine.autopilot:
            return await update.callback_query.message.reply_text("AutoPilot aktif degil.")
        await update.callback_query.message.reply_text("🤖 Analiz ediliyor...")
        actions = await engine.autopilot.generate_actions()
        if not actions:
            return await update.callback_query.message.reply_text(
                "🤖 <b>AutoPilot</b>\n\n✅ Oneri yok.", parse_mode="HTML"
            )
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        for action in actions:
            aid = await engine.autopilot.store_pending(action)
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ Onayla", callback_data=f"ap_yes_{aid}"),
                        InlineKeyboardButton("❌ Reddet", callback_data=f"ap_no_{aid}"),
                    ]
                ]
            )
            await update.callback_query.message.reply_text(
                f"{action['emoji']} <b>{action['desc']}</b>\n{action['reason']}",
                parse_mode="HTML",
                reply_markup=kb,
            )

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
            "<b>🎯 Strateji</b> <i>(devre disi — 2026-05-22 gizlendi)</i>\n"
            "<i>Strateji sistemi kapali (bos registry, auto-trade yok).</i>\n"
            "<i>Kendi kuralini yaz: /backtest → 🛠 Strateji Kurucu (LAB)</i>\n\n"
            "<b>🧪 Backtest LAB</b>\n"
            "/backtest — Backtest LAB tek kapi <i>(/bt, /lab)</i>\n"
            "  ↳ 4 panel: Hizli Test · Strateji Kurucu · Karsilastir · Kalibrasyon\n"
            "<i>Eski: /backtest_v2 /bt2 /backtest_replay /compare → LAB icinde</i>\n\n"
            "<b>📊 Istatistik</b>\n"
            "/stats_hub — Tum istatistikler (tab menu)\n"
            "/daily — Gunluk ozet\n"
            "/trades — Son trade listesi\n\n"
            "<b>🛡 Risk &amp; Kontrol</b>\n"
            "/risk_hub — Risk yonetimi (tab menu)\n"
            "/kill — Acil durdur\n"
            "/resume — Devam et\n\n"
            "<b>🧠 AI</b>\n"
            "/ai — Turkce dogal-dil komut <i>(/nl)</i>\n\n"
            "<b>⚙️ Sistem</b>\n"
            "/health — Modul sagligi <i>(/h)</i>\n"
            "/ws — WebSocket durumu\n\n"
        )

        # Aliases reference
        alias_text = (
            "<b>📌 Diger Komutlar (Alias)</b>\n"
            "Hala calisir ama menu'de gozukmez:\n"
            "<code>/lifecycle (/lc), /kelly, /patterns, /experiment (/exp), /rs</code>\n\n"
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
                save_admin_chat_id,
                shadow_report_job,
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

            pushed = await shadow_report_job(context, force=True, override_chat_id=caller_chat_id)
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
                lines.append(
                    f"halted: <code>{halted}</code>" + (f" ({halt_reason})" if halt_reason else "")
                )
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
                    lines.append(
                        f"WR=<code>{wins/len(last10)*100:.0f}%</code> "
                        f"PnL=<code>{sum(last10):+.2f}</code>"
                    )
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
            fe_count = getattr(engine, "_force_exits_today", 0) if engine else 0
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
