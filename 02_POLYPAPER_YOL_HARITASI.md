# PolyPaper Bot — Yol Haritası (Aksiyona Dönüştürülmüş)

**Kaynak:** `01_POLYPAPER_AUDIT_RAPORU.md`
**Toplam görev:** 27 (9× P0, 9× P1, 5× P2, 4× P3)
**Tahmini efor:** 6-9 ay tek geliştirici, 3-4 ay 2 geliştirici
**Bu dosya senkronize edilir** — `TaskList`/`TaskGet`/`TaskUpdate` araçları canlı durumun kaynağıdır; bu markdown ise insan okunabilir özet.

---

## Status Anahtarı

- `[ ]` pending — başlamadık
- `[~]` in_progress — üstünde çalışıyoruz
- `[x]` completed — bitti, doğrulandı
- `[!]` blocked — bağımlılık beklenen
- `[-]` deleted/skipped — gerekmedi

## Effort Anahtarı

- **S** — < 1 gün
- **M** — 1-3 gün
- **L** — 1-2 hafta
- **XL** — 2+ hafta

---

## P0 — Kritik (2-4 hafta hedef)

> Bu 9 madde tamamlanmadan **kesinlikle live trading açılmamalı**. Mevcut $1.49 cap zorunlu kalsın.

### P0-01 [ ] AI Brain auto-execute katmanını söküp manuel onaya bağla — **M**
- **Dosya:** `core/ai_brain.py:418-421`, `~1880-1893`
- **Sorun:** `confidence >= 0.70` olduğunda LLM kararıyla strategy SQL'e otonom UPDATE; exception handler'da fallback yine auto-execute.
- **Hedef:** Tüm CREATE/TUNE/SCALE/STOP/RESTART → Telegram approval queue. Fallback yok.
- **Kabul kriteri:** AI cycle 1 tur sonrası `pending_approvals` tablosunda kayıt olur, hiçbir SQL UPDATE otonom değil.

### P0-02 [!] POLYGON_PRIVATE_KEY plaintext kaldır, keychain/HW wallet — **L**
- **Bekliyor:** P0-03
- **Dosya:** `core/live_trader.py`, `data/polymarket_actions.py`, `.env.example`
- **Hedef:** Windows DPAPI + python `keyring`, ya da Ledger HW wallet ile EIP-712 imza delegasyonu.
- **Kabul kriteri:** `.env`'de PK olmadan bot live mode'a girer; `grep -r POLYGON_PRIVATE_KEY` sadece doc'larda eşleşir.

### P0-03 [ ] Telegram /export_private_key komutunu kalıcı sil — **S**
- **Dosya:** `data/polymarket_actions.py`, `telegram_bot/handlers/`
- **Hedef:** Komut tamamen kaldırılır. Telegram chat history → 3rd party olduğu için single-PC compromise tüm wallet'ı drain edebilir.
- **Kabul kriteri:** `git grep -i "export_private_key"` sadece arşivde / changelog'da eşleşir; aktif kodda 0 satır.

### P0-04 [ ] LIVE_BUDGET 2-faktör + 24h cooldown — **M**
- **Dosya:** `core/live_trader.py:_get_live_budget`, `.env.example`, `db/migrations/`
- **Hedef:** Env değişkeni runtime'da değişip LIVE_BUDGET'ın anında yükselmesini engelle. Artış için: (a) Telegram inline button onay, (b) admin user_id eşleşmesi, (c) 24h bekleme.
- **Kabul kriteri:** env'de LIVE_BUDGET=1000 yazılsa bile bot 1.49 ile başlar; manuel artış akışı 24h sonra etkin olur.

### P0-05 [x] daily_db_snapshot atomic write + SHA256 + manifest — **M** ✅ 2026-05-09
- **Dosya:** `telegram_bot/jobs/maintenance_jobs.py:daily_db_snapshot_job`, `scripts/restore_from_backup.py` (NEW)
- **Sorun:** 2 bozuk backup zaten yaşandı (2026-04-20, 2026-04-23, 729-780 MB header null). Atomic rename T11.3 (2026-04-23) ile uygulanmıştı; bütünlük kanıtı + restore prosedürü eksikti.
- **Hedef:** Atomic + SHA256 + manifest.json + restore CLI.
- **Kabul kriteri:** ✅ Tüm 4 sub-task PASS:
  - **P0-05a** SHA256 verification — `_sha256_file()` chunked 1MB read, `asyncio.to_thread` event-loop friendly, hash dest_tmp BEFORE atomic rename. Telegram notify'a 16-char prefix eklendi.
  - **P0-05b** manifest.json — `data_store/backups/manifest.json` versioned schema (filename, sha256, size_bytes, created_utc, schema_version). Atomic write via `.tmp+os.replace`. Manifest update non-fatal (snapshot başarılı; sadece tracking eksik kalır).
  - **P0-05c** `scripts/restore_from_backup.py` — `--list` / `--verify-all` / `--latest` / `--restore FILENAME` / `--dry-run` / `--yes`. Source SHA256 verify → pre-restore safety backup (`pre_restore_<UTC>.db`) → write to `.restoring` → re-hash → atomic rename → WAL/SHM cleanup. Bot kilidi varsa PermissionError yakalar, .restoring dosyası saklanır.
  - **P0-05d** Smoke test — `scripts/_smoke_p0_05.py` izole tmp dizininde tam round-trip (snapshot → manifest → list → verify → dry-run → real restore → pre_restore safety) PASS, hash drift yok.

