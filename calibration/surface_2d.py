"""
Phase 70: 2D Calibration Surface C(K,τ)
=========================================
Source: A4 (Calibration Surface Analysis — academic paper quality)

Extends the 1D Becker δ(p) (price-only) to a 2D surface:
    C(K,τ) = C_K(K) + C_τ(τ) + C_int(K,τ)

Where:
    K = strike / implied probability (0-1 range, 5% bins)
    τ = time remaining until market close (hours → bins)
    C_K = price-dimension calibration (existing Becker δ(p))
    C_τ = time-dimension calibration (new: how mispricing evolves over time)
    C_int = interaction term (new: price×time cross-effects)

Usage:
    adjusted_odds = raw_odds - C_hat(K, tau)

Antisymmetry check:
    C(K,τ) ≈ -C(1-K,τ) should hold → if violated → manipulation warning

Integration:
    - Builds on BeckerLoader's DuckDB calibration DB
    - Engine calls surface_delta(price, hours_remaining) instead of becker_delta(price)
    - Falls back to 1D δ(p) when time data unavailable

ENV:
    SURFACE_2D_ENABLED=true
    SURFACE_2D_WEIGHT=0.12          # Weight in signal boost (default slightly > 1D)
    SURFACE_2D_CLAMP=0.20           # Max absolute boost
    SURFACE_2D_TIME_BINS=6          # Number of time bins
    SURFACE_2D_ANTISYM_THRESHOLD=0.03  # Max allowed antisymmetry violation
    SURFACE_2D_FALLBACK_1D=true     # Fall back to 1D when no time data
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("polypaper.calibration.surface_2d")

# ── ENV ──
_ENABLED = os.getenv("SURFACE_2D_ENABLED", "true").lower() == "true"
_WEIGHT = float(os.getenv("SURFACE_2D_WEIGHT", "0.12"))
_CLAMP = float(os.getenv("SURFACE_2D_CLAMP", "0.20"))
_N_TIME_BINS = int(os.getenv("SURFACE_2D_TIME_BINS", "6"))
_ANTISYM_THRESHOLD = float(os.getenv("SURFACE_2D_ANTISYM_THRESHOLD", "0.03"))
_FALLBACK_1D = os.getenv("SURFACE_2D_FALLBACK_1D", "true").lower() == "true"

# Time bin edges in hours. Default: [0, 1, 4, 12, 24, 72, inf]
# Bin 0: <1h (about to close), Bin 1: 1-4h, Bin 2: 4-12h,
# Bin 3: 12-24h, Bin 4: 1-3 days, Bin 5: >3 days
DEFAULT_TIME_EDGES = [0, 1, 4, 12, 24, 72, float("inf")]

Curve = Sequence[Tuple[float, float]]  # (bin_low, delta_at_midpoint)


# ═══════════════════════════════════════════════════════════════════
#  DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════


@dataclass
class SurfaceCell:
    """Single cell in the 2D surface grid."""

    price_bin: float  # Lower edge of price bin (0.05, 0.10, ..., 0.90)
    time_bin: int  # Time bin index (0 = near close, N-1 = far)
    delta: float  # actual_wr - implied_p (mispricing)
    n_trades: int = 0  # Number of trades in this cell
    confidence: float = 1.0  # Confidence weight [0,1] based on n_trades


@dataclass
class SurfaceResult:
    """Result of a 2D surface lookup."""

    delta: float = 0.0  # Combined C(K,τ) value
    c_k: float = 0.0  # Price-only component
    c_tau: float = 0.0  # Time-only component
    c_int: float = 0.0  # Interaction term
    boost: float = 0.0  # Clamped signal boost
    confidence: float = 0.0  # Confidence [0,1]
    n_trades: int = 0  # Trades in the cell
    time_bin: int = -1  # Which time bin was used
    antisym_ok: bool = True  # Antisymmetry check passed
    antisym_violation: float = 0.0  # |C(K,τ) + C(1-K,τ)|
    source: str = "2d"  # "2d", "1d_fallback", "disabled"


@dataclass
class CalibrationSurface:
    """
    2D Calibration Surface C(K,τ).

    Grid structure:
        - Price axis: 18 bins from 0.05 to 0.90 (5% width each)
        - Time axis: N_TIME_BINS bins (configurable, default 6)
        - Each cell: (delta, n_trades, confidence)

    The surface is decomposed as:
        C(K,τ) = C_K(K) + C_τ(τ) + C_int(K,τ)

    Where C_K is the price marginal (= existing Becker δ(p)),
    C_τ is the time marginal, and C_int captures interactions.
    """

    # Grid: dict[(price_bin, time_bin)] → SurfaceCell
    cells: dict[tuple[float, int], SurfaceCell] = field(default_factory=dict)

    # Marginals
    price_marginal: dict[float, float] = field(default_factory=dict)  # C_K(K)
    time_marginal: dict[int, float] = field(default_factory=dict)  # C_τ(τ)

    # Metadata
    total_trades: int = 0
    n_populated_cells: int = 0
    time_edges: list[float] = field(default_factory=lambda: list(DEFAULT_TIME_EDGES))
    built: bool = False

    def lookup(
        self,
        price: float,
        hours_remaining: Optional[float] = None,
        fallback_1d_curve: Optional[Curve] = None,
    ) -> SurfaceResult:
        """
        Look up calibration delta for a given price and time remaining.

        Args:
            price: Market price / implied probability (0-1)
            hours_remaining: Hours until market close. None = unknown.
            fallback_1d_curve: Becker 1D curve for fallback.

        Returns:
            SurfaceResult with decomposed components.
        """
        if not _ENABLED:
            return SurfaceResult(source="disabled")

        if not self.built or not self.cells:
            # No 2D surface available → try 1D fallback
            if _FALLBACK_1D and fallback_1d_curve:
                return self._fallback_1d(price, fallback_1d_curve)
            return SurfaceResult(source="no_surface")

        # Price bin: snap to 5% grid
        if price < 0.05 or price > 0.95:
            return SurfaceResult(source="out_of_range")

        price_bin = round(math.floor(price * 20) / 20, 2)
        price_bin = max(0.05, min(0.90, price_bin))

        # Time bin
        time_bin = self._time_bin(hours_remaining)

        if hours_remaining is None and _FALLBACK_1D and fallback_1d_curve:
            # No time info → use price marginal only
            return self._price_only_lookup(price, price_bin, fallback_1d_curve)

        # Full 2D lookup
        cell = self.cells.get((price_bin, time_bin))

        if cell is None or cell.n_trades < 10:
            # Sparse cell → use marginals
            c_k = self.price_marginal.get(price_bin, 0.0)
            c_tau = self.time_marginal.get(time_bin, 0.0)
            c_int = 0.0  # No interaction data
            delta = c_k + c_tau
            confidence = 0.3  # Low confidence for marginal-only
            n_trades = 0
        else:
            # Rich cell → decompose
            c_k = self.price_marginal.get(price_bin, 0.0)
            c_tau = self.time_marginal.get(time_bin, 0.0)
            c_int = cell.delta - c_k - c_tau  # Interaction = residual
            delta = cell.delta
            confidence = cell.confidence
            n_trades = cell.n_trades

        # Antisymmetry check: C(K,τ) ≈ -C(1-K,τ)
        mirror_bin = round(1.0 - price_bin - 0.05, 2)  # Mirror price bin
        mirror_bin = max(0.05, min(0.90, mirror_bin))
        mirror_cell = self.cells.get((mirror_bin, time_bin))
        antisym_ok = True
        antisym_violation = 0.0
        if mirror_cell is not None and mirror_cell.n_trades >= 10:
            # Check: C(K,τ) + C(1-K,τ) should ≈ 0
            antisym_violation = abs(delta + mirror_cell.delta)
            if antisym_violation > _ANTISYM_THRESHOLD:
                antisym_ok = False
                logger.warning(
                    "Antisymmetry violation: C(%.2f,%d)=%.4f + C(%.2f,%d)=%.4f "
                    "= %.4f > threshold %.3f",
                    price_bin,
                    time_bin,
                    delta,
                    mirror_bin,
                    time_bin,
                    mirror_cell.delta,
                    antisym_violation,
                    _ANTISYM_THRESHOLD,
                )
                # Reduce confidence when antisymmetry violated
                confidence *= 0.5

        # Compute boost
        boost = max(min(delta * _WEIGHT * confidence, _CLAMP), -_CLAMP)

        return SurfaceResult(
            delta=round(delta, 6),
            c_k=round(c_k, 6),
            c_tau=round(c_tau, 6),
            c_int=round(c_int, 6),
            boost=round(boost, 6),
            confidence=round(confidence, 3),
            n_trades=n_trades,
            time_bin=time_bin,
            antisym_ok=antisym_ok,
            antisym_violation=round(antisym_violation, 6),
            source="2d",
        )

    def _time_bin(self, hours: Optional[float]) -> int:
        """Map hours_remaining to a time bin index."""
        if hours is None:
            return _N_TIME_BINS // 2  # Mid-range default
        hours = max(0.0, float(hours))
        for i in range(len(self.time_edges) - 1):
            if hours < self.time_edges[i + 1]:
                return i
        return len(self.time_edges) - 2  # Last bin

    # _fallback_1d removed 2026-04-29 (Heddas direktifi: Becker tam silme).
    # core.becker_calibration modülü silindi, fallback path artık geçersiz.

    def _price_only_lookup(self, price: float, price_bin: float, curve: Curve) -> SurfaceResult:
        """Use price marginal when time is unknown."""
        c_k = self.price_marginal.get(price_bin, 0.0)
        if abs(c_k) < 1e-6:
            # Price marginal empty + no Becker fallback → no data
            return SurfaceResult(source="2d_no_data")

        boost = max(min(c_k * _WEIGHT * 0.8, _CLAMP), -_CLAMP)
        return SurfaceResult(
            delta=round(c_k, 6),
            c_k=round(c_k, 6),
            boost=round(boost, 6),
            confidence=0.5,  # No time info
            source="2d_price_only",
        )


# ═══════════════════════════════════════════════════════════════════
#  SURFACE BUILDER — builds from DuckDB/Becker calibration data
# ═══════════════════════════════════════════════════════════════════


class SurfaceBuilder:
    """
    Builds a CalibrationSurface from the Becker calibration DB.

    For Kalshi: Uses kalshi_crypto table which has close_time + created_time
    → can compute hours_remaining = close_time - trade_time.

    For Polymarket: Uses poly_crypto table which has end_date + timestamp
    → can compute hours_remaining = end_date - timestamp.

    The builder:
    1. Queries trades grouped by (price_bin, time_bin)
    2. Computes actual_wr per cell
    3. Computes marginals (price-average and time-average)
    4. Sets confidence based on n_trades per cell
    """

    # Minimum trades per cell for inclusion
    MIN_CELL_TRADES = 10
    # Confidence thresholds
    CONF_HIGH = 100  # n >= 100 → confidence 1.0
    CONF_MED = 30  # n >= 30 → confidence 0.7
    CONF_LOW = 10  # n >= 10 → confidence 0.4

    def __init__(self, calib_db: Optional[Path] = None):
        # 2026-04-29 Aşama 3.C: Becker calibration DB silindi.
        # SurfaceBuilder.build() now no-op'a indirgendi (DB yok → empty surface).
        from pathlib import Path as _P

        self.calib_db = calib_db or _P("data_store/becker_calibration.db")

    def build(self, source: str = "kalshi") -> CalibrationSurface:
        """Build a 2D calibration surface from the Becker calibration DB.

        Args:
            source: "kalshi" or "poly"

        Returns:
            CalibrationSurface ready for lookup()
        """
        surface = CalibrationSurface()

        if not self.calib_db.exists():
            logger.warning("surface_2d: calibration DB not present")
            return surface

        try:
            import duckdb  # type: ignore
        except ImportError:
            logger.warning("surface_2d: DuckDB not installed")
            return surface

        con = duckdb.connect(str(self.calib_db), read_only=True)
        try:
            if source == "kalshi":
                rows = self._query_kalshi(con)
            elif source == "poly":
                rows = self._query_poly(con)
            else:
                logger.warning(f"surface_2d: unknown source {source!r}")
                return surface

            if not rows:
                logger.info(f"surface_2d: no data for source={source}")
                return surface

            # rows: [(price_bin, time_bin, actual_wr, n_trades), ...]
            return self._build_from_rows(rows)

        except Exception as e:
            if "does not exist" in str(e):
                logger.warning(
                    f"surface_2d: table not built yet ({source}). " "Run /becker_build to create."
                )
            else:
                logger.error(f"surface_2d build failed: {e}")
            return surface
        finally:
            con.close()

    def _query_kalshi(self, con) -> list:
        """Query Kalshi trades for 2D surface data."""
        time_case = self._time_case_sql("close_time", "created_time")
        return con.execute(f"""
            WITH timed AS (
                SELECT
                    FLOOR(yes_price / 5.0) * 5 / 100.0 AS price_bin,
                    {time_case} AS time_bin,
                    CASE WHEN market_result = 'yes' THEN 1.0 ELSE 0.0 END AS won
                FROM kalshi_crypto
                WHERE market_result IN ('yes', 'no')
                  AND yes_price BETWEEN 5 AND 95
                  AND close_time IS NOT NULL
                  AND created_time IS NOT NULL
            )
            SELECT
                price_bin,
                time_bin,
                AVG(won) AS actual_wr,
                COUNT(*) AS n
            FROM timed
            GROUP BY price_bin, time_bin
            HAVING n >= {self.MIN_CELL_TRADES}
            ORDER BY price_bin, time_bin
        """).fetchall()

    def _query_poly(self, con) -> list:
        """Query Polymarket trades for 2D surface data."""
        # Polymarket: timestamp is epoch seconds, end_date is a string date
        # We need to parse carefully. poly_crypto has:
        #   timestamp (bigint epoch), end_date (varchar ISO), outcome_prices (json)
        return con.execute(f"""
            WITH priced AS (
                SELECT
                    m.side,
                    t.outcome_prices,
                    t.timestamp AS trade_ts,
                    t.end_date,
                    CAST(t.taker_amount AS DOUBLE) / NULLIF(t.maker_amount, 0) AS token_price
                FROM poly_crypto AS t
                JOIN poly_crypto_markets AS m
                  ON t.maker_asset_id = m.token_id
                WHERE t.maker_amount > 0
                  AND t.taker_amount > 0
                  AND t.outcome_prices IS NOT NULL
                  AND t.end_date IS NOT NULL
                UNION ALL
                SELECT
                    m.side,
                    t.outcome_prices,
                    t.timestamp AS trade_ts,
                    t.end_date,
                    CAST(t.maker_amount AS DOUBLE) / NULLIF(t.taker_amount, 0) AS token_price
                FROM poly_crypto AS t
                JOIN poly_crypto_markets AS m
                  ON t.taker_asset_id = m.token_id
                WHERE t.maker_amount > 0
                  AND t.taker_amount > 0
                  AND t.outcome_prices IS NOT NULL
                  AND t.end_date IS NOT NULL
            ),
            yes_priced AS (
                SELECT
                    CASE WHEN side = 'yes' THEN token_price
                         ELSE 1.0 - token_price END AS yes_price,
                    CAST(json_extract_string(outcome_prices, '$[0]') AS DOUBLE) AS resolved_yes,
                    trade_ts,
                    end_date
                FROM priced
                WHERE token_price BETWEEN 0.0 AND 1.0
            ),
            timed AS (
                SELECT
                    FLOOR(yes_price * 20) / 20 AS price_bin,
                    CASE
                        WHEN (EPOCH(CAST(end_date AS TIMESTAMP)) - trade_ts) / 3600.0 < 1 THEN 0
                        WHEN (EPOCH(CAST(end_date AS TIMESTAMP)) - trade_ts) / 3600.0 < 4 THEN 1
                        WHEN (EPOCH(CAST(end_date AS TIMESTAMP)) - trade_ts) / 3600.0 < 12 THEN 2
                        WHEN (EPOCH(CAST(end_date AS TIMESTAMP)) - trade_ts) / 3600.0 < 24 THEN 3
                        WHEN (EPOCH(CAST(end_date AS TIMESTAMP)) - trade_ts) / 3600.0 < 72 THEN 4
                        ELSE 5
                    END AS time_bin,
                    resolved_yes AS won
                FROM yes_priced
                WHERE yes_price BETWEEN 0.05 AND 0.95
                  AND resolved_yes IS NOT NULL
            )
            SELECT
                price_bin,
                time_bin,
                AVG(won) AS actual_wr,
                COUNT(*) AS n
            FROM timed
            GROUP BY price_bin, time_bin
            HAVING n >= {self.MIN_CELL_TRADES}
            ORDER BY price_bin, time_bin
        """).fetchall()

    def _time_case_sql(self, close_col: str, trade_col: str) -> str:
        """Generate SQL CASE for Kalshi time binning.

        Kalshi stores times as datetime strings. We compute hours diff.
        """
        edges = DEFAULT_TIME_EDGES
        parts = []
        for i in range(len(edges) - 1):
            edges[i]
            high = edges[i + 1]
            if math.isinf(high):
                parts.append(f"ELSE {i}")
            else:
                hours_expr = (
                    f"EXTRACT(EPOCH FROM (CAST({close_col} AS TIMESTAMP) - "
                    f"CAST({trade_col} AS TIMESTAMP))) / 3600.0"
                )
                parts.append(f"WHEN {hours_expr} < {high} THEN {i}")
        return "CASE " + " ".join(parts) + " END"

    def _build_from_rows(self, rows: list[tuple]) -> CalibrationSurface:
        """Build CalibrationSurface from query rows.

        rows: [(price_bin, time_bin, actual_wr, n_trades), ...]
        """
        surface = CalibrationSurface()
        total_trades = 0

        # Build cells
        for price_bin, time_bin, actual_wr, n_trades in rows:
            price_bin = round(float(price_bin), 2)
            time_bin = int(time_bin)
            n_trades = int(n_trades)

            # delta = actual_wr - implied_p (at bin midpoint)
            implied_p = price_bin + 0.025
            delta = float(actual_wr) - implied_p

            # Confidence based on n_trades
            if n_trades >= self.CONF_HIGH:
                conf = 1.0
            elif n_trades >= self.CONF_MED:
                conf = 0.7
            elif n_trades >= self.CONF_LOW:
                conf = 0.4
            else:
                conf = 0.2

            cell = SurfaceCell(
                price_bin=price_bin,
                time_bin=time_bin,
                delta=round(delta, 6),
                n_trades=n_trades,
                confidence=conf,
            )
            surface.cells[(price_bin, time_bin)] = cell
            total_trades += n_trades

        surface.total_trades = total_trades
        surface.n_populated_cells = len(surface.cells)

        # Compute marginals
        self._compute_marginals(surface)

        surface.built = True
        logger.info(
            "surface_2d: built %d cells, %d total trades, " "%d price marginals, %d time marginals",
            surface.n_populated_cells,
            surface.total_trades,
            len(surface.price_marginal),
            len(surface.time_marginal),
        )
        return surface

    @staticmethod
    def _compute_marginals(surface: CalibrationSurface) -> None:
        """Compute price and time marginals from populated cells."""
        # Price marginal: C_K(K) = weighted average delta across all time bins for each price
        price_groups: dict[float, list[tuple[float, int]]] = {}
        time_groups: dict[int, list[tuple[float, int]]] = {}

        for (pb, tb), cell in surface.cells.items():
            price_groups.setdefault(pb, []).append((cell.delta, cell.n_trades))
            time_groups.setdefault(tb, []).append((cell.delta, cell.n_trades))

        for pb, deltas in price_groups.items():
            total_n = sum(n for _, n in deltas)
            if total_n > 0:
                weighted_avg = sum(d * n for d, n in deltas) / total_n
                surface.price_marginal[pb] = round(weighted_avg, 6)

        for tb, deltas in time_groups.items():
            total_n = sum(n for _, n in deltas)
            if total_n > 0:
                weighted_avg = sum(d * n for d, n in deltas) / total_n
                surface.time_marginal[tb] = round(weighted_avg, 6)


# ═══════════════════════════════════════════════════════════════════
#  CONVENIENCE FUNCTIONS for engine integration
# ═══════════════════════════════════════════════════════════════════


def surface_delta(
    surface: Optional[CalibrationSurface],
    price: float,
    hours_remaining: Optional[float] = None,
    fallback_1d_curve: Optional[Curve] = None,
) -> SurfaceResult:
    """
    Main entry point for engine_signals.py integration.

    2026-04-29: Becker fallback removed (fallback_1d_curve param kept for
    backward-compat, ignored). Surface 2D pure path only.
    """
    if surface is None:
        return SurfaceResult(source="no_surface")

    return surface.lookup(price, hours_remaining, fallback_1d_curve)


def surface_boost(result: SurfaceResult) -> float:
    """Extract clamped signal boost from SurfaceResult."""
    return result.boost


def format_surface_telegram(surface: CalibrationSurface) -> str:
    """Format surface summary for Telegram /surface_status command."""
    if not surface.built:
        return "📊 <b>2D Surface:</b> Henüz oluşturulmadı"

    lines = [
        "📊 <b>2D Calibration Surface</b>",
        f"Cells: <b>{surface.n_populated_cells}</b> | " f"Trades: <b>{surface.total_trades:,}</b>",
        "",
        "<b>Price Marginal C_K(K):</b>",
    ]

    # Show price marginals sorted
    for pb in sorted(surface.price_marginal.keys()):
        delta = surface.price_marginal[pb]
        bar = "▓" * max(1, int(abs(delta) * 100))
        sign = "+" if delta >= 0 else ""
        lines.append(f"  {pb:.2f}: {sign}{delta:.4f} {bar}")

    lines.append("")
    lines.append("<b>Time Marginal C_τ(τ):</b>")
    time_labels = ["&lt;1h", "1-4h", "4-12h", "12-24h", "1-3d", "&gt;3d"]
    for tb in sorted(surface.time_marginal.keys()):
        delta = surface.time_marginal[tb]
        label = time_labels[tb] if tb < len(time_labels) else f"bin{tb}"
        sign = "+" if delta >= 0 else ""
        lines.append(f"  {label}: {sign}{delta:.4f}")

    # Antisymmetry summary
    violations = 0
    for (pb, tb), cell in surface.cells.items():
        mirror_bin = round(1.0 - pb - 0.05, 2)
        mirror = surface.cells.get((mirror_bin, tb))
        if mirror and mirror.n_trades >= 10 and cell.n_trades >= 10:
            if abs(cell.delta + mirror.delta) > _ANTISYM_THRESHOLD:
                violations += 1

    lines.append("")
    if violations > 0:
        lines.append(f"⚠️ Antisymmetry violations: {violations}")
    else:
        lines.append("✅ Antisymmetry: OK")

    return "\n".join(lines)
