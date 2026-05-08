# PolyPaper Bot

> **Polymarket kripto Up/Down binary prediction market paper-trading + shadow-live-trading Telegram botu.**
> Polyscout.io klonu · Python 3.11 · Tamamen gerçek canlı verilerle

[![Python](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![Engine](https://img.shields.io/badge/engine-v34-brightgreen.svg)]()
[![Bot](https://img.shields.io/badge/bot-v9.8.0-brightgreen.svg)]()
[![Phase](https://img.shields.io/badge/phase-Sprint%202%20Mainnet%20Shadow-orange.svg)]()
[![Tests](https://img.shields.io/badge/tests-3474%20pass-brightgreen.svg)]()
[![Coverage](https://img.shields.io/badge/coverage-43.7%25-yellow.svg)]()
[![Security](https://img.shields.io/badge/security-13%20regex%20%C3%97%200%20match-brightgreen.svg)]()
[![License](https://img.shields.io/badge/license-Proprietary-red.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-Mainnet%20Live-green.svg)]()

---

## Hızlı Bakış

**Ne yapar:** Polymarket'in BTC/ETH/SOL kripto Up/Down binary piyasalarında, gerçek canlı verilerle kâğıt üstünde işlem yapar. Aynı sinyalleri aynı anda "shadow-live" modunda küçük gerçek USDC miktarıyla da çalıştırır — tam otomasyon, Telegram üzerinden tek tuş kontrol.

**Mevcut durum (2026-05-06):**

| Metric | Value |
|---|---|
| Paper bakiye | ~$10,386 |
| Live bakiye (gerçek pUSD) | $12.18 |
| Toplam PnL | +$355 |
| Trade sayısı | 1,417+ |
| Win Rate | 57% |
| Aktif strateji | 18 engine + Classic plugin + AI |
| Live Mode | ✅ Manuel BUY/SELL UI + auto-redeem (gasless) |
| Allowance | ✅ 3-contract (CTF + CTF Exchange + Neg Risk) MAX |
| AI Brain | Claude Sonnet, 10 dk cycle |
| Test baseline | **3,474 pass** + 41 skip + 0 fail |
| Coverage | **43.7%** (24-wave test push) |
| Security | 13 secret regex × 3 scope = 0 match · pip-audit 0 CVE |
| Sprint 2 mainnet | 17 May decision gate (~10 gün) |
| Son milestone | Mod-first dashboard + Live history CSV export (2026-05-06) |

**Neden özel:** 18+ stratejinin her biri kendi lifecycle'ında (exploration → evaluation → proven) otomatik gate filtresi öğrenir. 2-Agent AI Brain (Optimist+Critic) her saat parametreleri optimize eder, HyperOpt (Optuna TPE) gecelik overfit-gate'li parametre taraması yapar, Becker Calibrator gerçek Polymarket geçmişinden kalibre probability sağlar.

**Mühendislik temeli (Epic 1-11):** Tek fee oracle (`core/fees_v2.py`), 5 ghost-class doctrine, canonical 6-flag set, paper×0.66 ≈ live fill heuristic, advisory bare-except sweep (56 dosya × 373 site), 6 live-guard runtime validation (kill switch / live budget / PNL divergence / rolling WR kill / staleness), atomik backup + rollback dry-run plan, 13 secret leak regex × 3 scope. Tüm geliştirme tarihçesi: [docs/PHASES.md](docs/PHASES.md) · [TASKS.md](TASKS.md).

---

## Tech Stack

| Layer | Tool |
|---|---|
| Runtime | Python 3.11 (Windows yerel) |
| Telegram | python-telegram-bot 21.6 |
| DB | aiosqlite + WAL (busy_timeout 10s) |
| HTTP | httpx 0.27 + aiohttp |
| WS | websockets + reconnect-guard |
| Polymarket | py-clob-client-v2 1.0.0 (Gnosis Safe Proxy sig_type=2 + V2 API) |
| Relayer | py-builder-relayer-client (gasless approve + redeem) |
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

**2026-05-06 yeni Mod-First Dashboard:**

| Command | Açıklama |
|---|---|
| `/start` | **Mod seçim ekranı** (PAPER vs LIVE) |
| `/paper` | Paper-only menü |
| `/live` | Live-only menü (gerçek USDC) |
| `/lh`, `/livehistory` | Live trade history detay + CSV export |

**Live Mode (gerçek pUSD):**

| Command | Açıklama |
|---|---|
| `/buy {coin} {UP/DOWN} {amount}` | Manuel BUY market order (FOK) |
| `/sell` | SELL panel (PnL ile pozisyon listesi) |
| `/allowance`, `/approve` | 3-contract approve via Polymarket Relayer (gasless) |
| `/portfolio`, `/pf` | Polymarket bakiye + pozisyonlar + closed |

**Paper Mode:**

| Command | Açıklama |
|---|---|
| `/dashboard`, `/d` | Ana dashboard |
| `/balance`, `/pnl`, `/wr` | Anlık performans |
| `/strategies`, `/s` | Strateji durumları |
| `/trades`, `/open` | Aktif + geçmiş trade'ler |
| `/quick_strategy`, `/qs` | Sihirbaz ile yeni strateji oluştur |
| `/hyperopt {strat}` | Tek strateji için Optuna taraması |
| `/backtest`, `/bt_v2` | Natural-language backtest |
| `/why {trade_id}` | Decision explainer |

**Operasyon:**

| Command | Açıklama |
|---|---|
| `/health`, `/db_health` | Modül sağlığı |
| `/diagnose` | Sistem health check |
| `/lg`, `/live_guards` | 6 guard runtime snapshot |
| `/envt`, `/env_toggle` | Runtime ENV hot-tune (24 whitelisted) |
| `/mode` | Paper/Live banner toggle |

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
