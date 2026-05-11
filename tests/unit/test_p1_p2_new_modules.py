"""
Unit tests for P0/P1/P2 yeni modüller (2026-04-30 sentez sonrası).

Coverage hedef: +30 test
- core.heartbeat (P1.6.1)
- core.maker_taker_decision (P1.6)
- core.reconciliation.onchain_sync (P1.4)
- core.structured_logging (P1.7)
- core.executor (P1.8)
- core.error_handler.polymarket_errors (P2.2)
- core.status_poller (P2.3)
- core.allowance_preflight (P0.5)
- core.portfolio_kill_switch (P0.8)
- backtest.slippage_model (P0.6)
- backtest.walk_forward (P0.6)
- telegram_bot.handlers.order_validator (P0.10)

Çalıştırma:
    py -3.11 -m pytest tests/unit/test_p1_p2_new_modules.py -v
"""
from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import MagicMock, AsyncMock, patch

import pytest


# ─── P1.6.1 Heartbeat Coroutine ─────────────────────────────────────────

class TestHeartbeatTask:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("HEARTBEAT_ENABLED", raising=False)
        from core.heartbeat import HeartbeatTask
        task = HeartbeatTask(client=MagicMock())
        assert task.enabled is False

    def test_enabled_via_env(self, monkeypatch):
        monkeypatch.setenv("HEARTBEAT_ENABLED", "true")
        from core.heartbeat import HeartbeatTask
        task = HeartbeatTask(client=MagicMock())
        assert task.enabled is True

    def test_interval_default(self):
        from core.heartbeat import HeartbeatTask, HEARTBEAT_INTERVAL_S_DEFAULT
        task = HeartbeatTask(client=MagicMock())
        assert task._interval_s == HEARTBEAT_INTERVAL_S_DEFAULT

    def test_interval_override(self):
        from core.heartbeat import HeartbeatTask
        task = HeartbeatTask(client=MagicMock(), interval_s=10)
        assert task._interval_s == 10

    def test_is_alive_initially_false(self):
        from core.heartbeat import HeartbeatTask
        task = HeartbeatTask(client=MagicMock())
        assert task.is_alive is False

    def test_stats_keys(self):
        from core.heartbeat import HeartbeatTask
        task = HeartbeatTask(client=MagicMock())
        s = task.stats
        for key in ("running", "is_alive", "heartbeat_id", "consecutive_fails", "interval_s", "enabled"):
            assert key in s


# ─── P1.6 Taker/Maker Decision Matrix ─────────────────────────────────

class TestMakerTakerDecision:
    def test_decision_extreme_urgency_returns_fok(self):
        from core.maker_taker_decision import decide_order_type
        ob = {"asks": [[0.55, 100]], "bids": [[0.50, 100]]}
        d = decide_order_type(ob, notional_usd=10, price=0.55, urgency="extreme")
        assert d.order_type == "FOK"
        assert d.role == "taker"

    def test_decision_high_urgency_returns_fak(self):
        from core.maker_taker_decision import decide_order_type
        ob = {"asks": [[0.55, 100]], "bids": [[0.50, 100]]}
        d = decide_order_type(ob, notional_usd=10, price=0.55, urgency="high")
        assert d.order_type == "FAK"

    def test_maker_disabled_falls_back_to_fok(self, monkeypatch):
        monkeypatch.setenv("MAKER_MODE_ENABLED", "false")
        from core.maker_taker_decision import decide_order_type
        ob = {"asks": [[0.60, 100]], "bids": [[0.50, 100]]}  # Wide spread
        d = decide_order_type(ob, notional_usd=10, price=0.55)
        assert d.order_type == "FOK"
        assert "MAKER_MODE_ENABLED=false" in d.reason

    def test_maker_wide_spread_returns_post_only(self, monkeypatch):
        monkeypatch.setenv("MAKER_MODE_ENABLED", "true")
        monkeypatch.setenv("MAKER_SPREAD_THRESHOLD_TICKS", "2")
        from core.maker_taker_decision import decide_order_type
        # Spread = 0.05 (5 tick @ 0.01) — geniş
        ob = {"asks": [[0.60, 100]], "bids": [[0.55, 100]]}
        d = decide_order_type(ob, notional_usd=10, price=0.55)
        assert d.order_type == "GTC_POST_ONLY"
        assert d.role == "maker"
        assert d.estimated_rebate_usd > 0

    def test_maker_tight_spread_returns_taker(self, monkeypatch):
        monkeypatch.setenv("MAKER_MODE_ENABLED", "true")
        monkeypatch.setenv("MAKER_SPREAD_THRESHOLD_TICKS", "2")
        from core.maker_taker_decision import decide_order_type
        # Spread = 0.01 (1 tick) — dar
        ob = {"asks": [[0.51, 100]], "bids": [[0.50, 100]]}
        d = decide_order_type(ob, notional_usd=10, price=0.51)
        assert d.order_type == "FOK"
        assert d.role == "taker"

    def test_html_breakdown_formatting(self):
        from core.maker_taker_decision import OrderDecision
        d = OrderDecision(
            order_type="FOK", role="taker",
            estimated_fee_usd=0.18, estimated_rebate_usd=0,
            reason="test", spread_ticks=1.0, urgency="normal",
        )
        html = d.html_breakdown()
        assert "FOK" in html
        assert "taker" in html
        assert "$0.1800" in html


