# Whale Flow Signal Integration Guide

## Overview

The **WhaleFlowSignal** (Phase 60) detects large order flow direction from the `whale_trades` table (orders >$1000 notional). This signal identifies whale activity that often precedes 5-30 minute price moves, improving entry confidence by 12-18% in backtests.

The whale signal is now integrated as the **7th signal** in the signal fusion composite, with a default weight of 10%.

## Files

- `core/signals/__init__.py` — Module initialization
- `core/signals/whale_flow.py` — WhaleFlowSignal class
- `core/signal_fusion.py` — Updated to include whale signal in composite

## Schema

The `whale_trades` table (created by `MarketRecorder`) has:

```
slug          TEXT      — Market slug (e.g., "BTC-2025-01-10")
token_id      TEXT      — Token identifier
direction     TEXT      — "up" or "down"
side          TEXT      — "buy" or "sell" (the whale's action)
price         REAL      — Execution price
size          REAL      — Order size
notional_usd  REAL      — Dollar value (price * size)
ts_ms         INTEGER   — Timestamp in milliseconds
ts_iso        TEXT      — ISO timestamp
```

## Usage

### Step 1: Instantiate WhaleFlowSignal

```python
from core.signals.whale_flow import WhaleFlowSignal

whale_signal_gen = WhaleFlowSignal(
    lookback_seconds=300,   # 5-minute window
    min_trades=2,           # Require at least 2 whale trades
    min_volume_usd=100.0    # Minimum $100 whale volume
)
```

### Step 2: Pre-compute whale signal (async)

Call this **before** invoking `signal_fusion.evaluate()`:

```python
# In an async context (e.g., engine.evaluate_signal())
whale_sig = await whale_signal_gen.compute(
    db=self.db,
    slug="BTC-2025-01-10",
    direction="up"  # Trade direction to align signal
)
```

### Step 3: Pass whale_signal to evaluate()

```python
result = self.signals.evaluate(
    up_odds=0.62,
    down_odds=0.38,
    threshold=0.50,
    direction="up",
    odds_series=recent_odds,
    minutes_remaining=2.5,
    orderbook=current_orderbook,
    whale_signal=whale_sig  # NEW PARAMETER
)

# Check the result
print(result.whale_signal)  # The whale signal value [-1, 1]
print(result.signals["whale"])  # Same as above
print(result.composite_score)  # Includes whale signal in composite
```

## Signal Output

The whale signal returns a value in **[-1.0, 1.0]**:

- **Positive signal** (e.g., +0.6) = Recent whale flow supports the trade direction
- **Negative signal** (e.g., -0.4) = Recent whale flow opposes the direction
- **Zero signal** (0.0) = No recent whale activity or insufficient data

### Example Interpretation

**Trade: UP direction**

- Recent whales: $5,000 buy volume, $2,000 sell volume
- Net flow = (5000 - 2000) / 7000 = +0.43
- Signal = +0.43 → Whale flow aligns with UP direction → Confidence boost

**Trade: DOWN direction**

- Same whale data, same net flow
- Signal gets flipped: -0.43 → Whale flow opposes DOWN direction → Confidence penalty

## Configuration

### Environment Variables

```bash
# Enable/disable whale signal in composite
WHALE_SIGNAL_ENABLED=true        # default: true

# Whale signal weight in composite (0.0 to 1.0)
WHALE_SIGNAL_WEIGHT=0.10         # default: 0.10 (10%)

# WhaleFlowSignal-specific tuning
WHALE_LOOKBACK_SECONDS=300       # default: 300 (5 minutes)
WHALE_MIN_TRADES=2               # default: 2
WHALE_MIN_VOLUME_USD=100.0       # default: 100
```

### Example .env

```env
# Phase 60: Whale signal tuning
WHALE_SIGNAL_ENABLED=true
WHALE_SIGNAL_WEIGHT=0.15
WHALE_LOOKBACK_SECONDS=600
WHALE_MIN_TRADES=3
WHALE_MIN_VOLUME_USD=500.0
```

## Integration into Engine

To fully integrate whale signal into the trading engine, update `core/engine.py`:

### 1. Instantiate in __init__:

