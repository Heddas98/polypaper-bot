# PolyPaper Bot — Project Profile

> Source of truth: `README.md`, `02_POLYPAPER_YOL_HARITASI.md`, `03_POLYPAPER_PROGRESS_LOG.md`.

## Nedir

Polymarket V2 üzerinde otonom binary prediction market trading botu. BTC/ETH/SOL/XRP × 5m/15m/1h/24h Up/Down kontratları. Tek Telegram chat surface (paper + live aynı kod).

## Timeline

| Tarih | Olay |
|-------|------|
| 2026-02 (yakl.) | Solo development başladı |
| 2026-04-20 | İlk büyük cleanup taraması (`TASKS.md` oluşturuldu) |
| 2026-04-24 | Epic 11 FULL CLOSURE — mainnet-ready |
| 2026-04-28 | Polymarket V2 cutover (SDK migration) |
| 2026-05-03 | Mainnet shadow trading başladı |
| 2026-05-06 | Mod-first UX + V2 SDK + live trading stack (commit `ad906b9`) |
| 2026-05-09 | **MAINNET LIVE** — gerçek pUSD orders |
| 2026-05-11 | P1-07 mypy strict 0 hata + P1-02 Wave 1 AI Advisor service |

## Mimari

```
polypaper-bot/
├── core/                    # Engine — signals, risk, fills, AI Brain
│   ├── engine.py              # TradingEngine orchestration
│   ├── engine_signals.py      # Strategy evaluation (mixin)
│   ├── engine_fills.py        # Paper fill simulation
│   ├── engine_settlement.py   # Position close + payout
│   ├── engine_monitor.py      # TP/SL monitor
│   ├── live_trader.py         # Live order management
│   ├── ai_brain.py            # Claude/Groq 2-agent loop (990 stmt)
│   ├── risk_manager.py        # 9-gate trade safety
│   ├── strategy_plugins.py    # 12 strategies + Classic
│   ├── fees_v2.py             # Polymarket V2 fee oracle
│   └── observability/         # Sentry tx wrappers
│
├── data/                    # Polymarket API + WSS feeds
│   ├── polymarket_client.py   # CLOB V2 SDK wrapper
│   ├── polymarket_actions.py  # approve / redeem (gasless)
│   ├── websocket_client.py    # WSS V2 market feed
│   ├── market_scanner.py      # Active market discovery
│   ├── candle_collector.py    # Multi-TF OHLCV
│   ├── chainlink_oracle.py    # Reference price
│   └── external_feed.py       # Binance kline backup
│
├── services/                # Out-of-process services
│   └── ai_advisor/            # P1-02 microservice (FastAPI)
│       ├── app.py             # /health /suggest /stats
│       ├── prompts.py         # BRAIN_SYSTEM + 2-agent
│       └── llm_clients.py     # Stateless HTTP wrappers
│
├── telegram_bot/            # 40+ command handlers
│   ├── bot.py                 # Boot + handler registration
│   ├── handlers/              # 30+ command files
│   └── jobs/                  # APScheduler periodic
│       ├── auto_redeem_job.py
│       ├── reality_gap_job.py
│       ├── shadow_report_job.py
│       └── pnl_divergence_job.py
│
├── backtest/                # Backtest engine + 12 strategies
│   ├── replay_engine.py       # Real L2 history replay
│   ├── strategies/            # 12 strategies
│   ├── data_sources/          # gamma_hist / polybacktest / binance_hist
│   ├── walk_forward.py        # Out-of-sample calibration
│   └── analytics/             # Reports + charts
│
├── db/                      # SQLite + WAL + migrations
├── scripts/                 # Audit / prune / smoke .bat helpers
└── tests/                   # 3,569 tests
```

## Engineering Quality (2026-05-13 baseline)

- **3,569 tests passing** (0 fail, 42 skipped Windows-aiosqlite intentional)
- **%44.06 coverage** (ratchet target %60 by Q3 2026, source = core + data + telegram_bot + backtest)
- **mypy strict 0 hata** (P1-07 FULL CLOSE 2026-05-11)
- **ruff 0 violation** with `--unsafe-fixes` clean
- **Sentry custom transactions** wired on `engine.cycle`, `ai_brain.advise`, `live_trader.execute_buy` (env-gated)
- **13 secret-leak regex × 3 scope = 0 match** (weekly)
- **Bare except: 0 strict / 0 advisory** (T11.8 + T11.8-B FULL)

## Mode (PAPER vs LIVE)

| Mode | Davranış |
|------|----------|
| **PAPER** | Real market data, simulated fills via L2 depth replay, zero capital |
| **LIVE** | Real pUSD orders via Polymarket V2 CLOB, gasless via Relayer |

Aynı sinyal mantığı, sıfır divergence garantili.

## Test Komutu

```powershell
py -3.11 -m pytest -q                                # Full suite
py -3.11 -m pytest --cov=core --cov-fail-under=43    # Coverage gate
py -3.11 -m mypy --strict                            # Type check
py -3.11 -m ruff check .                             # Lint
```

## Run

```powershell
py -3.11 -m telegram_bot.bot
```

Telegram'da `/start` → mode-selection screen.

## Repo Layout (Önemli Dosyalar)

| Dosya | İşlev |
|-------|-------|
| `01_POLYPAPER_AUDIT_RAPORU.md` | Orijinal audit raporu (27 görev) |
| `02_POLYPAPER_YOL_HARITASI.md` | Aksiyona dökülmüş roadmap (P0-P3) — CANLI |
| `03_POLYPAPER_PROGRESS_LOG.md` | Her batch entry'si — CANLI |
| `TASKS.md` | Cleanup backlog (Epic 0-11 + post-mainnet defense-in-depth) — CANLI |
| `YOL_HARITASI_5AI_SYNTHESIS_2026_04_30.md` | 5-AI sentez snapshot |
| `CHANGELOG.md` | Public changelog |
| `SECURITY.md` | Security policy |
| `README.md` | Public-facing |
| `data_store/.auto-memory/` | Closure landmarks |
| `_archive/audit_snapshots/` | Historic audit raporları |
