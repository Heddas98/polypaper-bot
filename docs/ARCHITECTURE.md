# Mimari

PolyPaper Bot'un nasıl organize edildiğinin derinlemesine açıklaması.

## Yüksek Seviye Mimari

```
                  ┌─────────────────────────────────┐
                  │      TELEGRAM BOT (UI)          │
                  │  python-telegram-bot · handlers │
                  └──────────────┬──────────────────┘
                                 │
            ┌────────────────────┼────────────────────┐
            ▼                    ▼                    ▼
    ┌──────────────┐    ┌─────────────────┐   ┌──────────────┐
    │   ENGINE     │    │    JOBS         │   │   AI BRAIN   │
    │  core/       │    │  APScheduler    │   │  Claude SDK  │
    │  engine*.py  │    │  jobs/*.py      │   │  ai_brain.py │
    └──────┬───────┘    └────────┬────────┘   └──────┬───────┘
           │                     │                   │
           ▼                     ▼                   ▼
    ┌──────────────────────────────────────────────────────┐
    │              DATA LAYER (aiosqlite + WAL)            │
    │   polypaper.db · becker_calibration.db · caches      │
    └────┬──────────────┬──────────────┬─────────────┬─────┘
         │              │              │             │
         ▼              ▼              ▼             ▼
  ┌──────────┐  ┌──────────────┐ ┌──────────┐ ┌────────────┐
  │ POLY WS  │  │ CHAINLINK    │ │ BINANCE  │ │ KALSHI     │
  │  (LIVE)  │  │  Oracle      │ │   WS     │ │ (Becker)   │
  └──────────┘  └──────────────┘ └──────────┘ └────────────┘
```

## Dosya Ağacı (detaylı)

