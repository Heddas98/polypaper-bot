# Walk-Forward Backtest + Slippage Model — 2026-05 (P0.6 Closure)

**Tarih:** 2026-04-30
**Sahibi:** Claude (Lead Architect)
**Tetik:** YOL_HARITASI §5.1 P0.6 + Hyperopt silme (2026-04-28) sonrası boşluk

---

## 0 — TL;DR

| Madde | Status |
|---|---|
| `backtest/slippage_model.py` orderbook depth simulator | ✅ DONE (190 satır) |
| `backtest/walk_forward.py` rolling train/test runner | ✅ DONE (260 satır) |
| `core/fees_v2.py` integration (FAZ 0.1 oracle) | ✅ DONE |
| Min order $5 enforcement (Polymarket V2 docs) | ✅ DONE |
| `_compute_metrics` (Sharpe/PF/Expectancy/MaxDD) | ✅ DONE |
| Walk-forward grid search + objective ranking | ✅ DONE |
| FOK rejection logic (slippage cap) | ✅ DONE |
| `evaluate_fn` callback pattern (strategy plug-in) | ✅ DONE |

---

## 1 — Tasarım

### 1.1 `backtest/slippage_model.py`

**Sınıf:** `SlippageModel(orderbook, fee_rate_bps=None)`

**Public API:**
- `simulate_market_buy(notional_usd, max_price=None) -> FillResult`
- `simulate_market_sell(shares, max_price=None) -> FillResult`
- `simulate_market_order(side, notional_usd?, shares?, max_price?) -> FillResult`

**Özellikler:**
- Orderbook ladder traversal (ascending asks for BUY, descending bids for SELL)
- Min order $5 enforcement (Polymarket V2 docs)
- FOK semantics: `max_price` violation → reject (insufficient_liquidity / price_above_max)
- 0.1% tolerance for floating-point fill comparison
- Fee = `core/fees_v2.compute_taker_fee()` (FAZ 0.1 single oracle)
- Slippage_bps reported vs midpoint
- Levels consumed counter (depth probe)

### 1.2 `backtest/walk_forward.py`

**Sınıf:** `WalkForwardRunner(train_days=30, test_days=7, param_grid={}, step_days=None, min_train_trades=30, objective="sharpe")`

**Algorithm:**
```
1. Sort events chronologically
2. Generate windows: (train_start..train_end..test_start..test_end)
3. For each window:
   a. Optimize params on train (grid search on `param_grid`)
   b. Evaluate best params on test (out-of-sample)
   c. Concat test PnLs → out-of-sample equity
4. Aggregate metrics on stitched out-of-sample
```

**Anti-leak guarantees:**
- Test window strictly **after** train window (no overlap)
- Step ≥ test_days (default = test_days, no temporal leak across windows)
- `min_train_trades=30` filter (no fitting on tiny samples)

**Objective options:** `"sharpe"` (default) | `"pf"` | `"expectancy"` | `"total"`

**evaluate_fn signature:**
```python
def my_strategy_eval(events: list[dict], params: dict) -> list[float]:
    """Run strategy on events, return list of trade PnLs."""
    pnls = []
    for ev in events:
        # ... apply strategy params, compute trade PnL ...
        pnls.append(pnl)
    return pnls
```

---

## 2 — Hyperopt Silme Sonrası Boşluk

Memory `hyperopt_asama_1_closure` (2026-04-28): hyperopt 699 occurrence × 31 dosya purge.
- Eski hyperopt naive in-sample optimization yapıyordu
- Walk-forward = out-of-sample → fake edge'i azaltır

**5AI sentezi (Audit + GPT + Grok):**
> "Hyperopt + replay engine = %80 fake edge üretici. Komple sil veya sadece simple replay, no optimization bırak."

→ Hyperopt silindi ✅
→ Walk-forward eklendi ✅ (bu task)
→ `evaluate_fn` plug-in pattern: kullanıcı strateji simulasyonu kontrol eder

---

## 3 — Heddas Yerel Apply

### 3.1 Smoke test (sentetik data)

```python
import random
from datetime import datetime, timezone
from backtest.walk_forward import WalkForwardRunner

# Mock events (90 gün)
events = [{"ts": datetime(2026, 1, i % 28 + 1, tzinfo=timezone.utc).timestamp(), "price": 0.5 + random.gauss(0, 0.1)} for i in range(1, 90)]

def eval_fn(evs, params):
    threshold = params.get("threshold", 0.5)
    return [(ev["price"] - threshold) for ev in evs if ev["price"] > threshold]

runner = WalkForwardRunner(
    train_days=30, test_days=7,
    param_grid={"threshold": [0.4, 0.5, 0.6]}
)
result = runner.run(events, eval_fn)
print(result.aggregate)
```

### 3.2 Production walk-forward (DB üzerinden)

`scripts/run_walk_forward.py` (P1.X yeni script — bu audit'in forward work):
```python
# 1. DB'den 6+ ay event akışı (live_trades + features)
# 2. evaluate_fn → mevcut strategy class'ları üzerinden simulate
# 3. result['aggregate'] → docs/audits/walk_forward_<TS>.md
# 4. Eğer aggregate sharpe < 1.0 → strateji edge yok, SaaS pivot tetikler
```

### 3.3 Slippage model standalone

```python
from backtest.slippage_model import SlippageModel

ob = {
    "asks": [[0.55, 100], [0.56, 200], [0.57, 500]],
    "bids": [[0.54, 80], [0.53, 150]],
}
sim = SlippageModel(ob)
fill = sim.simulate_market_buy(notional_usd=50, max_price=0.60)
print(fill)
# FillResult(filled=True, avg_price=0.55, shares=90.91, fee=0.13, slippage_bps=...)
```

---

## 4 — Memory Landmark

`memory/project_p06_walk_forward_closure.md`:
```
P0.6 Walk-forward backtest + slippage model CLOSED 2026-04-30.
backtest/slippage_model.py 190 satır (orderbook depth + FOK + fee oracle).
backtest/walk_forward.py 260 satır (rolling train/test, grid search, anti-leak).
Hyperopt silme boşluğu kapatıldı. evaluate_fn callback pattern (strategy plug-in).
Heddas yerel: P1.X scripts/run_walk_forward.py (DB-driven production run).
```

---

## 5 — Bağlantılı Belgeler

- `docs/MASTER_PLAN_2026_04_30.md` §5.1 P0.6
- `TASKS.md` Epic 12.A P0.6
- `core/fees_v2.py` FAZ 0.1 oracle integration
- `memory/project_hyperopt_asama_1_closure.md` (silme historic)

**Sonuç:** P0.6 KAPALI (modüller hazır). Sıradaki: **P0.8 Drawdown kill-switch**.