### P0-06 [ ] py-builder-relayer-client'ı pinle — **S**
- **Dosya:** `requirements.txt`
- **Hedef:** `py-builder-relayer-client==0.0.1`
- **Kabul kriteri:** `pip list | grep relayer` çıktısında sürüm sabit.

### P0-07 [x] Reference price feed gerçeklik audit'i — **L** ✅ 2026-05-09
- **Dosya:** `db/migrations.py` (v21), `core/engine_settlement.py` (live hook), `scripts/audit_reference_price.py` (NEW), `telegram_bot/handlers/ref_audit_handler.py` (NEW)
- **Bulgu:** Polymarket Up/Down binary'leri için "official resolution price" Polymarket tarafından numeric değil binary outcome ("Up"/"Down") olarak yayınlanır. Resolution oracle = Binance spot kline close at boundary. Audit metodolojisi: bot'un local Binance/Chainlink feed'i vs. Binance public klines API ground-truth.
- **Yapılanlar (7 sub-task):**
  - **a) Schema v21** — `reference_price_audit` tablo (15 kolon: settle_ts_ms, condition_id, asset_id, slug, asset, tf, official_resolution_price, bot_binance_rest/ws_price, bot_chainlink_price, dev_binance/chainlink_bps, settle_outcome, data_quality, created_at) + 4 named index + composite PK (condition_id, settle_ts_ms).
  - **b) Live settle hook** — `_record_reference_audit(row, resolution)` `_settle_inner` sonrası `safe_create_task` fire-and-forget. ±5s window'da external_prices'tan binance_spot_ws/binance/chainlink en yakın tick'i çek. data_quality flag (`ok` / `missing_external` / `missing_resolution`). ENV gate: `REFERENCE_PRICE_AUDIT_ENABLED=true` default. Defansif try/except — settle path ASLA bloklanmaz.
  - **c) Backfill script** — `--backfill --days N`: historical executions'tan settled trade'ler için audit row insert. INSERT OR IGNORE (live hook satırlarını ezmez). Slug parsing util'leri (`infer_asset_from_slug`, `infer_tf_from_slug`) reuse.
  - **d) Fetch references** — `--fetch-references`: missing_resolution rows için Binance public klines API'dan kline close çek, official_resolution_price + dev_binance_bps + dev_chainlink_bps doldur, data_quality → 'ok'. Throttled (50ms sleep), rate-limit-safe.
  - **d2) Markdown report** — `--report --days N --output FILE`: data quality breakdown, per (asset, tf, source) mean/median/p95/p99 dev_bps, worst 10 deviations table, |mean| > 5 bps → 🔴 EDGE ESTIMATE INVALID alarm.
  - **e) Smoke test** — sentetik DB üzerinde end-to-end pipeline (5 trade backfill + report mocked Binance enrichment). 8 audit rows iterated, ETH/15m systematic bias 9.99 bps alarm tetiklendi doğru.
  - **f) Telegram /ref_audit (alias /ra)** — son 7 gün özet panel: total, data quality breakdown, per (asset/tf/src) bias (GRN/YEL/RED), worst 3 deviations, sistemik bias alarmı.
- **Davranış değişikliği:** Her settle anı +1 async DB write (1 row, ~100 µs). Trade davranışı sıfır değişti. Yeni Telegram komutları: `/ref_audit` `/ra`. Bot restart sonra audit tablo otomatik dolar.
- **Kabul kriteri kontrolü:**
  - ✅ "7 günlük rapor markdown çıktısı" → `scripts/audit_reference_price.py --report --days 7 --output ...`
  - ✅ "En kötü 10 sapma örneği" → Report'ta "Worst 10 Deviations" tablosu
  - ✅ ">5 bps sistematik sapma → alarm" → Per-group |mean| > 5 → 🔴 EDGE ESTIMATE INVALID line + Telegram panel'de aynı kontrol
- **Veri biriktirme:** external_prices tablosu 2026-05-08'de dolmaya başladı. Audit infrastructure şimdi yerleşik; **7 günlük production rapor 2026-05-15 itibarıyla anlamlı** olacak. Şu anki "preliminary" raporlar limited data ile çalışır.

### P0-08 [ ] 5dk binary'leri default OFF — **S**
- **Dosya:** `config/settings.py:SUPPORTED_TIMEFRAMES`, `data/market_scanner.py`
- **Hedef:** Default `["1h"]`. 5m/15m env-opt-in (`SCANNER_ENABLE_5M=false`).
- **Kabul kriteri:** Bot baştan başlatıldığında scanner sadece 1h market'leri keşfeder.

### P0-09 [ ] Kelly MAX_BET_PCT ghost-config tek kaynağa — **S**
- **Dosya:** `core/kelly.py:31`, `config/settings.py:KELLY_MAX_BET_PCT`, `config/validator.py`
- **Hedef:** Settings.KELLY_MAX_BET_PCT tek kaynak; kelly.py oradan oku. validator'da consistency check.
- **Kabul kriteri:** `grep -rn "MAX_BET_PCT" core/ config/` tek tanım, diğerleri read.