```
polyPaper-bot/
│
├── README.md                      # Proje tanıtımı
├── CHANGELOG.md                   # Phase bazlı değişiklik kaydı
├── SECURITY.md                    # Secrets & rotation politikası
├── LICENSE                        # Proprietary
├── requirements.txt               # Python bağımlılıkları
├── .env.example                   # ENV template (114+ değişken)
├── .gitignore                     # Secrets + runtime state hariç tutmaları
│
├── core/                          # ENGINE KALBI
│   ├── engine.py                  # Ana engine, market loop, pozisyon yönetimi
│   ├── engine_signals.py          # Sinyal üretimi + 14-gate pipeline (Confluence, EV, MCI, Parity, Penny, Slippage, Zones, UNSELLABLE, Classic-bypass…)
│   ├── engine_fills.py            # Fill modelleri (MAKER, TAKER, MAKER_HYBRID)
│   ├── engine_settlement.py       # Resolution fiyatı + CLOB unclamped, force_settle
│   ├── engine_monitor.py          # In-flight pozisyon gözetim
│   ├── engine_support.py          # Yardımcı fonksiyonlar
│   ├── risk_manager.py            # MAX_DAILY_LOSS, MAX_LOSS_STREAK, exposure, cooldown
│   ├── kelly.py                   # Sizing, MC-Kelly, dynamic bankroll
│   ├── ai_brain.py                # 2-Agent Optimist+Critic → synthesis
│   ├── auto_optimizer.py          # Otomatik Kelly + gate + TP/SL ince ayar
│   ├── strategy_lifecycle.py      # exploration / evaluation / proven
│   ├── strategy_plugins.py        # Classic stype plugin sistemi
│   ├── strategy_selector.py       # Hangi stratejinin aktif olacağına karar
│   ├── strategy_suggester.py      # Backtest + HyperOpt öneri motoru
│   ├── signal_fusion.py           # Çok kaynaklı sinyal birleşimi
│   ├── becker_calibration.py      # Becker Calibrator (2D C(K,τ) surface)
│   ├── becker_rolling_recal.py    # Rolling re-calibration
│   ├── becker_weight_tracker.py   # Adaptive Becker weight
│   ├── micro_weight_tracker.py    # Micro-structure weight
│   ├── ev_tracker.py              # EV threshold tracking
│   ├── circuit_breaker.py         # Sistem durduğunda fail-safe
│   ├── kill_switch.py             # Acil durdurma
│   ├── live_trader.py             # Shadow-live CLOB trader
│   ├── trade_memory.py            # Kalıcı pattern öğrenme (Phase 77)
│   ├── trade_journal.py           # Mistakes journal (Phase 59)
│   ├── decision_explainer.py      # /why trade_id için reasoning chain
│   ├── experiment_runner.py       # Sandbox parametre testi
│   ├── regime.py                  # Trending/Ranging/Volatile klasifikasyonu
│   ├── fees.py, fees_v2.py        # Maker rebate + fee curve (Phase 47f.9)
│   ├── intent_parser.py           # /ai /nl natural-language komut parser
│   ├── observability.py           # Metrics + correlation_id logger
│   ├── changelog.py               # /changelog komutu verisi
│   ├── indicators.py              # Compat katmanı (yeni indikatör skills/'da)
│   ├── bg_task.py                 # Background task exception guard (Sprint 2.1)
│   └── signals/                   # Eski signal moduleleri (legacy)
│
├── backtest/                      # BACKTEST MOTORU
│   ├── engine.py                  # ReplayEngine (event-driven tick replay)
│   ├── data_sources/
│   │   ├── binance_hist.py        # Binance historical candles
│   │   ├── gamma_hist.py          # Polymarket Gamma API historical
│   │   ├── polybacktest.py        # Polybacktest API
│   │   ├── collector.py           # OB snapshot collector
│   │   └── cache.py               # Local cache layer
│   ├── simulation/
│   │   ├── fee_model.py, fee_model_v3.py  # Maker/Taker fee kurguları
│   │   ├── fill_model.py          # Order book walker
│   │   └── portfolio.py           # Pozisyon takibi
│   ├── strategies/                # Backtest-ready adaptörler (live↔bt)
│   │   ├── base.py                # Soyut sınıf
│   │   ├── bonding_yield.py       # Phase 76
│   │   ├── calibration_arb.py     # Becker calibration arbitrage
│   │   ├── composite.py           # Composite signal
│   │   └── …                      # 11+ adaptör
│   ├── analytics/
│   │   ├── reporter.py            # PnL, WR, Sharpe, Sortino raporları
│   │   ├── charts.py              # Matplotlib görselleri
│   │   └── comparator.py          # A/B + split-backtest karşılaştırma
│   └── hyperopt_runner.py         # Optuna pipeline (Phase 67+)
│
├── telegram_bot/                  # BOT + HANDLER'LAR
│   ├── bot.py                     # Main entry — Application, dispatcher
│   ├── banners.py                 # ASCII banner + version splash
│   ├── hub_keyboard.py            # /hub inline keyboard
│   ├── version.py                 # Bot versiyon bilgisi
│   ├── handlers/                  # 35+ command handler
│   │   ├── start.py               # /start, /help
│   │   ├── dashboard.py           # /dashboard
│   │   ├── stats.py               # /stats, /wr, /pnl
│   │   ├── strategies.py          # /strategies, /pause, /resume
│   │   ├── positions.py           # /open, /trades
│   │   ├── markets.py             # /markets, /mkt
│   │   ├── ai_handler.py          # /ai, /nl, /why
│   │   ├── hyperopt_handler.py    # /hyperopt, /hyperopt_all
│   │   ├── backtest_v2.py         # /backtest_v2 (natural language)
│   │   ├── strategy_builder.py    # Classic plugin UI
│   │   ├── strategy_tester.py     # /test_strategy
│   │   ├── strategy_report.py     # /report
│   │   ├── lifecycle_handler.py   # /lifecycle
│   │   ├── risk_handler.py        # /risk_hub
│   │   ├── live_handler.py        # Shadow live toggle
│   │   ├── force_settle_handler.py # /force_settle (admin)
│   │   ├── diagnose_handler.py    # /diagnose
│   │   ├── becker_recal_handler.py # /becker_recal
│   │   ├── phase76_handler.py     # /markov, /capital
│   │   ├── phase77_handler.py     # /experiment, /health
│   │   └── …
│   ├── jobs/                      # APScheduler periyodik işler
│   │   ├── heartbeat.py           # 5dk aralıkla durum özeti
│   │   ├── daily_db_snapshot.py   # Gecelik DB snapshot
│   │   ├── db_retention_job.py    # 5 tablo nightly retention
│   │   ├── shadow_report_job.py   # 30dk shadow-vs-paper rapor
│   │   ├── ai_brain_job.py        # 1 saat AI brain cycle
│   │   ├── auto_resume_job.py     # Startup auto-resume
│   │   ├── hyperopt_nightly.py    # Nightly tournament
│   │   ├── wr_milestone_job.py    # 50/100/200/500 trade milestone
│   │   └── …
│   └── templates/                 # Message templates (HTML)
│
├── indicators/                    # TEKNIK INDIKATOR SKILLS
│   └── technical.py               # RSI, MACD, Bollinger, confluence gate
│
├── skills/                        # SKILL MODULLERI (Phase 73)
│   ├── ema_skill.py
│   ├── vol_skill.py
│   └── orderbook_skill.py
│
├── data_feeds/                    # GERÇEK ZAMANLI VERİ FEEDLERİ
│   ├── polymarket_ws.py           # Polymarket OB WebSocket
│   ├── binance_ws.py              # BTC/ETH spot fiyat feed
│   ├── chainlink_oracle.py        # Parity gate için
│   ├── whale_tracker.py           # $100+ trade'leri izler
│   ├── spread_signal.py           # Spread-based mikro sinyal
│   ├── latency_monitor.py         # Feed gecikme ölçümü
│   └── event_waves.py             # Haber/event kalite sinyali
│
├── calibration/                   # 2D KALİBRASYON (Phase 70)
│   ├── surface.py                 # C(K, τ) probability surface
│   └── mci.py                     # MCI quality score
│
├── config/                        # YAML + static config
│   ├── strategies/                # Per-strategy parametre yaml'ları
│   └── zones.yaml                 # ALLOWED_ZONES default
│
├── db/                            # DB KATMANI
│   ├── ro_connect.py              # Read-only connection + retry/fallback (Sprint 2.2)
│   ├── migrations/                # Versiyon bazlı schema değişiklikleri
│   └── schema.sql                 # Full schema snapshot
│
├── scripts/                       # YARDIMCI SCRIPTLER
│   ├── hyperopt_worker.py         # HyperOpt subprocess worker
│   ├── ob_archive.py              # Order book Parquet arşivi
│   ├── verify_db_health.py
│   ├── backfill_ob_trades.py
│   └── migrate_*.py
│
├── tests/                         # TEST SUITE (60+ test)
│   └── unit/                      # Unit testler
│
├── utils/                         # ORTAK YARDIMCILAR
│   ├── fmt.py                     # fmt_usd, safe_html
│   ├── correlation.py             # correlation_id filter
│   └── …
│
├── tools/                         # Ad-hoc analiz araçları
├── analysis/                      # Analiz notebooks
│
├── docs/                          # BU KLASÖR
│   ├── ARCHITECTURE.md            # Bu dosya
│   ├── STRATEGIES.md
│   ├── PHASES.md
│   ├── DEPLOYMENT.md
│   └── TROUBLESHOOTING.md
│
└── *.bat                          # Windows deployment scriptleri
    ├── watchdog.bat               # Production watchdog
    ├── rollback.bat               # Acil geri alma
    ├── backup.bat                 # Manuel yedekleme
    ├── reset_and_start.bat        # Tam sıfırlama
    └── deploy_phase*.bat          # Her phase için deploy bat
```

