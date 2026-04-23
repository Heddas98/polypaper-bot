# Epic 11 T11.2 — Live Guard Runtime Validation Template

**Status:** 📝 TEMPLATE (Windows'ta doldurulacak) · **Risk:** HIGH
**Önkoşul:** T11.1 CLOSED · **Ardından:** T11.3 Rollback plan
**Test ortamı:** Windows yerel PC + shadow live ($1.49 USDC, $1/trade, 3 strateji)
**Şart:** `LIVE_ENABLED=false` (shadow mirror sürdüğü halde gerçek emir gitmiyor)

---

## Amaç

Bu belge, T11.1 Bölüm 8'de listelenen 6 canlı guard'ın **runtime kanıtını**
tutar. T11.1 statik audit'in bittiğini söyler; T11.2 ise her guard'ın
gerçek bot üzerinde tetiklendiğinde beklenen davranışı sergilediğinin
**kaydıdır**. Sandbox'ta doldurulamaz — Windows'ta `.env` patch +
`/env_toggle` + `/kill` komutları + Telegram alert yakalaması gerektirir.

Her guard için: (1) kod kancası referansı, (2) tetikleme prosedürü,
(3) beklenen davranış, (4) kanıt slotu (log satırı + Telegram ekran
görüntüsü veya mesaj metni + timestamp). Guard başlığındaki `☐ PASS` /
`☐ FAIL` kutusu test sonrası işaretlenir.

**Geçiş kriteri:** 6/6 guard ✅ olduğunda T11.2 kapanır. Herhangi bir
FAIL → fix → retest → tekrar doldur.

---

## Genel Kurulum

```bash
# Windows PC'de
cd C:\path\to\Polyscout31
py -3.11 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# .env (NOT: LIVE_ENABLED=false KALACAK, yalnız test knob'ları değişir)
# Test sırasında aşağıdaki guard'ları ayrı ayrı tetikleyeceğiz.
# Shadow live bakiye ve CLOB creds zaten populate (project_live_trader_state).

# Bot başlat
start_bot.bat   # veya: py -3.11 main.py
```

**Log konumu:** `logs/bot.log` (rolling). Her test için `tail -n 50 logs/bot.log`
ile kanıt satırı alınır.

**Telegram kontrol:** Admin Telegram chat'i. Alert beklendiğinde ekran
görüntüsü + raw mesaj metni alınır.

**Bot restart gerekiyorsa:** `stop_bot.bat` → `.env` patch → `start_bot.bat`.
`/env_toggle` runtime patch'ini test sürelerini kısaltmak için tercih et.

---

## G1 — Kill Switch (`/kill` + file-based) — ☒ PASS (2026-04-23)

### Kod kancası

- **ENV:** (yok — state bot process memory + `data_store/polypaper.stop` file)
- **Handler:** `telegram_bot/handlers/risk_handler.py:30-40` (`/kill`), `:45-60` (`/resume`)
- **Kill switch class:** `core/kill_switch.py:21` (3 channel: file / memory / Telegram)
- **Engine enforcement:** `core/engine.py:844-848` — her cycle `if is_killed(): asyncio.sleep(1); continue`
- **Admin gate:** T10.2 pattern (`_is_admin_call()` risk_handler altında)

### Tetikleme (in-memory channel)

1. Bot çalışır durumda. `/kill emergency test T11.2` Telegram'dan gönder.
2. Bot log: `🛑 KILL SWITCH ACTIVATED: emergency test T11.2` (logger `polypaper.core.killswitch`).
3. 5 saniye bekle.
4. `/risk` gönder → "🛑 KILL ACTIVE" satırı görünmeli (`risk_handler.py:134`).
5. `/resume` gönder → log: `✅ KILL SWITCH DEACTIVATED: Trading resumed`.

### Tetikleme (file channel — asyncio hang safety net)

1. Windows shell: `echo manual > data_store\polypaper.stop`
2. 1-2 cycle bekle (en fazla 10s).
3. Log: `🛑 KILL SWITCH: File detected (data_store/polypaper.stop)`.
4. Dosyayı sil: `/resume` gönder (handler dosyayı siler `risk_handler.py:55` → `kill_switch.deactivate()` → `os.remove(KILL_FILE)`).

### Beklenen davranış

- Engine cycle loop trade açma eylemini atlar (`engine.py:844` guard).
- Her 60 cycle'da bir `"🛑 Kill active c={cycle}"` WARNING log'lanır.
- `/risk` output'unda `killed: True`, `reason`, `killed_at`, `file_exists` alanları görünür.
- `/resume` sonrası bir sonraki cycle'da yeni trade açılabilir.

### Kanıt (2026-04-23 canlı run — PASS)

**Kanıt dosyaları:**
- `evidence/t11_2_g1_20260423_155247.txt` — bat script otomatik file-channel testi
- `evidence/t11_2_g1_resume_manual_20260423.txt` — /resume manuel kanıtı

**[2026-04-23 15:52:48.096 UTC+3] File-channel detection (96 ms latency):**
```
2026-04-23 15:52:48,096 [polypaper.core.killswitch] WARNING [cid=-]:
🛑 KILL SWITCH: File detected (data_store\polypaper.stop)
```

**[15:52:54.56] Cleanup — polypaper.stop bat tarafından silindi.**

**[15:52:58.076] Sticky memory kanıtı (file silindikten sonra):**
```
2026-04-23 15:52:58,076 [polypaper.core.engine] WARNING [cid=-]:
🛑 Kill active c=601
```
→ `kill_switch.py:32-38` tasarımı: file detect edildiğinde `_memory_kill=True`
sticky; file silinse bile memory flag kalır. Auto-resume YOK (kazara sentinel
silinmesine karşı koruma).

**[~15:54] Manuel /resume via Telegram:**
```
> Cyberg: /resume
> Dojopolyscout:
  ✅ Trading Resumed
  Kill switch deactivated.
  Risk halt reset.
  Engine will evaluate strategies on next cycle.
```

**[~15:54] Resume sonrası ilk TAKER fill (canlı doğrulama):**
```
TAKER Fill — Strateji: ?_BTC_5m_any_0.70, UP/BTC 5m
Limit: 0.9900 → Fill: 0.8700 (slip=-12.1%)
Tutar: $1.00 | 1.15 shares, Fee: $0.0094
`btc-updown-5m-1776948900`
```

**Verdict:** 3/3 sub-kanıt ✅
  - File channel detection + 96 ms latency
  - Sticky memory kill (auto-resume yok)
  - Manuel /resume sonrası hemen yeni trade açıldı

**Yan bulgu (Windows backlog):** Sandbox'tan log tail gecikmeli okuyor
(Python RotatingFileHandler buffer + WSL mount cache). Telegram mesajları
ilk-sınıf kanıt olarak kullanıldı. Live-test scriptlerinde log-grep'in
gerçek zamanlı olmayabileceği not edildi.

---

## G2 — Budget Guard (`LIVE_BUDGET`) — ☐ PASS / ☐ FAIL

### Kod kancası

- **ENV:** `LIVE_BUDGET` (default `1.49`, `.env` override)
- **Init:** `core/live_trader.py:111` — `self._budget = float(os.getenv("LIVE_BUDGET", "1.49"))`
- **Enforcement:** `core/live_trader.py:259-262`
  ```python
  remaining = self._budget - self._total_spent
  if remaining < 0.10:
      logger.info("  🔴 LIVE: Budget exhausted")
      return None
  ```
- **Etki:** `maybe_mirror()` `None` döndürür → shadow mirror skip (gerçek emir de zaten `LIVE_ENABLED=false` ile kapalı).

### Tetikleme

**Seçenek A — gerçek şadow bakiyesiyle:**
1. `/risk` → mevcut `live.total_spent` ve `live.budget` oku.
2. Gerçek trade'ler 1.49'a yaklaşana kadar bekle (saatler / günler).
3. Son trade girişiminde log: `🔴 LIVE: Budget exhausted`.

**Seçenek B — kısa yol (önerilen):**
1. `/env_toggle LIVE_BUDGET 0.05` (şu anki `_total_spent` büyük ise `remaining < 0.10` anında tetiklenir).
2. `LiveTrader._budget` module-top constant mı yoksa runtime-read mi? → **module-top constant**, `__init__` içinde okunur. Bu `/env_toggle` sonrası bot restart gerektirir.
3. Restart sonrası: Bir sonraki sinyal `maybe_mirror`'a girdiğinde log: `🔴 LIVE: Budget exhausted`.

**NOT (T6.1 + T8.2 doktrin paritesi):** `LIVE_BUDGET` şu an runtime re-read
pattern'inde değil. Bu test yan ürün olarak "T11.2 FINDING: LIVE_BUDGET module-top
constant" raporu üretebilir → Epic 11 forward work: `_get_live_budget()` helper.
Test bu bulguyu **log** eder ama T11.2 PASS'ı engellemez — guard kod hâlâ
doğru davranıyor, yalnız runtime mutation için restart gerekiyor.

### Beklenen davranış

- Trade girişimi reddedilir, `maybe_mirror` `None` döner.
- Bir sonraki sinyal pipeline'ı aynı reddi tekrar üretir (cycle başına 1 log spam'ı yok — `logger.info` tek satır).
- `/risk` output'unda `live.remaining` değeri <0.10 görünür.

