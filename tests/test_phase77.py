"""
Phase 76+77 Integration Tests
================================
Tests: Trade Memory, Decision Explainer, Experiment Runner,
       Markov Estimator, Capital Allocator, BondingYield,
       DB migration, pipeline integration.
"""
import asyncio
import json
import os
import pytest
import aiosqlite

# ═══════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════

class FakeDB:
    """Lightweight in-memory DB for testing."""
    def __init__(self):
        self.conn = None

    async def init(self):
        self.conn = await aiosqlite.connect(":memory:")
        self.conn.row_factory = aiosqlite.Row
        # Create minimal schema
        await self.conn.execute("""
            CREATE TABLE executions (
                id TEXT PRIMARY KEY,
                user_id TEXT, wallet_id TEXT, strategy_id TEXT,
                event_slug TEXT, direction TEXT, trade_amount REAL,
                execution_price REAL, status TEXT DEFAULT 'pending',
                pnl REAL DEFAULT 0.0, result TEXT, signal_score REAL DEFAULT 0.0,
                closed_at TEXT, created_at TEXT, updated_at TEXT,
                win_probability REAL DEFAULT 0.5
            )
        """)
        await self.conn.execute("""
            CREATE TABLE strategies (
                id TEXT PRIMARY KEY, label TEXT, status TEXT DEFAULT 'active',
                strategy_type TEXT, asset TEXT, timeframe TEXT,
                direction TEXT, odds_threshold REAL
            )
        """)
        await self.conn.commit()

    async def close(self):
        if self.conn:
            await self.conn.close()


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def db(event_loop):
    db = FakeDB()
    event_loop.run_until_complete(db.init())
    yield db
    event_loop.run_until_complete(db.close())


# ═══════════════════════════════════════
# TRADE MEMORY TESTS
# ═══════════════════════════════════════

