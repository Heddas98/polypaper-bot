# Phase 50 — Manuel Yapılacaklar (Sandbox yapamadı)

Aşağıdaki işler Linux sandbox'ta yapılamadı (file permission, hot DB lock,
veya Windows-only araç gerektiriyor). Heddas'ın Windows host'ta yapması gereken adımlar:

## 1. Sıfır-byte test DB'lerini sil (P0-11)

Konum:
```
Polyscout31\data\bot.db       (0 bytes)
Polyscout31\data\polypaper.db (0 bytes)
```

> Sandbox Linux'tan sildiremedi — "Operation not permitted". Gerçek DB
> `data_store/polypaper.db` (Phase 47f.8+). `data/*.db` yanlış konumda
> kalmış boş dosyalar.

**Adımlar**:
1. Bot çalışıyorsa `/kill` ile durdur.
2. PowerShell aç → `cd C:\path\to\Polyscout31\data`
3. `Remove-Item .\bot.db, .\polypaper.db`
4. Bot'u restart et (`start.bat`).

## 2. .env CLOB credentials'ı doldur (A-01 live trader)

Eksik alanlar (`.env`):
```
POLYGON_PRIVATE_KEY=0x...
POLYGON_WALLET_ADDRESS=0x...
POLYMARKET_API_KEY=...
POLYMARKET_API_SECRET=...
POLYMARKET_API_PASSPHRASE=...
```

> 4/5 alan boşken live trader gate (`auth_verified=False`) engel oluyor — **doğru davranış**. Doldurulduğunda `/live on` ile açılabilir.
> Referans: `docs/SECRETS_ROTATION.md`.

## 3. Phase 49 + Phase 50 değişikliklerini devreye al

```
cd C:\path\to\Polyscout31
start.bat
```

Sonra Telegram'da `docs/REGRESSION_CHECKLIST_PHASE50.md` üzerindeki 15 adımı uygula.

## 4. Becker Replay ilk çalışma (36 GB tar gerekmez)

`data_store/becker_calibration.db` (849 MB) zaten Phase 47f'de build edilmişti. Replay harness doğrudan onun üzerinden çalışır.

Test komutu:
```
py -3.11 -m backtest.becker_replay --strategy threshold_70 --markets 25 --save
```

veya Telegram:
```
/becker_replay threshold_70 25
```

Beklenen: 30–120 saniye içinde özet (markets_seen, markets_traded, total_pnl, win_rate) ve `data_store/becker_replay_*.json` çıktısı.

## 5. Price Alert sistemini test et (yeni)

```
/alert btc-up-20260410 >= 0.65
/alerts
/alert_del 1
```

Not: `PRICE_ALERT_ENABLED=0` ile hızlıca kapatılabilir.

## 6. Opsiyonel environment flag'leri

| Flag | Default | Açıklama |
|------|---------|----------|
| `KEEPALIVE_ENABLED` | false | Replit redeployment için true (Windows'ta gerek yok) |
| `STRATS_ZERO_WARN_MINUTES` | 10 | Strats=0 watchdog eşiği |
| `PRICE_ALERT_ENABLED` | 1 | Price alert job |
| `PRICE_ALERT_INTERVAL_SEC` | 30 | Check frekansı |
| `SENTRY_DSN` | (boş) | Sentry enable |

## 7. Becker tar dosyası KORUNACAK

`becker_data.tar.zst` (36 GB) **SİLİNMEYECEK**. Walk-forward replay için gelecekte yeniden calibration build veya ham trade ihtiyacı olursa kullanılacak. Audit P0-10 iptal edildi.