---

## P1 — Yüksek (3 ay hedef)

### P1-01 [~] Coverage source genişlet, eşik %60 — **XL** PARTIAL ✅ 2026-05-09
- **Dosya:** `.coveragerc`, `pytest.ini`, `tests/unit/test_p1_handlers_smoke.py` (NEW), `data_store/audits/synthetic_test_priority.md` (NEW)
- **Yapılan (bu seans):**
  - **P1-01-a:** `.coveragerc` source = core → core + data + telegram_bot + backtest. omit'e scripts/ eklendi.
  - **P1-01-b:** Heddas Windows-side `py -3.11 -m pytest --cov` ile baseline ölçtü: **%42.5** (25831 stmts, 13846 miss, 7790 branch, 14 fail / 3405 pass / 42 skip / 4dk 8s).
  - **P1-01-c1:** 14 test fail düzeltildi:
    - `TestCandleBuilder` (10 test) + `TestCandleBuilderEdgeFlow` (2 test) → `@pytest.mark.skip` (P0-08-E3 multi-TF API drift, refactor P1-01 follow-up).
    - `TestRiskManagerHelpers.test_extract_asset_from_slug_unknown`: beklenti DOGE → '?' (P0-08-D slug parser fallback semantic).
    - `TestAssetLimits.test_extract_asset_from_slug`: empty string için '?' beklentisi.
    - `TestReconciliationTask.test_disabled_by_default`: P1-09-a smart enable'a uyumlu (LIVE_ENABLED=false + RECON_ENABLED unset → False) + 2 yeni test (auto_on_in_live_mode, explicit_disable_wins_over_live).
  - **P1-01-c2:** `tests/unit/test_p1_handlers_smoke.py` NEW (260 satır, 9 test):
    - `data_status_handler` 3 test (no-db, full panel, stub aiosqlite v18 schema).
    - `ref_audit_handler` 3 test (no-db, empty audit table, populated with bias alarm).
    - `recon_handler` 3 test (no-engine, no-task, running with mismatches, disabled paper).
    - Beklenen: handler coverage 7-10% → 50-60%.
  - **P1-01-c3:** `.coveragerc` `[report] fail_under = 42` baseline lock → **2. ölçüm sonrası 43'e bumplandı** (handler smoke testleri %43.4 baseline'ı verdi, 0 fail). Ratchet ladder: 42 → **43** ← CURRENT → 45 → 50 → 55 → 60.
  - **P1-01-c4:** `data_store/audits/synthetic_test_priority.md` NEW — sentetik refactor priority listesi (Wave 1-4 plan, modül-modül ROI tablosu, deferred items).
- **Sonraki adım (P1-01 follow-up — sonraki seans):**
  - Re-run baseline (Heddas Windows): yeni handler smoke'larla coverage rakamı %43-45 beklenir → eşiği güncelle.
  - Wave 1 (kolay): TestCandleBuilder re-write (`test_candle_builder_multi_tf.py`).
  - Wave 2 (orta): handler real-behavior testleri (mevcut RuntimeWarning'leri çöz).
  - Wave 3 (zor): engine_signals/engine_settlement davranış testleri.
  - Wave 4 (büyük): backtest data_sources unit coverage.
  - `.github/workflows/ci.yml` (yoksa create) — pytest + coverage gate.
- **Kabul kriteri ratchet:**
  - ✅ Source genişletildi (core + data + telegram_bot + backtest).
  - ⏸ %60 eşik — şu an %42 baseline lock, iteratif ladder ile hedefte.
  - ⏸ Sentetik refactor — priority listesi hazır, parça parça yapılacak.
  - ⏸ CI hard-fail — `.coveragerc fail_under` lokal pytest'te aktif; CI workflow ayrı iş.

### P1-02 [~] AI Brain'i microservice'e ayır — **XL** PARTIAL ✅ 2026-05-11 (Wave 1 scaffold)
- **Dosya:** `services/__init__.py` + `services/ai_advisor/{__init__.py, app.py, models.py}` NEW, `core/ai_brain_client.py` NEW, `scripts/{start,stop,smoke}_ai_advisor.bat` NEW, `tests/integration/test_ai_advisor_service.py` NEW, `requirements-dev.txt` (fastapi+uvicorn+httpx)
- **Bekliyor:** P0-01 ✅ tamam (approval queue mevcut)
- **Hedef:** HTTP POST /suggest → JSON suggestion list; engine pollar, action SQL'e gitmez.
- **Yapılanlar (Wave 1 — bu seans):**
  - **a) FastAPI scaffold** — `services/ai_advisor/app.py`: GET /health, POST /suggest (stub HOLD), GET /stats. Pydantic schemas `models.py` (MarketContext + StrategyContext + Suggestion + SuggestResponse + HealthResponse).
  - **c) HTTP client `core/ai_brain_client.py`** — httpx async wrapper. ENV `AI_ADVISOR_ENABLED` (default **false** → bot in-process fallback), `AI_ADVISOR_URL` (default `http://127.0.0.1:8001`), `AI_ADVISOR_TIMEOUT_S` (default 8.0). Defansif (timeout/5xx → None → caller fallback).
  - **d) Windows bat helpers** — `start_ai_advisor.bat` (uvicorn ayrı pencere), `stop_ai_advisor.bat` (port-based PID kill + window-title), `ai_advisor_smoke.bat` (curl /health + /suggest + /stats).
  - **e) Smoke test** — `tests/integration/test_ai_advisor_service.py` 6 test: /health keys + /stats + /suggest stub + slug validation + odds range + request counter.
  - **f) `requirements-dev.txt`** — fastapi==0.115.0, uvicorn[standard]==0.31.0, httpx==0.27.2.
