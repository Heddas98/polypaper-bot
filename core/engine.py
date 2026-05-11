"""
PolyPaper Bot - Trading Engine (v34)
=====================================
Ana trading döngüsü. Her 1 saniyede bir çalışır.

Akış:
  WebSocket odds → SignalFusion (6 sinyal) → StrategySelector (Thompson Sampling)
  → RegimeClassifier → RiskManager (9 kapı) → _fill() → TradeJournal

Güvenlik Kapıları (F-01 → F-14):
  F-01  asyncio.Lock — eş zamanlı trade önleme
  F-02  Atomik bakiye düşme — race condition koruması
  F-03  Transaction bloğu — DB tutarlılığı
  F-04  WS stale gate — eski veri koruması
  F-09  Top-level import — başlangıç doğrulaması
  F-14  Reconnect flush — bağlantı sonrası temizlik

BUG-03: FIXED in Phase 56 — verdict None guard + try/except in engine_signals.py.
Phase 56: Balance race fix — pending_reserved subtracted from effective_balance.
"""
import asyncio
import json  # Epic 8 T8.1: narrow JSONDecodeError in startup restore
import logging
import math
import os
import random
import time
import traceback  # Phase 82a hotfix: full traceback for silent death diagnostics
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiosqlite  # Epic 8 T8.1: narrow DB exception handling
import httpx  # Epic 8 T8.1: narrow HTTP exception handling

try:
    from telegram.error import TelegramError  # Epic 8 T8.1
except ImportError:  # pragma: no cover - python-telegram-bot is a hard dep
    class TelegramError(Exception):  # type: ignore[no-redef]
        ...

from config.settings import Settings
from core.kill_switch import KillSwitch
from core.risk_manager import RiskManager, RiskLimits
from core.signal_fusion import SignalFusion, SignalWeights
from core.strategy_plugins import StrategyRegistry, MarketSnapshot
from core.auto_optimizer import AutoOptimizer
from core.indicators import ema_direction_filter
# Phase 65: fees.py v1 removed — fees_v2 is the only active model
from core.fees_v2 import (  # Phase 43a: Mart 2026 linear model
    polymarket_taker_fee_v2,
    polymarket_fee_percent_v2,
    polymarket_maker_rebate,
    in_tail_zone as _fee_in_tail_zone,
)
from core.kelly import get_strategy_kelly  # Phase 27: Kelly position sizing
from core.ai_brain import AIBrain  # Phase 30: Autonomous AI
from core.autopilot import AutoPilot  # Phase 27.1: Semi-autonomous actions
from core.strategy_selector import StrategySelector  # Phase 33: Thompson Sampling
from core.regime import RegimeClassifier, DriftDetector  # Phase 33: Regime + Drift
from core.trade_journal import (log_entry, log_exit, log_settlement, log_rejection,
                                log_heartbeat, log_decision_close, set_db as journal_set_db)
from core.live_trader import LiveTrader  # Phase 34: Mainnet shadow mode
from data.market_scanner import MarketScanner
from data.odds_feed import OddsFeed
from data.polymarket_client import safe_float
from db.database import Database
from db.models import Strategy, Execution, ExecutionStatus, Direction

logger = logging.getLogger("polypaper.core.engine")

# Phase 51 P51-02 pilot: helpers/constants extracted to core/engine_support.
# Re-imported here for backward compatibility — existing `from core.engine
# import SkipCounter, VirtualOrder, ...` call sites continue to work.
from core.engine_support import (  # noqa: E402
    INTERVAL_SECS,
    MAX_MBE,
    WIDE_SPREAD,
    WS_STALE_THRESHOLD,
    SkipCounter,
    VirtualOrder,
    _slug_end,
    _slug_start,
    _stagger,
)
from core.engine_settlement import EngineSettlementMixin  # noqa: E402
from core.engine_fills import EngineFillsMixin  # noqa: E402
from core.engine_monitor import EngineMonitorMixin  # noqa: E402
from core.engine_signals import EngineSignalsMixin  # noqa: E402  # Phase 51 P51-02
from core.bg_task import safe_create_task  # Phase 82e Sprint 2.1: bg task guard


