# PolyPaper Bot — Acımasız Audit Raporu

**Tarih:** 2026-05-08
**Repo:** https://github.com/Heddas98/polypaper-bot (main branch head)
**İncelenen yüzey:** 277 Python dosyası, ~4.1 MB kaynak, 14 dependency, 52 `core/` + 54 handler dosyası, 55 test dosyası
**Yöntem:** GitHub raw + API ile dosya çekimi, kod okuma, PyPI doğrulama, fee-formula çapraz kontrol

---

## A. Reality Check — README iddiaları

### A.1 "3,474 test pass / 43.7% coverage" — Yarı doğru, yarı kozmetik

`tests/unit/` 52 dosya, `tests/integration/` 3 dosya. Test sayısının önemli kısmı **tek bir dev dosyada**: `tests/unit/test_p0_p1_extra_coverage.py` — 778 KB, 20.584 satır, 1.170 `def test_`, 1.870 assert. İçerik büyük ölçüde **import-coverage churn pattern**: `for name in dir(mod): obj = getattr(mod, name); try: await obj(...) except: pass`. Davranış doğrulaması değil, "her şeyi çağır, exception'ı yut, satır say". Aynı pattern `tests/unit/test_wave22_mega.py`'da da sistematik.

CI gerçeği (`.github/workflows/ci.yml`):

```
--cov=core --cov-fail-under=21
```

CI **sadece `core/` üzerinde %21** eşik koyuyor. README'deki **%43.7** birleşik (`core+data+telegram_bot+backtest`) sayıdır — apples-to-oranges.

**Hüküm:** Test sayısı şişirilmiş; sentetik dir-loop testleri silinse coverage anlamlı şekilde düşer. `data/`, `telegram_bot/handlers/` gerçek davranış testi neredeyse yok.

### A.2 SDK iddiası — `py-clob-client-v2==1.0.0` — DOĞRU

PyPI'da paket gerçek (Polymarket Engineering yayınlamış). V1 (`py-clob-client 0.34.6`) hâlâ ayakta ama bot V2'yi pinlemiş.

**Süpriz risk:** `requirements.txt` içinde `py-builder-relayer-client` **pin'siz**. PyPI'da yalnızca `0.0.1` ve `0.0.2rc1` var → release-candidate'a kayma riski.

### A.3 "12 strateji" iddiası — YANLIŞ

`core/strategy_plugins.py`'da **20 strateji class** var: Momentum, Contrarian, Scalper, Sniper, Martingale, FlashCrash, StreakReversal, HighThreshold, LateConvergence, PennyContract, BondingYieldLive, HourEdgeLive, OrderbookImbalanceLive, FadeRipLive, OpeningBreakoutLive, FundingRateLive, CalibrationArbLive, Fusion, Classic + base.

YOL_HARITASI'nın kendi kabulü: "5 AI'nin uzlaştığı: 18 strateji = overengineering / overfitting riski. Sadece 1-3 stabil olanı bırak." Repo bunu kabul ediyor ama uygulamamış.

### A.4 "Real L2 history replay" — Kısmen doğru

`backtest/replay_engine.py` gerçekten `ob_snapshots` tablosundan kaydedilmiş L2 verisini replay ediyor. Ama:

- Snapshot kaydını botun kendi `MarketRecorder` (10s interval) yapıyor → **survivorship bias**.
- `backtest/replay_engine.py:98-104` yorumu: *"Defaults (250ms / 75ms) are HEURISTIC, NOT empirically calibrated."*
- `walk_forward.py` parametre grid optimizer; gerçek end-to-end pipeline değil.
- 5dk binary'de gerçek edge ölçmek için backtest yetersiz: real-time fill probability, queue position, adverse selection modellenmemiş.

---

## B. Mimari — fragility analizi

### B.1 Tek-process, tek-event-loop kırılganlığı

`main.py`'da 7+ asenkron servis aynı asyncio event loop'unda: WS client, external feed, Binance multi-stream, Chainlink oracle, candle collector, scanner, market recorder, engine (1s cycle + AI Brain + autopilot), Telegram polling + 6+ background `safe_create_task`.