class TestTradeMemory:
    def test_helpers(self):
        from core.trade_memory import _price_zone, _time_zone, _day_type, _asset_from_slug

        assert _price_zone(0.0) == "0-10"
        assert _price_zone(0.15) == "10-20"
        assert _price_zone(0.50) == "50-60"
        assert _price_zone(0.55) == "50-60"
        assert _price_zone(0.92) == "90-100"
        assert _price_zone(1.0) == "100-100"

        assert _asset_from_slug("btc-usd-5m-up") == "BTC"
        assert _asset_from_slug("eth-usdc-15m") == "ETH"
        assert _asset_from_slug("sol-whatever") == "SOL"
        assert _asset_from_slug("random-market") == "OTHER"

        assert _time_zone() in ("morning", "afternoon", "evening", "night")
        assert _day_type() in ("weekday", "weekend")

    def test_singleton(self):
        from core.trade_memory import get_trade_memory
        a = get_trade_memory()
        b = get_trade_memory()
        assert a is b

    def test_initialize_creates_table(self, db, event_loop):
        from core.trade_memory import get_trade_memory
        tm = get_trade_memory()
        event_loop.run_until_complete(tm.initialize(db))

        # Verify table exists
        rows = event_loop.run_until_complete(
            db.conn.execute_fetchall("SELECT name FROM sqlite_master WHERE type='table' AND name='trade_memory'")
        )
        assert len(rows) == 1

    def test_record_and_retrieve(self, db, event_loop):
        from core.trade_memory import TradeMemory
        tm = TradeMemory()
        event_loop.run_until_complete(tm.initialize(db))

        # Record 6 trades (above MIN_PATTERN_TRADES=5)
        for i in range(6):
            result = "won" if i % 2 == 0 else "lost"
            pnl = 0.5 if result == "won" else -0.5
            event_loop.run_until_complete(
                tm.record("strat1", "btc-usd-5m", "up", result, pnl,
                          signal_score=0.6, entry_price=0.55)
            )

        # Verify records in DB
        rows = event_loop.run_until_complete(
            db.conn.execute_fetchall("SELECT COUNT(*) FROM trade_memory")
        )
        assert rows[0][0] == 6

    def test_pattern_stats(self, db, event_loop):
        from core.trade_memory import TradeMemory
        tm = TradeMemory()
        event_loop.run_until_complete(tm.initialize(db))

        # Record 10 trades: 8 wins, 2 losses → 80% WR → should boost
        for i in range(10):
            result = "won" if i < 8 else "lost"
            pnl = 1.0 if result == "won" else -1.0
            event_loop.run_until_complete(
                tm.record("strat1", "btc-usd-5m", "up", result, pnl,
                          entry_price=0.55)
            )

        # Get pattern (same context)
        pattern = event_loop.run_until_complete(
            tm.get_pattern("strat1", "btc-usd-5m", 0.55)
        )
        assert pattern is not None
        assert pattern.total_trades == 10
        assert pattern.win_rate == 80.0
        assert pattern.confidence_mult > 1.0  # Should boost

    def test_worst_patterns(self, db, event_loop):
        from core.trade_memory import TradeMemory
        tm = TradeMemory()
        event_loop.run_until_complete(tm.initialize(db))

        # Record 6 losses for a bad pattern
        for i in range(6):
            event_loop.run_until_complete(
                tm.record("bad_strat", "eth-usd-5m", "down", "lost", -1.0,
                          entry_price=0.35)
            )

        worst = event_loop.run_until_complete(tm.get_worst_patterns(5))
        assert len(worst) >= 1
        assert worst[0].win_rate == 0.0

    def test_mistakes_tracking(self, db, event_loop):
        from core.trade_memory import TradeMemory
        tm = TradeMemory()
        event_loop.run_until_complete(tm.initialize(db))

        # Record overconfident loss (signal > 0.5 but lost)
        event_loop.run_until_complete(
            tm.record("strat1", "btc-usd-5m", "up", "lost", -2.0,
                      signal_score=0.85, entry_price=0.60)
        )
        assert len(tm._mistakes) == 1
        assert tm._mistakes[0]["score"] == 0.85

    def test_format_telegram(self):
        from core.trade_memory import TradeMemory, PatternStats
        tm = TradeMemory()
        patterns = [
            PatternStats(pattern_key="strat1:BTC:50-60:morning:weekday",
                         total_trades=20, wins=14, losses=6,
                         total_pnl=5.50, avg_pnl=0.275, win_rate=70.0),
        ]
        text = tm.format_telegram(patterns, "Test")
        assert "🧠" in text
        assert "70" in text


# ═══════════════════════════════════════
# DECISION EXPLAINER TESTS
# ═══════════════════════════════════════