class TradingEngine(
    EngineSignalsMixin,
    EngineMonitorMixin,
    EngineFillsMixin,
    EngineSettlementMixin,
):
    def __init__(self, settings, db, scanner, odds_feed, bot_app=None, external_feed=None):
        self.settings: Settings = settings
        self.db: Database = db
        self.scanner: MarketScanner = scanner
        self.odds_feed: OddsFeed = odds_feed
        self.external_feed = external_feed  # Phase 24: Binance spot prices
        self.bot_app = bot_app
        self._running = False
        self._task = None
        self._cycle = 0
        self._open_positions: set[str] = set()
        self._settled_slugs: dict[str, datetime] = {}
        self._cooldowns: dict[str, datetime] = {}
        # Sprint 2 S2-03: max move tracker (per-instance, was class-attr bug)
        self._max_moves: dict[str, tuple[float, float]] = {}
        # Phase 47f.8: env overrides for risk limits (MAX_DAILY_LOSS, MAX_LOSS_STREAK, etc.)
        _rlimits = RiskLimits()
        import os
        try:
            if os.getenv("MAX_DAILY_LOSS"):
                _rlimits.max_daily_loss = float(os.getenv("MAX_DAILY_LOSS"))
            if os.getenv("MAX_LOSS_STREAK"):
                _rlimits.max_loss_streak = int(os.getenv("MAX_LOSS_STREAK"))
            if os.getenv("MAX_DAILY_TRADES"):
                _rlimits.max_daily_trades = int(os.getenv("MAX_DAILY_TRADES"))
            if os.getenv("MAX_TOTAL_EXPOSURE"):
                _rlimits.max_total_exposure = float(os.getenv("MAX_TOTAL_EXPOSURE"))
            # Phase 63: Missing env overrides — these were NOT configurable before,
            # causing Gate 3 (max_open_positions=5) to block all trades when open>5.
            # Phase 82a hotfix: renamed stale `_os` → `os` alias (was silently
            # failing with NameError → these 5 env overrides never applied).
            if os.getenv("MAX_OPEN_POSITIONS"):
                _rlimits.max_open_positions = int(os.getenv("MAX_OPEN_POSITIONS"))
            if os.getenv("MAX_POSITION_SIZE"):
                _rlimits.max_position_size = float(os.getenv("MAX_POSITION_SIZE"))
            if os.getenv("MIN_BALANCE_FLOOR"):
                _rlimits.min_balance_floor = float(os.getenv("MIN_BALANCE_FLOOR"))
        except (ValueError, TypeError) as _re:
            # Epic 8 T8.1: env override parse — float/int cast failures only
            logger.warning(f"risk env override parse failed: {_re}")
        self.risk = RiskManager(_rlimits)
        self.kill_switch = KillSwitch()
        # Phase 33: Adaptive Intelligence (must init BEFORE SignalFusion)
        self.selector = StrategySelector()
        self.regime = RegimeClassifier(window=30)
        self.drift = DriftDetector(window=100)
        self.signals = SignalFusion(SignalWeights(), drift_detector=self.drift)
        self.plugins = StrategyRegistry()
        self.optimizer = AutoOptimizer(db)
        # Phase 74b: Per-strategy adaptive parameter learning
        from core.strategy_lifecycle import StrategyLifecycle
        self.lifecycle = StrategyLifecycle(db)
        self._pending: list[VirtualOrder] = []
        self._cancel_count: int = 0  # Phase 40b: TIF + manual cancel tally
        self._ws_drop_count: int = 0  # Phase 40c: WS reconnect drop tally
        # ══ F-01: asyncio.Lock for all shared state mutations ══
        self._trade_lock = asyncio.Lock()
        self._ws_was_connected = False  # F-14: track WS state changes
        self.skips = SkipCounter()  # Phase 16.5: always-on skip tracking
        self._mg_streak: dict[str, int] = {}  # Phase 18.5: martingale loss streak per strategy
        self._last_trade_slug: dict[str, str] = {}  # Phase 24: anti-whipsaw guard
        self._last_daily_date: str = ""  # Phase 24: daily report tracking
        # Phase 82: Cycle-level orderbook cache (TTL=2.0s) — prevents redundant
        # /book API calls when multiple strategies evaluate the same market in
        # the same cycle. Used by plugin metadata enrichment (_get_ob_cached).
        self._ob_cache: dict[str, tuple[float, dict]] = {}  # token_id → (ts, data)
        self._OB_CACHE_TTL: float = float(os.getenv("OB_CACHE_TTL", "2.0"))
        # Phase 49 P0-04: strats=0 watchdog — track how long we've been empty
        self._strats_zero_since: datetime | None = None
        self._strats_zero_alerted: bool = False
        self._market_open_recorded: set[str] = set()  # Phase 26: track open price per slug
        self._kelly_mode: bool = True  # Phase 27: auto Kelly sizing
        # ─────────────────────────────────────────────────────────────
        # Phase 35: AI Brain feature toggles
        #
        # Epic 6 T6.3b: drift_monitor removed — no engine consumer existed
        # (no core/drift_monitor.py module, no brain_flags['drift_monitor']
        # read anywhere). UI toggle was purely cosmetic ("silent ghost").
        #
        # Epic 6 T6.3d: kelly_sizing removed from brain_flags. Authoritative
        # runtime state lives on `self._kelly_mode` (read by engine_signals
        # at sizing time, toggled by `/kelly_toggle` command + AI Brain
        # panel 📈 Kelly button). Having both caused "silent ghost toggle":
        # AI Brain panel wrote to brain_flags['kelly_sizing'] which nothing
        # consumed. Now the panel button retargets `_kelly_mode` directly.
        # ─────────────────────────────────────────────────────────────
        self.brain_flags = {
            "ai_brain": True,
            "thompson_sampling": True,
            "regime_detection": True,
            "autopilot": True,
            "candle_collector": True,
            "market_recorder": True,
        }
        self.analyst = AIBrain(db, self, bot_app, settings)  # Phase 30: AI Brain
        self.autopilot = AutoPilot(db, self)  # Phase 27.1
        self.live = LiveTrader(db, bot_app, settings)  # Phase 34: Real USDC shadow
        # Phase 47a: Adaptive micro weight tracker
        try:
            from core.micro_weight_tracker import MicroWeightTracker
            self.micro_weight = MicroWeightTracker(
                enabled=getattr(settings, "ADAPTIVE_MICRO_WEIGHT_ENABLED", False)
            )
        except Exception as _mwe:  # noqa: BLE001
            # Epic 8 T8.1: optional bootstrap — ImportError/AttributeError/init failures
            logger.warning(f"micro_weight init failed: {_mwe}")
            self.micro_weight = None
        # Becker calibration system removed 2026-04-28 (Aşama 1 functional
        # unwire) + 2026-04-29 (Aşama 2 cosmetic cleanup). becker_weight,
        # _becker_poly_curve, _becker_kalshi_curve attribute'ları tamamen
        # kaldırıldı — engine_signals + engine_fills dead code temizlendi,
        # downstream referans yok. Ref: docs/audits/fee_reality_check_2026_04.md.

        # Phase 59: Event calendar monitor — pre-event volatility adjustment
        try:
            from data.event_monitor import EventMonitor
            self._event_monitor = EventMonitor()
            logger.info("📅 Event calendar monitor initialized")
        except Exception as _eme:  # noqa: BLE001
            # Epic 8 T8.1: optional bootstrap — ImportError/AttributeError/init failures
            logger.debug(f"event_monitor init: {_eme}")
            self._event_monitor = None

        # T1.3 Commit 1 (2026-04-20): Phase 60 ghost modules removed —
        # cascade_detector, lag_arbitrage, whale_signal modülleri archive'a
        # taşınmıştı ve import her bootta sessiz fail ediyordu. Temizlendi.
        # (Aktif whale akışı için core/signals/whale_flow.py kullanılıyor.)

        # Phase 70: EV Threshold Tracker
        try:
            from calibration.ev_threshold import EVTracker
            self._ev_tracker = EVTracker()
        except Exception as _eve:  # noqa: BLE001
            # Epic 8 T8.1: optional bootstrap — ImportError/AttributeError/init
            logger.debug(f"ev_tracker init: {_eve}")
            self._ev_tracker = None

        # T1.3 Commit 1 (2026-04-20): Phase 76 markov_estimator + capital_allocator
        # ghost modülleri archive'a taşınmıştı, her bootta sessiz fail ediyordu.
        # Temizlendi — /markov + /capital komutları ve engine boost'u kalktı.

        # Phase 77: Trade Memory (persistent learning from wins/losses)
        self._trade_memory = None
        if os.getenv("TRADE_MEMORY_ENABLED", "true").lower() == "true":
            try:
                from core.trade_memory import get_trade_memory
                self._trade_memory = get_trade_memory()
                logger.info("🧠 Phase 77: Trade Memory initialized")
            except Exception as _tme:  # noqa: BLE001
                # Epic 8 T8.1: optional bootstrap — ImportError/AttributeError/init
                logger.debug(f"trade_memory init: {_tme}")

        # Phase 77: Decision Explainer (reasoning chains for every trade)
        self._explainer = None
        if os.getenv("DECISION_EXPLAINER_ENABLED", "true").lower() == "true":
            try:
                from core.decision_explainer import get_decision_explainer
                self._explainer = get_decision_explainer()
                logger.info("🔍 Phase 77: Decision Explainer initialized")
            except Exception as _dee:  # noqa: BLE001
                # Epic 8 T8.1: optional bootstrap — ImportError/AttributeError/init
                logger.debug(f"decision_explainer init: {_dee}")

        # Phase 77: Experiment Runner (safe parameter testing)
        self._experiment = None
        if os.getenv("EXPERIMENT_ENABLED", "true").lower() == "true":
            try:
                from core.experiment_runner import get_experiment_runner
                self._experiment = get_experiment_runner()
                logger.info("🧪 Phase 77: Experiment Runner initialized")
            except Exception as _ere:  # noqa: BLE001
                # Epic 8 T8.1: optional bootstrap — ImportError/AttributeError/init
                logger.debug(f"experiment init: {_ere}")

        # Phase 70: 2D Calibration Surface C(K,τ) — extends 1D δ(p) with time dimension
        self._calib_surface_2d = None
        if os.getenv("SURFACE_2D_ENABLED", "true").lower() == "true":
            try:
                from calibration.surface_2d import SurfaceBuilder
                builder = SurfaceBuilder()
                # Build from kalshi first (better time data), poly as supplement
                surface = builder.build(source="kalshi")
                if surface.built:
                    self._calib_surface_2d = surface
                    logger.info(
                        f"📊 Phase 70: 2D Surface loaded — "
                        f"{surface.n_populated_cells} cells, "
                        f"{surface.total_trades:,} trades"
                    )
                else:
                    logger.info("📊 Phase 70: 2D Surface — no data, using 1D fallback")
            except Exception as _s2e:  # noqa: BLE001
                # Epic 8 T8.1: optional bootstrap — ImportError/AttributeError/init
                logger.debug(f"surface_2d init: {_s2e}")

    @property
    def client(self):
        return self.scanner.client

    async def start(self):
        if self._running:
            return
        self._running = True
        await self._load_open()

        # Phase 74b: Ensure strategy_params column exists
        await self.lifecycle.ensure_column()

        # ══ Phase 82d: Restore Persisted Plugin Params ══
        # Aktif stratejilerin strategy_params JSON'undaki plugin_params
        # alt-dict'ini registry.set_config() ile plugin runtime'a uygula.
        # Bot restart edilince manuel parametre ayarları (AI Brain TUNE,
        # /strategies edit, vs.) yaşar. 2026-04-28: Hyperopt silindi, eski
        # "HyperOpt Apply Callback" referansları temizlendi — pattern artık
        # generic plugin config persistence.
        # NOT: set_config plugin TYPE bazında çalışır; aynı type'tan birden
        # çok aktif strateji varsa son yazan kazanır (uzun vade borç).
        try:
            import json as _json
            rows = await self.db.conn.execute_fetchall(
                "SELECT id, strategy_type, strategy_params FROM strategies "
                "WHERE status='active' AND strategy_params IS NOT NULL "
                "AND strategy_params != '' AND strategy_params != '{}'")
            applied_count = 0
            seen_types: dict[str, str] = {}   # stype -> last sid that wrote
            for sid, stype, sp_raw in rows:
                if not stype or not sp_raw:
                    continue
                try:
                    sp = _json.loads(sp_raw)
                    if not isinstance(sp, dict):
                        continue
                except (json.JSONDecodeError, ValueError, TypeError) as _pe:
                    # Epic 8 T8.1: corrupt strategy_params JSON — skip quietly
                    # but leave a breadcrumb for audit (previously silent pass).
                    logger.debug(f"plugin_params restore: skip sid={sid[:8]} bad JSON: {_pe}")
                    continue
                plugin_params = sp.get("plugin_params") or {}
                if not plugin_params:
                    continue
                if stype in seen_types:
                    logger.warning(
                        f"plugin_params restore: {stype} plugin config overwritten "
                        f"by sid={sid[:8]} (prev sid={seen_types[stype][:8]})")
                seen_types[stype] = sid
                for param, value in plugin_params.items():
                    try:
                        if self.plugins.set_config(stype, param, value):
                            applied_count += 1
                        else:
                            logger.warning(
                                f"plugin_params restore: set_config rejected "
                                f"{stype}.{param}={value}")
                    except (ValueError, TypeError, AttributeError, KeyError) as _e:
                        # Epic 8 T8.1: plugin config cast / missing attr / bad key
                        logger.warning(
                            f"plugin_params restore {stype}.{param}: {_e}")
            if applied_count > 0:
                logger.info(
                    f"🔧 plugin params restored: {applied_count} "
                    f"param(s) across {len(seen_types)} strategy type(s)")
        except (aiosqlite.Error, AttributeError) as _e:
            # Epic 8 T8.1: DB fetchall / connection down / db.conn missing
            logger.warning(f"plugin_params startup restore failed: {_e}")

        # T1.3 Commit 1 (2026-04-20): capital_allocator.initialize() kaldırıldı
        # (Phase 76 ghost modül temizliği — yukarıdaki init bloğuyla beraber).

        # Phase 77: Initialize Trade Memory, Decision Explainer, Experiment Runner
        if self._trade_memory is not None:
            try:
                await self._trade_memory.initialize(self.db)
            except (aiosqlite.Error, AttributeError) as _tmi:
                # Epic 8 T8.1: CREATE TABLE / initialize() contract
                logger.warning(f"trade_memory db init: {_tmi}")

        if self._explainer is not None:
            try:
                await self._explainer.initialize(self.db)
            except (aiosqlite.Error, AttributeError) as _dei:
                # Epic 8 T8.1: CREATE TABLE / initialize() contract
                logger.warning(f"decision_explainer db init: {_dei}")

        if self._experiment is not None:
            try:
                await self._experiment.initialize(self.db)
            except (aiosqlite.Error, AttributeError) as _eri:
                # Epic 8 T8.1: CREATE TABLE / initialize() contract
                logger.warning(f"experiment db init: {_eri}")

        # ══ Phase 22: Load persistent settings from DB ══
        try:
            from core.risk_manager import RiskLimits
            saved = await self.db.get_all_settings("risk.")
            if saved:
                self.risk.limits = RiskLimits.from_dict(saved)
                logger.info(f"⚙️ Risk settings loaded from DB ({len(saved)} params)")

            # Phase 35: Load brain flags from DB
            # Epic 6 T6.3b: Only load flags that exist in the canonical
            # brain_flags dict — prevents retired ghost flags (e.g.
            # drift_monitor) from resurrecting if they still have a row
            # in the settings table from an earlier bot version.
            brain_saved = await self.db.get_all_settings("brain_flags.")
            if brain_saved:
                loaded = 0
                for key, val in brain_saved.items():
                    feature = key.replace("brain_flags.", "")
                    if feature in self.brain_flags:
                        self.brain_flags[feature] = val == "1"
                        loaded += 1
                    else:
                        logger.debug(
                            f"🧠 Ignoring retired brain flag from DB: {feature}")
                logger.info(f"🧠 Brain flags loaded from DB ({loaded} flags)")

            # Epic 6 T6.3e-fix-2: Sibling-gate sync. Some brain flags drive
            # sibling objects via their own `_enabled` attribute (set in
            # ai_handler's brain_toggle_callback as a side effect). Since
            # CandleCollector/MarketRecorder constructors default _enabled
            # to True and the boot loader above only updated the dict,
            # the runner loops (`if not self._enabled: return`) would
            # silently run even if the user had toggled the flag OFF.
            # Sync here so a restart honors the persisted DB state.
            _sibling_gates = [
                ("candle_collector", "candle_collector"),
                ("market_recorder", "market_recorder"),
            ]
            for flag_key, attr_name in _sibling_gates:
                sibling = getattr(self, attr_name, None)
                if sibling is not None and hasattr(sibling, "_enabled"):
                    desired = self.brain_flags.get(flag_key, True)
                    if sibling._enabled != desired:
                        sibling._enabled = desired
                        logger.info(
                            f"🔄 Boot sync: {attr_name}._enabled "
                            f"← brain_flags['{flag_key}']={desired}")

            # Epic 6 T6.5: Kelly mode DB persistence. `self._kelly_mode`
            # is authoritative runtime state toggled by `/kelly_toggle`
            # command and the AI Brain panel 📈 Kelly button (virtual
            # flag — see T6.3d). Prior to T6.5 both writers were
            # in-memory-only, so a bot restart silently reset Kelly to
            # the constructor default (True). Now both writers persist
            # to the `engine.kelly_mode` setting and we read it back
            # here. Missing key → keep the constructor default.
            kelly_saved = await self.db.get_setting("engine.kelly_mode")
            if kelly_saved is not None:
                self._kelly_mode = (kelly_saved == "1")
                logger.info(
                    f"🎯 Kelly mode loaded from DB: "
                    f"{'ON' if self._kelly_mode else 'OFF'}")

            # Auto-name unnamed strategies
            unnamed = await self.db.conn.execute_fetchall(
                "SELECT id, asset, timeframe, direction, strategy_type, odds_threshold FROM strategies WHERE label IS NULL OR label=''")
            if unnamed:
                type_short = {"fusion": "F", "contrarian": "C", "sniper": "N",
                              "momentum": "M", "scalper": "S", "martingale": "MG", "highthreshold": "HT", "flashcrash": "FC", "streak": "SR"}
                for row in unnamed:
                    t = type_short.get(row[4] or "fusion", "?")
                    label = f"{t}_{row[1]}_{row[2]}_{row[3]}_{row[5]}"
                    await self.db.conn.execute(
                        "UPDATE strategies SET label=? WHERE id=?", (label, row[0]))
                await self.db.conn.commit()
                logger.info(f"📛 Auto-named {len(unnamed)} strategies")

            # Phase 25: Scalper threshold reform — raise to 0.70 (30t %50 = no edge below 0.70)
            sc = await self.db.conn.execute(
                """UPDATE strategies SET odds_threshold=0.70
                   WHERE strategy_type='scalper' AND status='active' AND odds_threshold<0.70""")
            if sc.rowcount > 0:
                await self.db.conn.commit()
                logger.info(f"📊 Scalper threshold raised to 0.70 ({sc.rowcount} strategies)")

            # Phase 27: Stop ETH HT (8t %38 -$8.98 = no edge)
            eth_ht = await self.db.conn.execute(
                """UPDATE strategies SET status='stopped'
                   WHERE strategy_type='highthreshold' AND label LIKE '%ETH%' AND status='active'""")
            if eth_ht.rowcount > 0:
                await self.db.conn.commit()
                logger.info(f"⚫ ETH HT stopped (no edge: 8t %38 -$8.98)")

            # Phase 32: Protect original strategy thresholds from AI corruption
            threshold_guards = [
                ("M_BTC_5m_any_0.92", 0.92),
                ("BTC High-Threshold Pure", 0.80),
            ]
            for label, orig_thr in threshold_guards:
                cur = await self.db.conn.execute_fetchall(
                    "SELECT odds_threshold FROM strategies WHERE label=?", (label,))
                if cur and cur[0][0] != orig_thr:
                    await self.db.conn.execute(
                        "UPDATE strategies SET odds_threshold=? WHERE label=?", (orig_thr, label))
                    await self.db.conn.commit()
                    logger.warning(f"🛡️ THRESHOLD RESTORE: {label} {cur[0][0]}→{orig_thr}")
            # Store guards for continuous enforcement
            self._threshold_guards = threshold_guards
        except Exception as e:  # noqa: BLE001
            # Epic 8 T8.1: omnibus startup settings block (DB / RiskLimits.from_dict /
            # brain_flags dict hydration / threshold UPDATE / auto-name loop).
            # Keep umbrella — any single failure here must NOT stop engine boot.
            logger.warning(f"Startup settings: {e}")

        self.optimizer.engine = self
        # Phase 28: Restore risk state from DB
        await self.risk.load_state(self.db)
        # Phase 30: Wire journal to DB for dual-write
        journal_set_db(self.db)
        # Phase 33: Load Thompson Sampling history
        await self.selector.load_from_db(self.db)
        self._task = asyncio.create_task(self._run())
        self._task.add_done_callback(self._on_engine_done)

        # Phase 82a hotfix: cycle-stall watchdog — detects a silently hung
        # main loop. If `_cycle` does not advance for STALL_TIMEOUT seconds
        # (default 90s), we force-cancel the task so `_on_engine_done` can
        # auto-restart. Without this, a hang in plugin.evaluate() or a lock
        # deadlock would leave the bot unresponsive indefinitely (root cause
        # of the 01:28 UTC freeze on 2026-04-17).
        # Phase 82e Sprint 2.1: safe_create_task wraps watchdog with exception
        # guard + Telegram alert. If the watchdog itself crashes, we want to
        # know immediately — otherwise a hung engine + dead watchdog = silent
        # death. reraise=False so the registry state is 'failed' (observable)
        # rather than the exception crashing the parent coroutine.
        self._stall_task = safe_create_task(
            self._stall_watchdog(), name="engine_stall_watchdog")

        # Phase 27: Start self-learning analyst
        await self.analyst.start()
        await self.live.start()  # Phase 34: Init mainnet shadow

        # 2026-05-05 Heddas direktifi: Manuel Market BUY/SELL UI için
        # live_trader.execute_market_order() scanner referansı bekler.
        try:
            self.live._engine_scanner = self.scanner
        except (AttributeError, Exception):  # noqa: BLE001
            pass

        # ═══════════════════════════════════════════════════════════════
        # Sprint 3 P1 WIRE (2026-05-05) — Heddas direktifi: paralel başla
        # Tüm wire'lar ENV-gated, default OFF — Sprint 2 SHADOW ACTIVE
        # mainnet'i bozmaz. ENV ile opt-in açılır:
        #   STRUCTURED_LOG_ENABLED=true
        #   ALLOWANCE_PREFLIGHT_ENABLED=true
        #   KILL_SWITCH_ENABLED=true (default true ama permissive)
        #   HEARTBEAT_ENABLED=true   (maker mode için zorunlu)
        #   RECON_ENABLED=true       (off-chain ↔ on-chain sync)
        # ═══════════════════════════════════════════════════════════════

        # P1.7 — Structured Logging (ENV: STRUCTURED_LOG_ENABLED)
        try:
            from core.structured_logging import setup_structured_logging
            setup_structured_logging()  # idempotent + ENV-gated
        except Exception as _sl_err:  # noqa: BLE001
            logger.debug(f"P1.7 structured_logging init: {_sl_err}")

        # P0.5 — Allowance Pre-flight (ENV: ALLOWANCE_PREFLIGHT_ENABLED)
        if os.getenv("ALLOWANCE_PREFLIGHT_ENABLED", "false").lower() in {"1", "true", "yes"}:
            try:
                from core.allowance_preflight import run_preflight
                if getattr(self.live, "_auth_verified", False):
                    pf_client = getattr(self.live, "_client", None)
                    if pf_client is not None:
                        ok, report = await run_preflight(pf_client)
                        if not ok:
                            logger.warning(f"⚠️ P0.5 Allowance pre-flight FAILED:\n{report}")
                        else:
                            logger.info("✅ P0.5 Allowance pre-flight OK")
            except Exception as _af_err:  # noqa: BLE001
                logger.debug(f"P0.5 allowance preflight wire: {_af_err}")

        # P0.8 — Portfolio Kill-Switch (ENV: KILL_SWITCH_ENABLED, default true)
        try:
            from core.portfolio_kill_switch import get_kill_switch
            self.portfolio_kill_switch = get_kill_switch()
            logger.info(
                f"🛑 P0.8 Kill-switch wired "
                f"(daily={self.portfolio_kill_switch.daily_max_loss_pct*100:.0f}% "
                f"weekly={self.portfolio_kill_switch.weekly_max_dd_pct*100:.0f}% "
                f"streak={self.portfolio_kill_switch.consecutive_limit})"
            )
        except Exception as _ks_err:  # noqa: BLE001
            logger.debug(f"P0.8 kill_switch wire: {_ks_err}")
            self.portfolio_kill_switch = None

        # P1.6.1 — Heartbeat 5s (ENV: HEARTBEAT_ENABLED, maker mode için zorunlu)
        if os.getenv("HEARTBEAT_ENABLED", "false").lower() in {"1", "true", "yes"}:
            try:
                from core.heartbeat import HeartbeatTask
                hb_client = getattr(self.live, "_client", None)
                if hb_client is not None:
                    self.heartbeat_task = HeartbeatTask(client=hb_client)
                    await self.heartbeat_task.start()
                    logger.info("💓 P1.6.1 Heartbeat task started (5s interval)")
            except Exception as _hb_err:  # noqa: BLE001
                logger.debug(f"P1.6.1 heartbeat wire: {_hb_err}")

        # P1.4 / P1-09 — Reconciliation loop.
        # P1-09-b (2026-05-09): single source of truth for the on/off
        # decision lives on `ReconciliationTask.enabled` (smart: explicit
        # RECON_ENABLED env wins; otherwise auto-on if LIVE_ENABLED=true).
        # We always instantiate + call start(); start() is a no-op when
        # disabled. This way the /recon panel can always inspect stats
        # instead of seeing a None task.
        try:
            from core.reconciliation.onchain_sync import ReconciliationTask
            self.recon_task = ReconciliationTask(
                db=self.db,
                wallet=os.getenv("POLYGON_WALLET", ""),
                alert_callback=getattr(self, "_notify_admin_html", None),
            )
            await self.recon_task.start()
            if self.recon_task.enabled:
                logger.info(
                    "🔁 Reconciliation task started "
                    f"(interval={self.recon_task.interval_s}s)")
        except Exception as _rc_err:  # noqa: BLE001
            logger.debug(f"reconciliation wire: {_rc_err}")
            self.recon_task = None

        # ═══ Sprint 3 P1 WIRE END ════════════════════════════════════════

        # Phase 39 (P1.2): Register trade listener with MarketRecorder so we
        # can advance maker queue position when real fills happen on the WS.
        recorder = getattr(self, "market_recorder", None)
        if recorder is not None:
            recorder._engine_trade_listener = self.on_real_trade
            logger.info("📨 Engine: trade listener registered (P1.2 maker queue)")

        of = self.odds_feed.get_status()
        bnc = ""
        if self.external_feed and self.external_feed.is_available:
            bnc = f" | Binance={self.external_feed._method}"
        logger.info(f"🚀 Engine v34 (Mainnet Ready){bnc} | open={len(self._open_positions)} | odds={of['total_records']}")

    def _on_engine_done(self, task: asyncio.Task):
        """Phase 66: Callback when engine task finishes — detect crashes and auto-restart."""
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            logger.info("Engine task cancelled (normal shutdown)")
            return
        if exc:
            logger.error(f"ENGINE TASK CRASHED: {exc!r}")
            # Auto-restart if still supposed to be running
            if self._running:
                logger.warning("Auto-restarting engine loop in 5s...")
                try:
                    asyncio.get_running_loop().call_later(5, self._restart_engine)
                except RuntimeError:
                    logger.error("No running loop — cannot auto-restart engine")
        else:
            if self._running:
                logger.warning("Engine task exited unexpectedly (no exception). Restarting...")
                try:
                    asyncio.get_running_loop().call_later(5, self._restart_engine)
                except RuntimeError:
                    logger.error("No running loop — cannot auto-restart engine")

    def _restart_engine(self):
        """Phase 66: Restart the engine loop task."""
        if not self._running:
            return
        logger.warning("Restarting engine loop NOW")
        self._task = asyncio.create_task(self._run())
        self._task.add_done_callback(self._on_engine_done)

    async def _stall_watchdog(self):
        """Phase 82a hotfix: watchdog that force-cancels a silently hung main loop.

        Monitors `self._cycle`. If it stops advancing for STALL_TIMEOUT seconds
        (default 90s), cancel `self._task` so `_on_engine_done` auto-restarts.
        The main loop sleeps 1s/cycle, so 90s = ~90 missed cycles — a real hang.

        ENV:
          ENGINE_STALL_TIMEOUT (int, seconds, default 90) — stall threshold
          ENGINE_STALL_ENABLED (0/1, default 1) — disable to debug
        """
        if os.getenv("ENGINE_STALL_ENABLED", "1") != "1":
            logger.info("stall_watchdog: disabled via ENGINE_STALL_ENABLED=0")
            return
        timeout = int(os.getenv("ENGINE_STALL_TIMEOUT", "90"))
        last_cycle = -1
        last_change = time.time()
        logger.info(f"stall_watchdog: started (timeout={timeout}s)")
        try:
            while self._running:
                await asyncio.sleep(10)
                cur = self._cycle
                now = time.time()
                if cur != last_cycle:
                    last_cycle = cur
                    last_change = now
                    continue
                stalled_for = now - last_change
                if stalled_for >= timeout:
                    logger.critical(
                        f"⛔ ENGINE STALL DETECTED: cycle={cur} frozen for "
                        f"{stalled_for:.0f}s (>= {timeout}s). Force-cancelling "
                        f"engine task — _on_engine_done will auto-restart."
                    )
                    # Push admin alert (fire-and-forget)
                    try:
                        if self.bot_app is not None:
                            admin_id = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_CHAT_ID")
                            if admin_id:
                                # Phase 82e Sprint 2.1: guarded fire-and-forget
                                safe_create_task(self.bot_app.bot.send_message(
                                    chat_id=int(admin_id),
                                    text=(
                                        f"⛔ <b>ENGINE STALL</b>\n"
                                        f"cycle={cur} frozen {stalled_for:.0f}s\n"
                                        f"Auto-restarting loop..."),
                                    parse_mode="HTML"),
                                    name="engine_stall_alert",
                                    notify=False)  # already an alert — no loop
                    except (TelegramError, ValueError, AttributeError) as _pe:
                        # Epic 8 T8.1: TG send failure / int(admin_id) cast / bot_app missing
                        logger.debug(f"stall push failed: {_pe}")
                    # Force-cancel hung task so _on_engine_done kicks restart
                    if self._task and not self._task.done():
                        self._task.cancel()
                    # Reset watchdog window so we don't spam cancels
                    last_change = now
        except asyncio.CancelledError:
            logger.info("stall_watchdog: cancelled, exiting")
        except Exception as e:  # noqa: BLE001 - T8.1 KEEP
            # Background supervisor loop — must survive any exception from
            # its watched task. Narrowing would defeat the point (this is
            # the *watchdog*, not the watched loop). Full traceback logged.
            logger.error(
                f"stall_watchdog: crashed: {type(e).__name__}: {e}\n"
                f"{traceback.format_exc()}")

    async def stop(self):
        # Phase 79b: Graceful shutdown — log open positions warning
        if self._open_positions:
            logger.warning(
                f"⚠️ SHUTDOWN with {len(self._open_positions)} open position(s). "
                f"They will be cleaned up as orphans on next startup.")
            for pk in self._open_positions:
                logger.warning(f"  Open: {pk}")
        else:
            logger.info("✅ SHUTDOWN: No open positions — clean exit.")
        self._running = False
        # Phase 82a hotfix: stop stall watchdog before main task
        _stall = getattr(self, "_stall_task", None)
        if _stall and not _stall.done():
            _stall.cancel()
            try:
                await _stall
            except asyncio.CancelledError:
                pass
            except Exception as _stall_err:  # noqa: BLE001
                # Epic 8 T8.1: watchdog may crash with anything — we are
                # shutting down, so a breadcrumb is enough (previously silent).
                logger.debug(f"stall_watchdog cleanup: {_stall_err}")
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _load_open(self):
        try:
            orphan_count = 0
            async with self.db.conn.execute(
                "SELECT id, strategy_id, event_slug, trade_amount, fee_amount, "
                "execution_price, direction, created_at "
                "FROM executions WHERE status='bet_placed'"
            ) as c:
                rows = [dict(r) async for r in c]

            for row in rows:
                # Phase 79b: Orphan detection — if position is older than 15 minutes,
                # the market has definitely settled. Close it as "lost" (conservative).
                created = row.get("created_at")
                is_orphan = False
                if created:
                    try:
                        t0 = datetime.fromisoformat(str(created))
                        if t0.tzinfo is None:
                            t0 = t0.replace(tzinfo=timezone.utc)
                        age_min = (datetime.now(timezone.utc) - t0).total_seconds() / 60
                        if age_min > 15:  # 5m market + 10min buffer
                            is_orphan = True
                    except (ValueError, TypeError) as _age_err:
                        # Epic 8 T8.1: malformed created_at string — skip orphan
                        # age check (was silent pass — now observable in debug).
                        logger.debug(f"orphan age parse: {_age_err} (created={created!r})")

                if is_orphan:
                    # Phase 79b: Try to resolve via API first, then fallback to last odds
                    slug = row.get("event_slug", "")
                    direction = row.get("direction", "up")
                    entry_price = row.get("execution_price") or 0.5
                    fee = row.get("fee_amount") or 0
                    amount = row.get("trade_amount") or 1
                    shares = amount / entry_price if entry_price > 0 else 0

                    resolved = None
                    try:
                        resolved = await self.client.check_market_resolved(slug)
                    except (httpx.HTTPError, asyncio.TimeoutError, AttributeError,
                            KeyError, ValueError) as _mres_err:
                        # Epic 8 T8.1: API lookup failure — fall through to
                        # odds-based fallback (was silent pass — now observable).
                        logger.debug(f"market_resolution API: {_mres_err}")

                    if resolved:
                        # API told us the actual result
                        won = (direction == resolved)
                        payout = round(shares * 1.0, 4) if won else 0.0
                        pnl = round(payout - amount - fee, 4)
                        result = "won" if won else "lost"
                        logger.info(
                            f"🧹 ORPHAN resolved via API: {slug} {direction} "
                            f"→ {result.upper()} PnL={pnl:+.2f} (resolution={resolved})")
                    else:
                        # No API resolution available — check last known odds
                        last_odds = self.odds_feed.get_last(slug) if hasattr(self, 'odds_feed') else None
                        if last_odds:
                            up_odds = last_odds.get("up", 0.5) if isinstance(last_odds, dict) else 0.5
                            won = (direction == "up" and up_odds > 0.70) or \
                                  (direction == "down" and up_odds < 0.30)
                            payout = round(shares * 1.0, 4) if won else 0.0
                            pnl = round(payout - amount - fee, 4)
                            result = "won" if won else "lost"
                            logger.warning(
                                f"🧹 ORPHAN settled by odds: {slug} {direction} "
                                f"→ {result.upper()} PnL={pnl:+.2f} (last_up={up_odds})")
                        else:
                            # Last resort: conservative lost
                            pnl = round(-amount - fee, 4)
                            payout = 0.0
                            result = "lost"
                            logger.warning(
                                f"🧹 ORPHAN no data: {slug} {direction} "
                                f"→ LOST (default) PnL={pnl:+.2f}")

                    now_iso = datetime.now(timezone.utc).isoformat()
                    await self.db.conn.execute(
                        "UPDATE executions SET status='claimed', pnl=?, payout=?, "
                        "result=?, closed_at=?, updated_at=? WHERE id=?",
                        (pnl, payout, result, now_iso, now_iso, row["id"]))
                    # Credit wallet if won
                    if payout > 0:
                        wallet_id = row.get("wallet_id") or (
                            await self.db.conn.execute_fetchall(
                                "SELECT wallet_id FROM executions WHERE id=?", (row["id"],))
                        )[0][0]
                        await self.db.conn.execute(
                            "UPDATE wallets SET balance = balance + ? WHERE id = ?",
                            (payout, wallet_id))
                    orphan_count += 1
                else:
                    self._open_positions.add(f"{row['strategy_id']}:{row['event_slug']}")
                    self.risk.record_trade_opened(row['trade_amount'], row['event_slug'],
                                                    strategy_id=row['strategy_id'])

            if orphan_count:
                await self.db.conn.commit()
                # Credit wallet for orphans (they lost, so debit was already done at open)
                logger.info(f"🧹 Cleaned up {orphan_count} orphan position(s) on startup")

        except (aiosqlite.Error, KeyError, TypeError, OSError) as e:
            # Epic 8 T8.1: DB fetchall / row-dict access / FS I/O
            logger.error(f"Load: {e}")

    async def _run(self):
        # ══ Phase 62: Warm-up gate — wait for data pipeline before trading ══
        _warmup_logged = False
        _warmup_max = int(os.getenv("WARMUP_MAX_WAIT", "120"))  # seconds
        _warmup_start = time.time()
        while self._running:
            elapsed = time.time() - _warmup_start
            scanner_ready = bool(self.scanner.active_markets)
            ws_ready = self.scanner.ws and self.scanner.ws.is_connected
            odds_ready = self.odds_feed.get_status().get("total_records", 0) >= 2
            if scanner_ready and (ws_ready or elapsed > 30) and (odds_ready or elapsed > 60):
                logger.info(
                    f"✅ Warm-up complete: scanner={scanner_ready} ws={ws_ready} "
                    f"odds={odds_ready} ({elapsed:.0f}s)")
                break
            if elapsed > _warmup_max:
                logger.warning(f"⚠️ Warm-up timeout ({_warmup_max}s) — starting anyway")
                break
            if not _warmup_logged:
                logger.info("⏳ Warm-up: waiting for scanner + WS + odds data...")
                _warmup_logged = True
            await asyncio.sleep(2)

        while self._running:
            self._cycle += 1
            try:
                if self.kill_switch.is_killed():
                    if self._cycle % 60 == 1:
                        logger.warning(f"🛑 Kill active c={self._cycle}")
                    await asyncio.sleep(1)
                    continue

                # ══ F-04: WS stale data gate ══
                await self._check_ws_health()

                strats = await self.db.get_active_strategies()
                if self._cycle % 30 == 1:
                    rs = self.risk.get_status()
                    ws_ok = "🟢" if self._is_ws_fresh() else "⚫"
                    skip_info = self.skips.summary()
                    bnc = ""
                    if self.external_feed and self.external_feed.is_available:
                        btc_price = self.external_feed.get_price("BTC")
                        bnc = f" | bnc=${btc_price:.0f}" if btc_price else " | bnc=stale"
                        # Phase 33: Update regime classifier
                        if btc_price:
                            self.regime.update(btc_price)
                    regime_str = f" | {self.regime.regime}" if self.regime.regime else ""
                    # Phase 41b: surface Phase 39/40 instrumentation in heartbeat
                    extras = ""
                    if self._cancel_count or self._ws_drop_count:
                        extras = f" | cnl={self._cancel_count} wsd={self._ws_drop_count}"
                    # Phase 53b: forced exit counter
                    fe_count = getattr(self, '_force_exits_today', 0)
                    fe_str = f" | fe={fe_count}" if fe_count else ""
                    # Phase 60: smart exit counter
                    se_count = getattr(self, '_smart_exits_today', 0)
                    se_str = f" | se={se_count}" if se_count else ""
                    fe_str += se_str
                    logger.info(
                        f"💓 c={self._cycle} | strats={len(strats)} | open={rs['open_positions']} | "
                        f"exp=${rs['total_exposure']:.0f} | pnl={rs['daily_pnl']:+.2f} | "
                        f"pend={len(self._pending)} | ws={ws_ok}{bnc}{regime_str}{extras}{fe_str} | {skip_info}")
                    log_heartbeat(self._cycle, len(strats), rs['open_positions'],
                                  list(self.scanner.active_markets.keys()))
                    # Sprint 2 S2-02: Log cycle skip summary to decisions.jsonl
                    try:
                        from core.trade_journal import log_decision_cycle_summary
                        _sc = self.skips.get_counts()
                        if _sc:
                            log_decision_cycle_summary(self._cycle, _sc)
                    except (ImportError, AttributeError, OSError) as _dcs_err:
                        # Epic 8 T8.1: journal import / skips API / disk I/O —
                        # previously silent pass masked observability bug.
                        logger.debug(f"decision_cycle_log: {_dcs_err}")
                    self.skips.reset()  # Reset after reporting

                    # Phase 49 P0-04: strats=0 watchdog
                    try:
                        zero_min = float(os.getenv("STRATS_ZERO_WARN_MINUTES", "10"))
                        if len(strats) == 0:
                            now_utc = datetime.now(timezone.utc)
                            if self._strats_zero_since is None:
                                self._strats_zero_since = now_utc
                            elapsed_min = (now_utc - self._strats_zero_since).total_seconds() / 60.0
                            if elapsed_min >= zero_min and not self._strats_zero_alerted:
                                logger.warning(
                                    f"⚠️ STRATS_ZERO: {elapsed_min:.1f} min boyunca aktif strateji yok — auto-resume veya manuel /resume gerekli")
                                self._strats_zero_alerted = True
                                # Fire-and-forget admin push
                                try:
                                    if self.bot_app is not None:
                                        admin_id = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_CHAT_ID")
                                        if admin_id:
                                            # Phase 82e Sprint 2.1: guarded fire-and-forget
                                            safe_create_task(self.bot_app.bot.send_message(
                                                chat_id=int(admin_id),
                                                text=f"⚠️ <b>STRATS=0 ALERT</b>\n{elapsed_min:.0f} dakikadır aktif strateji yok.\nAuto-resume env kontrolü veya /resume gerekli.",
                                                parse_mode="HTML"),
                                                name="engine_strats_zero_alert",
                                                notify=False)
                                except (TelegramError, ValueError, AttributeError) as _push_e:
                                    # Epic 8 T8.1: TG send / int(admin_id) / bot_app missing
                                    logger.debug(f"strats_zero push failed: {_push_e}")
                        else:
                            # Recovery: reset watchdog state
                            if self._strats_zero_since is not None or self._strats_zero_alerted:
                                logger.info(f"✅ STRATS_ZERO cleared: {len(strats)} strategies active again")
                            self._strats_zero_since = None
                            self._strats_zero_alerted = False
                    except Exception as _wd_e:  # noqa: BLE001
                        # Epic 8 T8.1: watchdog wrapper — env parse / datetime /
                        # dict access — must never crash main loop.
                        logger.debug(f"strats_zero watchdog error: {_wd_e}")

                # Phase 34: Continuous threshold protection (every ~5min)
                if self._cycle % 300 == 150:
                    for label, orig_thr in getattr(self, '_threshold_guards', []):
                        try:
                            cur = await self.db.conn.execute_fetchall(
                                "SELECT odds_threshold FROM strategies WHERE label=?", (label,))
                            if cur and cur[0][0] != orig_thr:
                                await self.db.conn.execute(
                                    "UPDATE strategies SET odds_threshold=? WHERE label=?", (orig_thr, label))
                                await self.db.conn.commit()
                                logger.warning(f"🛡️ GUARD: {label} {cur[0][0]}→{orig_thr}")
                        except (aiosqlite.Error, IndexError, TypeError) as _tg_err:
                            # Epic 8 T8.1: DB fetch / UPDATE / empty row slice —
                            # previously silent pass masked threshold drift bugs.
                            logger.debug(f"threshold_guard: {_tg_err}")

                # ══ F-01: All pending/trade ops under lock ══
                async with self._trade_lock:
                    await self._check_pending()

                verbose = (self._cycle % 60 == 1)
                for s in strats:
                    try:
                        await self._evaluate(s, verbose)
                    except Exception as e:  # noqa: BLE001 - T8.1 KEEP
                        # Per-strategy eval isolation: ONE strategy must
                        # never be able to crash the whole eval loop and
                        # freeze all other strategies. Phase 82a hotfix:
                        # include full traceback — previously only the
                        # exception message was logged, masking root cause.
                        logger.error(
                            f"Eval {s.id[:8]} ({getattr(s, 'strategy_type', '?')}): "
                            f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                        )

                await self._monitor()
                self._cleanup()
                await self.optimizer.run_check(self._cycle)

                # ══ Phase 74b: Per-strategy lifecycle check every 300 cycles (~5 min) ══
                if self._cycle % 300 == 0 and self._cycle > 0:
                    try:
                        await self.lifecycle.run_lifecycle_check()
                    except (aiosqlite.Error, AttributeError, RuntimeError,
                            ValueError) as _lc_err:
                        # Epic 8 T8.1: DB ops / lifecycle contract / value guards
                        logger.debug(f"lifecycle check: {_lc_err}")

                # ══ Phase 26: Adaptive threshold every ~10 min ══
                if self._cycle % 600 == 0 and self._cycle > 0:
                    await self.optimizer.adaptive_threshold_check()

                # ══ Phase 24: Daily auto-report at UTC 00:00 ══
                if self._cycle % 1800 == 0:  # Check every ~30 min
                    await self._check_daily_report()
            except asyncio.CancelledError:
                logger.info(f"Engine loop: cycle {self._cycle} cancelled")
                break
            except Exception as e:  # noqa: BLE001 - T8.1 KEEP
                # Engine main loop wrapper — must keep the loop alive
                # across transient failures (DB locks, WS blips, LLM
                # timeouts). Fatal exceptions (MemoryError / SystemExit /
                # KeyboardInterrupt) re-raise at L1000 so the watchdog
                # can restart the process cleanly.
                # Phase 82a hotfix: traceback + keep loop alive. Previously
                # `logger.error(f"Engine: {e}")` hid the root cause. If the
                # exception type is fatal (MemoryError, SystemExit), re-raise
                # so the process can be restarted cleanly by the watchdog.
                logger.error(
                    f"Engine cycle {self._cycle}: {type(e).__name__}: {e}\n"
                    f"{traceback.format_exc()}"
                )
                if isinstance(e, (MemoryError, SystemExit, KeyboardInterrupt)):
                    logger.critical(
                        f"Fatal exception in cycle {self._cycle} — re-raising to exit")
                    raise
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                logger.info("Engine loop: sleep cancelled, exiting")
                break

        # Phase 66: If we reach here, engine loop exited
        logger.warning(f"Engine loop exited at c={self._cycle} running={self._running}")

    def _is_ws_fresh(self) -> bool:
        """F-04: Check if WebSocket data is fresh enough to trade.

        Phase 82e Sprint 6+: threshold env'den runtime okunuyor, /env_toggle
        WS_STALE_THRESHOLD <secs> restart gerektirmez. engine_support.py'deki
        constant legacy default olarak kalir.
        """
        if not self.scanner.ws or not self.scanner.ws.is_connected:
            return False
        age = time.time() - self.scanner.ws._last_msg_ts if self.scanner.ws._last_msg_ts else 999
        ws_stale_secs = float(os.getenv("WS_STALE_THRESHOLD", "60.0"))
        return age < ws_stale_secs

    async def _check_ws_health(self):
        """F-14: Detect WS reconnect and flush stale pending orders.

        Epic 5 T5.1 (2026-04-21): Converted to async + _trade_lock wrap for
        F-01 hygiene. _pending.clear() was previously lock-free (safe via sync
        atomicity), but that invariant would break silently if this fn ever
        gained an await. Lock acquisition is cheap (rare drop-event path).

        Epic 5 T5.4 (2026-04-21): Also backfills live_prices on reconnect
        edge via REST /midpoint so we don't miss price movements during the
        drop gap. Reduces stale-gap from "next WS tick" (2-15s on sparse
        crypto markets) down to ~500ms REST latency. "Fiyatlar hep güncel
        olsun, bağlanıcaz diye aradaki hareketi kaçırmayalım."
        """
        ws = self.scanner.ws
        if not ws:
            return
        is_connected = ws.is_connected
        if self._ws_was_connected and not is_connected:
            # WS just dropped — flush pending orders to prevent stale fills
            self._ws_drop_count += 1  # Phase 40c: track for /status visibility
            if self._pending:
                logger.warning(
                    f"🔌 WS dropped (#{self._ws_drop_count})! "
                    f"Flushing {len(self._pending)} pending orders")
                async with self._trade_lock:
                    self._pending.clear()
            else:
                logger.warning(f"🔌 WS dropped (#{self._ws_drop_count}) — no pending to flush")
        # Epic 5 T5.4: reconnect edge (offline→online) → backfill prices
        if not self._ws_was_connected and is_connected:
            await self._backfill_prices_on_reconnect()
        self._ws_was_connected = is_connected

    async def _backfill_prices_on_reconnect(self):
        """Epic 5 T5.4: On WS reconnect, fetch fresh midpoints for all
        subscribed tokens via REST /midpoint in parallel. This reduces the
        price-gap from "wait for next WS tick" (2-15s, worse on sparse
        5-min crypto markets) to ~500ms (REST latency).

        Non-fatal: per-token None/exception results are skipped; a partial
        backfill is better than none. Pre-reconnect cache entries remain
        in live_prices but are already invalidated by _connected_since —
        backfilled entries have fresh timestamps so get_live_price accepts
        them. No bloat: dict keys stay the same size.
        """
        ws = self.scanner.ws
        if not ws or not ws.is_connected:
            return
        subscribed = list(ws._subscribed)
        if not subscribed:
            return
        client = self.client
        if not client:
            return
        try:
            results = await asyncio.gather(
                *(client.get_live_midpoint(tid) for tid in subscribed),
                return_exceptions=True)
        except Exception as e:  # noqa: BLE001
            # Epic 8 T8.1: gather() with return_exceptions=True rarely raises at
            # the top level — this is a defensive catch for coroutine-build
            # failures (AttributeError on client method, etc.). Keep umbrella.
            logger.warning(f"WS reconnect backfill failed: {type(e).__name__}: {e}")
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        backfilled = 0
        for tid, p in zip(subscribed, results):
            if isinstance(p, Exception) or p is None:
                continue
            if not (0.005 < p < 0.995):
                continue
            ws.live_prices[tid] = {"price": p, "ts": now_iso}
            backfilled += 1
        # Distinguish first-boot online vs real reconnect in the log
        event = "reconnect" if self._ws_drop_count > 0 else "online"
        logger.info(
            f"🔌 WS {event}: backfilled {backfilled}/{len(subscribed)} "
            f"prices via REST /midpoint")

    async def _check_daily_report(self):
        """Phase 24 + Sprint 2 S2-04: Send daily report + save snapshot at UTC 00:00."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hour = datetime.now(timezone.utc).hour
        if hour != 0 or self._last_daily_date == today:
            return
        self._last_daily_date = today
        try:
            # Generate report from yesterday's trades
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
            rows = await self.db.conn.execute_fetchall(
                """SELECT COUNT(*) as t,
                    COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) as w,
                    COALESCE(SUM(pnl),0) as pnl,
                    COALESCE(SUM(fee_amount),0) as fees,
                    COALESCE(AVG(signal_score),0) as avg_sig
                FROM executions WHERE result IS NOT NULL AND DATE(created_at)=?""",
                (yesterday,))
            if rows and rows[0][0] > 0:
                t, w, pnl, fees, avg_sig = rows[0]
                losses = t - w
                wr = w / t * 100 if t > 0 else 0
                bal = (await self.db.conn.execute_fetchall("SELECT balance FROM wallets LIMIT 1"))[0][0]

                # Sprint 2 S2-04: Best/worst strategy
                _strat_rows = await self.db.conn.execute_fetchall(
                    """SELECT s.label, SUM(e.pnl) as spnl
                    FROM executions e JOIN strategies s ON e.strategy_id=s.id
                    WHERE e.result IS NOT NULL AND DATE(e.created_at)=?
                    GROUP BY e.strategy_id ORDER BY spnl DESC""",
                    (yesterday,))
                top_strat = _strat_rows[0][0] if _strat_rows else None
                worst_strat = _strat_rows[-1][0] if _strat_rows and len(_strat_rows) > 1 else None

                # Active strategy count
                _act = await self.db.conn.execute_fetchall(
                    "SELECT COUNT(*) FROM strategies WHERE status='active'")
                active_strats = _act[0][0] if _act else 0

                # Sprint 2 S2-04: Save daily snapshot to DB
                try:
                    await self.db.conn.execute(
                        """INSERT OR REPLACE INTO daily_snapshots
                        (date,total_trades,wins,losses,pnl,wr,avg_signal_score,
                         active_strategies,top_strategy,worst_strategy,balance,fees,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (yesterday, t, w, losses, round(pnl, 4), round(wr, 1),
                         round(avg_sig, 4) if avg_sig else None,
                         active_strats, top_strat, worst_strat,
                         round(bal, 2), round(fees, 4),
                         datetime.now(timezone.utc).isoformat()))
                    await self.db.conn.commit()
                    logger.info(f"📸 Daily snapshot saved: {yesterday}")
                except (aiosqlite.Error, ValueError, TypeError) as _se:
                    # Epic 8 T8.1: INSERT / commit / numeric cast failures
                    logger.debug(f"Snapshot save: {_se}")

                text = (
                    f"📊 <b>Günlük Özet — {yesterday}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"İşlem: {t} | Kazanma: %{wr:.0f} | PnL: {pnl:+.2f}\n"
                    f"Komisyon: ${fees:.2f} | Bakiye: ${bal:.2f}\n"
                    f"Ort. Sinyal: {avg_sig:.2f} | Aktif Strat: {active_strats}\n"
                    f"En İyi: {top_strat or '-'} | En Kötü: {worst_strat or '-'}\n")
                # Send to admin
                admin_id = self.settings.ADMIN_TELEGRAM_ID
                if admin_id and self.bot_app:
                    # Epic 8 T8.1 ALARM 7: Telegram send must not abort the
                    # surrounding analytics block if TG backoff/network hiccups.
                    # Wrap in its own narrow guard rather than letting the
                    # outer except eat it silently.
                    try:
                        await self.bot_app.bot.send_message(
                            chat_id=admin_id, text=text, parse_mode="HTML")
                        logger.info(f"📊 Daily report sent: {t}t {wr:.0f}% {pnl:+.2f}")
                    except (TelegramError, ValueError, AttributeError) as _tg_err:
                        logger.warning(f"daily report TG send: {_tg_err}")
        except (aiosqlite.Error, KeyError, IndexError, TypeError, ValueError) as e:
            # Epic 8 T8.1: DB fetchall / row-slice / numeric cast — outer guard
            # only covers analytics SELECTs, not the TG send above.
            logger.debug(f"Daily report: {e}")

    def _cleanup(self):
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        for k in [k for k, v in self._settled_slugs.items() if v < cutoff]:
            del self._settled_slugs[k]
        # Phase 26: cleanup old open price records
        try:
            active_slugs = set()
            for mkts in self.scanner.active_markets.values():
                if isinstance(mkts, list):
                    for m in mkts:
                        if isinstance(m, dict):
                            active_slugs.add(m.get("slug", ""))
            self._market_open_recorded = self._market_open_recorded & active_slugs
        except (AttributeError, TypeError) as _mct_err:
            # Epic 8 T8.1: scanner/active_markets missing or unexpected shape —
            # previously silent pass masked scanner API drift.
            logger.debug(f"market cache trim: {_mct_err}")

    # ═══ PENDING ORDERS — Phase 38c: VWAP + signal→fill slippage + partial fill ═══
    # Phase 38c adds:
    #   (a) Signal→fill slippage logging (best_ask at signal time vs fill time)
    #   (b) Partial fill execution when orderbook depth < order amount
    #       (instead of silently skipping, as real Polymarket market orders do)
    #   (c) PARTIAL_FILL_MIN_USD minimum — avoid $0.01 dust fills
    PARTIAL_FILL_MIN_USD = 1.0  # match Polymarket $1 minimum order

    # ─── Phase 39 (P1.2): Maker queue position ────────────────────────
    PRICE_TICK_TOL = 0.005  # 0.5¢ — within tick = same price level

    # ─── Phase 40a: Polymarket exchange constraints ───────────────────
    # Real CLOB rejects orders with these violations. Paper trading must
    # mirror them so we can't paper-fill orders that the live wire would
    # have refused.
    PRICE_TICK = 0.01      # $0.01 minimum price increment on Polymarket
    MIN_ORDER_USD = 1.0    # $1 minimum notional per order
    # Polymarket also enforces a minimum of 5 *shares* per order at the CLOB
    # level — a $1 notional order at p=0.95 is only ~1.05 shares and would be
    # rejected even though it clears MIN_ORDER_USD. Phase 47f.9 gate.
    # Phase 62: ENV-configurable (paper trading uses 1.0 to avoid blocking $1 trades)
    MIN_ORDER_SHARES = float(os.getenv("MIN_ORDER_SHARES", "1.0"))


    # ═══ MONITOR + SETTLE ═══

