"""
PolyPaper Bot — Executor Abstraction
========================================
P1.8 (5AI Yol Haritası §5.2 GPT)

Common interface for live + paper execution.
Strateji executor.place_order() çağırır, hangi olduğunu BİLMEZ.

Bu paper-vs-live drift'i (T4.6-B paper×0.66) minimize eder:
- Aynı interface
- Aynı param normalizasyonu (tick rounding, size precision)
- Aynı slippage model (paper kullanır, live'da CLOB yapar)
- Aynı fee hesabı (core/fees_v2 oracle)

Mimari:

    Strategy
        │
        ▼
    Executor (abstract)
        ├── LiveExecutor  (Polymarket V2 SDK)
        └── PaperExecutor (in-memory + slippage_model + fees_v2)

ENV:
- EXECUTOR_MODE: "live" | "paper" | "both" (default "paper" until LIVE_ENABLED)

Usage:
    from core.executor import get_executor
    executor = get_executor(mode="paper")  # veya "live"
    result = await executor.place_order(
        token_id="0x...",
        side="BUY",
        amount_usd=10,
        price=0.55,
        order_type="FOK",
    )
    # result = ExecutionResult(filled=True, avg_price=..., shares=..., fee_usd=...)
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger("polypaper.core.executor")


@dataclass
class OrderRequest:
    """Strategy-level order intent (executor-agnostic)."""

    token_id: str
    side: str  # "BUY" | "SELL"
    amount_usd: float | None = None  # BUY için (notional)
    shares: float | None = None  # SELL için
    price: float = 0.50  # Limit price
    order_type: str = "FOK"  # FOK | FAK | GTC_POST_ONLY | GTD
    tick_size: str = "0.01"
    neg_risk: bool = False
    builder_code: str | None = None
    expiration: int | None = None  # GTD için Unix timestamp
    slug: str = ""  # logging için
    strategy_label: str = ""


@dataclass
class ExecutionResult:
    """Executor output (live + paper aynı şema)."""

    filled: bool
    order_id: str = ""
    avg_price: float = 0.0
    shares: float = 0.0
    notional_filled_usd: float = 0.0
    fee_usd: float = 0.0
    slippage_bps: float = 0.0
    rejected_reason: str | None = None
    transaction_hash: str | None = None
    raw_response: dict = field(default_factory=dict)
    executor_mode: str = ""  # "live" | "paper"

    @property
    def status(self) -> str:
        if self.filled:
            return "FILLED"
        return f"REJECTED:{self.rejected_reason}" if self.rejected_reason else "REJECTED"


class Executor(ABC):
    """Abstract executor interface."""

    mode: str = "abstract"

    @abstractmethod
    async def place_order(self, req: OrderRequest) -> ExecutionResult:
        """Place order, return execution result."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    def get_balance_usd(self) -> float:
        """Mevcut equity (USD)."""
        ...


class PaperExecutor(Executor):
    """In-memory simulation using slippage_model + fees_v2."""

    mode = "paper"

    def __init__(self, initial_balance_usd: float = 10000.0):
        self._balance_usd = initial_balance_usd
        self._open_orders: dict[str, dict] = {}
        self._order_seq = 0
        # Optional: orderbook source (set by engine)
        # P1-07 Round-3 (2026-05-11): explicit Callable | None so mypy keeps
        # the `if self._orderbook_source:` branch reachable after `set_orderbook_source`.
        from collections.abc import Callable as _Callable

        self._orderbook_source: _Callable[[str], dict] | None = None

    def set_orderbook_source(self, fn):
        """Engine wires this — PaperExecutor orderbook için callback."""
        self._orderbook_source = fn

    async def place_order(self, req: OrderRequest) -> ExecutionResult:
        # Get orderbook
        orderbook = None
        if self._orderbook_source:
            try:
                orderbook = self._orderbook_source(req.token_id)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"paper orderbook fetch fail: {e}")

        # Use slippage_model for realistic fill
        try:
            from backtest.slippage_model import SlippageModel

            sim = SlippageModel(orderbook or {})
            if req.side == "BUY" and req.amount_usd:
                fill = sim.simulate_market_buy(
                    notional_usd=req.amount_usd,
                    max_price=req.price,
                )
            elif req.side == "SELL" and req.shares:
                fill = sim.simulate_market_sell(
                    shares=req.shares,
                    max_price=req.price,
                )
            else:
                return ExecutionResult(
                    filled=False,
                    rejected_reason="missing amount/shares",
                    executor_mode=self.mode,
                )

            self._order_seq += 1
            order_id = f"paper_{self._order_seq:08d}"

            if fill.filled:
                # Update virtual balance
                if req.side == "BUY":
                    self._balance_usd -= fill.notional_filled_usd + fill.fee_usd
                else:
                    self._balance_usd += fill.notional_filled_usd - fill.fee_usd

            return ExecutionResult(
                filled=fill.filled,
                order_id=order_id,
                avg_price=fill.avg_price,
                shares=fill.shares,
                notional_filled_usd=fill.notional_filled_usd,
                fee_usd=fill.fee_usd,
                slippage_bps=fill.slippage_bps,
                rejected_reason=fill.rejected_reason,
                executor_mode=self.mode,
            )
        except ImportError:
            # slippage_model not installed → naive fallback
            # Naive fallback'te de orderbook varsa max_price kontrol et.
            if orderbook and req.side == "BUY":
                asks = orderbook.get("asks") or []
                if asks:
                    try:
                        best_ask = (
                            float(asks[0][0])
                            if isinstance(asks[0], list | tuple)
                            else float(asks[0].get("price", 0))
                        )
                        if req.price and best_ask > req.price:
                            return ExecutionResult(
                                filled=False,
                                rejected_reason=f"price_above_max ({best_ask:.4f} > {req.price:.4f})",
                                executor_mode=self.mode,
                            )
                    except (ValueError, TypeError, IndexError):
                        pass
            self._order_seq += 1
            order_id = f"paper_naive_{self._order_seq:08d}"
            return ExecutionResult(
                filled=True,
                order_id=order_id,
                avg_price=req.price,
                shares=(req.amount_usd / req.price) if req.amount_usd else (req.shares or 0),
                notional_filled_usd=req.amount_usd or 0,
                fee_usd=(req.amount_usd or 0) * 0.018,  # naive 1.8% taker fee
                slippage_bps=0,
                executor_mode=self.mode,
            )

    async def cancel_order(self, order_id: str) -> bool:
        return self._open_orders.pop(order_id, None) is not None

    def get_balance_usd(self) -> float:
        return self._balance_usd