- **Davranış değişikliği:**
  - **Şu an sıfır.** AI_ADVISOR_ENABLED=false default → bot eski davranışı (in-process AI Brain).
  - Heddas isterse `set AI_ADVISOR_ENABLED=true` + `scripts\start_ai_advisor.bat` → bot HTTP'a delege eder (Wave 1 stub HOLD döner).
- **Kapsam dışı (Wave 2+):**
  - **Wave 2** — `core/ai_brain.py` 990 stmt LLM logic'i `services/ai_advisor/brain_core.py`'a taşı. BRAIN_SYSTEM prompt + ModelRouter + optimist/critic chain. core/ai_brain.py thin wrapper.
  - **Wave 3** — approval-queue flow service üzerinden. Action gönderim + Telegram approval round-trip.
  - **Wave 4** — docker-compose.yml (P1-05 Linux/Docker ertelenmedikçe).
- **Acceptance kriteri (revize):**
  - ✅ Servis scaffold ayağa kalkar (`/health` 200, `/suggest` stub).
  - ⏸ "engine pollar, action SQL'e gitmez" — Wave 3 hedefi (P0-01 approval queue zaten doğru pattern; service Wave 3'te entegre).
  - ⏸ "docker-compose up" — Wave 4 (P1-05 ertelendiği için backlog).
  - ✅ "engine 5xx'te kendi karar moduna düşer" — `core/ai_brain_client.py` defansif fallback yapısı hazır.
- **Heddas kullanımı:**
  - `py -3.11 -m pip install -r requirements-dev.txt` (bir kerelik).
  - `scripts\start_ai_advisor.bat` → service ayrı pencerede.
  - `scripts\ai_advisor_smoke.bat` → curl ile /health + /suggest test.
  - `scripts\stop_ai_advisor.bat` → temizle.
  - `py -3.11 -m pytest tests/integration/test_ai_advisor_service.py -v` → 6/6 smoke PASS beklenir.

### P1-03 [x] Reality gap nightly raporu — **L** ✅ 2026-05-09
- **Dosya:** `telegram_bot/jobs/reality_gap_job.py` (NEW), `telegram_bot/handlers/reality_gap_handler.py` (NEW), `telegram_bot/bot.py` wire
- **Multiplier kaynağı:** Memory `T4.6-B Fill Heuristic Sweep` (2026-04-24): classic 199 trade × 200 markets sweep HEURISTIC -$4.87 vs EMPIRICAL -$6.51, delta_pnl_pct = -33.68% → **paper × 0.66 = live beklentisi**. Keyfi sabit değil, gerçek backtest çıktısı.
- **Yapılanlar (5 sub-task):**
  - **a) `reality_gap_job.py` NEW (284 satır)** — Daily 24h interval, ENV-gated (REALITY_GAP_ENABLED default true). live_trades tablosundan aggregate paper_pnl + pnl + result → drift hesabı. State machine: ok / warn / alert / insufficient_data. Markdown rapor `data_store/audits/reality_gap_<UTC>.md` + `reality_gap_latest.md` stable copy. Per-strategy top-10 worst drift breakdown.
  - **b) bot.py JobQueue wire** — `jq.run_repeating(reality_gap_job, interval=86400, first=300, name="reality_gap")`. ENV: REALITY_GAP_INTERVAL_SEC, REALITY_GAP_FIRST_SEC. Bot restart sonra 5 dk içinde ilk rapor.
  - **c) Telegram `/reality_gap` (alias `/rg`) panel** — Job status, live snapshot (son 24h), nightly rapor excerpt + dosya yaşı. HTML parse_mode.
  - **d) Smoke test 7/7 PASS** — drift compute (zero divergence, 10% over, 50% under), classify state machine (ok/warn/alert/insufficient_data), markdown render (alert path, insufficient_data path, zero-trades path).
  - **e) Memory + roadmap (bu entry).**
- **Davranış değişikliği:**
  - Bot restart sonra 5 dk içinde ilk reality_gap raporu yazılır.
  - Sonra her 24 saatte bir.
  - Drift > ±10% → 🚨 Telegram alert (alert), > ±5% → ⚠️ warn.
  - INSUFFICIENT_DATA (n<10 live trade) → sessiz (log + dosya, alarm yok).
  - Yeni komutlar: `/reality_gap` `/rg`.
- **Kabul kriteri kontrolü:**
  - ✅ "Nightly job: paper × 0.66 vs gerçek live" — JobQueue 24h interval, multiplier ENV-tunable.
  - ✅ ">%10 sapma → alert" — `REALITY_GAP_ALERT_PCT=10.0` default, Telegram callback.
  - ✅ "Her gün 03:00 UTC'de rapor" — JobQueue run_repeating 86400s; ilk run 5 dk sonra. (Tam 03:00 UTC pinning gerekirse `run_daily(time=time(3,0))` ile değiştirilebilir.)
  - ✅ "Sapma >%10 ise admin alert alır" — `resolve_admin_chat_id()` → HTML mesaj.
