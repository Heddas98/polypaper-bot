# Phase 50 — Telegram Regression Checklist (P0-12)

**Amaç:** Windows host'ta bot restart sonrası, Phase 49 + Phase 50'de
değişen tüm komutları 15 dakika içinde manuel doğrula. Her adım PASS/FAIL
işaretlenir; FAIL varsa ilgili fazı rollback et.

**Önkoşul:** Bot v9.7.3 running on Windows host, admin Telegram oturumu açık.

| # | Komut | Beklenen | PASS |
|---|-------|----------|------|
| 1 | `/h` | `health_check` ana özet — running=true, strats sayısı, WS status | ☐ |
| 2 | `/rs` | RiskState alanları: bankroll, realized_pnl, streak, halted=false | ☐ |
| 3 | `/db_health` (`/dbh`) | Tablo adı + satır sayısı listesi (en büyük 10), HTML escape'li | ☐ |
| 4 | `/dbc` | Manuel retention tetikler; "ob_snapshots: N rows deleted" gibi özet | ☐ |
| 5 | `/shadow_report` (`/sr`) | Son 30dk shadow vs paper karşılaştırması, admin DM | ☐ |
| 6 | `/stats_hub` | Inline keyboard (General / Strategy / Market sekmeleri) | ☐ |
| 7 | `/risk_hub` | Risk limit inline editörü açılır | ☐ |
| 8 | `/becker_status` / `/calibration_status` | δ(p) calibration DB yaş + satır sayısı | ☐ |
| 9 | `/becker_replay threshold_70 5 maker` | **Phase 50 NEW** — "Replay started… done in Ns" + özet (markets_seen, PnL, WR) | ☐ |
| 10 | `/alert btc-up >= 0.65` | **Phase 50 NEW** — "Alert #1 eklendi" HTML mesajı | ☐ |
| 11 | `/alerts` | Aktif alertleri listeler (sadece eklenen tek alert) | ☐ |
| 12 | `/alert_del 1` | "Alert #1 silindi" | ☐ |
| 13 | `/backtest_v2 threshold_70` (`/bt2`) | Event-driven v2 backtest çalışır, kalibrasyon hatası yok | ☐ |
| 14 | `/compare threshold_60 threshold_70 split` | Phase 48 train/test split compare — overfit gate çalışıyor | ☐ |
| 15 | `/canary strategy_x` → `/promote strategy_x` → `/demote strategy_x` | Phase 47f.10 promotion pipeline HTML-escaped, 3 ayrı admin DM | ☐ |

## Ek kontroller (opsiyonel ama önerilir)

- Log'larda `[cid=-]` yerine `[cid=<uuid>]` görünmeli (Phase 48 correlation_id filter).
- Restart sonrası 10 dakika içinde **`⚠️ STRATS_ZERO`** warning log **olmamalı**
  (P0-04 watchdog: gerçek boş kalma olmadığı sürece sessiz).
- `data_store/polypaper.log` içinde Phase 50 startup banner'ı: `PolyPaper Bot v34 - Mainnet Ready`.
- `get_status()` içinde `tick_gaps` alanı (P1-10) exposed — `/ws` output'unda görünmeli.

## Başarısızlık durumunda rollback

- **P0-04 FAIL (yanlış warning)**: `.env`'de `STRATS_ZERO_WARN_MINUTES=999` ile sustur; engine.py logic'ini incele.
- **P0-07 FAIL (StrategyStatus)**: `db/models.py::_missing_` hook'u eski enum'a sar.
- **P0-08 FAIL (HTML)**: ilgili handler'da `esc()` importunu geri al, eski `str(...)` interpolasyonunu kullan.
- **Becker replay FAIL**: `backtest/becker_replay.py` yeni dosya — bot.py'de import satırı + cmd registration'ı yoruma al.
- **Price alert FAIL**: bot.py'de `price_alert_job` scheduling + import satırlarını yoruma al; `PRICE_ALERT_ENABLED=0` ile hızlı kapat.

## Checklist tamamlandıktan sonra

1. Sonucu `data_store/regression_phase50_YYYYMMDD.txt` olarak kaydet.
2. Varsa FAIL maddelerini `MEMORY.md` altındaki `project_phase50.md`'ye "bilinen sorun" olarak yaz.
3. Phase 51'e geç.
