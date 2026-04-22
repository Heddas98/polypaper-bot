"""
Phase 75+: EV Tracking & Edge Realization Analysis
===================================================

Expected Value (EV) per trade calculation:
- EV = p × (1/odds - 1) - (1 - p) - fee
  where p = win probability (from model/signal)

Realized Value = actual PnL

Edge Realization Ratio = realized_pnl / expected_ev
  1.0 = model perfect
  0.7-1.0 = acceptable
  < 0.7 = overfitting/model broken
"""
import logging

import aiosqlite

logger = logging.getLogger("polypaper.ev_tracker")


class EVTracker:
    """Track expected vs realized edge per strategy."""

    def __init__(self, db):
        self.db = db

    async def calculate_trade_ev(
        self,
        win_probability: float,
        execution_price: float,
        trade_amount: float,
        total_fee: float,
    ) -> float:
        """
        Calculate expected value of a single trade.

        Args:
            win_probability: Model's P(winning) — from signal/confidence
            execution_price: Entry odds (0.0-1.0)
            trade_amount: Amount bet ($)
            total_fee: Fee paid ($)

        Returns:
            Expected value in dollars
        """
        if execution_price <= 0 or execution_price >= 1:
            return 0.0

        # Payout if win: bet × (1/odds)
        payout_if_win = trade_amount * (1.0 / execution_price)

        # Expected value = P(win) × payout_if_win - P(loss) × bet - fee
        ev = (
            win_probability * payout_if_win
            - (1 - win_probability) * trade_amount
            - total_fee
        )
        return round(ev, 4)

    async def calculate_edge_realization(
        self,
        expected_ev: float,
        realized_pnl: float,
    ) -> float:
        """
        Calculate edge realization ratio.

        Args:
            expected_ev: Expected value (from model)
            realized_pnl: Actual PnL from trade

        Returns:
            Ratio: realized / expected (1.0 = perfect, <0.7 = bad).
            When ``expected_ev <= 0`` the ratio is undefined — we fall back
            to the same sentinel used by ``get_strategy_ev_stats`` (L127-129):
            ``1.0`` if realised PnL is non-negative, else ``0.0``. This keeps
            the single-trade calculation consistent with the aggregate stats.

        Note:
            Currently no in-tree callers; reserved public API (T7.6 audit A3).
        """
        if expected_ev <= 0:
            # Align with aggregate logic: non-negative realised → "good", else "bad".
            return 1.0 if realized_pnl >= 0 else 0.0

        ratio = realized_pnl / expected_ev
        return round(ratio, 3)

    async def get_strategy_ev_stats(self, strategy_id: int) -> dict:
        """
        Get EV statistics for a strategy.

        Returns:
            {
                'trade_count': int,
                'avg_expected_ev': float,
                'avg_realized_pnl': float,
                'edge_realization_avg': float,
                'win_rate': float,
                'edge_quality': str ('excellent', 'good', 'acceptable', 'bad')
            }
        """
        try:
            rows = await self.db.conn.execute_fetchall(
                """
                SELECT
                    COUNT(*) as cnt,
                    COALESCE(AVG(expected_ev), 0) as avg_ev,
                    COALESCE(AVG(pnl), 0) as avg_pnl,
                    COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0) as wins,
                    COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 0) as wr
                FROM executions
                WHERE strategy_id = ? AND result IS NOT NULL
                """,
                (strategy_id,),
            )

            if not rows or rows[0][0] == 0:
                return {
                    'trade_count': 0,
                    'avg_expected_ev': 0.0,
                    'avg_realized_pnl': 0.0,
                    'edge_realization_avg': 0.0,
                    'win_rate': 0.0,
                    'edge_quality': 'insufficient_data'
                }

            row = rows[0]
            cnt, avg_ev, avg_pnl, wins, wr = row

            # Calculate edge realization
            if avg_ev > 0:
                edge_realization = avg_pnl / avg_ev
            else:
                edge_realization = 1.0 if avg_pnl >= 0 else 0.0

            # Classify edge quality
            if edge_realization >= 0.9:
                quality = 'excellent'
            elif edge_realization >= 0.75:
                quality = 'good'
            elif edge_realization >= 0.6:
                quality = 'acceptable'
            else:
                quality = 'bad'

            return {
                'trade_count': int(cnt),
                'avg_expected_ev': round(avg_ev, 3),
                'avg_realized_pnl': round(avg_pnl, 3),
                'edge_realization_avg': round(edge_realization, 3),
                'win_rate': round(wr, 1),
                'edge_quality': quality,
            }
        except (aiosqlite.Error, ValueError, TypeError, ArithmeticError,
                IndexError, AttributeError) as e:
            # T1.4 Faz 3: DB fetch + rows[0] unpack + avg_pnl/avg_ev division
            # + dict build inside one try. Narrow to the realistic failure
            # modes:
            #   - aiosqlite.Error: executions table missing / locked / schema
            #   - ValueError/TypeError: row shape, numeric coercion
            #   - ArithmeticError: ZeroDivisionError if avg_ev -> 0.0 between
            #     guard and division (race-free here but covers future edits)
            #   - IndexError/AttributeError: rows[0] guard skip or db.conn
            logger.error(f"get_strategy_ev_stats failed: {e}")
            return {
                'trade_count': 0,
                'avg_expected_ev': 0.0,
                'avg_realized_pnl': 0.0,
                'edge_realization_avg': 0.0,
                'win_rate': 0.0,
                'edge_quality': 'error'
            }

    async def get_all_strategies_ev_summary(self) -> list:
        """
        Get EV summary for all strategies sorted by edge_realization.

        Returns:
            List of (strategy_label, stats) tuples
        """
        try:
            rows = await self.db.conn.execute_fetchall(
                """
                SELECT
                    s.label,
                    COUNT(e.id) as cnt,
                    COALESCE(AVG(e.expected_ev), 0) as avg_ev,
                    COALESCE(AVG(e.pnl), 0) as avg_pnl,
                    COALESCE(SUM(CASE WHEN e.pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(e.id), 0) as wr
                FROM strategies s
                LEFT JOIN executions e ON e.strategy_id = s.id AND e.result IS NOT NULL
                WHERE s.status = 'active'
                GROUP BY s.id, s.label
                ORDER BY avg_pnl DESC
                """
            )

            summary = []
            for row in rows:
                label, cnt, avg_ev, avg_pnl, wr = row
                if cnt == 0:
                    continue

                if avg_ev > 0:
                    edge_real = avg_pnl / avg_ev
                else:
                    edge_real = 1.0 if avg_pnl >= 0 else 0.0

                summary.append((
                    label,
                    {
                        'trades': int(cnt),
                        'avg_ev': round(avg_ev, 3),
                        'avg_pnl': round(avg_pnl, 3),
                        'wr': round(wr, 1),
                        'edge_real': round(edge_real, 3),
                    }
                ))

            return summary
        except (aiosqlite.Error, ValueError, TypeError, ArithmeticError,
                IndexError, AttributeError) as e:
            # T1.4 Faz 3: Same failure surface as get_strategy_ev_stats —
            # DB fetch + per-row unpack + avg_pnl/avg_ev division + list
            # append. Empty result is handled by the loop (not raising),
            # but a malformed row or DB-level failure surfaces here.
            logger.error(f"get_all_strategies_ev_summary failed: {e}")
            return []