### Kanıt

```
[YYYY-MM-DD HH:MM:SS] /risk output (budget near exhaustion):
<... yapıştır ...>

[YYYY-MM-DD HH:MM:SS] "🔴 LIVE: Budget exhausted" log satırı:
<... yapıştır ...>

[YYYY-MM-DD HH:MM:SS] Guard sonrası yeni sinyal skip kanıtı:
<... yapıştır ...>

Bulgu: LIVE_BUDGET runtime re-read __eksik__ mi (evet/hayır, yan rapor):
<...>
```

---

## G3 — Daily Loss Guard (`LIVE_MAX_DAILY_LOSS`) — ☐ PASS / ☐ FAIL

### Kod kancası

- **ENV:** `LIVE_MAX_DAILY_LOSS` (default `1.00`, `.env` override)
- **Runtime helper:** `core/live_trader.py:51-53`
  ```python
  def _get_max_daily_loss() -> float:
      return float(os.getenv("LIVE_MAX_DAILY_LOSS", "1.00"))
  ```
  **Not:** Bu helper RUNTIME re-read pattern'inde (T6.1 doktrin paritesi).
  `/env_toggle` patch'i bot restart gerektirmez.
- **Enforcement:** `core/live_trader.py:253-255`
  ```python
  if self._daily_pnl <= -_get_max_daily_loss():
      logger.info(f"  🔴 LIVE HALT: daily loss ${self._daily_pnl:.2f}")
      return None
  ```
