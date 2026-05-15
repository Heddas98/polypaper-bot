<div align="center">

# 🎯 PolyPaper Bot

### Autonomous Polymarket trading bot with paper + live modes, AI-assisted strategy generation, and Telegram-first UX

[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![Polymarket V2](https://img.shields.io/badge/Polymarket-V2%20SDK-success.svg)](https://docs.polymarket.com)
[![Tests](https://img.shields.io/badge/tests-3,569%20passing-brightgreen.svg)]()
[![Coverage](https://img.shields.io/badge/coverage-44%25-yellowgreen.svg)]()
[![Mypy](https://img.shields.io/badge/mypy-strict%20clean-blue.svg)]()
[![Mode](https://img.shields.io/badge/mode-paper%20%2B%20live-orange.svg)]()
[![Status](https://img.shields.io/badge/status-mainnet%20active-brightgreen.svg)]()

**Live trading on Polymarket since 2026-05-09** · **Built solo, in production since day one**

[Features](#-features) · [Quick Start](#-quick-start) · [Architecture](#-architecture) · [Strategies](#-strategies) · [Why It's Different](#-why-its-different)

</div>

---

## 📋 What Is This?

PolyPaper Bot is a **production-grade autonomous trading bot** for Polymarket's binary prediction markets — focused on cryptocurrency **Up/Down** markets (BTC, ETH, SOL, XRP across 5m / 15m / 1h / 24h timeframes), with a roadmap to expand into geopolitics, sports, and other zero-fee categories.

The entire system is controlled from a **single Telegram chat**: start strategies, place manual orders, redeem winnings, review AI suggestions — all without ever opening polymarket.com.

Two execution modes share **a single codebase** with identical signal logic, ensuring zero behavioral divergence between sim and reality:

| Mode | What Happens | Risk |
|---|---|---|
| 📋 **PAPER** | Real-time market data, simulated fills via L2 orderbook depth replay | Zero — no capital deployed |
| 💰 **LIVE** | Real pUSD orders via Polymarket V2 CLOB, gasless via Relayer | Capital at risk — bounded by configurable limits |

> **Built solo over 2 months, in mainnet shadow trading since 2026-05-03, fully live since 2026-05-09.**

---

## ✨ Features

### 🤖 Autonomous trading
- **6-signal fusion engine** — odds, EMA, momentum, volatility, time-decay, and orderbook imbalance, weighted by per-strategy adaptive parameters
- **3-stage strategy lifecycle** — exploration (loose gates) → evaluation → proven (strict gates), automatic phase transitions based on win rate + sample size
- **12 backtest strategies + Classic plugin** — fade_rip, streak_reversal, opening_breakout, late_convergence, hour_edge, taker_flow, orderbook_imbalance, calibration_arb, composite, cross_coin, funding_rate, bonding_yield
- **Per-strategy adaptive parameters** — `min_composite`, `conviction_min`, `edge_gate_mult` self-tune per strategy phase

### 🧠 AI Brain
- **Claude Sonnet 4.6 integration** — autonomous strategy creation, threshold tuning, scaling decisions
- **2-agent decision loop** — Optimist (Groq Llama 70B) ↔ Critic (Claude) synthesis with confidence + risk scores
- **Approval queue** — every AI action is human-reviewed via Telegram inline buttons before execution
- **Per-trade analysis** — short post-mortem on every closed trade (`mistake_type` + lesson)
- **Budget-bounded** — `MAX_BUDGET=$15` hard cap, 429 rate-limit guard with cooldown

### 📊 Backtest engine
- **Real L2 history replay** — Polymarket orderbook + Binance kline + Chainlink oracle data
- **Walk-forward calibration** — out-of-sample validation with adjustable lookback windows
- **Fee + slippage realism** — calibrated `paper × 0.66 = live` multiplier from empirical sweep (199 trades × 200 markets)
- **Natural-language queries** — `/backtest fade_rip BTC 30 days` directly from Telegram

### 🛡️ Risk + safety
- **6 runtime guards** — kill switch, PnL pause threshold, rolling WR window, oracle parity, stale data gate, pre-flight allowance check
- **9-gate trade safety check** — market halt, liquidity, price sanity, risk limits, min size, min shares, STP, plugin errors, max exposure
- **`/env_toggle` runtime hot-tune** — 37 whitelisted parameters changeable without bot restart, with full audit log
- **Auto-redeem winners** — closed winning positions automatically redeemed (gasless via Relayer)

### 📱 Telegram UX
- **40+ commands** — single chat surface for paper + live, dashboard, strategies, trades, analytics, AI Brain panel
- **Inline keyboard everything** — no command memorization required
- **Mode banner** — every screen shows current mode (PAPER vs LIVE) prominently
- **CSV export** — trade history, reality-gap reports, strategy audits

### 🔬 Engineering quality
- **3,569 tests passing**, 0 failing, 42 skipped (intentional Windows-aiosqlite gate)
- **44% real-behavior coverage** with 60% ratchet target by Q3 2026
- **0 mypy errors** under strict mode (P1-07 FULL CLOSE 2026-05-11)
- **0 ruff violations** with `--unsafe-fixes` clean
- **13 secret-leak regex × 3 scope = 0 match** verified weekly
- **Sentry custom transactions** wired on `engine.cycle`, `ai_brain.advise`, `live_trader.execute_buy` (env-gated, zero cost when DSN unset)
- **Architecture decision records** + memory system carrying decisions across sessions

---

## 🚀 Quick Start

### Prerequisites

| Requirement | Notes |
|---|---|
| Windows 10/11 | Bot runs Windows-local; Linux/Docker on roadmap (P1-05) |
| Python 3.11 | `py -3.11 --version` must work |
| Telegram Bot Token | Create one with [@BotFather](https://t.me/BotFather) |
| Anthropic API Key | [console.anthropic.com](https://console.anthropic.com) — starter credits are enough for paper mode |
| Polymarket account | Only required for LIVE mode |

### Installation

```powershell
# 1. Clone
git clone https://github.com/Heddas98/polypaper-bot.git
cd polypaper-bot

# 2. Virtual environment
py -3.11 -m venv .venv
.venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env
notepad .env
```

**Minimum `.env` (paper mode):**
```ini
TELEGRAM_BOT_TOKEN=...      # from @BotFather
ADMIN_TELEGRAM_ID=...       # from @userinfobot
ANTHROPIC_API_KEY=...       # AI Brain (required even for paper mode)
```

**Live mode additionally needs:**
```ini
POLYGON_PRIVATE_KEY=0x...   # Rabby/MetaMask private key (signer EOA)
POLYGON_WALLET=0x...        # Polymarket Profile / Safe Proxy address (funder)
RELAYER_API_KEY=...         # polymarket.com/settings → API keys
RELAYER_API_KEY_ADDRESS=0x...
LIVE_ENABLED=true           # default false — set explicitly to enable live orders
```

### Run

```powershell
py -3.11 -m telegram_bot.bot
```

In Telegram, send **`/start`** to your bot — the mode-selection screen appears.

---

## 📲 Telegram Commands

### Mode selection
| Command | Action |
|---|---|
| `/start` | Mode selection screen (PAPER vs LIVE) |
| `/mode` | Banner toggle between paper and live |

### LIVE mode (real pUSD)
| Command | Action |
|---|---|
| `/buy {coin} {UP/DOWN} {amount}` | Manual market BUY (e.g. `/buy BTC UP 5`) |
| `/sell` | SELL panel — list open positions with PnL, % sizing |
| `/allowance`, `/approve` | 3-contract pUSD approval (gasless via Relayer) |
| `/portfolio`, `/pf` | On-chain balance + open positions + closed history |
| `/lh`, `/livehistory` | On-chain trade history + CSV export |

### PAPER mode (simulation)
| Command | Action |
|---|---|
| `/dashboard`, `/d` | Main panel — balance, PnL, win rate, active strategies |
| `/strategies`, `/s` | Strategy list with start/stop/edit/delete inline buttons |
| `/quick_strategy`, `/qs` | 5-step wizard to create a new strategy |
| `/backtest`, `/bt_v2` | Natural-language backtest (e.g. `/bt_v2 fade rip btc 30 days`) |
| `/why {trade_id}` | Decision explainer — why this trade was opened |

### Operations (both modes)
| Command | Action |
|---|---|
| `/health`, `/db_health` | Module health summary |
| `/diagnose` | System health check + warnings |
| `/lg`, `/live_guards` | 6-guard status snapshot |
| `/envt`, `/env_toggle` | Hot-tune 37 whitelisted parameters at runtime |
| `/reality_gap`, `/rg` | Paper × 0.66 vs live PnL drift report |
| `/recon`, `/rc` | On-chain pUSD vs DB reconciliation |
| `/ref_audit`, `/ra` | Reference price audit (bot vs Binance kline ground truth) |

Full command listing inside `/help`.

---

## 🏗️ Architecture

```
polypaper-bot/
├── core/                              # Engine — signals, risk, fills, AI Brain
│   ├── engine.py                        # Main orchestration (TradingEngine)
│   ├── engine_signals.py                # Strategy evaluation (mixin)
│   ├── engine_fills.py                  # Fill simulation (paper)
│   ├── engine_settlement.py             # Position close + payout
│   ├── engine_monitor.py                # Open position TP/SL monitor
│   ├── live_trader.py                   # Live order management
│   ├── ai_brain.py                      # 2-agent Claude/Groq decision loop
│   ├── risk_manager.py                  # 9-gate trade safety
│   ├── strategy_plugins.py              # 12 strategies + Classic plugin
│   ├── fees_v2.py                       # Polymarket fee oracle (V2 schedule)
│   ├── observability/sentry_tx.py       # Env-gated Sentry transactions
│   └── reconciliation/onchain_sync.py   # pUSD on-chain vs DB
│
├── data/                              # Polymarket API + WSS feeds
│   ├── polymarket_client.py             # CLOB V2 SDK wrapper
│   ├── polymarket_actions.py            # approve / redeem (gasless)
│   ├── polymarket_portfolio.py          # Balance / positions
│   ├── websocket_client.py              # WSS V2 market feed
│   ├── market_scanner.py                # Active market discovery
│   ├── candle_collector.py              # Multi-TF OHLCV aggregation
│   ├── chainlink_oracle.py              # Reference price feed
│   └── external_feed.py                 # Binance kline backup
│
├── services/                          # Out-of-process services
│   └── ai_advisor/                      # AI Brain microservice (P1-02)
│       ├── app.py                         # FastAPI /health /suggest /stats
│       ├── prompts.py                     # BRAIN_SYSTEM + 2-agent prompts
│       ├── router.py                      # 4-tier ModelRouter
│       └── llm_clients.py                 # Stateless HTTP wrappers
│
├── telegram_bot/                      # Telegram bot layer
│   ├── bot.py                           # Boot + handler registration
│   ├── handlers/                        # 30+ command handlers
│   └── jobs/                            # APScheduler periodic jobs
│       ├── auto_redeem_job.py             # Auto-redeem winning positions
│       ├── reality_gap_job.py             # Paper × 0.66 vs live drift
│       ├── shadow_report_job.py           # 30-min shadow report
│       └── pnl_divergence_job.py          # Daily paper/live PnL alert
│
├── backtest/                          # Backtest engine + 12 strategies
│   ├── replay_engine.py                 # Real L2 history replay
│   ├── strategies/                      # 12 strategies (fade_rip, ...)
│   ├── data_sources/                    # gamma_hist, polybacktest, binance_hist
│   ├── walk_forward.py                  # Out-of-sample calibration
│   └── analytics/                       # Reports + charts
│
├── db/                                # SQLite + WAL migrations
├── scripts/                           # Audit / prune / smoke .bat helpers
└── tests/                             # 3,569 tests (44% coverage, 0 mypy errors)
```

Architecture details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
Strategy lifecycle deep dive: [docs/STRATEGIES.md](docs/STRATEGIES.md)
PostgreSQL migration deep dive (P1-08): [docs/architecture/p1_08_postgresql_deep_dive.md](docs/architecture/p1_08_postgresql_deep_dive.md)

---

## 🎯 Polymarket V2 Compliance

Fully aligned with Polymarket's 2026-04-28 V2 cutover.

| Component | V2 Implementation |
|---|---|
| Python SDK | `py-clob-client-v2==1.0.0` (latest) |
| Relayer SDK | `py-builder-relayer-client` (gasless tx layer) |
| WSS endpoint | `wss://ws-subscriptions-clob.polymarket.com/ws/market` |
| Order types | `MarketOrderArgs` + `PartialCreateOrderOptions` typed dataclass |
| Allowance format | `bal["allowances"]` dict (per-spender) |
| 3-contract approve | pUSD → CTF + CTF Exchange + Neg Risk (gasless) |
| Redeem flow | `CTF.redeemPositions` (gasless) |
| WSS events | `book`, `price_change`, `tick_size_change`, `last_trade_price`, `new_market`, `market_resolved` |
| Subscribe flag | `custom_feature_enabled: true` |
| Fee oracle | `core/fees_v2.py` — 11 categories, docs-verified 2026-05-11 |

**Verified contract addresses:**
- pUSD: `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`
- CTF: `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`
- CTF Exchange: `0xE111180000d2663C0091e4f400237545B87B996B`
- Neg Risk CTF Exchange: `0xe2222d279d744050d28e00520010520000310F59`

---

## 📈 Strategies

12 backtest strategies + Classic user-directed plugin + AI Brain auto-generated. Every strategy progresses through three phases automatically based on trade count and win rate:

| Strategy | Edge thesis |
|---|---|
| `fade_rip` | Mean-reversion against extreme rallies |
| `streak_reversal` | Counter-trend after consecutive same-direction moves |
| `opening_breakout` | Range breakout on session open |
| `late_convergence` | Price converges to 0.50 near expiry |
| `hour_edge` | Hour-of-day statistical bias |
| `taker_flow` | Aggregate buy/sell pressure imbalance |
| `orderbook_imbalance` | Top-3-level bid/ask depth ratio |
| `calibration_arb` | Brier-calibration gap exploitation |
| `composite` | Multi-signal weighted ensemble |
| `cross_coin` | BTC ↔ ETH ↔ SOL correlation breaks |
| `funding_rate` | Perp funding-rate carry |
| `bonding_yield` | Bonding curve yield decay |

| Phase | Trade count | Filter level | Stake multiplier |
|---|---|---|---|
| `exploration` | 0–30 | Loose | 1.0× (start small) |
| `evaluation` | 30–100 | Medium | 1.0× (no scale yet) |
| `proven` | 100+ | Strict | up to 5.0× for AI-validated |

Details: [docs/STRATEGIES.md](docs/STRATEGIES.md)

---

## 🛡️ Why It's Different

**Most Polymarket bots stop at "place orders via SDK."** PolyPaper Bot operates as a full trading system:

- **Paper ↔ live behavior identity** — single signal engine, fill realism calibrated empirically (paper × 0.66 ≈ live), reality-gap nightly alerts at 03:00 UTC
- **Self-healing strategy lifecycle** — losing strategies auto-pause via `auto_optimizer`, winners auto-promote phases
- **AI Brain with approval gate** — Claude proposes, Telegram inline buttons dispose — no autonomous trades without human review
- **On-chain reconciliation** — pUSD on-chain balance vs DB cross-checked, > $1 drift triggers Telegram alert
- **Reference price audit** — bot's signal price vs Binance kline ground truth tracked per-trade, bias dashboarded
- **Multi-wallet ready** — DB schema supports N wallets, single-tenant today, SaaS-ready
- **Cross-FS aware** — Heddas's Windows-local + sandbox dual workflow honored throughout (atomic snapshots, online backup, WAL contention doctrine)

---

## 🔒 Security

- **`.env` never committed** — `.gitignore` enforced
- **13 secret-leak regex × 3 scope** = **0 match** verified each PR
- **`pip-audit`** → 0 known CVE on direct deps (as of 2026-05-11)
- **6 runtime guards** — kill switch, PnL pause, rolling WR, staleness, oracle parity, allowance pre-flight
- **3-contract allowance whitelist** — pUSD only grants to verified Polymarket contracts
- **Admin-only callbacks** — every Telegram callback gated by `_is_admin_call()` (5 callbacks added in Epic 10)

Vulnerability disclosure: [SECURITY.md](SECURITY.md)

---

## 🧪 Development & Testing

```powershell
# All tests
py -3.11 -m pytest tests/ -v

# With coverage
py -3.11 -m pytest --cov

# Mypy strict
py -3.11 -m mypy core/ --no-incremental --show-error-codes

# Lint
py -3.11 -m ruff check .

# Specific test class
py -3.11 -m pytest tests/unit/test_p0_p1_extra_coverage.py::TestCandleBuilder -v
```

**Current test profile (2026-05-11):**
- **3,569 tests passing** / 0 failing / 42 skipped
- **44% real-behavior coverage** (target 60% by Q3)
- **0 mypy errors** under strict mode
- **0 ruff violations** with `--unsafe-fixes` applied

Engineering decisions captured in [02_POLYPAPER_YOL_HARITASI.md](02_POLYPAPER_YOL_HARITASI.md) and the progress log [03_POLYPAPER_PROGRESS_LOG.md](03_POLYPAPER_PROGRESS_LOG.md).

---

## 🗺️ Roadmap

**Done (P0 + most of P1):** Audit pipeline, snapshot integrity, reference price audit, V2 SDK migration, structured logging, reality-gap nightly, strategy pruning, mypy + ruff CI, reconciliation smart-on, AI Advisor microservice scaffold, Sentry tracing.

**Active (P1 partial):** Coverage ratchet 44% → 60%, AI Brain extraction Wave 3 (approval flow via service), Polymarket connector deep-cross-check.

**Backlog (P1-P3):**
- PostgreSQL migration ([deep dive](docs/architecture/p1_08_postgresql_deep_dive.md)) — gated on SaaS pivot decision
- Linux/Docker deployment
- Multi-tenant SaaS layer + Stripe billing
- Public read-only dashboard (`dashboard.polypaper.io`)
- Geopolitics 0-fee market expansion (scaffold ready)
- Fill probability ML model (queue position + adverse selection)
- XGBoost signal scoring fusion

Full roadmap: [02_POLYPAPER_YOL_HARITASI.md](02_POLYPAPER_YOL_HARITASI.md)

---

## 📜 License & Contact

**License:** Proprietary — personal use only. See [LICENSE](LICENSE).

**Author:** Solo project, built and operated by [@Heddas98](https://github.com/Heddas98).

**Stack credit:** Polymarket (V2 SDK) · Anthropic (Claude Sonnet) · Groq (Llama 3.3) · python-telegram-bot · FastAPI · httpx · SQLite WAL.

---

<div align="center">

**Built for predicting markets that bet on markets.**

⭐ Star the repo if you find it useful — feedback welcome via Issues or Telegram `/feedback`.

</div>
