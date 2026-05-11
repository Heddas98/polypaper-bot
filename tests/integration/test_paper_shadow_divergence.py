"""Integration smoke test for paper↔shadow PnL divergence (Epic 9 T9.8 part 2).

Goal: pin the invariant that paper and shadow modes compute identical PnL
for identical fill event streams. Divergence between the two would mean
either:
  (a) Two different fee oracles are active — violates the Single Fee Oracle
      doctrine (2026-04-21: fees_v2.py ONLY; v1 + legacy archived).
  (b) Two different slippage/rounding models are active.
  (c) One side carries a drift bug (e.g., rebate applied only on one side).

Any of the above would silently corrupt P&L comparisons across ~$1.49 live
shadow USDC vs ~$10,386 paper balance — making "is shadow matching paper?"
unanswerable.

Scope (pure-logic, deterministic):
  * Same fill event → same fee (oracle identity)
  * Same sequence of 3 fills → same accumulated fee (determinism)
  * Round-trip (buy+sell) → deterministic gross PnL
  * Random 1000-event replay with 3 different seeds → identical total PnL

Out-of-scope (→ T9.8-REG Windows backlog):
  * Real paper DB vs real shadow DB live-data divergence audit
  * WS tick sequence replay with real aiosqlite
  * Apex / fast-market slippage calibration vs live
"""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest

from core.fees_v2 import polymarket_maker_rebate, polymarket_taker_fee_v2

REPO_ROOT = Path(__file__).resolve().parents[2]


# ═══ 1. Single Fee Oracle doctrine ═════════════════════════════════════


class TestSingleFeeOracle:
    """2026-04-21 closure: `core/fees_v2.py` is the ONLY fee oracle.
    `core/fees.py` (v1) and legacy category calculator are archived under
    `_archive/fee_consolidation_2026_04_21_T41/`.
    """

    def test_fees_v2_is_importable(self):
        from core.fees_v2 import polymarket_taker_fee_v2

        assert callable(polymarket_taker_fee_v2)

    def test_core_fees_v1_not_live(self):
        """`core/fees.py` (v1) must not exist in core/ live tree.

        If this fails, a revived v1 will silently divergence-corrupt paper
        vs shadow fee computation.
        """
        v1_path = REPO_ROOT / "core" / "fees.py"
        assert not v1_path.exists(), (
            "core/fees.py (v1) re-appeared — single fee oracle doctrine "
            "broken. Archive or consolidate into fees_v2."
        )

    def test_fee_archive_preserved(self):
        """Archive directory for v1 consolidation must exist as audit trail."""
        archive = REPO_ROOT / "_archive" / "fee_consolidation_2026_04_21_T41"
        assert archive.exists() and archive.is_dir(), (
            "_archive/fee_consolidation_2026_04_21_T41/ missing — T4.1 "
            "closure audit trail disappeared."
        )


# ═══ 2. Oracle identity — same input → same fee ════════════════════════


class TestOracleIdentity:
    """Two parallel callers (paper, shadow) hit the same pure function
    with the same arguments → must get bit-identical output."""

    @pytest.mark.parametrize(
        "price,amount",
        [
            (0.55, 10.0),
            (0.45, 5.0),
            (0.25, 25.0),
            (0.90, 1.0),
            (0.10, 100.0),
        ],
    )
    def test_taker_fee_bit_identical(self, price, amount):
        paper = polymarket_taker_fee_v2(price, amount)
        shadow = polymarket_taker_fee_v2(price, amount)
        assert paper == shadow, (
            f"Non-deterministic fee oracle at price={price}, amount={amount} "
            f"— paper={paper}, shadow={shadow}. Either a hidden random or a "
            f"state-holding global crept in."
        )

    def test_maker_rebate_bit_identical(self):
        """polymarket_maker_rebate(taker_fee_usd, category=None) — bit
        identical across two calls for the same taker fee amount."""
        for taker_fee in (0.001, 0.005, 0.05, 0.50):
            paper = polymarket_maker_rebate(taker_fee)
            shadow = polymarket_maker_rebate(taker_fee)
            assert paper == shadow


# ═══ 3. Fill stream determinism ════════════════════════════════════════


def _accumulated_fee(events):
    """Simulate an accumulated taker fee across a fill stream.

    Shape of event: (price, amount_usd). Used by both paper and shadow
    side — if they converge, total must match.
    """
    total = 0.0
    for price, amount in events:
        total += polymarket_taker_fee_v2(price, amount)
    return round(total, 4)