class TestDecisionExplainer:
    def test_singleton(self):
        from core.decision_explainer import get_decision_explainer
        a = get_decision_explainer()
        b = get_decision_explainer()
        assert a is b

    def test_chain_lifecycle(self):
        from core.decision_explainer import DecisionExplainer
        de = DecisionExplainer()

        chain = de.new_chain("strat1", "btc-usd-5m")
        chain.direction = "up"
        chain.final_score = 0.72
        chain.trade_amount = 5.0
        chain.decision = "trade"

        chain.add_step("strategy", "evaluate", "conf=0.80", "positive")
        chain.add_step("confluence", "gate_pass", "4/6", "positive")
        chain.add_step("markov", "boost", "+0.05", "positive")
        chain.add_step("memory", "penalty", "-0.03", "negative")

        de.finalize(chain)

        assert len(chain.steps) == 4
        assert "Güçlü" in chain.summary_tr
        assert "Zayıf" in chain.summary_tr

    def test_to_json(self):
        from core.decision_explainer import ReasoningChain
        chain = ReasoningChain(
            strategy_id="s1", slug="btc", direction="up",
            final_score=0.65, decision="trade"
        )
        chain.add_step("test", "act", "val", "positive")
        j = chain.to_json()
        data = json.loads(j)
        assert data["strategy"] == "s1"
        assert len(data["steps"]) == 1

    def test_telegram_formats(self):
        from core.decision_explainer import DecisionExplainer
        de = DecisionExplainer()
        chain = de.new_chain("s1", "btc-5m")
        chain.direction = "up"
        chain.final_score = 0.65
        chain.decision = "trade"
        chain.trade_amount = 5.0
        chain.add_step("strat", "eval", "0.8", "positive")
        de.finalize(chain)

        short = chain.format_telegram_short()
        assert "strat" in short

        full = chain.format_telegram_full()
        assert "Adımlar" in full
        assert "strat" in full

    def test_recent_retrieval(self):
        from core.decision_explainer import DecisionExplainer
        de = DecisionExplainer()

        for i in range(5):
            c = de.new_chain(f"s{i}", f"slug{i}")
            c.decision = "trade" if i % 2 == 0 else "skip"
            c.direction = "up"
            de.finalize(c)

        trades = de.get_recent(10, trades_only=True)
        assert len(trades) == 3  # 0, 2, 4

        all_decisions = de.get_recent(10, trades_only=False)
        assert len(all_decisions) == 5

    def test_db_persist_and_load(self, db, event_loop):
        from core.decision_explainer import DecisionExplainer
        de = DecisionExplainer()
        event_loop.run_until_complete(de.initialize(db))

        # Insert a dummy execution
        event_loop.run_until_complete(db.conn.execute(
            "INSERT INTO executions (id, user_id, wallet_id, strategy_id, event_slug, direction, trade_amount, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ex1", "u1", "w1", "s1", "btc", "up", 5.0, "2026-01-01", "2026-01-01")
        ))
        event_loop.run_until_complete(db.conn.commit())

        chain = de.new_chain("s1", "btc")
        chain.direction = "up"
        chain.decision = "trade"
        chain.add_step("test", "act", "val", "positive")
        de.finalize(chain)

        event_loop.run_until_complete(de.persist(chain, "ex1"))

        # Load back
        loaded = event_loop.run_until_complete(de.get_from_db("ex1"))
        assert loaded is not None
        assert loaded.strategy_id == "s1"
        assert len(loaded.steps) == 1


# ═══════════════════════════════════════
# EXPERIMENT RUNNER TESTS
# ═══════════════════════════════════════