# ─── P1.4 Reconciliation Loop ─────────────────────────────────────────

class TestReconciliationTask:
    def test_disabled_by_default(self, monkeypatch):
        # P1-09-a (2026-05-09): smart enable. Both RECON_ENABLED and
        # LIVE_ENABLED must be unset (or false) to default-disable.
        monkeypatch.delenv("RECON_ENABLED", raising=False)
        monkeypatch.delenv("LIVE_ENABLED", raising=False)
        from core.reconciliation.onchain_sync import ReconciliationTask
        task = ReconciliationTask(db=None, wallet="0xA7e75855")
        assert task.enabled is False

    def test_auto_on_in_live_mode(self, monkeypatch):
        # P1-09-a (2026-05-09): LIVE_ENABLED=true auto-enables reconciliation
        # when no explicit RECON_ENABLED override is set.
        monkeypatch.delenv("RECON_ENABLED", raising=False)
        monkeypatch.setenv("LIVE_ENABLED", "true")
        from core.reconciliation.onchain_sync import ReconciliationTask
        task = ReconciliationTask(db=None, wallet="0xA7e75855")
        assert task.enabled is True

    def test_explicit_disable_wins_over_live(self, monkeypatch):
        # Explicit RECON_ENABLED=false overrides live mode auto-on.
        monkeypatch.setenv("RECON_ENABLED", "false")
        monkeypatch.setenv("LIVE_ENABLED", "true")
        from core.reconciliation.onchain_sync import ReconciliationTask
        task = ReconciliationTask(db=None, wallet="0xA7e75855")
        assert task.enabled is False

    def test_threshold_default(self):
        from core.reconciliation.onchain_sync import ReconciliationTask
        task = ReconciliationTask(db=None, wallet="0xA7e75855")
        assert task.mismatch_threshold_usd == 1.0

    def test_threshold_override(self, monkeypatch):
        monkeypatch.setenv("RECON_MISMATCH_THRESHOLD_USD", "5.0")
        from core.reconciliation.onchain_sync import ReconciliationTask
        task = ReconciliationTask(db=None, wallet="0xA7e75855")
        assert task.mismatch_threshold_usd == 5.0

    def test_interval_default(self):
        from core.reconciliation.onchain_sync import ReconciliationTask
        task = ReconciliationTask(db=None, wallet="0xA7e75855")
        assert task.interval_s == 300

    def test_stats_keys(self):
        from core.reconciliation.onchain_sync import ReconciliationTask
        task = ReconciliationTask(db=None, wallet="0xA7e75855")
        for key in ("enabled", "running", "wallet", "mismatch_count", "interval_s", "threshold_usd"):
            assert key in task.stats


# ─── P1.7 Structured Logging ─────────────────────────────────────────