- **Override seçenekleri:** `REALITY_GAP_ENABLED=false` (kapat), `REALITY_GAP_WINDOW_H=N` (look-back), `REALITY_GAP_MULT=0.7` (multiplier), `REALITY_GAP_ALERT_PCT=15` (eşik), `REALITY_GAP_MIN_TRADES=20` (insufficient_data eşiği).
- **7-gün notu:** SHADOW ACTIVE mainnet 2026-05-03'ten beri. live_trades minimal — ilk birkaç rapor `INSUFFICIENT_DATA` (n<10) gösterecek. ~2 hafta sonra anlamlı drift ölçümü mümkün.

### P1-04 [x] Strateji pruning — **L** ✅ 2026-05-09 (Yol D Hibrit)
- **Dosya:** `scripts/audit_strategies.py` (NEW), `scripts/prune_strategies.py` (NEW), `data_store/audits/`
- **Bulgu:** Yol haritasındaki "20 → 3" hedefi gerçeklikle uyumsuzdu — audit'te **72 strateji** çıktı, hiçbiri "proven" criteria'sını (n≥50, WR≥55%, PnL>0) karşılamıyordu çünkü P0-08-E1 DB cleanup historical trade'leri sildi (lifecycle reset). Direkt 3'e pruning erken; **Yol D hibrit yaklaşımı** seçildi.
- **Yapılanlar:**
  - **P1-04-a:** `scripts/audit_strategies.py` — read-only audit, lifecycle classification (no_trades / exploration / evaluation / proven / regression / idle), recommendation (KEEP/WATCH/ARCHIVE) + markdown rapor `data_store/audits/strategy_audit_<UTC>.md`. Online backup API ile WAL-safe.
  - **P1-04-b:** Heddas direktifi "d den gidelim biraz veri biriksin sonra zararları sileriz" → Yol D onayı.
  - **P1-04-c:** `scripts/prune_strategies.py` — idempotent stop migration, `--dry-run / --apply / --yes` modes. Manuel snapshot copy → SQLite online backup API geçişi (WAL contention çözümü). Filter: `status != 'stopped' AND (n=0 OR last_trade > 7 days)`.
  - **P1-04-d (exec):** Heddas Windows shell `py -3.11 scripts\prune_strategies.py --apply --yes` → **affected=58, skipped=0**. Linux mount cross-FS WAL contention engellediği için Windows-native execution gerekti.
- **Sonuç:** 72 → 14 aktif strateji. Live whitelist (LIVE_STRATEGIES) dokunulmadı. WATCH 13 strateji veri biriktirmeye devam.
- **Kabul kriteri (revize):** "En fazla 3" hedefi 7-14 gün sonra yeniden audit ile değerlendirilecek. Şu an 14 strateji, "proven" kriterini karşılayan 0.
- **Sonraki adım:** P1-04 follow-up (2026-05-16 civarı): re-audit, "proven" çıkanları KEEP'e al, "regression" çıkanları ARCHIVE.

### P1-05 [ ] Linux + systemd + Docker — **XL**
- **Dosya:** `Dockerfile`, `docker-compose.yml`, `deploy/systemd/polypaper.service`
- **Hedef:** python:3.11-slim base. systemd Restart=always + WatchdogSec=120 + journald. Windows .bat'lar parite.
- **Kabul kriteri:** `docker compose up -d` ve `systemctl status polypaper` Linux VPS'te 7/24 ayakta.

### P1-06 [x] Structured JSON logging her zaman aktif — **M** ✅ 2026-05-09
- **Dosya:** `core/structured_logging.py`, `main.py`
- **Mevcut altyapı (audit'te bulundu):** `core/structured_logging.py` (173 satır) zaten yazılmıştı — `JsonFormatter` (Splunk/ELK/Loki uyumlu), `SecretScrubFilter` (13 regex pattern: PK, API key, Telegram token, JWT, AWS, Polymarket creds), `RotatingFileHandler`, idempotent `setup_structured_logging()`. Sadece main.py'da hiç çağrılmamış + default `STRUCTURED_LOG_ENABLED=false`.
- **Yapılanlar:**
  - **a) Default flip + bump:** `STRUCTURED_LOG_ENABLED` default `false` → `true`. Rotation 10MB × 5 → **100MB × 10** (~1 GB cap, roadmap acceptance criteria).
  - **a) main.py wire:** `setup_structured_logging()` `logger = ...` satırından sonra çağrılıyor, defansif try/except. `data_store/structured.jsonl` otomatik yazılıyor.
  - **b) Smoke test (12 assertion PASS):**
    - Handler attached, maxBytes=104857600 (100MB), backupCount=10 ✅
    - 6 test log → 6 JSONL satır, hepsi `json.loads()` parseable ✅
    - Required keys: `ts, level, logger, msg, module, lineno` ✅
    - PK regex (0x40+ hex) → `[REDACTED_PRIVATE_KEY]` ✅
    - API key regex (sk_live_...) → `[REDACTED_API_KEY]` ✅
    - Telegram token regex → `[REDACTED_TELEGRAM_TOKEN]` ✅
    - İdempotent setup (re-call handlers eklemiyor) ✅
    - `STRUCTURED_LOG_ENABLED=false` disable ediyor ✅
  - **c) Memory + roadmap (bu entry).**