- WS task, Telegram polling, AI Brain LLM çağrısı, SQLite WAL writer hepsi **tek thread**de. CPU-bound iş tüm event loop'u kilitler.
- LLM çağrıları `run_in_executor` ile sync HTTP — doğru.
- Loop ölünce `_on_engine_done` → `call_later(5, _restart_engine)` çağıran kişi de yok.

**Hüküm:** Monolitik async daemon Windows masaüstünde paper için kabul edilebilir; 1k$+ gerçek sermaye için endüstri standardı değil.

### B.2 SQLite WAL + Windows yerel

- `db/database.py:44-49`: WAL aktif, `busy_timeout=10000`, `synchronous=NORMAL`, `wal_autocheckpoint=5000`.
- `start.bat` + `watchdog.bat` (210 satır) + `watchdog.vbs` (görünmez launcher).
- Tek dosya: `data_store/polypaper.db` (live trade, paper executions, trade_log, ai_decisions hepsi).
- HA / failover yok. `backup.bat` interaktif (`set /p CHOICE`) → cron'da çalışmaz.
- **TASKS.md T11.3 bulgusu:** *"daily_db_snapshot_job atomic write yapmıyor — 2 bozuk backup (2026-04-20 + 2026-04-23, 729-780 MB, header null)."* Fix uygulandığı net değil.
- WAL büyüme riski: MarketRecorder 10s/market.

### B.3 Lockfile / PID race

- `main.py:127-159`: `polypaper.lock` PID dosyası.
- TOCTOU açığı: dosyayı oku → kontrol et → yaz arasında race condition.
- `watchdog.bat` ek lock katmanı; systemd/supervisor'a kıyasla amatörce.

---

## C. Finans / Quant — derin

### C.1 Kelly fraction, position sizing

- Quarter Kelly (0.25), regime-aware decay (trending 0.25 / ranging 0.167 / volatile 0.125).
- MIN_TRADES_FOR_KELLY=15, exploration MIN_BET=$1.
- **Tutarsızlık:** `core/kelly.py:31` MAX_BET_PCT=0.15 ile `Settings.KELLY_MAX_BET_PCT=0.05`. İki farklı yerde tanımlı, ghost-config riski.

### C.2 Slippage / fee / maker-taker

- `core/fees_v2.py`: `fee = shares × feeRate × p × (1−p)`, crypto için `feeRate=0.072`. Doğru.
- Maker rebate %20 of taker fee. Tail-zone (`p<0.15` veya `p>0.85`) guard var.
- Slippage: "0.2% adverse" + dinamik rolling avg. **Adverse selection modellenmemiş** — paper-vs-live fark yaratır.

### C.3 5dk binary'de TRUE EDGE — yok

1. **Mikrostruktür rakip:** Maker tarafı genellikle profesyonel quant (Jump, Wintermute) ve Polymarket MM programı. Retail bot taker olarak girer → fee + slippage öder.
2. **Reference price belirsizliği:** Polymarket'in tam olarak hangi feed'i hangi anda okuduğu deterministik public değil. YOL_HARITASI P0.3 "doğrulanmamış" işaretliyor.
3. **AI Brain düşük frekanslı (1h) öneriler 5dk market'te anlamsız.**
4. **Sample size:** Lifecycle "proven" eşiği N=100 trade. Bernoulli WR'yi ±0.10 95% CI ile kestirmek için ~96 trade gerek — sınırda. Sembolik, istatistiksel anlamlılık değil.

### C.4 AI Brain — en kritik finansal güvenlik açığı

`core/ai_brain.py:418-421`:

```python
if confidence >= _auto_threshold or not actions:
    # High confidence → auto-execute
    results = await self._execute(actions)
```

`AI_AUTO_CONFIDENCE` default `0.70`. LLM confidence>=0.70 → otomatik strateji oluşturur, durdurur, threshold'u tune eder.

Düşük confidence'ta approval kuyruğu var, AMA exception handler'ı **fallback olarak yine auto-execute eder**:

```python
except Exception as e:
    logger.error(f"Approval queue: {e}", exc_info=True)
    # Fallback: execute anyway
    results = await self._execute(actions)
```

Confidence LLM'in döndürdüğünden okunuyor — yalan söylerse / prompt injection olursa otonom alana girer.

### C.5 Settlement / Redeem

- Per-market `asyncio.Lock` ile race koruması.
- Orphan handling 15 dk üstü.
- `core/uma_dispute.py` UMA dispute window awareness.
- **Reconciliation loop default OFF (`RECON_ENABLED=false`).** Off-chain ↔ on-chain sync exploit riski açık.

### C.6 Lifecycle — istatistiksel anlamlılık yok

0-30 / 30-100 / 100+ eşikleri hardcoded. P-test/Bayesian posterior yok. Thompson Sampling var ama bandit non-stationary olduğunda regime change'de kötüleşir.

---

## D. Yazılım / Mühendislik

### D.1 God-module sorunu

- `core/engine.py`: 69 KB, 1.278 satır
- `core/engine_signals.py`: 94 KB, 1.770 satır
- `core/strategy_plugins.py`: 56 KB, 1.368 satır
- `core/ai_brain.py`: 99 KB, 1.908 satır
- `core/live_trader.py`: 49 KB, 992 satır
- `telegram_bot/handlers/strategies.py`: 81 KB
- `telegram_bot/handlers/live_handler.py`: 62 KB
- `telegram_bot/bot.py`: 65 KB

TradingEngine = 4 mixin. Multiple inheritance + mixin'ler IDE navigasyonunu zorlaştırır.

### D.2 Coverage gap

`.coveragerc` yalnızca `source = core`. Live trading kritik path için davranış testi yetersiz: `tests/unit/test_live_trader.py` 24 test (17 KB).

### D.3 Tip / lint / CI

- mypy yok.
- Ruff CI'da `continue-on-error: true` — block etmiyor.
- CI matrix: yalnızca Python 3.11 / Ubuntu. Windows yerel rejim olmasına rağmen Windows CI yok.

### D.4 Error handling

- Çoğu yerde `except Exception:` `# noqa: BLE001`.
- Triple-layer guard (kill_switch, portfolio_kill_switch, RiskManager 9-gate). İyi.
- Kelly DB hatasında fail-default MIN_BET=$1.0 — kötü trade emit edebilir.

### D.5 Secrets

- `.env.example` 20 KB, **156 değişken**. README "20'nin altına in" diyor — şu an 156.
- **`POLYGON_PRIVATE_KEY=` `.env` plaintext.** Hardware wallet, KMS, OS keychain yok.
- **Heddas direktifi:** `data/polymarket_actions.py` içinde `export_private_key()` admin Telegram komutu **ham private key'i Telegram chat'e döküyor**. CRITICAL güvenlik gaffe.

### D.6 Dependency hygiene

- 14 dependency, 13'ü pinned, 1'i (`py-builder-relayer-client`) unpinned.

### D.7 Async hygiene

- `safe_create_task` exception swallow'u observable.
- Stall watchdog 90s.
- Cancellation sızıntısı: KeyboardInterrupt'ta async-cleanup eksik.

### D.8 Sentry

- Env-gated, default no-op. `traces_sample_rate=0.0` default.
- **Manuel `capture_exception` çağrısı 0 kez** — basic Sentry, distributed tracing yok.

---

## E. Operasyonel — Windows yerel

### E.1 Watchdog

`watchdog.bat` + `watchdog.vbs`. Bot ölünce 30s sonra restart, saatte max 5, 10 dk pause. Production-grade değil.

### E.2 Backup

`backup.bat` interaktif, cron'da çalışmaz. 30 günden eski yedekler `forfiles` ile silinir. Atomic write bug T11.3'te bulundu, fix net değil. Off-site replication yok.

### E.3 Loglar

`RotatingFileHandler(maxBytes=5MB, backupCount=3)` → toplam **20 MB log**. Çok yetersiz. 1 günlük log bile zor sığar — incident postmortem imkansız. Structured JSON log default off.

### E.4 Monitoring & alerting