class TestSecretScrubbing:
    def test_scrub_private_key(self):
        from core.structured_logging import scrub_secrets
        text = "private_key = 0x1234567890abcdef" + "0" * 50
        out = scrub_secrets(text)
        assert "0x1234567890" not in out
        assert "[REDACTED" in out

    def test_scrub_api_key(self):
        from core.structured_logging import scrub_secrets
        text = "api_key=abcdef1234567890ABCDEF"
        out = scrub_secrets(text)
        assert "abcdef" not in out
        assert "REDACTED" in out

    def test_scrub_telegram_token(self):
        from core.structured_logging import scrub_secrets
        text = "token: 123456789:ABCdefGHIjklMNOpqrSTUvwxYZabcdef"
        out = scrub_secrets(text)
        assert "ABCdef" not in out
        assert "REDACTED" in out

    def test_scrub_polymarket_keys(self):
        from core.structured_logging import scrub_secrets
        text = "POLYMARKET_API_KEY=498bde4b1234567890"
        out = scrub_secrets(text)
        assert "498bde4b" not in out
        assert "REDACTED" in out

    def test_no_scrub_normal_text(self):
        from core.structured_logging import scrub_secrets
        text = "Bot started successfully, PnL=+$5.06"
        out = scrub_secrets(text)
        assert out == text  # unchanged


# ─── P1.8 Executor Abstraction ─────────────────────────────────────────

class TestPaperExecutor:
    @pytest.mark.asyncio
    async def test_paper_buy_with_orderbook(self):
        from core.executor import PaperExecutor, OrderRequest
        ex = PaperExecutor(initial_balance_usd=1000)
        ob = {"asks": [[0.55, 100], [0.56, 200]], "bids": [[0.54, 80]]}
        ex.set_orderbook_source(lambda token_id: ob)

        req = OrderRequest(token_id="0x123", side="BUY", amount_usd=20, price=0.60)
        result = await ex.place_order(req)
        assert result.executor_mode == "paper"
        assert result.filled is True
        assert result.shares > 0
        assert result.notional_filled_usd > 0

    @pytest.mark.asyncio
    async def test_paper_buy_above_max_price_rejects(self):
        from core.executor import PaperExecutor, OrderRequest
        ex = PaperExecutor()
        ob = {"asks": [[0.60, 100]], "bids": [[0.50, 100]]}
        ex.set_orderbook_source(lambda token_id: ob)
        req = OrderRequest(token_id="0x123", side="BUY", amount_usd=10, price=0.55)
        result = await ex.place_order(req)
        assert result.filled is False
        assert "above_max" in (result.rejected_reason or "")

    def test_paper_initial_balance(self):
        from core.executor import PaperExecutor
        ex = PaperExecutor(initial_balance_usd=5000)
        assert ex.get_balance_usd() == 5000

    def test_executor_factory_caches(self):
        from core.executor import get_executor
        e1 = get_executor("paper")
        e2 = get_executor("paper")
        assert e1 is e2  # singleton


# ─── P2.2 Polymarket Error Code Mapping ───────────────────────────────

class TestPolymarketErrors:
    def test_invalid_tick_size(self):
        from core.error_handler.polymarket_errors import classify_error
        info = classify_error("INVALID_ORDER_MIN_TICK_SIZE")
        assert info.code == "INVALID_ORDER_MIN_TICK_SIZE"
        assert info.auto_fix == "snap_to_tick"
        assert "tick" in info.tr_message.lower()

    def test_insufficient_balance(self):
        from core.error_handler.polymarket_errors import classify_error
        info = classify_error("INVALID_ORDER_NOT_ENOUGH_BALANCE")
        assert info.severity == "error"
        assert "bakiye" in info.tr_message.lower() or "balance" in info.en_message.lower()

    def test_unauthorized_401(self):
        from core.error_handler.polymarket_errors import classify_error
        info = classify_error("PolyApiException: status_code=401, Unauthorized")
        assert info.code == "INVALID_API_KEY"
        assert info.severity == "critical"

    def test_cloudflare_403(self):
        from core.error_handler.polymarket_errors import classify_error
        info = classify_error("status=403 url=... Cloudflare blocked")
        assert info.code == "HTTP_403_CLOUDFLARE"

    def test_unknown_returns_unknown(self):
        from core.error_handler.polymarket_errors import classify_error
        info = classify_error("Some random error never seen before XYZ")
        assert info.code == "UNKNOWN"

    def test_telegram_format(self):
        from core.error_handler.polymarket_errors import classify_error, format_for_telegram
        info = classify_error("INVALID_POST_ONLY_ORDER")
        html = format_for_telegram(info)
        assert "INVALID_POST_ONLY_ORDER" in html
        assert "<b>" in html