class TestExperimentRunner:
    def test_singleton(self):
        from core.experiment_runner import get_experiment_runner
        a = get_experiment_runner()
        b = get_experiment_runner()
        assert a is b

    def test_parse_params(self):
        from core.experiment_runner import ExperimentRunner
        er = ExperimentRunner()
        params = er.parse_params(["MIN_COMPOSITE=0.30", "EDGE_GATE=0.40", "bad"])
        assert params == {"MIN_COMPOSITE": "0.30", "EDGE_GATE": "0.40"}

    def test_parse_empty(self):
        from core.experiment_runner import ExperimentRunner
        er = ExperimentRunner()
        assert er.parse_params([]) == {}
        assert er.parse_params(["noequals"]) == {}

    def test_run_experiment(self, db, event_loop):
        from core.experiment_runner import ExperimentRunner
        er = ExperimentRunner()
        event_loop.run_until_complete(er.initialize(db))

        # Insert some baseline trades
        for i in range(20):
            result = "won" if i % 3 != 0 else "lost"
            pnl = 0.5 if result == "won" else -1.0
            event_loop.run_until_complete(db.conn.execute(
                "INSERT INTO executions (id, user_id, wallet_id, strategy_id, event_slug, direction, trade_amount, execution_price, status, pnl, result, signal_score, closed_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"ex{i}", "u1", "w1", "s1", "btc", "up", 5.0, 0.55,
                 "claimed", pnl, result, 0.6, "2026-04-12T10:00:00", "2026-04-12", "2026-04-12")
            ))
        event_loop.run_until_complete(db.conn.commit())

        result = event_loop.run_until_complete(
            er.run_experiment({"MIN_COMPOSITE": "0.40"})
        )
        assert result.baseline_trades == 20
        assert result.recommendation in ("apply", "discard", "neutral")
        assert er.has_pending

    def test_apply_discard(self, db, event_loop):
        from core.experiment_runner import ExperimentRunner
        er = ExperimentRunner()
        event_loop.run_until_complete(er.initialize(db))

        os.environ["TEST_PARAM_XYZ"] = "old_value"
        event_loop.run_until_complete(
            er.run_experiment({"TEST_PARAM_XYZ": "new_value"})
        )
        assert er.has_pending

        applied = er.apply_pending()
        assert applied is not None
        assert os.environ["TEST_PARAM_XYZ"] == "new_value"
        assert not er.has_pending

        # Cleanup
        del os.environ["TEST_PARAM_XYZ"]

    def test_discard(self, db, event_loop):
        from core.experiment_runner import ExperimentRunner
        er = ExperimentRunner()
        event_loop.run_until_complete(er.initialize(db))

        event_loop.run_until_complete(er.run_experiment({"FOO": "bar"}))
        assert er.has_pending
        assert er.discard_pending()
        assert not er.has_pending

    def test_format_telegram(self):
        from core.experiment_runner import ExperimentRunner, ExperimentResult
        er = ExperimentRunner()
        result = ExperimentResult(
            params_changed={"MIN_COMPOSITE": ("0.35", "0.30")},
            baseline_trades=100, baseline_wr=58.0, baseline_pnl=12.50,
            experiment_trades=110, experiment_wr=56.0, experiment_pnl=15.30,
            improvement=22.4, recommendation="apply",
            details="PnL artışı bekleniyor."
        )
        text = er.format_result_telegram(result)
        assert "Experiment" in text
        assert "MIN_COMPOSITE" in text
        assert "apply" in text.lower()


# ═══════════════════════════════════════
# MARKOV ESTIMATOR TESTS (Phase 76)
# ═══════════════════════════════════════

class TestMarkovEstimator:
    def test_basic_estimate(self):
        from core.markov_estimator import MarkovEstimator
        m = MarkovEstimator()
        series = [0.45 + i * 0.005 for i in range(20)]
        result = m.estimate(series, 0.55)
        assert result.direction in ("up", "down", None)
        assert result.enabled

    def test_short_series(self):
        from core.markov_estimator import MarkovEstimator
        m = MarkovEstimator()
        result = m.estimate([0.5, 0.51], 0.50)
        assert result.direction is None  # Too short

    def test_becker_fusion(self):
        from core.markov_estimator import MarkovEstimator
        m = MarkovEstimator()
        series = [0.45 + i * 0.005 for i in range(20)]
        result = m.estimate_with_becker(series, 0.55, becker_prob=0.60)
        assert result.estimated_prob > 0
        assert "fused" in result.reason

    def test_format_telegram(self):
        from core.markov_estimator import MarkovEstimator
        m = MarkovEstimator()
        series = [0.50] * 20
        result = m.estimate(series, 0.50)
        text = m.format_telegram(result, "test-slug")
        assert "Markov" in text or "markov" in text.lower()


# ═══════════════════════════════════════
# CAPITAL ALLOCATOR TESTS (Phase 76)
# ═══════════════════════════════════════