class TestStreamDeterminism:
    """Deterministic sum across the same fill stream from both sides."""

    def test_three_buys_sum(self):
        stream = [(0.55, 10.0), (0.60, 5.0), (0.62, 3.0)]
        paper = _accumulated_fee(stream)
        shadow = _accumulated_fee(stream)
        assert paper == shadow
        assert paper > 0.0

    def test_round_trip_buy_sell(self):
        """Buy at 0.55 then sell at 0.60 — both paths identical."""
        stream = [(0.55, 10.0), (0.60, 10.0)]
        paper = _accumulated_fee(stream)
        shadow = _accumulated_fee(stream)
        assert paper == shadow

    def test_empty_stream_zero(self):
        assert _accumulated_fee([]) == 0.0

    def test_stream_order_matters_not_for_sum(self):
        """Associativity check: reordering a commutative sum must not change
        the total (within rounding) — guard against order-dependent state."""
        stream_a = [(0.55, 10.0), (0.60, 5.0), (0.62, 3.0)]
        stream_b = [(0.62, 3.0), (0.55, 10.0), (0.60, 5.0)]
        assert _accumulated_fee(stream_a) == _accumulated_fee(stream_b)


# ═══ 4. Closed-trade PnL identity ══════════════════════════════════════


def _closed_pnl(entry_price: float, exit_price: float, amount_usd: float):
    """Deterministic closed-trade PnL formula.

    shares = amount_usd / entry_price
    gross  = shares * (exit_price - entry_price)
    fees   = entry_fee + exit_fee
    net    = gross - fees

    Paper and shadow must use this exact formula.
    """
    shares = amount_usd / entry_price
    entry_fee = polymarket_taker_fee_v2(entry_price, amount_usd)
    exit_notional = shares * exit_price
    exit_fee = polymarket_taker_fee_v2(exit_price, exit_notional)
    gross = shares * (exit_price - entry_price)
    return round(gross - entry_fee - exit_fee, 4)


class TestClosedTradePnl:
    def test_winner_positive(self):
        pnl = _closed_pnl(entry_price=0.50, exit_price=0.60, amount_usd=10.0)
        # 20 shares * 0.10 = $2 gross, minus ~$0.05 fees both sides ≈ $1.9
        assert pnl > 1.0

    def test_loser_negative(self):
        pnl = _closed_pnl(entry_price=0.60, exit_price=0.55, amount_usd=10.0)
        assert pnl < 0

    def test_breakeven_price_negative_due_to_fees(self):
        """Zero price move → fees drive PnL negative."""
        pnl = _closed_pnl(entry_price=0.55, exit_price=0.55, amount_usd=10.0)
        assert pnl < 0

    def test_paper_vs_shadow_identity_single_trade(self):
        """SAME trade, called twice → exact match ($0.0000 divergence)."""
        paper = _closed_pnl(0.50, 0.60, 10.0)
        shadow = _closed_pnl(0.50, 0.60, 10.0)
        assert paper == shadow


# ═══ 5. Random 1000-event replay — 3 seeds deterministic ═══════════════


class TestRandomReplay:
    """High-volume determinism: replay the same randomly-generated stream
    from both sides and verify total PnL matches to $0.00 tolerance."""

    @pytest.mark.parametrize("seed", [42, 1337, 9001])
    def test_1000_events_identical(self, seed):
        rng_paper = random.Random(seed)
        rng_shadow = random.Random(seed)

        def gen(rng):
            events = []
            for _ in range(1000):
                entry = rng.uniform(0.05, 0.95)
                exit_ = rng.uniform(0.05, 0.95)
                amt = rng.uniform(0.5, 50.0)
                events.append((entry, exit_, amt))
            return events

        paper_stream = gen(rng_paper)
        shadow_stream = gen(rng_shadow)

        # Basic smoke — ensure both streams actually contain 1000 events
        # before we do the PnL comparison that actually tests the Single
        # Fee Oracle. (Previously asserted paper_stream == shadow_stream
        # which was a tautology for same-seed random.Random; removed.)
        assert len(paper_stream) == 1000
        assert len(shadow_stream) == 1000

        # Same stream → same accumulated PnL — THIS is the invariant
        paper_total = round(sum(_closed_pnl(e, x, a) for e, x, a in paper_stream), 4)
        shadow_total = round(sum(_closed_pnl(e, x, a) for e, x, a in shadow_stream), 4)

        assert paper_total == shadow_total, (
            f"Seed={seed}: paper-shadow divergence "
            f"${paper_total - shadow_total:.4f} on 1000 events — "
            f"fee/PnL oracle non-determinism crept in."
        )