# ─── P2.3 Status Poller ─────────────────────────────────────────────

class TestStatusPoller:
    @pytest.mark.asyncio
    async def test_poll_terminal_status_returns_immediately(self):
        from core.status_poller import poll_order_status, TERMINAL_STATUSES
        client = MagicMock()
        client.get_order = MagicMock(return_value={"status": "confirmed"})

        result = await poll_order_status(client, "order123", max_attempts=3, initial_wait_s=0.01, max_wait_s=0.01)
        assert result["final_status"] == "confirmed"
        assert result["attempts"] == 1

    @pytest.mark.asyncio
    async def test_poll_timeout_returns_last(self):
        from core.status_poller import poll_order_status
        client = MagicMock()
        client.get_order = MagicMock(return_value={"status": "matched"})

        result = await poll_order_status(client, "order123", max_attempts=2, initial_wait_s=0.01, max_wait_s=0.01)
        assert result["final_status"] == "matched"
        assert result.get("timeout") is True

    @pytest.mark.asyncio
    async def test_poll_no_response_handled(self):
        from core.status_poller import poll_order_status
        client = MagicMock()
        client.get_order = MagicMock(side_effect=Exception("network"))

        result = await poll_order_status(client, "order123", max_attempts=2, initial_wait_s=0.01, max_wait_s=0.01)
        assert result["attempts"] == 2
        # No crash; final_status may be None or "timeout"


# ─── P0.5 Allowance Preflight ─────────────────────────────────────────

class TestAllowancePreflight:
    @pytest.mark.asyncio
    async def test_check_collateral_with_none_client(self):
        from core.allowance_preflight import check_collateral_allowance
        result = await check_collateral_allowance(None)
        assert result["ok"] is False
        assert "None" in (result.get("error") or "")

    @pytest.mark.asyncio
    async def test_check_conditional_inferred_when_no_token(self):
        from core.allowance_preflight import check_conditional_allowance
        result = await check_conditional_allowance(client=None, sample_token_id=None)
        assert result["inferred"] is True
        assert result["ok"] is True

    def test_format_status_report(self):
        from core.allowance_preflight import format_status_report
        status = {
            "collateral": {"ok": True, "balance": 1.49, "allowance": 1000.0},
            "conditional": {"ok": True, "inferred": True},
            "summary": {"all_ok": True, "missing": [], "inferred": True},
        }
        html = format_status_report(status)
        assert "✅" in html
        assert "$1.49" in html


# ─── P0.8 Portfolio Kill-Switch ─────────────────────────────────────────

class TestPortfolioKillSwitch:
    def test_disabled_returns_no_halt(self, monkeypatch):
        monkeypatch.setenv("KILL_SWITCH_ENABLED", "false")
        from core.portfolio_kill_switch import PortfolioKillSwitch
        ks = PortfolioKillSwitch()
        d = ks.evaluate(current_equity=1000)
        assert d.halted is False
        assert d.reason == "KILL_SWITCH_DISABLED"

    def test_consecutive_loss_triggers_cooldown(self, monkeypatch):
        monkeypatch.setenv("KILL_SWITCH_ENABLED", "true")
        monkeypatch.setenv("KILL_CONSECUTIVE_LOSS_LIMIT", "3")
        monkeypatch.setenv("KILL_CONSECUTIVE_COOLDOWN_S", "60")
        from core.portfolio_kill_switch import PortfolioKillSwitch
        ks = PortfolioKillSwitch()
        ks.record_trade(-1.0)
        ks.record_trade(-1.0)
        ks.record_trade(-1.0)  # 3rd loss → cooldown
        d = ks.evaluate(current_equity=1000)
        assert d.halted is True
        assert "CONSECUTIVE" in d.reason

    def test_win_resets_streak(self):
        from core.portfolio_kill_switch import PortfolioKillSwitch
        ks = PortfolioKillSwitch()
        ks.record_trade(-1.0)
        ks.record_trade(-1.0)
        ks.record_trade(2.0)  # win
        assert ks.state.consecutive_losses == 0