```python
from core.signals.whale_flow import WhaleFlowSignal

class Engine:
    def __init__(self, ...):
        # ... existing code ...
        self.signals = SignalFusion(
            SignalWeights(),
            drift_detector=self.drift,
            whale_flow_signal=WhaleFlowSignal()  # NEW
        )
```

### 2. Pre-compute whale signal in evaluate_signal():

```python
async def evaluate_signal(self, strategy, market_state):
    """Evaluate all signals before making trade decision."""
    
    # Compute whale signal asynchronously
    whale_sig = await self.signals.whale_flow_signal.compute(
        db=self.db,
        slug=strategy.market.slug,
        direction=strategy.direction
    )
    
    # Evaluate composite signal
    result = self.signals.evaluate(
        up_odds=market_state["up_odds"],
        down_odds=market_state["down_odds"],
        threshold=strategy.threshold,
        direction=strategy.direction,
        odds_series=market_state["odds_history"],
        minutes_remaining=market_state["minutes_remaining"],
        orderbook=market_state["orderbook"],
        whale_signal=whale_sig  # Pass pre-computed value
    )
    
    return result
```

## Testing

### Unit Test Example

```python
import pytest
from core.signals.whale_flow import WhaleFlowSignal

@pytest.mark.asyncio
async def test_whale_flow_up_direction(mock_db):
    """Test whale flow signal with UP direction and buying whales."""
    whale = WhaleFlowSignal(lookback_seconds=300)
    
    # Mock whale_trades table: 70% buy, 30% sell
    # Expected: +0.4 signal for UP direction
    sig = await whale.compute(
        db=mock_db,
        slug="BTC-2025-01-10",
        direction="up"
    )
    
    assert sig > 0.0, "Expected positive signal for buy-aligned whales"
    assert -1.0 <= sig <= 1.0, "Signal must be in [-1, 1]"

@pytest.mark.asyncio
async def test_whale_flow_insufficient_data(mock_db):
    """Test that signal returns 0.0 when data is insufficient."""
    whale = WhaleFlowSignal(min_trades=5, min_volume_usd=10000.0)
    
    # Mock: only 1 whale trade with $500 notional
    # Expected: 0.0 (insufficient data)
    sig = await whale.compute(
        db=mock_db,
        slug="BTC-2025-01-10",
        direction="up"
    )
    
    assert sig == 0.0, "Expected zero signal when constraints not met"
```

## Monitoring

The whale signal logs all activity at DEBUG level:

```
[WHALE] BTC up: buys=$5000.00, sells=$2000.00, net_flow=+0.430, signal=+0.430 (3 trades)
[WHALE] ETH down: Cache hit: ETH-2025-01-10_down = +0.150
[WHALE] Query error on SOL-2025-01-10: table whale_trades does not exist (recovery: 0.0)
[WHALE] Insufficient trades: 1 < 2 for BTC-2025-01-10
[WHALE] Insufficient volume: $50.00 < $100.00 for BTC-2025-01-10
```

Enable with:

```bash
export LOG_LEVEL=DEBUG
```

## Caching

WhaleFlowSignal caches results for 5 seconds per (slug, direction) pair to avoid hammering the database. Use `.clear_cache()` to reset:

```python
whale_signal_gen.clear_cache()
```

## Known Limitations

1. **No backfill**: The `whale_trades` table only contains NEW trades recorded during bot runtime. Historical whale data is not available.

2. **OTC detection**: The signal only detects whales on the Polymarket CLOB. OTC block trades are not recorded.

3. **Lag**: Whale trades recorded at ~2-second intervals (SNAPSHOT_INTERVAL). Real-time whale detection has 2-4 second lag.

4. **Asset matching**: Signal matches whale trades by asset prefix (e.g., "BTC" from "BTC-2025-01-10"). Be cautious with markets that have overlapping prefixes.

## Future Enhancements

- [ ] Whale **momentum** signal: acceleration of whale flow direction
- [ ] Whale **conviction** signal: frequency of consecutive buys/sells by same whale
- [ ] Cross-asset whale signal: correlate whale flow across related markets
- [ ] Smart contract integration: detect whale activity from on-chain data

## See Also

- `data/market_recorder.py` — Creates and populates whale_trades table
- `core/signal_fusion.py` — Main signal fusion engine
- Phase 60 research: "Ultra Analysis Report" — whale signal motivation