## Data Flow

### Trade Açılış Akışı
1. **Polymarket WS** → order book snapshot → `data_feeds/polymarket_ws.py`
2. **Binance WS** → BTC/ETH spot → `data_feeds/binance_ws.py`
3. **Chainlink oracle** → parity check → `data_feeds/chainlink_oracle.py`
4. `engine.py` → `generate_signals()` her aktif strateji için
5. `engine_signals.py` → **14-gate pipeline**: Confluence → EV → MCI → Parity → Penny → Slippage → Zones → Kelly → Becker → vb.
6. Geçen sinyal → `kelly.py` sizing → `engine.py._open_position()`
7. `polypaper.db`'ye insert + Telegram notification

### Settlement Akışı
1. Market close approach → `engine_settlement.py` force_after (TF-aware)
2. Resolution price from Gamma API `outcomePrices` (unclamped)
3. CLOB `get_resolution_price` (fallback)
4. `engine_settlement.py._settle_position()` → P&L hesapla → DB update

### AI Brain Cycle (Phase 69)
1. APScheduler `ai_brain_job` saatte bir tetikler
2. **Optimist Agent** (Claude Sonnet) — son N trade üzerinden "daha agresif olmalıyız" önerileri
3. **Critic Agent** (Claude Sonnet) — aynı veri üzerinden "bu sinyaller neden yanlış olabilir" eleştirisi
4. Synthesis → `champion_tracker.py` → en iyi parametreler
5. `AI_AUTO_CONFIDENCE` (0.70+) üstü öneriler direkt uygulanır, alt olanlar sadece log'lanır