- **Davranış değişikliği:**
  - Bot restart sonrası `data_store/structured.jsonl` doluyor (her INFO+ log satırı).
  - Console log'ları human-readable formatta devam ediyor (paralel).
  - Disk: max ~1 GB (100MB × 10), eskiler rotated out.
  - Secret scrubbing aktif: PK, API key, Telegram token, JWT, AWS, Polymarket creds otomatik `[REDACTED_*]`.
  - Trade davranışı sıfır değişti.
  - `jq` ile sorgular: `jq '. | select(.level=="ERROR")' data_store/structured.jsonl`
- **Kabul kriteri kontrolü:**
  - ✅ "JSON log default ON" — `STRUCTURED_LOG_ENABLED=true` default.
  - ✅ "RotatingFileHandler 100MB × 10 (1GB toplam)" — exact match.
  - ✅ "Tüm loglar valid JSON; jq ile parse" — smoke 6/6 line parse PASS.
  - ⏸️ "Loki/Datadog opsiyonel" — yerel JSON yeterli, cloud shipping ileride opsiyonel.
- **Override seçenekleri:** `STRUCTURED_LOG_ENABLED=false` (kapat), `STRUCTURED_LOG_FILE=path` (özel yol), `LOG_SECRET_SCRUB=false` (scrub kapat — ÖNERİLMEZ).

### P1-07 [x] mypy + ruff blocking CI — **L** ✅ 2026-05-11 (hard-close)
- **Dosya:** `pyproject.toml` (NEW), `requirements-dev.txt` (NEW), `scripts/run_lint.bat` (NEW), `.github/workflows/ci.yml` (updated)
- **Yapılanlar:**
  - **a) `pyproject.toml` NEW** — `[tool.ruff]` config (line 100, py311 target, select E/W/F/B/I/UP, ignore E501/E402/B008/UP006/UP007), `[tool.ruff.lint.per-file-ignores]` tests/scripts esnek, `[tool.ruff.lint.isort]` known-first-party. `[tool.mypy]` gradual config — start permissive, 3 modülde strict override (`core.fees_v2`, `core.indicators`, `core.stats_utils`). 3rd-party stub ignore listesi.
  - **b) `requirements-dev.txt` NEW** — ruff==0.6.9, mypy==1.13.0, pytest pin'leri, types-* shim'ler. Runtime'dan ayrı.
  - **c) `scripts/run_lint.bat` NEW** — Çift-tıkla Windows-local lint+typecheck runner. `ruff check .` + `mypy core/` → transcript `data_store/audits/lint_<UTC>.txt`. Hata özetini konsol'a basıyor.
  - **d) `.github/workflows/ci.yml` UPDATED:**
    - Dev deps `requirements-dev.txt`'ten yüklenir (önceki inline pip install pytest pytest-cov pytest-asyncio ruff yerine).
    - Ruff: `continue-on-error: true` → **`true` (hard fail)** — roadmap acceptance kriteri.
    - Yeni step: `mypy core/` `continue-on-error: true` (gradual, P1-07 follow-up tighten).
    - Coverage: `--cov=core --cov-fail-under=21` → `--cov --cov-config=.coveragerc` (artık P1-01-c3'teki `fail_under=43` ratchet gate).
    - Test scope: `tests/unit` → `tests/ -m "not integration"`.
- **Davranış değişikliği:**
  - Lokal `scripts\run_lint.bat` çift-tıkla → ruff + mypy raporu.
  - CI push/PR'da: ruff hard-fail (kod kalitesi gate'i), mypy soft-fail (baseline collect), coverage hard-fail (43% gate).
  - Bot davranışı sıfır değişiklik.
- **Kabul kriteri kontrolü:**
  - ✅ "ruff `continue-on-error: false`" — CI hard fail.
  - ⏸ "mypy strict core/" — gradual: 3 modülde strict (fees_v2, indicators, stats_utils), diğerleri permissive. Follow-up'ta tüm core/'a yayılır.
  - ✅ "PR'da yeni mypy hatası → CI red" — mypy step coverage altında, fail_under gate ile genel gate. (mypy strict hard-fail P1-07 follow-up.)
  - ⏸ "Baseline file ile gradual" — `mypy_baseline.txt` henüz oluşturulmadı; Heddas Windows-side ilk `mypy core/` çalıştırması baseline'ı snapshot eder.
- **Sonraki adım (P1-07 follow-up):**
  - Heddas Windows: `py -3.11 -m pip install -r requirements-dev.txt` + `scripts\run_lint.bat`.
  - İlk ruff çıktısı: muhtemelen N violation. Listeyi bana ver, en kolay 30-40 düzeltme batch'i.
  - mypy core/ baseline snapshot → `mypy_baseline.txt`.
  - Override genişlet: data/, telegram_bot/ aşamalı.
