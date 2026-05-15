# Glossary — PolyPaper Bot Decoder Ring

> Akronim + kısaltma + iç kodadlar. Bilinmeyen bir term gelirse buraya bak.

## Polymarket / DeFi

| Term | Açılım |
|------|--------|
| **pUSD** | Polymarket USD stablecoin (`0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`) |
| **CLOB** | Central Limit Order Book — Polymarket'in emir defteri |
| **CTF** | Conditional Token Framework — Polymarket pozisyon NFT'leri (`0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`) |
| **Neg Risk** | Negative Risk CTF Exchange (`0xe2222d279d744050d28e00520010520000310F59`) |
| **WSS** | WebSocket subscription endpoint (`wss://ws-subscriptions-clob.polymarket.com/ws/market`) |
| **Relayer** | Gasless transaction layer (`py-builder-relayer-client`) |
| **Up/Down** | Polymarket binary outcome (BTC/ETH/SOL/XRP × 5m/15m/1h/24h) |
| **L2** | Level 2 orderbook depth (bid/ask + size) |
| **STP** | Self-Trade Prevention |
| **Builder Code** | `POLYMARKET_BUILDER_CODE` — bot order builder fee attribution |

## 3-Adres Sistemi (Polymarket)

| Term | Açılım |
|------|--------|
| **Profile / Safe Proxy** | Gnosis Safe contract, asıl hesap; `POLYGON_WALLET` (funder) |
| **Signer EOA** | Rabby/MetaMask EOA, sadece imzalar; `POLYGON_PRIVATE_KEY` |
| **Deposit Address** | Para yatırılan adres (USDC/pUSD); funding only |

## Bot iç terim

| Term | Açılım |
|------|--------|
| **AI Brain** | 2-agent Claude/Groq decision loop, strategy lifecycle owner |
| **Optimist** | Groq Llama 70B yarı — bullish bias karar |
| **Critic** | Claude Sonnet 4.6 yarı — risk + downside karar |
| **Classic** | User-directed manuel strategy plugin (protected, AI dokunamaz) |
| **Reality Gap** | Paper × 0.66 vs live PnL drift metric |
| **Reality Gap Job** | Periyodik drift report `telegram_bot/jobs/reality_gap_job.py` |
| **6-guard / 6 Live Guard** | G1 KillSwitch, G2 LiveBudget, G3 DailyLoss, G4 PnLDivergence, G5 RollingWR, G6 WSStale |
| **9-gate** | Trade safety pre-flight: halt, liquidity, price-sanity, risk, min-size, min-shares, STP, plugin-err, max-exposure |
| **Reference Price Audit** | Bot Binance/Chainlink feed vs Binance public klines ground-truth |
| **External Feed** | Binance kline backup feed |
| **Shadow Trading** | LIVE_ENABLED=false ama orderbook açıkken simulasyon |
| **Engine Cycle** | `TradingEngine.cycle()` ana orchestration |
| **Approval Queue** | AI action'lar Telegram inline button onay sırası |

## Strateji Adları (12 backtest + plugins)

| Strateji | Edge thesis |
|----------|-------------|
| `fade_rip` | Mean-reversion against extreme rallies |
| `streak_reversal` | Counter-trend after consecutive same moves |
| `opening_breakout` | Range breakout on session open |
| `late_convergence` | Price → 0.50 near expiry |
| `hour_edge` | Hour-of-day statistical bias |
| `taker_flow` | Taker-side flow imbalance |
| `orderbook_imbalance` | Book skew |
| `calibration_arb` | Brier-score driven |
| `composite` | Multi-signal fusion |
| `cross_coin` | BTC↔ETH↔SOL correlation |
| `funding_rate` | Perp funding cross-feed |
| `bonding_yield` | Yield-bearing market arb |
| `classic` | User-directed (PROTECTED) |

## Strateji Lifecycle Fazları

| Faz | Anlamı |
|-----|--------|
| **exploration** | Gevşek gate, sample toplama |
| **evaluation** | Win rate + sample threshold check |
| **proven** | Strict gate, full size |
| **paused** | Manuel veya guard tetiklendi |
| **retired** | Permanent off |

## Yol Haritası Etiketleri

| Term | Açılım |
|------|--------|
| **P0** | Kritik — mainnet'i bloklar (2-4 hafta hedef) |
| **P1** | Yüksek — 3 ay hedef |
| **P2** | Orta — 6 ay hedef |
| **P3** | Düşük — 9 ay+ |
| **S/M/L/XL** | Effort: <1 gün / 1-3 gün / 1-2 hafta / 2+ hafta |
| **Epic 0-11** | Cleanup + mainnet readiness epic'leri (HEPSİ KAPALI 2026-04-24) |
| **T11.1-T11.8** | Epic 11 alt görevleri (mainnet pre-gate) |
| **Faz / Aşama** | Roadmap sprint dilimi (Faz 0.1, Aşama 3.A vb.) |
| **Wave** | P1-01 coverage sprint dilimi (Wave 1, 1b, 2, 3, 3b) |
| **Batch** | Progress log iş paketi (Batch 1-6) |
| **Bulgu A/B/C** | Audit'te yakalanan kritik bulgu (numaralı) |

## Test + Engineering

| Term | Açılım |
|------|--------|
| **WR** | Win Rate |
| **EV** | Expected Value |
| **TF** | Timeframe (5m/15m/1h/24h) |
| **WAL** | SQLite Write-Ahead Log |
| **Brier** | Brier score — calibration metric |
| **TP/SL** | Take Profit / Stop Loss |
| **3-seed deterministik** | Test reproducibility 42/1337/9001 |
| **Sandbox** | Cowork agent VM (Linux mount) |
| **Windows-side** | Heddas'ın yerel PC'sinde çalıştırması gereken iş |
| **mypy strict** | `--strict` mode 0 hata gereksinim |
| **noqa: BLE001** | Bare-except documented exemption (ruff) |
| **noqa: BLE-OK** | Custom bare-except marker |
| **/envt whitelist** | 37 runtime-hot-tunable env var |

## Slash Komutlar (Telegram)

| Komut | Anlam |
|-------|-------|
| `/d` `/dashboard` | Ana panel |
| `/s` `/strategies` | Strateji listesi |
| `/qs` `/quick_strategy` | 5-step wizard |
| `/bt_v2` | Natural-language backtest |
| `/lg` `/live_guards` | 6-guard snapshot |
| `/rg` `/reality_gap` | Paper×0.66 vs live drift |
| `/ra` `/ref_audit` | Reference price audit |
| `/rc` `/recon` | pUSD on-chain ↔ DB |
| `/drt` | Dump REST timing |
| `/envt` | Hot-tune env param |
| `/why <trade_id>` | Decision explainer |
| `/pf` `/portfolio` | On-chain balance + positions |
| `/lh` `/livehistory` | On-chain trade history |
