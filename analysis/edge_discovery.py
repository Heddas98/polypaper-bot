"""
PolyPaper Bot - Edge Discovery Analysis Script
==============================================
Systematic analysis of 2.5M orderbook snapshots to identify trading edges.

Analyzes:
1. Zone × Direction Win Rate Matrix (6 zones × 2 directions)
2. Time-of-Day Performance (hourly breakdown)
3. Orderbook Imbalance → Next Outcome
4. Binance Momentum → Polymarket Lag
5. Spread Analysis (tight vs wide)
6. Per-Strategy Performance Deep Dive

Usage: py -3.11 analysis/edge_discovery.py [--db polypaper.db]
"""

import argparse
import sqlite3
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Optional


class EdgeDiscovery:
    """Edge discovery analyzer for PolyPaper Bot."""

    def __init__(self, db_path: str = "polypaper.db"):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self.results = {}

    def connect(self) -> bool:
        """Connect to database."""
        try:
            if not Path(self.db_path).exists():
                print(f"ERROR: Database not found at {self.db_path}")
                return False
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            print(f"Connected to {self.db_path}")
            return True
        except Exception as e:
            print(f"ERROR: Could not connect to database: {e}")
            return False

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()

    def check_tables_exist(self) -> bool:
        """Check if required tables exist."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('executions', 'ob_snapshots')"
        )
        tables = cursor.fetchall()
        existing = {row[0] for row in tables}

        if "executions" not in existing:
            print("WARNING: 'executions' table not found")
        if "ob_snapshots" not in existing:
            print("WARNING: 'ob_snapshots' table not found")

        return len(existing) > 0

    def get_trade_count(self) -> int:
        """Get total number of trades in executions table."""
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT COUNT(*) FROM executions WHERE result IS NOT NULL")
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            print(f"ERROR getting trade count: {e}")
            return 0

    def get_snapshot_count(self) -> int:
        """Get total number of orderbook snapshots."""
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT COUNT(*) FROM ob_snapshots")
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            print(f"ERROR getting snapshot count: {e}")
            return 0

    # ════════════════════════════════════════════════════════════════
    # ANALYSIS 1: Zone × Direction → Win Rate Matrix
    # ════════════════════════════════════════════════════════════════

    def analyze_zone_direction_matrix(self) -> Dict:
        """
        For each price zone and direction, calculate:
        - Trade count
        - Win rate
        - Avg PnL
        - Total PnL
        """
        print("\n[ANALYSIS 1] Zone × Direction Win Rate Matrix...")
        cursor = self.conn.cursor()

        zones = [
            (0, 20, "0-20c"),
            (20, 35, "20-35c"),
            (35, 50, "35-50c"),
            (50, 65, "50-65c"),
            (65, 80, "65-80c"),
            (80, 100, "80c+"),
        ]

        results = []

        for low, high, label in zones:
            for direction in ["up", "down"]:
                # Determine which column to use for price (depends on direction)
                # For UP bets, use up_odds; for DOWN, use down_odds
                if direction == "up":
                    price_col = "execution_price"
                    dir_clause = "direction='up'"
                else:
                    price_col = "execution_price"
                    dir_clause = "direction='down'"

                query = f"""
                    SELECT
                        COUNT(*) as trade_count,
                        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                        SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
                        AVG(pnl) as avg_pnl,
                        SUM(pnl) as total_pnl
                    FROM executions
                    WHERE result IS NOT NULL
                        AND {dir_clause}
                        AND {price_col} IS NOT NULL
                        AND {price_col} >= ? AND {price_col} < ?
                """

                try:
                    cursor.execute(query, (low / 100.0, high / 100.0))
                    row = cursor.fetchone()

                    if row and row[0] and row[0] > 0:
                        trade_count = row[0]
                        wins = row[1] or 0
                        losses = row[2] or 0
                        avg_pnl = row[3] or 0.0
                        total_pnl = row[4] or 0.0
                        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0

                        results.append({
                            "zone": label,
                            "direction": direction.upper(),
                            "trades": trade_count,
                            "wins": wins,
                            "losses": losses,
                            "win_rate": f"{win_rate:.1f}%",
                            "avg_pnl": f"{avg_pnl:.4f}",
                            "total_pnl": f"{total_pnl:.2f}",
                        })
                except Exception as e:
                    print(f"ERROR in zone analysis ({label}, {direction}): {e}")

        self.results["zone_direction_matrix"] = results
        return results

    # ════════════════════════════════════════════════════════════════
    # ANALYSIS 2: Time-of-Day Performance
    # ════════════════════════════════════════════════════════════════

    def analyze_time_of_day(self) -> Dict:
        """
        Group trades by hour of day (UTC) and calculate WR per hour.
        """
        print("\n[ANALYSIS 2] Time-of-Day Performance...")
        cursor = self.conn.cursor()

        query = """
            SELECT
                CAST(strftime('%H', created_at) AS INTEGER) as hour_utc,
                COUNT(*) as trade_count,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
                AVG(pnl) as avg_pnl,
                SUM(pnl) as total_pnl
            FROM executions
            WHERE result IS NOT NULL
            GROUP BY CAST(strftime('%H', created_at) AS INTEGER)
            ORDER BY hour_utc
        """

        results = []
        best_hour = None
        worst_hour = None
        best_wr = -1
        worst_wr = 101

        try:
            cursor.execute(query)
            rows = cursor.fetchall()

            for row in rows:
                hour = row[0]
                trade_count = row[1]
                wins = row[2] or 0
                losses = row[3] or 0
                avg_pnl = row[4] or 0.0
                total_pnl = row[5] or 0.0

                if trade_count > 0 and (wins + losses) > 0:
                    win_rate = wins / (wins + losses) * 100
                    results.append({
                        "hour_utc": f"{hour:02d}:00",
                        "trades": trade_count,
                        "wins": wins,
                        "losses": losses,
                        "win_rate": f"{win_rate:.1f}%",
                        "avg_pnl": f"{avg_pnl:.4f}",
                        "total_pnl": f"{total_pnl:.2f}",
                    })

                    if win_rate > best_wr:
                        best_wr = win_rate
                        best_hour = hour
                    if win_rate < worst_wr:
                        worst_wr = win_rate
                        worst_hour = hour

        except Exception as e:
            print(f"ERROR in time-of-day analysis: {e}")

        self.results["time_of_day"] = {
            "hourly": results,
            "best_hour": f"{best_hour:02d}:00 ({best_wr:.1f}%)" if best_hour is not None else "N/A",
            "worst_hour": f"{worst_hour:02d}:00 ({worst_wr:.1f}%)" if worst_hour is not None else "N/A",
        }
        return self.results["time_of_day"]

    # ════════════════════════════════════════════════════════════════
    # ANALYSIS 3: Orderbook Imbalance → Next Outcome
    # ════════════════════════════════════════════════════════════════

    def analyze_orderbook_imbalance(self) -> Dict:
        """
        For snapshots with strong buy pressure (bid_depth / total > 0.6)
        and strong sell pressure (< 0.4), analyze outcomes.
        """
        print("\n[ANALYSIS 3] Orderbook Imbalance Analysis...")
        cursor = self.conn.cursor()

        results = {}

        # Strong buy pressure (imbalance > 0.6)
        query_buy = """
            SELECT
                CASE
                    WHEN (implied_prob_up > implied_prob_down) THEN 'UP resolved'
                    WHEN (implied_prob_up < implied_prob_down) THEN 'DOWN resolved'
                    ELSE 'Mixed'
                END as outcome,
                COUNT(*) as count
            FROM ob_snapshots
            WHERE up_bid_depth_usd IS NOT NULL
                AND up_ask_depth_usd IS NOT NULL
                AND (up_bid_depth_usd + up_ask_depth_usd) > 0
                AND (up_bid_depth_usd / (up_bid_depth_usd + up_ask_depth_usd)) > 0.6
            GROUP BY outcome
        """

        # Strong sell pressure (imbalance < 0.4)
        query_sell = """
            SELECT
                CASE
                    WHEN (implied_prob_up > implied_prob_down) THEN 'UP resolved'
                    WHEN (implied_prob_up < implied_prob_down) THEN 'DOWN resolved'
                    ELSE 'Mixed'
                END as outcome,
                COUNT(*) as count
            FROM ob_snapshots
            WHERE up_bid_depth_usd IS NOT NULL
                AND up_ask_depth_usd IS NOT NULL
                AND (up_bid_depth_usd + up_ask_depth_usd) > 0
                AND (up_bid_depth_usd / (up_bid_depth_usd + up_ask_depth_usd)) < 0.4
            GROUP BY outcome
        """

        try:
            cursor.execute(query_buy)
            buy_results = cursor.fetchall()
            buy_summary = [{"pressure": "Strong BUY (>60%)", "outcome": row[0], "count": row[1]}
                          for row in buy_results]
            results["strong_buy_pressure"] = buy_summary

            cursor.execute(query_sell)
            sell_results = cursor.fetchall()
            sell_summary = [{"pressure": "Strong SELL (<40%)", "outcome": row[0], "count": row[1]}
                           for row in sell_results]
            results["strong_sell_pressure"] = sell_summary

        except Exception as e:
            print(f"ERROR in orderbook imbalance analysis: {e}")

        self.results["orderbook_imbalance"] = results
        return results

    # ════════════════════════════════════════════════════════════════
    # ANALYSIS 4: Binance Momentum → Polymarket Lag
    # ════════════════════════════════════════════════════════════════

    def analyze_binance_momentum_lag(self) -> Dict:
        """
        When Binance price change > 0.3% in a snapshot,
        analyze what happened in Polymarket price in the next period.
        """
        print("\n[ANALYSIS 4] Binance Momentum → Polymarket Lag...")
        cursor = self.conn.cursor()

        query = """
            SELECT
                CASE
                    WHEN binance_price_change_pct > 0.3 THEN 'Strong UP move'
                    WHEN binance_price_change_pct < -0.3 THEN 'Strong DOWN move'
                    ELSE 'Neutral'
                END as momentum,
                COUNT(*) as snapshot_count,
                AVG(ABS(binance_price_change_pct)) as avg_abs_change,
                AVG(implied_prob_up) as avg_up_prob,
                AVG(implied_prob_down) as avg_down_prob
            FROM ob_snapshots
            WHERE binance_price_change_pct IS NOT NULL
                AND ABS(binance_price_change_pct) > 0.1
            GROUP BY momentum
        """

        results = {}

        try:
            cursor.execute(query)
            rows = cursor.fetchall()

            for row in rows:
                momentum = row[0]
                count = row[1]
                avg_change = row[2] or 0.0
                avg_up = row[3] or 0.0
                avg_down = row[4] or 0.0

                results[momentum] = {
                    "snapshots": count,
                    "avg_abs_change_pct": f"{avg_change:.3f}%",
                    "avg_up_prob": f"{avg_up:.3f}",
                    "avg_down_prob": f"{avg_down:.3f}",
                }

        except Exception as e:
            print(f"ERROR in Binance momentum analysis: {e}")

        self.results["binance_momentum"] = results
        return results

    # ════════════════════════════════════════════════════════════════
    # ANALYSIS 5: Spread Analysis
    # ════════════════════════════════════════════════════════════════

    def analyze_spread(self) -> Dict:
        """
        Compare win rate for entries during tight spread (<2c) vs wide (>5c).
        """
        print("\n[ANALYSIS 5] Spread Analysis...")
        cursor = self.conn.cursor()

        results = []

        spread_buckets = [
            (0, 0.02, "Tight (<2c)"),
            (0.02, 0.05, "Medium (2-5c)"),
            (0.05, 1.0, "Wide (>5c)"),
        ]

        for low, high, label in spread_buckets:
            query = """
                SELECT
                    COUNT(*) as trade_count,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
                    AVG(pnl) as avg_pnl,
                    SUM(pnl) as total_pnl
                FROM executions e
                LEFT JOIN ob_snapshots s ON e.created_at = s.created_at
                WHERE e.result IS NOT NULL
                    AND (s.up_spread >= ? AND s.up_spread < ?)
            """

            try:
                cursor.execute(query, (low, high))
                row = cursor.fetchone()

                if row and row[0] and row[0] > 0:
                    trade_count = row[0]
                    wins = row[1] or 0
                    losses = row[2] or 0
                    avg_pnl = row[3] or 0.0
                    total_pnl = row[4] or 0.0
                    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0.0

                    results.append({
                        "spread_range": label,
                        "trades": trade_count,
                        "wins": wins,
                        "losses": losses,
                        "win_rate": f"{win_rate:.1f}%",
                        "avg_pnl": f"{avg_pnl:.4f}",
                        "total_pnl": f"{total_pnl:.2f}",
                    })

            except Exception as e:
                print(f"ERROR in spread analysis ({label}): {e}")

        self.results["spread_analysis"] = results
        return results

    # ════════════════════════════════════════════════════════════════
    # ANALYSIS 6: Strategy Performance Deep Dive
    # ════════════════════════════════════════════════════════════════

    def analyze_strategy_performance(self) -> Dict:
        """
        Per-strategy breakdown by zone, hour, and direction.
        Find strategy + condition combos with WR > 55%.
        """
        print("\n[ANALYSIS 6] Per-Strategy Performance Deep Dive...")
        cursor = self.conn.cursor()

        # First, get list of strategies
        query_strats = """
            SELECT DISTINCT s.id, s.label, s.strategy_type
            FROM strategies s
            LEFT JOIN executions e ON e.strategy_id = s.id
            WHERE e.id IS NOT NULL
            ORDER BY s.label
        """

        results = {}
        high_wr_combos = []

        try:
            cursor.execute(query_strats)
            strategies = cursor.fetchall()

            for strat_row in strategies:
                strat_id, strat_label, strat_type = strat_row[0], strat_row[1], strat_row[2]
                label = strat_label or f"{strat_type}"

                # Performance by zone
                query_by_zone = """
                    SELECT
                        CASE
                            WHEN execution_price < 0.20 THEN '0-20c'
                            WHEN execution_price < 0.35 THEN '20-35c'
                            WHEN execution_price < 0.50 THEN '35-50c'
                            WHEN execution_price < 0.65 THEN '50-65c'
                            WHEN execution_price < 0.80 THEN '65-80c'
                            ELSE '80c+'
                        END as zone,
                        COUNT(*) as trades,
                        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                        AVG(pnl) as avg_pnl
                    FROM executions
                    WHERE result IS NOT NULL
                        AND strategy_id = ?
                        AND execution_price IS NOT NULL
                    GROUP BY zone
                """

                zone_perf = []
                try:
                    cursor.execute(query_by_zone, (strat_id,))
                    for zone_row in cursor.fetchall():
                        zone = zone_row[0]
                        trades = zone_row[1]
                        wins = zone_row[2] or 0
                        avg_pnl = zone_row[3] or 0.0
                        wr = (wins / trades * 100) if trades > 0 else 0.0

                        zone_perf.append({
                            "zone": zone,
                            "trades": trades,
                            "wr": f"{wr:.1f}%",
                            "avg_pnl": f"{avg_pnl:.4f}",
                        })

                        if wr > 55 and trades >= 5:
                            high_wr_combos.append({
                                "strategy": label,
                                "condition": f"zone_{zone}",
                                "wr": f"{wr:.1f}%",
                                "trades": trades,
                            })

                except Exception as e:
                    print(f"ERROR getting zone perf for {label}: {e}")

                results[label] = {"by_zone": zone_perf}

        except Exception as e:
            print(f"ERROR in strategy analysis: {e}")

        self.results["strategy_performance"] = results
        self.results["high_wr_combos"] = high_wr_combos
        return results

    # ════════════════════════════════════════════════════════════════
    # REPORT GENERATION
    # ════════════════════════════════════════════════════════════════

    def print_results(self):
        """Pretty-print all results."""
        print("\n" + "=" * 80)
        print("EDGE DISCOVERY ANALYSIS REPORT")
        print("=" * 80)

        # Summary
        trade_count = self.get_trade_count()
        snapshot_count = self.get_snapshot_count()
        print(f"\nData Summary:")
        print(f"  Total Completed Trades: {trade_count:,}")
        print(f"  Total Orderbook Snapshots: {snapshot_count:,}")

        # Analysis 1: Zone × Direction Matrix
        if "zone_direction_matrix" in self.results:
            print("\n" + "-" * 80)
            print("ANALYSIS 1: Zone × Direction Win Rate Matrix")
            print("-" * 80)
            rows = self.results["zone_direction_matrix"]
            if rows:
                headers = ["Zone", "Direction", "Trades", "Wins", "Losses", "WR", "Avg PnL", "Total PnL"]
                self._print_table(headers, rows)
            else:
                print("No data available")

        # Analysis 2: Time-of-Day
        if "time_of_day" in self.results:
            print("\n" + "-" * 80)
            print("ANALYSIS 2: Time-of-Day Performance (UTC)")
            print("-" * 80)
            tof = self.results["time_of_day"]
            if tof.get("hourly"):
                headers = ["Hour", "Trades", "Wins", "Losses", "WR", "Avg PnL", "Total PnL"]
                self._print_table(headers, tof["hourly"])
            print(f"Best Hour:  {tof.get('best_hour', 'N/A')}")
            print(f"Worst Hour: {tof.get('worst_hour', 'N/A')}")

        # Analysis 3: Orderbook Imbalance
        if "orderbook_imbalance" in self.results:
            print("\n" + "-" * 80)
            print("ANALYSIS 3: Orderbook Imbalance → Outcome")
            print("-" * 80)
            imbalance = self.results["orderbook_imbalance"]
            if imbalance.get("strong_buy_pressure"):
                print("\nStrong BUY Pressure (>60% bid depth):")
                for item in imbalance["strong_buy_pressure"]:
                    print(f"  {item['outcome']}: {item['count']} snapshots")
            if imbalance.get("strong_sell_pressure"):
                print("\nStrong SELL Pressure (<40% bid depth):")
                for item in imbalance["strong_sell_pressure"]:
                    print(f"  {item['outcome']}: {item['count']} snapshots")

        # Analysis 4: Binance Momentum
        if "binance_momentum" in self.results:
            print("\n" + "-" * 80)
            print("ANALYSIS 4: Binance Momentum → Polymarket Response")
            print("-" * 80)
            for momentum, stats in self.results["binance_momentum"].items():
                print(f"\n{momentum}:")
                for key, val in stats.items():
                    print(f"  {key}: {val}")

        # Analysis 5: Spread
        if "spread_analysis" in self.results:
            print("\n" + "-" * 80)
            print("ANALYSIS 5: Spread Analysis")
            print("-" * 80)
            rows = self.results["spread_analysis"]
            if rows:
                headers = ["Spread Range", "Trades", "Wins", "Losses", "WR", "Avg PnL", "Total PnL"]
                self._print_table(headers, rows)
            else:
                print("No data available")

        # Analysis 6: Strategy Performance
        if "strategy_performance" in self.results:
            print("\n" + "-" * 80)
            print("ANALYSIS 6: Per-Strategy Performance")
            print("-" * 80)
            for strat_label, perf in self.results["strategy_performance"].items():
                print(f"\n{strat_label}:")
                if perf.get("by_zone"):
                    for zone_data in perf["by_zone"]:
                        print(f"  {zone_data['zone']}: {zone_data['trades']} trades, "
                              f"WR={zone_data['wr']}, avg_pnl={zone_data['avg_pnl']}")

        # High WR Combos
        if "high_wr_combos" in self.results and self.results["high_wr_combos"]:
            print("\n" + "-" * 80)
            print("HIGH-EDGE COMBINATIONS (WR > 55%)")
            print("-" * 80)
            combos = self.results["high_wr_combos"]
            headers = ["Strategy", "Condition", "WR", "Trades"]
            self._print_table(headers, combos)

    def _print_table(self, headers: List[str], rows: List[Dict]):
        """Print a formatted table."""
        if not rows:
            print("  (no data)")
            return

        # Calculate column widths
        widths = {h: len(h) for h in headers}
        for row in rows:
            for h in headers:
                key = h.lower().replace(" ", "_")
                val = str(row.get(key) or row.get(h.lower()) or "")
                widths[h] = max(widths[h], len(val))

        # Print header
        header_line = " | ".join(h.ljust(widths[h]) for h in headers)
        print(f"  {header_line}")
        print(f"  {'-' * len(header_line)}")

        # Print rows
        for row in rows:
            values = []
            for h in headers:
                key = h.lower().replace(" ", "_")
                val = str(row.get(key) or row.get(h.lower()) or "")
                values.append(val.ljust(widths[h]))
            print(f"  {' | '.join(values)}")

    def save_markdown_report(self, output_path: str = "analysis/edge_report.md"):
        """Save results as markdown report."""
        Path("analysis").mkdir(exist_ok=True)

        with open(output_path, "w") as f:
            f.write("# PolyPaper Bot - Edge Discovery Analysis Report\n\n")
            f.write(f"Generated: {datetime.now(timezone.utc).isoformat()}\n\n")

            # Summary
            trade_count = self.get_trade_count()
            snapshot_count = self.get_snapshot_count()
            f.write("## Data Summary\n\n")
            f.write(f"- **Total Completed Trades**: {trade_count:,}\n")
            f.write(f"- **Total Orderbook Snapshots**: {snapshot_count:,}\n")
            f.write(f"- **Current Win Rate**: {self._get_overall_wr():.1f}%\n\n")

            # Analysis 1
            if "zone_direction_matrix" in self.results:
                f.write("## Analysis 1: Zone × Direction Win Rate Matrix\n\n")
                f.write(self._dict_list_to_markdown_table(self.results["zone_direction_matrix"]))

            # Analysis 2
            if "time_of_day" in self.results:
                f.write("## Analysis 2: Time-of-Day Performance (UTC)\n\n")
                tof = self.results["time_of_day"]
                f.write(f"**Best Hour**: {tof.get('best_hour', 'N/A')}\n\n")
                f.write(f"**Worst Hour**: {tof.get('worst_hour', 'N/A')}\n\n")
                f.write(self._dict_list_to_markdown_table(tof.get("hourly", [])))

            # Analysis 3
            if "orderbook_imbalance" in self.results:
                f.write("## Analysis 3: Orderbook Imbalance → Outcome\n\n")
                imbalance = self.results["orderbook_imbalance"]
                if imbalance.get("strong_buy_pressure"):
                    f.write("### Strong BUY Pressure (>60% bid depth)\n\n")
                    for item in imbalance["strong_buy_pressure"]:
                        f.write(f"- **{item['outcome']}**: {item['count']} snapshots\n")
                    f.write("\n")
                if imbalance.get("strong_sell_pressure"):
                    f.write("### Strong SELL Pressure (<40% bid depth)\n\n")
                    for item in imbalance["strong_sell_pressure"]:
                        f.write(f"- **{item['outcome']}**: {item['count']} snapshots\n")
                    f.write("\n")

            # Analysis 4
            if "binance_momentum" in self.results:
                f.write("## Analysis 4: Binance Momentum → Polymarket Response\n\n")
                for momentum, stats in self.results["binance_momentum"].items():
                    f.write(f"### {momentum}\n\n")
                    for key, val in stats.items():
                        f.write(f"- **{key}**: {val}\n")
                    f.write("\n")

            # Analysis 5
            if "spread_analysis" in self.results:
                f.write("## Analysis 5: Spread Analysis\n\n")
                f.write(self._dict_list_to_markdown_table(self.results["spread_analysis"]))

            # Analysis 6
            if "strategy_performance" in self.results:
                f.write("## Analysis 6: Per-Strategy Performance\n\n")
                for strat_label, perf in self.results["strategy_performance"].items():
                    f.write(f"### {strat_label}\n\n")
                    if perf.get("by_zone"):
                        f.write("| Zone | Trades | WR | Avg PnL |\n")
                        f.write("|------|--------|----|---------|\n")
                        for zone_data in perf["by_zone"]:
                            f.write(f"| {zone_data['zone']} | {zone_data['trades']} | {zone_data['wr']} | {zone_data['avg_pnl']} |\n")
                        f.write("\n")

            # High WR Combos
            if "high_wr_combos" in self.results and self.results["high_wr_combos"]:
                f.write("## High-Edge Combinations (WR > 55%)\n\n")
                f.write(self._dict_list_to_markdown_table(self.results["high_wr_combos"]))

            f.write("\n---\n\n")
            f.write("*Report generated by PolyPaper Bot Edge Discovery Analysis*\n")

        print(f"\nReport saved to {output_path}")

    def _get_overall_wr(self) -> float:
        """Get overall win rate from all trades."""
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                SELECT
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses
                FROM executions
                WHERE result IS NOT NULL
            """)
            row = cursor.fetchone()
            if row:
                wins = row[0] or 0
                losses = row[1] or 0
                total = wins + losses
                return (wins / total * 100) if total > 0 else 0.0
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _dict_list_to_markdown_table(rows: List[Dict]) -> str:
        """Convert list of dicts to markdown table."""
        if not rows:
            return "(no data)\n\n"

        headers = list(rows[0].keys())
        table = "| " + " | ".join(headers) + " |\n"
        table += "|" + "|".join(["-" * (len(h) + 2) for h in headers]) + "|\n"

        for row in rows:
            values = [str(row.get(h, "")) for h in headers]
            table += "| " + " | ".join(values) + " |\n"

        return table + "\n"

    def run_all_analyses(self):
        """Run all analyses."""
        if not self.connect():
            return False

        if not self.check_tables_exist():
            print("WARNING: Some tables missing. Analysis may be incomplete.")

        self.analyze_zone_direction_matrix()
        self.analyze_time_of_day()
        self.analyze_orderbook_imbalance()
        self.analyze_binance_momentum_lag()
        self.analyze_spread()
        self.analyze_strategy_performance()

        return True


def main():
    parser = argparse.ArgumentParser(
        description="PolyPaper Bot Edge Discovery Analysis"
    )
    parser.add_argument(
        "--db",
        default="polypaper.db",
        help="Path to polypaper.db (default: polypaper.db)",
    )
    parser.add_argument(
        "--output",
        default="analysis/edge_report.md",
        help="Output markdown report path",
    )

    args = parser.parse_args()

    analyzer = EdgeDiscovery(args.db)

    if not analyzer.run_all_analyses():
        sys.exit(1)

    analyzer.print_results()
    analyzer.save_markdown_report(args.output)
    analyzer.close()

    print("\nAnalysis complete!")


if __name__ == "__main__":
    main()