### Shadow Live Akışı
1. Paper trade aç (normal pipeline)
2. `core/live_trader.py` → CLOB API → gerçek USDC emri ($1/trade default)
3. Filled → `shadow_trades` tablosu
4. Her 30dk `shadow_report_job.py` → paper vs shadow kıyası → Telegram'a rapor

## Kritik Modüller Arası Bağımlılıklar

- `engine.py` ↔ `risk_manager.py` → her trade `risk.check_limits()` geçmeli
- `engine_signals.py` → `indicators/technical.py` + `skills/*_skill.py`
- `ai_brain.py` → `strategy_suggester.py` (yeni strat önerisi)
- `auto_optimizer.py` ← `trade_memory.py` (pattern öğrenme)
- `becker_calibration.py` ↔ `data_store/becker_calibration.db` (lokal, 50GB raw + 1.4GB calibDB)
- Tüm handler'lar ← `core/bg_task.py` (exception guard sarmalayıcı)

## Performans Notları

- **DB**: WAL mode, `busy_timeout=10000`, `synchronous=NORMAL`
- **HyperOpt**: Subprocess izolasyon, `N_JOBS=1` default (tek core), memory abort 2.5GB
- **Discovery cache**: Sprint 2.3'te `idx_ob_snap_atf_slug_mst_ts` (5-col covering) ile 222s → 7s (32x)
- **Split-backtest cache**: Sprint 4.4 `idx_ob_snap_atf_slug_mst_ts` ile TEMP B-TREE GROUP BY kaldırıldı
- **Memory**: HyperOpt worker 800MB warn, 1500MB crit, 2500MB abort
- **WS**: 20s stale threshold, 300s force-reconnect

## Schema Özeti (polypaper.db)

Ana tablolar:
- `trades` — tüm açık+kapalı trade'ler
- `positions` — aktif pozisyonlar
- `ob_snapshots` — order book snapshots (covering index'li)
- `ob_trades` — her trade'in OB durumu (backfill'li)
- `whale_trades` — $100+ volume trade'ler (Phase 78)
- `strategy_status` — her strateji için WR, PnL, trade count
- `hyperopt_results` — Optuna çıktıları (v14, asset/tf granularity)
- `shadow_trades` — shadow-live executed orders
- `mistakes_journal` — AI learning için hatalı trade'ler (Phase 59)
- `correlation_limits` — aynı anda açılabilecek korelasyonlu pozisyon sayısı