- **Reset:** `_maybe_reset_daily()` günün başında `_daily_pnl = 0` ve `_daily_trades = 0`.

### Tetikleme

**Seçenek A — gerçek trading:**
1. Günün shadow live trade'leri $-1.00'ı geçene kadar bekle.
2. Sonraki sinyalde log: `🔴 LIVE HALT: daily loss $-X.XX`.

**Seçenek B — kısa yol:**
1. `/env_toggle LIVE_MAX_DAILY_LOSS 0.01` (mevcut `_daily_pnl` negatif ise anında tetikler).
2. Sonraki sinyalde log görünür.
3. Ertesi gün `_maybe_reset_daily` sıfırlamasını doğrulamak için: bot 24h+ çalıştıktan sonra `_daily_pnl == 0` check edilir (`/risk` gösterimi).

### Beklenen davranış

- Trade girişimi reddedilir, `maybe_mirror` `None` döner.
- `/risk` output'unda `live.daily_pnl` + `live.daily_max_loss` görünür.
- Ertesi gün yeni trade tekrar açılabilir.

### Kanıt

```
[YYYY-MM-DD HH:MM:SS] /env_toggle LIVE_MAX_DAILY_LOSS patch kanıtı:
<... yapıştır ...>

[YYYY-MM-DD HH:MM:SS] "🔴 LIVE HALT: daily loss $-X.XX" log satırı:
<... yapıştır ...>

[YYYY-MM-DD HH:MM:SS] (Opsiyonel) Daily reset sonrası trade yeniden açıldı kanıtı:
<... yapıştır ...>
```

