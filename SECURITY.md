# Security Policy

PolyPaper Bot'un güvenlik politikası ve secret yönetimi.

## Secrets Hijyeni

### Ne commit edilir
- `.env.example` — placeholder değerlerle tüm env variable'ların listesi
- Kod — API key'leri `os.getenv()` üzerinden okur, asla hardcode edilmez

### Ne commit edilmez
- `.env` — gerçek key'ler buradadır, `.gitignore` ile hariç
- Herhangi bir `*.key`, `*.pem`, `credentials.json`
- DB dosyaları (`*.db`, `*.db-wal`, `*.db-shm`) — trade history içerir
- Log dosyaları — correlation_id içerebilir

## Kritik Secret'lar

| Secret | Önem | Rotate prosedürü |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Kritik | [@BotFather](https://t.me/BotFather) → `/revoke` → yeni token al |
| `ANTHROPIC_API_KEY` | Yüksek | [console.anthropic.com](https://console.anthropic.com) → Settings → API Keys → revoke + generate new |
| `POLYGON_PRIVATE_KEY` | Maksimum | **ASLA rotate edilemez** — yeni cüzdan oluştur, fonları transfer et, eskisini imha et |
| `POLYMARKET_API_KEY` | Yüksek | Polymarket CLOB → API Keys → revoke + create new |
| `RELAYER_API_KEY` | Yüksek | [polymarket.com/settings/api-keys](https://polymarket.com/settings/api-keys) → Relayer API Keys → revoke + create new |

## Pre-commit Hook

Repo'ya eklenmiş `pre-commit` hook her commit öncesi şu kontrolleri yapar:

1. **`.env` dosyası staged mı?** → Evet ise commit reddedilir
2. **API key pattern detection** — aşağıdaki pattern'leri staged content'te arar:
   - `sk-ant-api[0-9a-zA-Z_\-]{20,}` (Anthropic)
   - `sk-or-v1-[0-9a-f]{64}` (OpenRouter)
   - `gsk_[0-9a-zA-Z]{40,}` (Groq)
   - `AIzaSy[0-9a-zA-Z_\-]{33}` (Gemini)
   - `\d{10}:[A-Za-z0-9_\-]{35}` (Telegram bot)
   - `0x[0-9a-fA-F]{64}` (Ethereum private key)
3. Eşleşme bulunursa commit **abort** edilir

## Incident Response

### Key leak tespit ederseniz

1. **DERHAL** (dakikalar önemli):
   - Sızan key'i ilgili platformdan **revoke** et
   - `.env`'deki değeri yeni key ile güncelle
   - Bot'u restart et (`rollback.bat` veya manuel)

2. **Git history'den temizle** (eğer commit olduysa):
   ```powershell
   # BFG Repo-Cleaner veya git filter-repo kullan
   # Ama push'dan sonra temizleme zordur — key'i rotate etmek tek garantili yol
   ```

3. **Monitor:**
   - Bot'un Telegram log'unda anormal trade var mı?
   - Polymarket cüzdanında beklenmeyen transfer var mı?
   - API usage dashboard'unda artış var mı?

4. **Post-incident:**
   - `docs/SECRETS_ROTATION.md` içinde rotate tarihi ve sebep kaydet
   - Eğer private key leak olduysa tüm fonları yeni cüzdana taşı

## Güvenlik Katmanları (runtime)

### Finansal koruma
```
LIVE_ENABLED=false           # Shadow-only default
MIN_BALANCE_FLOOR=50.0       # Bakiye < 50$ olursa trade durur
MAX_DAILY_LOSS=50            # Günlük kayıp limiti ($)
MAX_LOSS_STREAK=10           # Üst üste kayıp sonrası auto-pause
MAX_OPEN_POSITIONS=30        # Aynı anda max pozisyon
MAX_POSITION_SIZE=25.0       # Tek pozisyon max $
```

### Sistem koruma
- **Circuit breaker** — anomali tespitinde kill switch (Phase 48)
- **Watchdog v2** — single-instance lock, otomatik restart (Phase 57)
- **RO connection pool** — DB integrity için retry/fallback (Sprint 2.2)
- **bg_task exception guard** — Handler çöküşünde bot ana thread'i etkilenmez (Sprint 2.1)

### API Rate Limit koruma
```
MAX_429_RETRIES=3            # 429 rate-limit retry (Phase 56)
CLOB_TIMEOUT=5.0             # CLOB REST timeout
```

## Responsible Disclosure

Bu repo **private** — sadece sahip erişebilir. Yine de güvenlik zaafı fark ederseniz:
- Issue açma, direkt sahibe Telegram üzerinden mesaj at
- Exploit POC'yi **paylaşma**, sadece anla

## Compliance Notları

- Kripto işlemler yerel düzenlemelere tabidir (Türkiye: MASAK, BDDK)
- Bu bot **paper trading** amaçlıdır, shadow-live mode çok küçük miktarlarla ($1-2) test amaçlıdır
- Gerçek (live) trading'e geçmek için yasal danışmanlık önerilir
