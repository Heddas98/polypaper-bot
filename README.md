# PolyPaper Bot

> **Polymarket'te kripto Up/Down piyasalarında otomatik kâğıt + canlı işlem yapan Telegram botu.**
>
> Polyscout.io klonu · Python 3.11 · Polymarket V2 SDK · Tek bir Telegram penceresinden tam kontrol

[![Python](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![Polymarket SDK](https://img.shields.io/badge/polymarket-V2-success.svg)](https://docs.polymarket.com)
[![Tests](https://img.shields.io/badge/tests-3,474%20pass-brightgreen.svg)]()
[![Coverage](https://img.shields.io/badge/coverage-43.7%25-yellow.svg)]()
[![Mode](https://img.shields.io/badge/mode-paper%20%2B%20live-orange.svg)]()
[![License](https://img.shields.io/badge/license-Proprietary-red.svg)](LICENSE)

---

## Neye Yarıyor

PolyPaper Bot, Polymarket'in **BTC / ETH / SOL / XRP "Up/Down 5dk-1h" binary piyasalarında** otomatik karar veren bir trading bot'udur. İki modu vardır:

- **📋 PAPER MODE** — Gerçek piyasa verileriyle simülasyon. Para risk yok.
- **💰 LIVE MODE** — Gerçek pUSD ile gerçek emirler. Polymarket Relayer üzerinden gasless onay (kullanıcı gas ödemez).

Tek bir Telegram penceresinden:
- Stratejileri başlat / durdur / düzenle
- Anlık BUY / SELL emir ver
- Kazanan pozisyonları tek tıkla **redeem** et
- Trade geçmişini CSV olarak indir
- AI Brain'in (Claude Sonnet) önerilerini onayla / reddet

---

## Hızlı Başlangıç (10 dakika)

### Önkoşullar

| Gereksinim | Açıklama |
|---|---|
| Windows 10/11 | Bot Windows yerelde çalışıyor |
| Python 3.11 | `py -3.11 --version` çalışmalı |
| Telegram bot token | [@BotFather](https://t.me/BotFather)'dan al |
| Anthropic API key | [console.anthropic.com](https://console.anthropic.com) — ücretsiz başlangıç paketi yeterli |
| Polymarket hesap | LIVE mode için — sadece paper test edeceksen gerek yok |

### Kurulum

```powershell
# 1. Repo'yu klonla
git clone https://github.com/Heddas98/polypaper-bot.git
cd polypaper-bot

# 2. Sanal ortam (önerilir)
py -3.11 -m venv .venv
.venv\Scripts\activate

# 3. Bağımlılıklar
pip install -r requirements.txt

# 4. Environment dosyasını hazırla
copy .env.example .env
notepad .env
```

`.env` içinde **mutlaka doldurulması gerekenler**:

```ini
TELEGRAM_BOT_TOKEN=...      # @BotFather'dan
ADMIN_TELEGRAM_ID=...       # @userinfobot ile öğren
ANTHROPIC_API_KEY=...       # AI Brain için (paper mode için bile lazım)
```

LIVE mode için ek olarak:

```ini
POLYGON_PRIVATE_KEY=0x...   # Rabby/MetaMask private key
POLYGON_WALLET=0x...        # Polymarket Profile/Safe Proxy address
RELAYER_API_KEY=...         # polymarket.com/settings/api-keys
RELAYER_API_KEY_ADDRESS=0x...
LIVE_ENABLED=true           # Gerçekten canlı için (default false)
```

### Çalıştır

```powershell
py -3.11 -m telegram_bot.bot
```

Telegram'da bot'una **`/start`** yaz → mod seçim ekranı açılır.

---

## Telegram Komutları

### Mod Seçimi

| Komut | Açıklama |
|---|---|
| `/start` | Mod seçim ekranı (PAPER vs LIVE) |
| `/paper` | Paper-only menüye direkt geç |
| `/live` | Live-only menüye direkt geç |

### LIVE Mode (gerçek pUSD)

| Komut | Açıklama |
|---|---|
| `/buy {coin} {UP/DOWN} {amount}` | Manuel BUY market emri (ör: `/buy BTC UP 5`) |
| `/sell` | SELL paneli — açık pozisyonları PnL ile listele, % bazlı sat |
| `/allowance`, `/approve` | 3 contract için pUSD onayı (gasless, Polymarket Relayer öder) |
| `/portfolio`, `/pf` | Polymarket bakiyesi + açık pozisyonlar + closed history |
| `/lh`, `/livehistory` | On-chain trade geçmişi + CSV export |

### Paper Mode (simülasyon)

| Komut | Açıklama |
|---|---|
| `/dashboard`, `/d` | Ana panel: bakiye, PnL, win rate |
| `/strategies`, `/s` | Strateji listesi: başlat/durdur/düzenle/sil |
| `/trades` | Aktif + kapalı işlemler |
| `/quick_strategy`, `/qs` | Sihirbazla yeni strateji oluştur (5 adımda) |
| `/backtest`, `/bt_v2` | Doğal dil ile backtest (örn: "fade rip btc 30 gün") |
| `/why {trade_id}` | Karar açıklayıcı: bu trade neden açıldı? |

### Operasyon (her iki modda)

| Komut | Açıklama |
|---|---|
| `/health`, `/db_health` | Modül sağlığı |
| `/diagnose` | Sistem health check + uyarılar |
| `/lg`, `/live_guards` | 6 koruma katmanı durumu (kill switch, PnL pause, ...) |
| `/envt`, `/env_toggle` | Runtime ayar değiştir (24 onaylı parametre) |
| `/mode` | Paper ↔ Live banner toggle |

Tam komut listesi `/help` içinde.

---

## Mimari Özet

```
polypaper-bot/
├── core/                  # Engine — sinyaller, risk, fill, AI Brain
│   ├── engine.py            # Ana orkestrasyon
│   ├── engine_signals.py    # Strateji değerlendirme
│   ├── engine_fills.py      # Fill simülasyonu (paper)
│   ├── engine_settlement.py # Pozisyon kapatma
│   ├── live_trader.py       # Live emir yönetimi
│   ├── ai_brain.py          # Claude Sonnet entegrasyonu
│   ├── risk_manager.py      # Risk limitleri
│   └── strategy_plugins.py  # 12 strateji modülü
│
├── data/                  # Polymarket API + WebSocket katmanı
│   ├── polymarket_client.py    # CLOB V2 SDK wrapper
│   ├── polymarket_actions.py   # approve / redeem (gasless)
│   ├── polymarket_portfolio.py # Bakiye / pozisyonlar
│   ├── websocket_client.py     # WSS V2 market feed
│   └── market_scanner.py       # Aktif piyasa keşfi
│
├── telegram_bot/          # Telegram Bot katmanı
│   ├── bot.py                  # Boot + handler register
│   ├── handlers/               # 30+ komut handler'ı
│   │   ├── main_dashboard.py     # Mod-first /start ekranı
│   │   ├── live_handler.py       # BUY/SELL/Redeem UI
│   │   ├── live_history_handler.py  # Trade history + CSV
│   │   └── ...
│   └── jobs/                   # APScheduler periodic job'lar
│       ├── auto_redeem_job.py    # Otomatik kazanan pozisyon redeem
│       ├── shadow_report_job.py  # 30dk shadow report
│       └── ...
│
├── backtest/              # Backtest motoru + 12 strateji
│   ├── replay_engine.py        # Real L2 history replay
│   ├── strategies/             # fade_rip, streak_reversal, ...
│   └── data_sources/           # gamma, polybacktest, binance
│
├── db/                    # SQLite + WAL migrations
└── tests/                 # 3,474 test (43.7% coverage)
```

Detaylı mimari: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## Polymarket V2 Uyumluluğu

Bot, Polymarket'in 28 Nisan 2026'da yapılan V2 cutover'ına tamamen uygundur:

| Bileşen | V2 Implementation |
|---|---|
| Python SDK | `py-clob-client-v2==1.0.0` (resmi en güncel) |
| Relayer SDK | `py-builder-relayer-client` (gasless) |
| WSS endpoint | `wss://ws-subscriptions-clob.polymarket.com/ws/market` |
| Order types | `MarketOrderArgs` + `PartialCreateOrderOptions` (typed dataclass) |
| Allowance format | `bal["allowances"]` dict (per-spender) |
| 3-contract approve | pUSD → CTF + CTF Exchange + Neg Risk (gasless) |
| Redeem flow | `CTF.redeemPositions` (gasless) |
| WSS events | `book`, `price_change`, `tick_size_change`, `last_trade_price`, `new_market`, `market_resolved` |
| Subscribe flag | `custom_feature_enabled: true` |

**Onaylı contract adresleri:**
- pUSD: `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`
- CTF: `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`
- CTF Exchange: `0xE111180000d2663C0091e4f400237545B87B996B`
- Neg Risk CTF Exchange: `0xe2222d279d744050d28e00520010520000310F59`

---

## Stratejiler

12 backtest stratejisi + Classic plugin + AI Brain önerileri:

| Strateji | Açıklama |
|---|---|
| `fade_rip` | Aşırı yükselişe karşı satış |
| `streak_reversal` | Üst üste hareketin terse dönüşü |
| `opening_breakout` | Açılış kırılımı |
| `late_convergence` | Bitime yakın dengelenme |
| `hour_edge` | Saat-bazlı sapmalar |
| `taker_flow` | Alıcı/satıcı baskısı |
| `orderbook_imbalance` | Emir defteri dengesizliği |
| `calibration_arb` | Brier-kalibre arbitraj |
| `composite` | Çoklu sinyal birleştirme |
| `cross_coin` | BTC↔ETH↔SOL korelasyon |
| `funding_rate` | Funding rate temelli |
| `bonding_yield` | Bonding curve verim |

Her strateji 3 yaşam evresinde gelişir:

| Evre | Trade sayısı | Filtre seviyesi |
|---|---|---|
| `exploration` | 0–30 | Gevşek |
| `evaluation` | 30–100 | Orta |
| `proven` | 100+ | Sıkı |

Detaylı: [docs/STRATEGIES.md](docs/STRATEGIES.md)

---

## Güvenlik

- **`.env` asla commit edilmez** (`.gitignore` koruması)
- 13 secret leak regex × 3 scope = **0 match**
- `pip-audit` → 0 known CVE
- 6 runtime guard (kill switch, PnL pause, rolling WR, staleness, ...)
- 3-contract allowance whitelisted

`SECURITY.md` → vulnerability disclosure + key rotation prosedürü.

---

## Geliştirme & Test

```powershell
# Tüm testleri çalıştır
py -3.11 -m pytest tests/ -v

# Coverage raporu
py -3.11 -m pytest --cov=core --cov=data --cov=telegram_bot --cov=backtest

# Belirli bir wave
scripts\coverage_v24.bat
```

**Mevcut test profili:**
- 3,474 test pass / 41 skip / 0 fail
- 43.7% coverage (24-wave incremental push)
- Wave 23 integration suite Linux/WSL'de aktive edilebilir (env-gated)

---

## Lisans & İletişim

**Lisans:** Proprietary — kişisel kullanım. Detay: [LICENSE](LICENSE)

**Repo:** Private — issue ve PR sadece owner tarafından açılır.

**Geri bildirim:** Bot içinden `/support` veya `/feedback` komutları.