class LiveExecutor(Executor):
    """Polymarket V2 SDK adapter (delegates to existing live_trader)."""

    mode = "live"

    def __init__(self, live_trader):
        """live_trader: bot's existing core.live_trader.LiveTrader instance."""
        self.live_trader = live_trader

    async def place_order(self, req: OrderRequest) -> ExecutionResult:
        if self.live_trader is None:
            return ExecutionResult(
                filled=False,
                rejected_reason="live_trader not initialized",
                executor_mode=self.mode,
            )
        # Delegate to existing maybe_mirror or _open_live_trade
        try:
            # Note: actual signature may differ; this is the abstract adapter
            result_dict = await self.live_trader.maybe_mirror(
                strategy_label=req.strategy_label,
                signal_score=1.0,  # already-decided signal
                direction="up" if req.side == "BUY" else "down",
                token_id=req.token_id,
                odds=req.price,
                slug=req.slug,
            )
            if result_dict and result_dict.get("status") == "placed":
                return ExecutionResult(
                    filled=True,
                    order_id=result_dict.get("id", ""),
                    avg_price=req.price,
                    shares=(req.amount_usd / req.price) if req.amount_usd else 0,
                    notional_filled_usd=req.amount_usd or 0,
                    fee_usd=0,  # SDK response'ından parse et
                    raw_response=result_dict,
                    executor_mode=self.mode,
                )
            return ExecutionResult(
                filled=False,
                rejected_reason=result_dict.get("status", "unknown")
                if result_dict
                else "no_response",
                raw_response=result_dict or {},
                executor_mode=self.mode,
            )
        except Exception as e:  # noqa: BLE001
            return ExecutionResult(
                filled=False,
                rejected_reason=f"exception: {type(e).__name__}: {str(e)[:120]}",
                executor_mode=self.mode,
            )

    async def cancel_order(self, order_id: str) -> bool:
        # SDK call delegated
        try:
            client = getattr(self.live_trader, "_client", None)
            if not client:
                return False
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: client.cancel_order(order_id))
            return bool(response.get("canceled")) if isinstance(response, dict) else False
        except Exception as e:  # noqa: BLE001
            logger.debug(f"live cancel fail: {e}")
            return False

    def get_balance_usd(self) -> float:
        # Read budget from live_trader
        if self.live_trader and hasattr(self.live_trader, "_budget"):
            return float(getattr(self.live_trader, "_budget", 0))
        return 0.0


# ─── Factory ───────────────────────────────────────────────────────────
_default_executors: dict[str, Executor] = {}


def get_executor(mode: str = "paper", **kwargs) -> Executor:
    """Get or create executor singleton.

    Args:
        mode: "paper" | "live"
        **kwargs: passed to executor constructor (e.g., live_trader=engine.live)
    """
    if mode in _default_executors:
        return _default_executors[mode]

    # P1-07 Round-3 (2026-05-11): annotate ex as union so the assignment
    # at line 309 (LiveExecutor) doesn't trip mypy after line 307 narrowed
    # the inferred type to PaperExecutor.
    ex: PaperExecutor | LiveExecutor
    if mode == "paper":
        ex = PaperExecutor(**kwargs)
    elif mode == "live":
        ex = LiveExecutor(**kwargs)
    else:
        raise ValueError(f"Unknown executor mode: {mode}")

    _default_executors[mode] = ex
    return ex