Telegram `/diagnose`, `/health`, `/db_health`, `/live_guards` admin manuel. **Metric exporter yok** (Prometheus/StatsD).

---

## F. Uyumluluk / Risk

### F.1 Polymarket KYC + jurisdiction

- README'de US users blocked'a referans yok.
- KYC/region check yok.
- TR'den Polymarket: TOS gri alan; bot bunu adresleyip terms sunmuyor.

### F.2 Crypto wallet private key

Yukarıda detaylı. Hardware wallet yok, KMS yok, Telegram'a key export riski açık.

### F.3 Yasal — TR

- Prediction market yasal gri alan.
- Vergi raporlaması yok.
- KVKK: tek-kullanıcı bot olduğundan kapsam sınırlı.

### F.4 Self-trading / wash trading

- Aynı bot içinde bir strateji UP, başka strateji DOWN açabilir → Polymarket wash trading olarak işaretleyebilir.
- `MAX_TOKEN_EXPOSURE_USD=50` aynı token kümülatif limit, ama YES + NO farklı token_id → yakalamıyor.

---

## G. Sürdürülebilirlik

### G.1 Bus factor 1

Tek committer (Heddas98). Private repo. README: "issue ve PR sadece owner tarafından açılır". TASKS.md 161 KB, "Phase 1 → 82e + Sprint 1-3 + Epic 0-12 + T0.1-T11.8". Roadmap karmaşası.

### G.2 Documentation debt

İç jargon yığını ("Phase 24-65 + Epic 0-12 + T0.1-T11.8 + Sprint 1-3"). Yeni biri devraldığında haftalar.

### G.3 Technical debt

- ~%20-30 dead code / archive (Becker calibrator silindi, Hyperopt silindi, keepalive silindi — referansları yorum olarak kalmış).
- `_archive/`, `dead_code_nuke/` arşiv taşıyor.

### G.4 Roadmap netliği

YOL_HARITASI komite "iç ses" — gerçek piyasa kanıtı değil. Özü: *"Bot teknik olarak güzel, ürün olarak kanıtlanmamış, ekonomik olarak henüz pozitif değil."*

---

## I. Jüri Kararı

### I.1 Sermayene göre çalıştırma kararı

- **$1k:** Hayır. P0 kapanmadan asla.
- **$10k:** Kesinlikle hayır. SQLite + Windows + tek event loop = underspec.
- **$100k:** Bu mimaride asla. Linux + systemd + Postgres + Prometheus + 24/7 SRE on-call + ayrı engine/AI/risk process'leri + HW wallet/HSM gerekli.

### I.2 "Polyscout.io klonu" iddiası

Kompetitif değil — hobby project + uzman seviyede mühendislik egzersizi. YOL_HARITASI'nın kendisi onaylıyor: "Bot mühendislik harikası, ÜRÜN değil. Edge kanıtlanmamış."

### I.3 En endişe verici 3 şey

1. AI Brain otonom CREATE/STOP/TUNE + fallback auto-execute.
2. `POLYGON_PRIVATE_KEY` plaintext + Telegram `/export_private_key` komutu.
3. Mimari fragility: tek SQLite + tek Windows + tek event loop + watchdog.bat + atomic backup bug + sentetik test coverage.

### I.4 En övgüye değer 3 şey

1. Disiplin + öz-farkındalık. 5 AI'a analiz yaptırıp acımasız sonuçları kabullenmiş.
2. Defansif kod katmanları. Stall watchdog, KillSwitch ×2, 9-gate RiskManager, lockfile, atomic deduct, per-market `asyncio.Lock`, orphan recovery, env-gated Sentry.
3. Polymarket V2 SDK uyumu. `py-clob-client-v2 1.0.0` resmi paket, GNOSIS_SAFE signature, tick_size + neg_risk per-market, fee model v2.

### I.5 Tek cümle hüküm

> Bu bot **kişisel paper-trading laboratuvarı + mühendislik portfolyosu** olarak çok başarılı; **gerçek sermayeli production trading bot** olarak henüz hazır değil. Edge kanıtlanmadan SaaS pivot daha düşük-riskli yol.
