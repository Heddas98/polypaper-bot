# T4.5 Empirical Slippage Analysis — 2026-04-24

**Source:** `data_store/polypaper.db` (960 settled executions with
`realized_slippage IS NOT NULL`)
**Unit:** Signed percent (`(fill_price - signal_price) / signal_price * 100`)
**Convention:** POSITIVE = adverse (fill worse than signal), NEGATIVE = favorable.
**Formula source:** `core/engine_fills.py::_compute_slippage`

## Overall

| stat | value (%) |
|---|---|
| n | 960 |
| mean | **-3.27** |
| stdev | 8.62 |
| p10 | -11.43 |
| p50 | **0.00** |
| p90 | **+2.30** |
| p99 | +5.26 |

**Interpretation:**
- Median fill = exactly at signal price (p50=0). Thin Polymarket books
  often fill at posted best bid/ask.
- Distribution is ASYMMETRIC: the "favorable" tail (-p10 to mean) runs
  out to -11% while the "adverse" tail (p90 to p99) tops at ~+5%.
- Negative mean means filling at **better-than-signal** price is more
  common than adverse — a combination of thin orderbook + our order
  cycle timing (scanner tick -> decision -> submit).
- **Heuristic `FILL_SPREAD_COST=0.005` in `fill_model.py` is too low**:
  p90 adverse is +2.30% which is 4.6x the heuristic. Empirical
  calibration needed for sim → live parity (T4.6).

## Critical Bug Found — Maker fills 0/960

`is_maker=1` count is **zero** across all 960 settled trades. But
`ADAPTIVE_MAKER_ENABLED=true` is in whitelist and
`engine_signals.py:1604` reads it at runtime. Possible causes (require
investigation):

1. Classic TAKER limit ceiling (`CLASSIC_TAKER_LIMIT_CEIL=0.99`) forces
   all classic strategies to taker path regardless of adaptive flag.
2. `ADAPTIVE_MAKER_ENABLED` check exists but is shadowed by a higher
   priority taker gate.
3. Persisting `is_maker` flag failure — order lands as maker but
   `executions.is_maker` stays at default `0`.

**Action (forward work, T4.5-B):** Grep `ADAPTIVE_MAKER_ENABLED` read
sites; trace if any order set `o.is_maker = True`; verify UPDATE path.

## By Strategy Type

| Type | n | mean (%) | p50 | p10 | p90 | Verdict |
|---|---|---|---|---|---|---|
| **classic** | 156 | **+1.27** | **+1.20** | -1.25 | +3.77 | ✅ **BEST** — positive slippage (taker ama hızlı) |
| highthreshold | 61 | -2.56 | -1.28 | -6.25 | 0.00 | neutral-favorable |
| unknown | 261 | -3.43 | -1.11 | -12.50 | +1.39 | ? (strategy_id not mapped) |
| martingale | 103 | -3.77 | 0.00 | -10.58 | +2.30 | borderline |
| streak | 21 | -3.40 | 0.00 | -8.33 | 0.00 | low sample |
| fusion | 126 | -4.92 | -1.73 | -13.28 | +2.43 | bad tail |
| **contrarian** | 198 | **-5.69** | -1.00 | **-17.63** | 0.00 | 🔴 **WORST bulk** |
| **sniper** | 7 | -5.16 | **-8.70** | -12.46 | +2.93 | low sample, severe median |
| scalper | 15 | -1.38 | 0.00 | -6.63 | +1.61 | neutral (low sample) |
| momentum | 6 | -1.71 | 0.00 | -7.35 | +2.22 | low sample |
| flashcrash | 6 | -2.22 | 0.00 | -6.67 | 0.00 | low sample |

**Key insight:** **Classic is the ONLY strategy type with positive
slippage**. All other types have negative mean (fills better than
signal). This inverts "classic bypass is reckless" narrative: classic's
free-path execution actually picks up +1.27% mean favorable-for-fill
surprise (fills at better-than-expected price since market moves away
between signal and fill, and classic bypasses gates that would delay).