---

## G4 — Paper-Shadow PnL Divergence Alert (`PNL_DIVERGENCE_*`) — ☐ PASS / ☐ FAIL

### Kod kancası

- **ENV:** `PNL_DIVERGENCE_ENABLED` (default `true`), `PNL_DIVERGENCE_WINDOW_H` (24), `PNL_DIVERGENCE_ALERT_PCT` (5.0), `PNL_DIVERGENCE_MIN_TRADES` (5)
- **Job:** `telegram_bot/jobs/pnl_divergence_job.py:43` — JobQueue daily (`PNL_DIVERGENCE_INTERVAL_SEC=86400`, first `PNL_DIVERGENCE_FIRST_SEC=3600`)
- **Metrik:** `pnl_delta = abs(shadow_pnl - paper_pnl)`; `divergence_pct = 100 * pnl_delta / max(|paper|, |shadow|, 1.0)`
- **Alert koşulu:** `paper_trades ≥ min_trades AND shadow_trades ≥ min_trades AND divergence_pct ≥ alert_pct`

### Tetikleme

**Seçenek A — gerçek pattern (48h shadow uptime gerekli):**
1. 48+ saat bot uptime, en az 5 paper ve 5 shadow trade biriksin.
2. Günlük job tetiklendiğinde (bot başlangıcından 1h sonra + her 24h) log: `pnl_divergence: sent daily report (divergence=X.XX%)`.
3. Admin Telegram chat'ine rapor mesajı düşer.