class TestCapitalAllocator:
    def test_basic_budget(self, db, event_loop):
        from core.capital_allocator import CapitalAllocator
        ca = CapitalAllocator()
        event_loop.run_until_complete(ca.initialize(db))
        budget = event_loop.run_until_complete(ca.get_budget("strat1"))
        assert budget.strategy_id == "strat1"
        assert budget.allocated > 0

    def test_can_trade(self, db, event_loop):
        from core.capital_allocator import CapitalAllocator
        ca = CapitalAllocator()
        event_loop.run_until_complete(ca.initialize(db))

        result = event_loop.run_until_complete(ca.can_trade("strat1", 5.0))
        assert result["allowed"]

    def test_reserve_release(self, db, event_loop):
        from core.capital_allocator import CapitalAllocator
        ca = CapitalAllocator()
        event_loop.run_until_complete(ca.initialize(db))

        # Get initial used amount
        b = event_loop.run_until_complete(ca.get_budget("strat_rr"))
        initial_used = b.used  # should be 0

        event_loop.run_until_complete(ca.reserve("strat_rr", 10.0))
        b2 = event_loop.run_until_complete(ca.get_budget("strat_rr"))
        assert b2.used == initial_used + 10.0

        event_loop.run_until_complete(ca.release("strat_rr", 10.0))
        b3 = event_loop.run_until_complete(ca.get_budget("strat_rr"))
        assert b3.used == initial_used

    def test_format_telegram(self):
        from core.capital_allocator import CapitalAllocator
        ca = CapitalAllocator()
        text = ca.format_telegram()
        assert "💰" in text


# ═══════════════════════════════════════
# BONDING YIELD STRATEGY TESTS (Phase 76)
# ═══════════════════════════════════════

class TestBondingYield:
    def test_qualifying_trade(self):
        from core.strategy_plugins import StrategyRegistry, MarketSnapshot
        reg = StrategyRegistry()
        snap = MarketSnapshot(up_odds=0.95, down_odds=0.05, spread=0.01,
                              minutes_remaining=2.0, total_minutes=5.0)
        sig = reg.evaluate("bonding_yield", snap)
        assert sig.should_trade
        assert sig.direction == "up"
        assert sig.confidence > 0.9

    def test_non_qualifying(self):
        from core.strategy_plugins import StrategyRegistry, MarketSnapshot
        reg = StrategyRegistry()
        snap = MarketSnapshot(up_odds=0.60, down_odds=0.40, spread=0.02,
                              minutes_remaining=2.0, total_minutes=5.0)
        sig = reg.evaluate("bonding_yield", snap)
        assert not sig.should_trade

    def test_spread_too_wide(self):
        from core.strategy_plugins import StrategyRegistry, MarketSnapshot
        reg = StrategyRegistry()
        # 95c contract with 5c spread → yield is ~3%, spread is too wide
        snap = MarketSnapshot(up_odds=0.95, down_odds=0.05, spread=0.05,
                              minutes_remaining=2.0, total_minutes=5.0)
        sig = reg.evaluate("bonding_yield", snap)
        assert not sig.should_trade
        assert "spread" in sig.reason.lower()

    def test_down_direction(self):
        from core.strategy_plugins import StrategyRegistry, MarketSnapshot
        reg = StrategyRegistry()
        snap = MarketSnapshot(up_odds=0.05, down_odds=0.95, spread=0.01,
                              minutes_remaining=2.0, total_minutes=5.0)
        sig = reg.evaluate("bonding_yield", snap)
        assert sig.should_trade
        assert sig.direction == "down"

    def test_registry_has_11_strategies(self):
        from core.strategy_plugins import StrategyRegistry
        reg = StrategyRegistry()
        assert len(reg.names) >= 11
        assert "bonding_yield" in reg.names

    def test_configurable(self):
        from core.strategy_plugins import StrategyRegistry
        reg = StrategyRegistry()
        assert "bonding_yield" in reg.CONFIGURABLE
        cfg = reg.get_config("bonding_yield")
        assert "MIN_PRICE" in cfg


# ═══════════════════════════════════════
# HANDLER IMPORT TESTS
# ═══════════════════════════════════════

class TestHandlerImports:
    def test_phase76_handler(self):
        from telegram_bot.handlers.phase76_handler import markov_command, capital_command
        assert callable(markov_command)
        assert callable(capital_command)

    def test_phase77_handler(self):
        from telegram_bot.handlers.phase77_handler import (
            why_command, mistakes_command, patterns_command,
            health_command, experiment_command,
            experiment_apply_command, experiment_discard_command,
            why_callback, patterns_callback, health_callback,
        )
        assert callable(why_command)
        assert callable(health_command)
        assert callable(experiment_command)