## By Asset

| Asset | n | mean (%) | p10 | p50 | p90 | Verdict |
|---|---|---|---|---|---|---|
| **BTC** | 514 | **-2.21** | -8.53 | 0.00 | +2.49 | ✅ best liquidity |
| XRP | 62 | -2.30 | -7.89 | 0.00 | +3.45 | BTC-like |
| unknown | 261 | -3.43 | -12.50 | -1.11 | +1.39 | strategy map gap |
| **ETH** | 70 | **-6.28** | -18.26 | -3.51 | 0.00 | 🔸 3x worse |
| **SOL** | 53 | **-9.97** | **-23.56** | -3.85 | 0.00 | 🔴 worst liquidity |

**Key insight:** SOL/ETH markets on Polymarket have materially worse
liquidity. SOL p10 -23.56% means 10% of SOL trades filled 23 points
below signal. BTC/XRP are roughly equivalent and acceptable.

## By Direction

| Direction | n | mean (%) | p50 | p90 |
|---|---|---|---|---|
| UP | 480 | -3.89 | 0.00 | +2.44 |
| DOWN | 480 | -2.65 | 0.00 | +2.04 |

**No structural asymmetry** — UP fills slightly more favorable than
DOWN on average, but p50/p90 are nearly identical.

## By Amount Bucket

| Bucket | n | mean (%) |
|---|---|---|
| $0-2 | 957 | -3.28 |
| $2-5 | 3 | +0.79 |
| $5-15 | 0 | — |
| $15+ | 0 | — |

Shadow live budget = $1/trade → all trades land in $0-2 bucket. Amount
scaling vs slippage impossible to assess without larger-trade samples
(would require live or elevated paper budget).

## Strategic Recommendations (for "kesin kazanan strateji" search)

### ✅ FAVORED combinations
1. **Classic + BTC** — 156 classic trades got +1.27% mean favorable
   slippage. Pair with BTC's best-in-class liquidity → likely highest
   edge-over-slippage ratio.
2. **Classic + XRP** — same liquidity profile, classic's positive fills.

### 🔴 AVOID combinations
1. **Any strategy + SOL** — mean -9.97%, p10 -23.56. Slippage eats
   signal edge for lunch. Cleanest win = disable SOL at event filter.
2. **Any strategy + ETH** — mean -6.28%, p10 -18.26. Same story.
3. **Contrarian + any asset** — mean -5.69%, p10 -17.63 (worst bulk
   type). If contrarian setup signals a specific trade class, that
   class needs unusually high edge to overcome slippage.
4. **Sniper strategy** — 7 samples is low, but median -8.70 is
   red-flag. Monitor with more data before committing capital.

### 📋 Forward work (T4.5 follow-ups)

1. **Maker bug (T4.5-B):** investigate why 0/960 is_maker. Could unlock
   meaningful fee-rebate edge if maker path is reachable.
2. **Heuristic update (T4.6):** bump `FILL_SPREAD_COST` from 0.005 to
   empirical p90 ≈ 0.023 in `backtest/simulation/fill_model.py`. Run
   backtest sweep parity check (T4.6 proper): PnL delta between old
   and new heuristic should match observed live vs paper drift.
3. **Asset filter:** consider env-gated `ALLOWED_ASSETS=BTC,XRP` to
   hard-cap strategy universe to acceptable-liquidity assets.
4. **Contrarian review:** is the signal edge worth -5.69% mean
   slippage? Audit PnL per contrarian strategy explicitly.
5. **Regime tagging:** all 960 rows have `regime_at_entry=unknown`.
   The column exists (migrations.py Phase 79+) but is never populated.
   Forward work to wire regime capture at order submission time —
   would enable regime × slippage correlation.

## Artifact

`backtest/calibration/slippage_2026q2.json` — full bucket payload
machine-readable. Run `scripts/calibrate_slippage.py` with `--quiet`
for CI/cron refresh.