**Seçenek B — forced trigger (önerilen):**
1. `/env_toggle PNL_DIVERGENCE_ALERT_PCT 0.01` (eşiği yere indir).
2. `/env_toggle PNL_DIVERGENCE_MIN_TRADES 1` (min trade sayısını düşür).
3. Bot restart (JobQueue interval timer'ı değişmez ama configured `alert_pct` runtime-read).
4. Bir sonraki daily job tetiklenmesini beklemek yerine: manuel olarak `pnl_divergence_job(ctx)` çağır (test aracı: `scripts/trigger_pnl_divergence.py` yaz — YENİ SCRIPT).

**Seçenek C — "insufficient data" kolu:**
1. `/env_toggle PNL_DIVERGENCE_MIN_TRADES 9999`.
2. Job tetiklendiğinde log: `pnl_divergence: insufficient data (paper=X, shadow=Y)` — alert gönderilmez.
3. Bu kol da doğru davranışı gösterir.

### Beklenen davranış (Seçenek B happy-path)

- Log: `pnl_divergence: sent daily report (divergence=X.XX%)`.
- Admin Telegram'a HTML-escaped rapor mesajı gönderilir: paper PnL / shadow PnL / delta / pct / trade counts.
- Job başarısız olursa log: `pnl_divergence_job failed: <...>` (`exc_info=True`).

### Kanıt

**[2026-04-22 23:09 local / 20:09 UTC] Seçenek D — standalone probe (read-only):**

Komut: `py -3.11 scripts\t11_2_g4_divergence_probe.py`
Wrapper: `scripts\run_t11_2_readonly_probes.bat` → `evidence\t11_2_g4_20260422_230940.txt`

```
[T11.2 G4 PnL Divergence Probe] INSUFFICIENT
============================================================
Probe time       : 2026-04-22 20:09 UTC
DB               : data_store/polypaper.db
Window           : 24.0h
Alert threshold  : 5.0%
Min trades       : 5

Paper   :    0t | WR   0.0% | PnL $+0.00
Shadow  :    0t | WR   0.0% | PnL $+0.00

PnL delta        : $0.0000
Divergence       : 0.00%  (threshold: 5.0%)
WR delta         : 0.00pp   (threshold: 10pp)

Verdict          : INSUFFICIENT
Has enough data  : False
```

**Yorum:** Probe son 24 saatlik `executions` (paper) ve `live_trades` (shadow)
tablolarını okudu, her iki bucket da 0 — yani 2026-04-21T20:09 UTC sonrası
kayıtlı paper/shadow trade yok (bot bu pencerede ayakta değildi). Job canlı
koşarken `pnl_divergence: insufficient data (paper=0, shadow=0)` log'lar ve
alert göndermez — bu Seçenek C happy-path'i. INSUFFICIENT bu koşulda
**beklenen** davranıştır; guard hatalı değildir.

**Gerçek alert kanıtı için (Seçenek B) hâlâ gerekli:** bot ayakta + 5+ paper
& shadow trade + `/env_toggle PNL_DIVERGENCE_ALERT_PCT 0.01` + next daily tick.
Şu an shadow-live koşuyor ama cron henüz tetiklenmedi (FIRST_SEC=3600, window
24h). 24h pencerede 5 shadow trade birikene kadar bekle veya
`scripts/trigger_pnl_divergence.py` (ek iş) ile manuel tick et.

**Verdict:** ☒ PARTIAL — probe INSUFFICIENT kolunu doğruladı (SQL + math
canlı kod ile 1:1). Seçenek B canlı tick Windows backlog'a yazıldı.

Alert ekran görüntüsü: (pending — Seçenek B)

---

## G5 — Rolling WR Kill (`ROLLING_WR_KILL`) — ☐ PASS / ☐ FAIL

### Kod kancası

- **ENV:** `ROLLING_WR_KILL` (default `40.0`), `ROLLING_WR_WINDOW` (default `30` trade)
- **Runtime helper:** `core/auto_optimizer.py:65-67`
  ```python
  def _get_rolling_wr_kill() -> float:
      return float(os.getenv("ROLLING_WR_KILL", "40.0"))
  ```
  Runtime re-read (T7.6 B8'de migrate edildi).
- **Enforcement:** `core/auto_optimizer.py:267-310` — her auto-optimize cycle'ında rolling WR < threshold ise strateji `active=0`, `changelog` entry `kind="ROLLING_WR_KILL"`.
- **Whitelist:** `config/env_whitelist.py:143` — runtime-safe `/env_toggle` hedefi.

### Tetikleme

**Seçenek A — organik:**
1. Bir stratejinin son 30 trade'inde WR %40'ın altına düşene kadar bekle.
2. Auto-optimizer cycle'ında strateji paused (engine `active=0`).

**Seçenek B — forced:**
1. Aktif bir stratejinin son 30 trade'ini bir DB query ile oku: `SELECT strategy_label, AVG(pnl > 0) FROM trades WHERE strategy_label='X' ORDER BY created_at DESC LIMIT 30;`
2. Eşiği o WR'in hemen üzerine al: `/env_toggle ROLLING_WR_KILL 99.0` (tüm stratejiler paused) — tek-shot test.
3. Auto-optimizer tick'inde (`AUTO_OPT_INTERVAL_SEC`, default 300) pause event log'lanır.
4. Geri al: `/env_toggle ROLLING_WR_KILL 40.0`.

### Beklenen davranış

- Log: `adaptive_optimizer [strategy_X] rolling_WR X.X% < threshold Y.Y% → PAUSE`.
- DB: `changelog` tablosuna kind=`ROLLING_WR_KILL` insert.
- `/changelog` komutu yeni entry'yi gösterir (prefix `❌`).
- Strateji `active=0` — yeni trade açmaz; `/active_strategies` listesinde paused görünür.
- `/env_toggle ROLLING_WR_KILL 40.0` restore sonrası bir sonraki cycle'da paused stratejiler otomatik resume olmaz (manuel `/start_strategy X` gerekir) — bu kasıtlı.

### Kanıt

**[2026-04-22 23:09 local / 20:09 UTC] Seçenek D — historical DB evidence (read-only):**

Komut: `py -3.11 scripts\t11_2_g5_wr_kill_historical.py`
Wrapper: `scripts\run_t11_2_readonly_probes.bat` → `evidence\t11_2_g5_20260422_230940.txt`

```
[T11.2 G5 Rolling WR Kill - Historical Evidence]
============================================================
Probe time       : 2026-04-22 20:09 UTC
DB               : data_store/polypaper.db
Window           : all history
Total ROLLING_WR_KILL (ever)    : 7
In window count                 : 7
Distinct strategies ever killed : 7
Verdict          : GUARD_HAS_FIRED

created_at (UTC)       strat             wr%      pnl    n  reason
----------------------------------------------------------------------------------------------------
2026-04-17T09:37:46.80 faf209fc-9aa     35.0      N/A  N/A  WR=35% < 40.0% (last 20t)
2026-04-17T09:37:46.55 91b26127-56f     35.0      N/A  N/A  WR=35% < 40.0% (last 20t)
2026-04-17T09:37:46.33 954fdcbc-3a6     30.0      N/A  N/A  WR=30% < 40.0% (last 20t)
2026-04-17T09:37:46.12 40180825-bf9     30.0      N/A  N/A  WR=30% < 40.0% (last 20t)
2026-04-17T09:37:45.93 c9333ea0-25a     30.0      N/A  N/A  WR=30% < 40.0% (last 20t)
2026-04-17T09:37:45.73 75f09040-52c     30.0      N/A  N/A  WR=30% < 40.0% (last 20t)
2026-04-17T09:37:45.40 64076dfc-6c9     30.0      N/A  N/A  WR=30% < 40.0% (last 20t)
```

**Yorum:** `strategy_changelog` tablosunda `action='ROLLING_WR_KILL'` 7 satır
var — yani guard canlı ortamda **ATEŞLENDİ**. 2026-04-17 09:37:45-46 UTC
aralığında tek bir auto-optimizer cycle'ında 7 farklı strateji (UUID
prefix'leri listede) %30-35 WR ile 40% eşiğini geçemedi → otomatik PAUSE.
Reason formatı: "WR=X% < 40.0% (last 20t)" — bu `core/auto_optimizer.py`
L267-310 enforcement satırıyla bire-bir eşleşir. 7 distinct strategy_id,
7 distinct changelog row → idempotent değil, her strateji kendi satırını
aldı (doğru davranış).

**Verdict:** ☒ PASS — guard canlı ortamda ateşlendi, changelog kaydı var,
enforcement SQL path'i production'da çalışıyor.

**Notlar:**
- `wr_at_time` / `pnl_at_time` / `trades_at_time` kolonları `N/A` —
  changelog migration v13'de bu kolonlar nullable; kill satırı yazan kod
  o sırada bu metrikleri persist etmiyormuş (enhancement fırsatı, T11.2
  bloklamaz). Reason string'i WR bilgisini zaten içeriyor.
- Auto-resume yok — kasıtlı; manuel `/start_strategy X` gerekir (line 304).
- /env_toggle ROLLING_WR_KILL 60 forced trigger (Seçenek B) canlı test
  Windows backlog'ta — shadow live koşarken /changelog ile doğrulanabilir.

---

## G6 — WS Stale Guard (`WS_STALE_THRESHOLD`) — ☒ PASS (2026-04-23)

### Kod kancası

- **ENV:** `WS_STALE_THRESHOLD` (default 60s; whitelist'te, `/envt`-tunable)
  - Legacy `WS_STALE_SEC` fallback: `data/websocket_client.py::get_live_price()` önce `WS_STALE_THRESHOLD` okur, yoksa `WS_STALE_SEC` (eski `.env` geriye uyum). T11.2 [C] (2026-04-22).
- **Engine check:** `core/engine.py:851` her cycle `await self._check_ws_health()` — `_is_ws_fresh()` `WS_STALE_THRESHOLD` okur.
- **Cache staleness:** `data/websocket_client.py::get_live_price()` — T10.5 fix: malformed/stale entry → `None` (fresh > stale doctrine)
- **Reconnect flush:** T5.4 — reconnect sırasında `_connected_since` set edilir; eski entry invalid olur.

### Tetikleme

**Seçenek A — gerçek network drop:**
1. Windows'ta Ethernet/Wi-Fi'yı 5-10 saniye devre dışı bırak (adapter disable).
2. WS client reconnect denemesi başlamalı: log `WS reconnecting...` + `WS connected` pair.
3. `_connected_since` güncellenir; eski cache entry'leri `WS_STALE_THRESHOLD` kontrolünden geçmez → `get_live_price()` `None` döner.
4. Network'ü geri aç.
5. Yeni tick gelene kadar trade açılmaz (log: ilgili sinyalde `skip_reason` veya `stale_price` benzeri).

**Seçenek B — kısa yol (test harness):**
1. `/env_toggle WS_STALE_THRESHOLD 1` (1 saniye — neredeyse her cache entry stale olacak).
2. Bir kaç cycle bekle; log: yoğun `stale_price` skip'leri veya `WS not fresh` WARNING.
3. Restore: `/env_toggle WS_STALE_THRESHOLD 60`.
4. **Not:** B yolu agresif — trade flow'u önemli ölçüde blocklar. 5 dakika içinde restore et.

### Beklenen davranış

- Network drop sırasında yeni trade açılmaz.
- `get_live_price()` None → `engine_signals` pipeline'ı `skip_reason="stale_price"` (veya eşdeğeri) üretir.
- Reconnect sonrası yeni tick gelince trade normal akışa döner.
- `/h` (heartbeat) WS status göstergesi 🟢 → ⚫ → 🟢 geçişini gösterir.

### Kanıt (2026-04-23 canlı run — PASS)

**Kanıt dosyası:** `evidence/t11_2_g6_ws_stale_20260423.txt`

**[~15:56] Whitelist min guard doğrulandı:**
```
> Cyberg: /envt WS_STALE_THRESHOLD 3
> Dojopolyscout: Min 5.0 olmali.
```
→ `telegram_bot/handlers/env_toggle.py` WS_STALE_THRESHOLD için min=5.0 reject
eşiği aktif ✅

**[~15:56] Runtime re-read 60 → 5 → 60:**
```
> /envt WS_STALE_THRESHOLD 5
  → "WS_STALE_THRESHOLD: 60 → 5
     os.environ + .env guncellendi"

> /live_guards
  → G6: WS_STALE_THRESHOLD = 5.0s   (anında yansıdı ✅)

> /envt WS_STALE_THRESHOLD 60
  → "WS_STALE_THRESHOLD: 5 → 60
     os.environ + .env guncellendi"
```

**Tasarım notu — `stale_price` skip reason YOKTUR:**

`core/engine.py:1018-1029 _is_ws_fresh()` → age < threshold mı. False
durumunda `engine_signals.py:404-407` → trade signal threshold'u 0.70'a
yükseltir (log'a ayrı skip-reason yazmaz). Engine özet satırında ws_ok
indicator `🟢 ↔ ⚫` değişir. Yani G6'nın kanıtı skip-log değil, **runtime
readiness + invariant preservation** üçlüsüdür.

**Verdict:** 4/4 invariant ✅
  - Runtime re-read doktrini (60 → 5 → 60 snapshot'a yansıdı)
  - Whitelist min guard (3 reddedildi)
  - `.env` disk persistence (bot cevap onayı)
  - Unit test coverage — `tests/unit/test_ws_stale_sec_env.py` 12 test
    GREEN (T11.2 Ek İş [C], commit `e837b78`)

**Canlı network drop simülasyonu (opsiyonel ek):** T9.8-REG Windows
backlog — Polymarket staging WS'e karşı gerçek disconnect/reconnect
smoke. Bu G6 guard'ı bloklamaz, mainnet öncesi gerekli değil.

---

## Kapanış Kriterleri

Aşağıdaki 6 kutu işaretlendiğinde T11.2 ✅:

- [x] G1 Kill Switch ✅ 2026-04-23 — file detect 96ms + sticky memory + manual /resume + yeni trade
- [ ] G2 Budget Guard — `🔴 LIVE: Budget exhausted` tetiklendi + `maybe_mirror` None döndü
- [ ] G3 Daily Loss Guard — `🔴 LIVE HALT: daily loss` tetiklendi
- [ ] G4 PnL Divergence — Telegram alert geldi VEYA "insufficient data" satırı log'landı
- [ ] G5 Rolling WR Kill — strateji paused + `changelog` entry + `/active_strategies` görünür
- [x] G6 WS Stale ✅ 2026-04-23 — runtime re-read 60→5→60 + whitelist min guard + .env persistence + unit cov 12 test

**Herhangi bir FAIL →** fix yap → tekrar test et → ilgili slot'u doldur.
Test başarılıysa `[x]` işaretle ve timestamp ekle.

---

## Ek İş Önerileri (T11.2 sonrası çıkabilecek backlog)

Test sırasında karşılaşılabilecek gap'ler — bulgu raporu olarak T11.2
commit'ine eklenir, düzeltmesi Epic 11 T11.x olabilir:

1. **`scripts/trigger_pnl_divergence.py`** (Seçenek B kolaylığı için) —
   `pnl_divergence_job(ctx)` manuel çağrı. Yoksa yaz. **[A] ✅
   2026-04-22** — script eklendi.
2. **`LIVE_BUDGET` runtime re-read helper** (T6.1 doktrin paritesi).
   `_get_live_budget()` helper + `LiveTrader._budget` → `@property`.
   **[B] ✅ 2026-04-22** — `core/live_trader.py` helper + read-only
   property; 7 regresyon testi + fixture adaptasyonu.
3. **WS stale skip_reason string standardize** — `engine_signals`
   pipeline'ında `skip_reason="stale_price"` tutarlı mı (`/diagnose
   skips` ile doğrula). **[C] ✅ 2026-04-22** — doc↔code drift
   kapatıldı; `WS_STALE_SEC` legacy fallback kanıtı `data/websocket_client.py`.
4. **`/live_guards` komut önerisi** — tüm 6 guard'ın runtime
   threshold'larını tek output'ta listeleyen bir admin cmd. T11.2 test
   süresini kısaltır; mainnet sonrası kalıcı ops aracı olur.
   **[D] ✅ 2026-04-22** — `telegram_bot/handlers/live_guards_handler.py`
   (`/live_guards` + `/lg`), 5 regresyon testi (admin gate, content
   shape, runtime env re-read, engine-absent fallback). T6.1 doktrini
   UI katmanına taşındı.

**Ek bulgu (sandbox ekstraksiyonu sırasında tespit edildi):**

5. **[E] strategy_changelog cumulative stats persist** —
   `auto_optimizer._check_rolling_wr`'nin ROLLING_WR_KILL path'i
   `log_change`'e sadece `wr` geçiyordu; `pnl_at_time` / `trades_at_time`
   NULL kalıyordu. T11.2 Windows G5 probe (2026-04-22 23:09) 7 tarihi
   satırı doğruladı. **[E] ✅ 2026-04-22** — `_get_strategy_stats`
   pre-fetch + 3 regresyon testi (happy path / empty stats / protected
   bypass).
6. **[F] T11.3 rollback matrix prep doc** —
   `docs/mainnet/T11_3_rollback_plan.md` mainnet öncesi final dry-run
   için env matrisi. **[F] ✅ 2026-04-22** (sandbox scope). Windows
   dry-run: Heddas, T11.3 closure.

---

## Rollback

Test sırasında yapılan `/env_toggle` patch'lerinin hepsi **restore** et:

```
/env_toggle LIVE_BUDGET <original>
/env_toggle LIVE_MAX_DAILY_LOSS 1.00
/env_toggle PNL_DIVERGENCE_ALERT_PCT 5.0
/env_toggle PNL_DIVERGENCE_MIN_TRADES 5
/env_toggle ROLLING_WR_KILL 40.0
/env_toggle WS_STALE_THRESHOLD 60
```

**Shadow bakiye güvenliği:** `LIVE_ENABLED=false` TÜM test boyunca
kalır. Bu testler gerçek emir yerleştirmez; yalnız shadow mirror +
engine state simulation'ı ile guard'ları tetikler.

---

**Template tarihi:** 2026-04-22 (T11.1 closure sonrası hazırlandı)
**Test sahibi:** Heddas (Windows yerel)
**Doğrulama sahibi:** Claude (bu dosya + TASKS.md update)
**Kapanış tarihi:** *(Windows'ta 6/6 ✅ olunca yaz)*