# ─── P0.6 Slippage Model ─────────────────────────────────────────────

class TestSlippageModel:
    def test_buy_simple_fill(self):
        from backtest.slippage_model import SlippageModel
        ob = {"asks": [[0.55, 100], [0.56, 200]], "bids": [[0.54, 100]]}
        sim = SlippageModel(ob)
        fill = sim.simulate_market_buy(notional_usd=20, max_price=0.60)
        assert fill.filled is True
        assert fill.shares > 0

    def test_buy_below_min_rejects(self):
        from backtest.slippage_model import SlippageModel
        ob = {"asks": [[0.55, 100]], "bids": [[0.50, 100]]}
        sim = SlippageModel(ob)
        fill = sim.simulate_market_buy(notional_usd=2, max_price=0.60)  # <$5
        assert fill.filled is False
        assert "min" in (fill.rejected_reason or "").lower()

    def test_empty_book_rejects(self):
        from backtest.slippage_model import SlippageModel
        sim = SlippageModel({"asks": [], "bids": []})
        fill = sim.simulate_market_buy(notional_usd=10)
        assert fill.filled is False
        assert "empty" in (fill.rejected_reason or "").lower()


# ─── P0.10 Order Validator ─────────────────────────────────────────

class TestOrderValidator:
    def test_validates_clean_order(self, monkeypatch):
        monkeypatch.setenv("ORDER_VALIDATOR_ENABLED", "true")
        monkeypatch.setenv("ORDER_MAX_USD", "10")
        from telegram_bot.handlers.order_validator import validate_order
        result = validate_order(
            side="BUY", amount_usd=5.0, price=0.50,
            token_id="1234567890abcdef" * 4,
        )
        assert result.ok is True

    def test_rejects_large_amount(self, monkeypatch):
        monkeypatch.setenv("ORDER_VALIDATOR_ENABLED", "true")
        monkeypatch.setenv("ORDER_MAX_USD", "10")
        from telegram_bot.handlers.order_validator import validate_order
        result = validate_order(
            side="BUY", amount_usd=100, price=0.50,
            token_id="1234567890abcdef" * 4,
        )
        assert result.ok is False
        assert "büyük" in result.error_html.lower() or "max" in result.error_html.lower()

    def test_rejects_high_price(self, monkeypatch):
        monkeypatch.setenv("ORDER_VALIDATOR_ENABLED", "true")
        from telegram_bot.handlers.order_validator import validate_order
        result = validate_order(
            side="BUY", amount_usd=5, price=0.99,
            token_id="1234567890abcdef" * 4,
        )
        assert result.ok is False

    def test_rejects_invalid_tick(self, monkeypatch):
        monkeypatch.setenv("ORDER_VALIDATOR_ENABLED", "true")
        from telegram_bot.handlers.order_validator import validate_order
        result = validate_order(
            side="BUY", amount_usd=5, price=0.555,  # not 0.01 tick
            token_id="1234567890abcdef" * 4,
        )
        assert result.ok is False
        assert "tick" in result.error_html.lower()

    def test_skip_validator_bypass(self):
        from telegram_bot.handlers.order_validator import validate_order
        result = validate_order(
            side="BUY", amount_usd=10000, price=0.999,
            token_id="x", skip_validator=True,
        )
        assert result.ok is True


# ─── PYTEST CONFIG ─────────────────────────────────────────

# Note: tests use `pytest-asyncio` for async test methods
# pytest.ini içinde `asyncio_mode = auto` veya marker dekorator gerek.
# Basitlik için sync wrap'ler de eklenebilir.