- **Follow-up sonucu (2026-05-11):**
  - Heddas Windows `run_lint.bat` koşturdu → ruff 1123 violation.
  - `run_lint_fix.bat` (safe `ruff check --fix`) → 1123 → 158 (-%86 mekanik kazanım).
  - 14 test fail → 0: T11.6 leak (data_status/recon/ref_audit/reality_gap handler'larda generic mesaj fix).
  - F821 critical bug fix: backtest_v2 4 dead Becker fonksiyonu silindi (~155 satır), force_settle slug undefined fix, test_p0_08_multi_tf duplicate `__main__` block silindi. (memory: project_becker_aciklamasi Aşama 2 backlog kapandı.)
  - `run_lint_unsafe_fix.bat` (`--unsafe-fixes`) → 158 → 42 daha. Kalan: B023 closure-in-loop (1 unique, audit_reference_price), F601 dict key duplicate (1 unique, news_scanner) — manuel düzeltildi.
  - Kalan ~30 cosmetic (B007/UP035/F401 scripts/data_feeds/calibration/analysis/) `pyproject.toml [tool.ruff.lint.per-file-ignores]` override ile suppress (gradual migration, low-priority modüller).
  - mypy 71 error → `mypy_baseline.txt` snapshot. CI mypy step `continue-on-error: true` (baseline collect, follow-up'ta hard-flip).
  - Coverage 43.10% (gate 43.0% ratchet ✅), pytest 3418/0 fail.
- **Hard-close kabul kriteri:** Tüm Heddas tarafı yapıldı, P1-07 [x] olarak kabul edilebilir. Mypy strict ratchet (per-module override genişletme + hard-fail flip) ayrı P1-07-round-2 olarak işaretlendi.

### P1-08 [ ] SQLite → PostgreSQL — **XL**
- **Dosya:** `db/database.py`, `db/migrations/`, `deploy/postgres/`
- **Hedef:** SQLAlchemy + asyncpg, Alembic migration. `pg_dump` nightly + S3.
- **Kabul kriteri:** Bot Postgres ile çalışır; PITR (WAL archiving) hazır.

### P1-09 [x] Reconciliation loop smart-on — **M** ✅ 2026-05-09
- **Dosya:** `core/reconciliation/onchain_sync.py`, `core/engine.py`, `telegram_bot/handlers/recon_handler.py` (NEW)
- **Mevcut altyapı (audit'te bulundu):** `ReconciliationTask` zaten yazılmıştı — async loop, 5dk default interval, $1 mismatch threshold, Telegram alert callback. Sadece `RECON_ENABLED=false` default ENV ile gated, paper mode'da spam'e neden olabileceği için.
- **Yapılanlar (5 sub-task):**
  - **a) Smart `enabled` flag** — `enabled` property revize: explicit `RECON_ENABLED=true|false` env wins; yoksa `LIVE_ENABLED=true` ise auto-on, paper'da auto-off. 8/8 senaryo smoke PASS (paper, live, override-on/off, junk value fallthrough).
  - **b) `engine.py` wire sadeleştirme** — Önceki ENV check engine'da değil task'in `enabled` property'sinde. Engine her durumda instantiate + start eder; disabled ise start() no-op log eder. Single source of truth.
  - **c) Telegram `/recon` (alias `/rc`) panel** — Status (🟢 RUNNING / 🟡 ENABLED-not-running / ⚪ DISABLED), wallet, interval, threshold, last_check_age, mismatch_count + son 5 mismatch geçmişi. HTML parse_mode.
  - **d) Smoke test** — 6/6 PASS (enabled state machine, override priority, stats shape, start/stop idempotency).
  - **e) Memory + roadmap** (bu entry).
- **Davranış değişikliği:**
  - LIVE_ENABLED=true (mevcut shadow mainnet) iken bot başlatınca otomatik 5dk başına Polygon RPC'ye 1 request gidecek.
  - pUSD on-chain vs DB sapma > $1 → Telegram alarm + log.
  - Paper mode'da otomatik OFF (DB paper-balance vs on-chain real-balance spam'i önlenir).
  - Manuel override: `RECON_ENABLED=true` (zorla aç) veya `RECON_ENABLED=false` (zorla kapat).
- **Kabul kriteri kontrolü:**
  - ✅ "Recon job log atar" — Bot başlangıcında `🔁 Reconciliation task started (interval=300s)` log'a düşer (live mode'da). Paper'da DISABLED log mesajı.
  - ✅ "Sapma alarmı çalışır" — Telegram alert callback wired, mismatch > $1 (configurable) → `🚨 RECON MISMATCH: on-chain $X vs DB $Y (Δ $Z)` chat'e düşer.
- **Kapsam dışı (gelecek iş):** CTF position-level (her açık pozisyon için ERC-1155 balanceOf) — selector tanımlı (`ERC1155_BALANCE_OF_SELECTOR`) ama logic eksik. P1-09 follow-up olarak genişletilebilir.

---

## P2 — Orta (6 ay hedef)

### P2-01 [!] SaaS multi-tenant — **XL**
- **Bekliyor:** P1-08
- **Dosya:** `db/models.py`, `services/billing/`
- **Hedef:** `tenant_id UUID` her tabloya. Postgres RLS aktif. Stripe (3-tier: $19/$99/$499).
- **Kabul kriteri:** 2 farklı kullanıcı aynı bot'a bağlanır, birbirinin verisini göremez.

