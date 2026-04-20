# PolyPaper Bot - Edge Discovery Analysis

This directory contains systematic edge discovery and analysis scripts for identifying trading patterns and exploitable edges in the PolyPaper Bot's 2.5M+ orderbook snapshots.

## Files

- **edge_discovery.py** - Main analysis script that runs 6 complementary analyses
- **edge_report.md** - Generated report from the latest analysis run

## Quick Start

```bash
# Run all analyses with default database
python3 analysis/edge_discovery.py

# Specify custom database path
python3 analysis/edge_discovery.py --db /path/to/custom.db

# Save report to custom location
python3 analysis/edge_discovery.py --output /path/to/report.md
```

## Analyses Included

### 1. Zone × Direction Win Rate Matrix
For each price zone (0-20c, 20-35c, 35-50c, 50-65c, 65-80c, 80c+) and direction (UP/DOWN):
- Trade count
- Win rate (%)
- Average PnL per trade
- Total PnL

**Use case**: Identify which price zones have the best win rates for each direction.

### 2. Time-of-Day Performance
Hourly breakdown (UTC) showing:
- Trade count by hour
- Hourly win rate
- Best and worst performing hours

**Use case**: Find if there are specific times of day with exploitable edges.

### 3. Orderbook Imbalance → Next Outcome
Analyzes snapshots with:
- Strong BUY pressure (bid depth > 60%)
- Strong SELL pressure (bid depth < 40%)

Then checks what market resolutions followed these imbalances.

**Use case**: Determine if orderbook imbalance predicts next outcome.

### 4. Binance Momentum → Polymarket Lag
When Binance spot price moves >0.3%:
- Tracks implied probability changes in Polymarket
- Measures response lag

**Use case**: Identify if there's a measurable lag to exploit.

### 5. Spread Analysis
Compares performance when:
- Spread is tight (<2c)
- Spread is medium (2-5c)
- Spread is wide (>5c)

**Use case**: Determine if entering with tight spreads improves win rate.

### 6. Per-Strategy Performance Deep Dive
For each strategy:
- Breakdown by price zone
- Win rate by zone
- Average PnL by zone
- Identifies strategy+zone combos with WR > 55%

**Use case**: Find which strategy/condition combinations have edges >55%.

## Output

The script generates:

1. **Console output** - Real-time progress and formatted tables
2. **Markdown report** (analysis/edge_report.md) - Complete findings with:
   - Summary statistics
   - All analysis tables
   - High-edge combinations (WR > 55%)
   - Markdown-formatted tables for easy sharing

## Database Schema

The script expects these tables in polypaper.db:

### executions
- `id`, `user_id`, `wallet_id`, `strategy_id`
- `event_slug`, `market_token_id`, `direction`
- `trade_amount`, `fee_amount`
- `execution_price`, `odds_threshold`
- `status`, `pnl`, `payout`, `result`
- `created_at`, `closed_at`, `updated_at`

### ob_snapshots
- `id`, `slug`, `asset`, `timeframe`
- `up_token_id`, `down_token_id`
- `up_best_bid`, `up_best_ask`, `up_spread`
- `up_bid_depth_usd`, `up_ask_depth_usd`
- `down_best_bid`, `down_best_ask`, `down_spread`
- `down_bid_depth_usd`, `down_ask_depth_usd`
- `implied_prob_up`, `implied_prob_down`
- `binance_price`, `binance_price_change_pct`
- `ts_ms`, `ts_iso`, `created_at`

## Requirements

- Python 3.10+ (3.11 preferred)
- sqlite3 (built-in)
- No external dependencies

## Implementation Notes

- Uses synchronous sqlite3 (not aiosqlite) since this is a standalone analysis tool
- Handles empty/missing tables gracefully
- All SQL queries have error handling to prevent crashes
- Results cached in memory during run for efficient report generation
- Markdown report generated automatically with proper formatting

## Future Enhancements

Potential additions:
- Statistical significance testing (chi-square, t-tests)
- Confidence intervals on win rates
- Correlation matrix between conditions
- Hidden Markov Model for state transitions
- Bootstrap sampling for robustness testing
- Time-series analysis (autoregression)
- Cross-validation of findings

## Troubleshooting

**"Database not found"**: Ensure polypaper.db exists in project root

**"executions table not found"**: Database is empty or not properly initialized

**"No data available"**: All analyses returned 0 results - check if table has data

**Performance slow**: On 2.5M+ snapshots, analyses may take 1-5 minutes depending on hardware
