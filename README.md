# PolyPaper Bot

> **Polymarket kripto Up/Down binary prediction market paper-trading + shadow-live-trading Telegram botu.**
> Polyscout.io klonu · Python 3.11 · Tamamen gerçek canlı verilerle

[![Python](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![Engine](https://img.shields.io/badge/engine-v34-brightgreen.svg)]()
[![Bot](https://img.shields.io/badge/bot-v9.7.9-brightgreen.svg)]()
[![Phase](https://img.shields.io/badge/phase-82e%20Sprint%206-orange.svg)]()
[![License](https://img.shields.io/badge/license-Proprietary-red.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-Live-success.svg)]()

---

## Hızlı Bakış

**Ne yapar:** Polymarket'in BTC/ETH/SOL kripto Up/Down binary piyasalarında, gerçek canlı verilerle kâğıt üstünde işlem yapar. Aynı sinyalleri aynı anda "shadow-live" modunda küçük gerçek USDC miktarıyla da çalıştırır — tam otomasyon, Telegram üzerinden tek tuş kontrol.

**Mevcut durum (2026-04-20):**

| Metric | Value |
|---|---|
| Bakiye | ~$10,386 |
| Toplam PnL | +$355 |
| Trade sayısı | 1,417+ |
| Win Rate | 57% |
| Aktif strateji | 18 engine + Classic plugin + AI |
| Shadow live | Aktif ($1.49 USDC, $1/trade, 3 strateji) |
| AI Brain | Claude Sonnet, 10 dk cycle |
| Son hotfix | Sprint 6 — /env_toggle hot-tune (2026-04-20) |

**Neden özel:** 18+ stratejinin her biri kendi lifecycle'ında (exploration → evaluation → proven) otomatik gate filtresi öğrenir. 2-Agent AI Brain (Optimist+Critic) her saat parametreleri optimize eder, HyperOpt (Optuna TPE) gecelik overfit-gate'li parametre taraması yapar, Becker Calibrator gerçek Polymarket geçmişinden kalibre probability sağlar.

---

## Tech Stack

| Layer | Tool |
|---|---|
| Runtime | Python 3.11 (Windows yerel) |
| Telegram | python-telegram-bot 21.6 |
| DB | aiosqlite + WAL (busy_timeout 10s) |
| HTTP | httpx 0.27 + aiohttp |
| WS | websockets + reconnect-guard |
| Polymarket | py-clob-client 0.18.0 (EOA type-0 + ApiCreds) |
| Schedule | APScheduler 3.10 |
| Data | pandas, numpy |
| AI | Anthropic Claude Sonnet (primary), Groq/OpenRouter fallback |
| Optimization | Optuna TPE + overfit gate + MC Kelly |

---

## Başlarken

### Önkoşullar
- Windows 10/11
- Python 3.11 (`py -3.11 --version` çalışmalı)
- Telegram hesabı + [@BotFather](https://t.me/BotFather)'dan alınmış bot token
- Anthropic API key (ücretsiz tier yeterli)

### Kurulum

```powershell
# 1. Repo'yu klonla
git clone https://github.com/YOUR_USERNAME/polyPaper-bot.git
cd polyPaper-bot

# 2. Sanal ortam (önerilir)
py -3.11 -m venv .venv
.venv\Scripts\activate

# 3. Paketler
pip install -r requirements.txt

# 4. Environment template'i kopyala ve doldur
copy .env.example .env
notepad .env
```

`.env` içinde doldurulması zorunlu alanlar:
- `TELEGRAM_BOT_TOKEN` — @BotFather'dan
- `ADMIN_TELEGRAM_ID` — sayısal kullanıcı kimliğin ([@userinfobot](https://t.me/userinfobot))
- `ANTHROPIC_API_KEY` — Claude için
- Polymarket CLOB kimlikleri (sadece shadow-live açılacaksa)

Detaylı kurulum: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

### İlk Çalıştırma

```powershell
# Tekil çalıştırma (test)
py -3.11 -m telegram_bot.bot

# Watchdog ile arka plan (production)
watchdog.bat
```

Telegram'da bot'a `/start` gönder. Ana komut hub'ı açılır.

---

## Mimari

```
polyPaper-bot/
├── core/              # Engine, risk, signals, AI brain, Becker calibrator
├── backtest/          # ReplayEngine, data_sources, strategy adapters
├── telegram_bot/      # Bot, handlers/, jobs/, templates/
├── indicators/        # RSI, MACD, BB, confluence gate
├── skills/            # EMA, vol, orderbook skill modülleri (Phase 73)
├── data_feeds/        # Polymarket WS, Chainlink oracle, whale tracker
├── calibration/       # 2D C(K,τ) surface + MCI quality score
├── scripts/           # HyperOpt runner, migrations, backfills
├── config/            # Per-strategy YAML configs
├── db/                # Schema migrations, RO connection pool
├── docs/              # Full documentation
└── analysis/          # Ad-hoc analysis notebooks + reports
```

Derinlemesine mimari: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## Telegram Komutları (seçki)

| Command | Açıklama |
|---|---|
| `/start`, `/hub` | Ana komut paneli |
| `/balance`, `/pnl`, `/wr` | Anlık performans |
| `/strategies`, `/status` | Strateji durumları |
| `/trades`, `/open` | Aktif + geçmiş trade'ler |
| `/hyperopt <strat>` | Tek strateji için Optuna taraması |
| `/mc_kelly <strat>` | Monte Carlo Kelly validasyonu |
| `/backtest_v2` | Natural-language backtest |
| `/why <trade_id>` | Decision explainer |
| `/experiment` | Güvenli parametre testi |
| `/report` | Günlük performans raporu |
| `/shadow` | Shadow-live mode status |
| `/health`, `/db_health` | Modül sağlığı |
| `/force_settle <mkt>` | Admin: manuel settle |
| `/alert`, `/alerts` | Fiyat/PnL alarmları |

Tam komut listesi `/help` içinde.

---

## Stratejiler

Bot içinde 3 stratejik katman:
1. **Engine stratejileri** — late convergence, fusion, fade, breakout, mean reversion, penny contract, bonding yield vb. (18+ adet)
2. **Classic plugin** — algoritmasız, sadece direction_filter + threshold + TP/SL. Hyperopt'a girmez.
3. **AI stratejileri** — AI Brain'in önerdiği parametre setleriyle. $1 ile başlar, 20+ trade sonrası scale.

Her strateji kendi **lifecycle**'ında:
- `exploration` — ilk 30 trade, gevşek filtreler
- `evaluation` — 30-100, orta filtreler
- `proven` — 100+, sıkı filtreler

Detaylar: [docs/STRATEGIES.md](docs/STRATEGIES.md)

---

## Güvenlik

`.env` dosyası **asla commit edilmez** — `.gitignore` ile koruma altında, `pre-commit` hook'u pattern tarar. Detay ve rotation prosedürü: [SECURITY.md](SECURITY.md)

Eğer yanlışlıkla bir API key leak olursa: tüm key'leri derhal rotate et ([SECURITY.md](SECURITY.md) → "Incident Response").

---

## Bilinen Sorunlar / Troubleshooting

WAL busy_timeout, CLASSIC_BYPASS flag'leri, shadow report sorunları, CLOB signature fix — [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

---

## Geliştirme Geçmişi

82+ phase, Phase 47'den Phase 82e Sprint 5'e kadar evrim. Tüm milestone'lar: [docs/PHASES.md](docs/PHASES.md) · Yakın değişiklikler: [CHANGELOG.md](CHANGELOG.md)

---

## Lisans

Proprietary — şahsi kullanım için. Detay: [LICENSE](LICENSE)

---

## İletişim

Private repo — issue ve PR'lar sadece owner tarafından açılabilir. Bot'un kendi Telegram arayüzünden `/support` ya da `/feedback` komutlarıyla geribildirim verilebilir.