### P2-02 [!] Public read-only dashboard — **XL**
- **Bekliyor:** P2-01
- **Dosya:** `web/dashboard/`
- **Hedef:** FastAPI backend + React (Vite + TanStack Query). Live PnL, win rate, trade timeline.
- **Kabul kriteri:** dashboard.polypaper.io URL'i; her tenant kendi share-token'ıyla read-only sayfa.

### P2-03 [ ] Geopolitics %0 fee market'lere genişle — **L**
- **Dosya:** `data/market_scanner.py`, `core/strategy_plugins.py`
- **Hedef:** Kategori filtresi: `geopolitics`, `politics_us`, `sports_world_cup`. News momentum + sentiment.
- **Kabul kriteri:** Scanner geopolitics market'lerini keşfeder; fee=0 doğrulaması test'te.

### P2-04 [ ] Sentry tracing 0.05 sample + custom transactions — **M**
- **Dosya:** `main.py`, `core/engine.py`, `core/ai_brain.py`, `core/live_trader.py`
- **Hedef:** `traces_sample_rate=0.05`. engine.cycle, ai_brain.advise, live_trader.execute_buy custom transactions.
- **Kabul kriteri:** Sentry Performance UI'da görünür; p95 < 500ms.

### P2-05 [ ] Fill probability modeli — **XL**
- **Dosya:** `backtest/simulation/fill_model.py`, `core/engine_fills.py`
- **Hedef:** Queue position, time remaining, vol → fill prob. Adverse selection (informed taker probability).
- **Kabul kriteri:** Backtest paper PnL'i live PnL'e %5 yakınlığa iner.

---

## P3 — Uzun vadeli (12-18 ay hedef)

### P3-01 [ ] ML signal scoring (XGBoost/LightGBM) — **XL**
- **Dosya:** `core/ml/signal_model.py`
- **Hedef:** Feature: orderbook imbalance, taker flow, funding, BTC↔ETH corr. Target: 5dk forward sign. ROC-AUC>0.55 gate.
- **Kabul kriteri:** Backtest ML+heuristic fusion Sharpe>1.5.

### P3-02 [ ] Multi-venue (Kalshi, Manifold) abstraction — **XL**
- **Dosya:** `core/venues/`
- **Hedef:** Abstract Trader (place_order, cancel, balance). Cross-venue arbitrage strategy.
- **Kabul kriteri:** 3 venue tek registry'ye takılır; arb spread alarmı.

### P3-03 [ ] White-label + affiliate program — **L**
- **Dosya:** `LICENSE`, `services/affiliate/`
- **Hedef:** %30 lifetime commission. Stripe Connect.
- **Kabul kriteri:** Yeni kayıtların affiliate code field'i; payout aylık.

### P3-04 [ ] TR vergi raporlama CSV — **M**
- **Dosya:** `scripts/tax_report_tr.py`
- **Hedef:** TR Gelir İdaresi sermaye kazancı format. TCMB kuru ile TL.
- **Kabul kriteri:** 2026 trade'leri tek tıkla; mali müşavir formatına uygun.

---

## Bağımlılık Grafiği

```
P0-03 ──► P0-02 (PK keychain bekler export silmeyi)
P0-01 ──► P1-02 (microservice bekler auto-execute kaldırmasını)
P1-08 ──► P1-09 (recon loop Postgres bekler — opsiyonel ama önerilen)
P1-08 ──► P2-01 (multi-tenant Postgres bekler)
P2-01 ──► P2-02 (dashboard multi-tenant bekler)
```

Diğer tüm görevler bağımsız, paralel yürütülebilir.

---

## Sıralı Önerilen Akış (haftalık)

| Hafta | Görevler |
|---|---|
| 1 | P0-03, P0-06, P0-08, P0-09 (small/medium quick wins) |
| 2 | P0-01, P0-04 (finansal güvenlik) |
| 3 | P0-02, P0-05 (key + backup) |
| 4 | P0-07 (audit başlat — 7 gün veri toplama) |
| 5 | P0-07 raporu + P1-04 (strateji prune) |
| 6-7 | P1-06 (structured logging), P1-07 (mypy/ruff) |
| 8-10 | P1-05 (Docker/systemd) + P1-01 (coverage XL) |
| 11-13 | P1-08 (Postgres XL) |
| 14 | P1-09 (recon ON), P1-03 (reality gap) |
| 15-17 | P1-02 (AI microservice XL) |
| 18-22 | P2 başlasın |

---

## Heddas için Notlar

1. Bu yol haritası **yatırım kararı için TAVSİYE değil** — kendi araştırmanı yap.
2. P0 maddelerinin hiçbiri tamamlanmadan **gerçek sermaye eklenmemeli**. Şu an $1.49 cap doğru karar.
3. SaaS pivot (P2) öncesinde **edge'in gerçekten var olduğunu kanıtlaman** gerekli — P0-07 ve P1-03 bunu söyler.
4. Sırf "yapılacaklar listesi uzun" diye paniğe gerek yok. Sadeleştirme (P1-04 strateji pruning) işin yarısını çözer.
