# Epic 11 T11.1 — Final Audit Raporu (Mainnet Pre-Gate)

**Tarih:** 2026-04-22
**Audit kapsamı:** Epic 0 → Epic 10 closure konsolidasyonu.
**Bot versiyonu:** `v9.7.9` — `Phase 82e Sprint 6 — /env_toggle hot-tune`
**HEAD commit:** `51966d1` (checkpoint: Epic 11'e hazır)
**Risk sınıflandırma:** MED (statik audit; canlı kanıt T11.2'de).

---

## 1. Executive Summary

Epic 0 → Epic 10 **tamamen kapalı**. Bilinen mainnet bloklayan güvenlik
veya test item'ı **yok**. Test baseline **735 pass + 8 skip + 0 fail**
(3-seed deterministic 42/1337/9001). `pip-audit` **0 CVE**. 13-pattern
secret scan × 3 scope = **0 match**. Admin-gate eksik callback **0**.

**Bu rapor tek başına mainnet açmaz.** T11.1 statik + tarihsel konsolidasyon;
Go/No-Go kararı T11.2 (canlı kill-switch / budget / divergence doğrulaması)
ve T11.3 (rollback plan kanıtı) tamamlandıktan sonra verilir. Bkz. Bölüm 8.

---

## 2. Epic 0 → Epic 10 Closure Matrix

| Epic | Kapsam | Kapanış | Anahtar commit(ler) | Açık kalan |
|---|---|---|---|---|
| **0 — Baseline & Ground Truth** | T0.1-T0.5: TASKS onay, BOT_VERSION tek kaynak, ARCHITECTURE sync, TEMIZLEME_PLANI rol ayrımı, codename sync | 2026-04-20 | *(baseline)* | — |
| **1 — Ghost Modules & Dead Imports** | `data_feeds/*` ghost import temizliği (T1.3), `core/` soft-fail sessiz degradation kaldırıldı; T1.4 Faz 1 bare except (65 blok) | 2026-04-20/21 | *(T1.3, T1.4 Faz 1 commits)* | T7.6 Aşama C (146 HIGH-risk blok) — Windows final |
| **2 — Root Cleanup** | 100+ dosya → 19; 97 dosya `_archive/` 11 subfolder | 2026-04-20 | *(bkz. memory `project_cleanup_epic2_closed`)* | — |
| **3 — Classic / RiskLimits** | T3.1-T3.5: Classic bypass audit + docstring, BUG-10 temizlik, RiskLimits round-trip test | 2026-04-21 | *(Epic 3 commits)* | — |
| **4 — Simulator Doğruluğu** | T4.1 Single Fee Oracle (`core/fees_v2.py`), T4.2 ENV-overridable slippage, T4.3 REST latency honesty, T4.4 LIVE↔PROTECTED parity | 2026-04-21 | `3264add` | **T4.5/T4.6** slippage kalibrasyonu + backtest parity (yerel Windows, 24h telemetry gerekli); **T4.7-T4.9** REST RTT telemetry (`time_call` integration + `/dump_rest_timing`) |
| **5 — Atomicity / State** | T5.1-T5.6: `_trade_lock` + `_pending`, balance atomicity, `pending_reserved`, WS reconnect flush, WAL autocheckpoint, WS subscription cap overflow fix | 2026-04-21 | *(T5.6: prune wiring + priority_first + telemetry)* | — |
| **6 — UI↔Engine Ghost Audit** | T6.1-T6.5: PNL_PAUSE runtime-read, handler ghost audit, brain_flags parity (5 ghost), Kelly DB-persist, whitelist runtime-readiness guard | 2026-04-21 | *(Epic 6 closure commits)* | — |
| **7 — Dead Code & Duplicate** | T7.1-T7.5 ✅; T7.6 Aşama A (37 blok) + Aşama B (23 blok) ✅; post-audit 10 commit (stats_utils triplicate → `core/stats_utils.py`, live_trader ENV-override, trade_journal GC-safe) | 2026-04-22 | *(T7.6 A/B commits + post-audit 10)* | **T7.6 Aşama C** (`ai_brain` 47 + `auto_optimizer` 22 + `engine` 35 + `engine_signals` 42 = 146 HIGH-risk blok) — Windows final |
| **8 — ai_brain + LLM hygiene** | T8.1 (146 HIGH-risk blok Aşama C narrow), T8.2 LLM rate-limit guard (429 Retry-After + cooldown + MIN_COST anti-bypass), genel tarama (B3/B6/B7 docs + LLM_RATELIMIT_* runtime helpers) | 2026-04-22 | `c967726`, `4fdc781`, `99844b9`, `990d234`, `69553c4` | — |
| **9 — Test Infrastructure** | T9.1-T9.10 ✅: DI + autouse fixture + asyncio.run, 6 pre-existing fail triage, critical-path coverage fill (8 modül × 160 test), ghost guards, integration tests, pytest marker taxonomy, tests/README + regression scripts | 2026-04-22 | `5a73c7e`, `3435032`, `b81275a`, `4a06ea5`, `dbf81c2`, `e918567`, `d2cb442`, `25bbd4f` | **T9.8-REG** real asyncio engine.start + live WS probe (Windows); **T7.6-REG** Windows regression |
| **10 — Security Pass** | T10.1-T10.5 ✅ + post-audit T10.6-T10.10 ✅: secret leak scan (13 pattern × 3 scope = 0 match), Telegram input sanitization (admin gate × 8 callback), pip-audit (24 CVE → 0, 3 upgrade), `.env` sync audit, get_live_price fresh>stale, hyperopt_apply_callback admin gate kapsam kaçağı, Batch 2 exception leak (2 site) | 2026-04-22 | `77fba3a`, `9d84204`, `5c606ab`, `a74540b`, `27a2b81`, `03377db`, `6998f6f`, `a9cbc89`, `bdff7ff`, `0cf35b3`, `9006853`, `e37aa8b` | T11.6 (wider `esc(str(e))` sweep ~20 site × 12 handler), T11.7 (`docs/env_reference.md` AST-gen) — defense-in-depth, blocker değil |

**Epic kapanış toplamı:** 11 (Epic 0-10) ✅. Epic 11 (T11.1-T11.8) bu rapor ile açık.

---

## 3. Canlı Koda Etki Eden Invariants

Mainnet'e geçerken kırılmaması gereken kurallar. Her invariant hem testlerle
hem doktrin memory'si ile korunuyor.

### 3.1 Single Fee Oracle

**Kural:** Yeni fee logic SADECE `core/fees_v2.py`'da. Legacy `core/fees.py`
v1 + "pre-Mart 2026" kategorileri `_archive/` altında.
**Savunma:** Epic 4 T4.1 kapattı. Invariant test: `tests/unit/test_fees_v2.py`
ve entegration'da PnL identity (`b81275a`: "Single Fee Oracle PnL identity").
**Risk:** Yeni fee tip'i eklenirken v1'e dokunan PR → Single Fee Oracle
doktrini ihlali.

### 3.2 Admin Gate Pattern

**Kural:** Tüm state-mutating `CallbackQueryHandler`'lar `_is_admin_call()`
helper'ını çağırır; non-admin `_deny_callback()` ile reddedilir.
**Savunma:** T10.2 (7 callback) + T10.6 (hyperopt_apply_callback) + 10 AST
regression test (`tests/unit/test_callback_admin_gate.py`).
**Risk:** Yeni callback eklendiğinde gate unutulursa kapsam kaçağı. T10.6
tam bu kalıbın yakalanmasıdır.

### 3.3 Fresh > Stale Price Doctrine

**Kural:** Eski veri dönmektense `None` dönmek daha güvenli. WS reconnect
flush, cache staleness invalidation, malformed 'ts' → None (T10.5).
**Savunma:** T5.4 (WS reconnect flush + live_prices cache), T10.5
(`get_live_price` malformed entry narrow'u), fresh>stale doctrine memory
(`feedback_price_freshness_doctrine`).
**Risk:** Epic 11 T11.8'de genelleştirilecek (orderbook / regime_detector /
signal cache).

### 3.4 Runtime Env Re-Read Pattern

**Kural:** Module-top `CONST = os.getenv(...)` yapma; her okuyan kod
`_get_*()` helper üzerinden runtime'da okusun. `/env_toggle` runtime
mutation'ı ghost toggle'a dönmesin.
**Savunma:** T6.1 (PNL_PAUSE), T8.2 (LLM_RATELIMIT_*), T6.4 (auto_optimizer
module-top env constants), whitelist runtime-readiness guard (`69553c4`).
**Risk:** Yeni ENV eklerken module-top constant'a düşme — T6.1 pattern
tekrar edilmeli.

### 3.5 Bare Except Narrow Doctrine

**Kural:** `except Exception: pass` yasak. Swallow istiyorsan: (a) dar
tuple (`KeyError, ValueError, TypeError, AttributeError`), (b) `logger.debug`,
(c) explicit return value, (d) `# noqa: bare-except-ok — <gerekçe>` escape
hatch (T7.6 Aşama A pattern).
**Savunma:** T1.4 Faz 1 (65 blok), T7.6 Aşama A (37) + B (23), T10.5
(1 malformed entry) — toplam 126+ narrow. `core/` bare count 341 → 237.
**Risk:** Epic 11 T11.8 pre-commit grep hook kalıcı çözüm.

### 3.6 BOT_VERSION / BOT_CODENAME Tek Kaynak

**Kural:** `telegram_bot/version.py` TEK kaynak. `config/settings.py`
legacy pin silindi. `main.py`, `/help`, Sentry release tag bu tek dosyadan
okur.
**Savunma:** T0.2 + T0.5 AST parse + dataclass fields + f-string assertion
doğrulaması.
**Risk:** Faz ilerlerken yeni hardcoded literal sızdırmak → test ile
yakalanmalı.

### 3.7 Protected Strategies

**Kural:** `core/ai_brain.py::PROTECTED_STRATEGIES` ve
`PROTECTED_STRATEGY_TYPES={"classic"}` dokunulmaz. AI stratejileri $1 ile
başlar, 20+ trade sonrası scale.
**Savunma:** Proje doktrini (project_instructions). Ortak PR review kuralı.
**Risk:** Auto-optimizer veya hyperopt PR'ları bu listeye otomatik ekleme
yapmasın.

---

## 4. Test ve Kalite Baseline

| Metrik | Değer | Kaynak |
|---|---|---|
| Pytest (tests/unit) | **735 pass + 8 skip + 0 fail** | Epic 10 closure + T10.6 |
| 3-seed determinism | **GREEN** (42/1337/9001) | T9.10 (`4a06ea5`) |
| Coverage (`core/`) | **21.2%** (baseline ratchet) | T9.6 (`dbf81c2`) |
| Coverage başlangıç | 17.5% | T9.2 pre-T9.6 |
| Active Python LOC | ~71,891 | TASKS.md Ölçümler |
| Python dosyası (archive/backup hariç) | 238 | TASKS.md Ölçümler |
| `core/` bare except yakalama | 341 → **237** | T1.4 + T7.6 |
| Regression script | `tests/run_full_regression.sh` | T9.10 (`dbf81c2`) |

**Uyarı:** Coverage %21.2 mainnet için düşük sayılabilir ama `core/` modüllerinin
kritik path'leri Epic 9 T9.6-T9.8 ile kapatıldı (engine boot + WS reconnect +
Single Fee Oracle PnL identity + 50 integration test). T11.4 coverage ratchet
hook'u bu baseline'ı koruyacak; yeni kod 60%+ floor'a zorlanacak.

---

## 5. Security Baseline Özeti

| Kontrol | Sonuç | Referans |
|---|---|---|
| Secret leak scan (tracked files) | 13 regex × 0 match | `docs/security/T10_1_secret_leak_scan.md` |
| Secret leak scan (git history) | 13 regex × 0 match | aynı |
| Secret leak scan (ephemeral FS: logs/ reports/ backups/) | 0 match | aynı |
| Pattern set | AWS `AKIA` + HuggingFace `hf_` + OpenAI project `sk-proj-` + Stripe `sk_live_`/`sk_test_` + Anthropic `sk-ant-` + OpenRouter `sk-or-v1-` + Groq `gsk_` + Google `AIza` + Telegram bot + bare 64-hex + `0x` priv + BIP-39 | T10.8 (`bdff7ff`) |
| Telegram admin gate | 8 state-mutating callback gate'li | `docs/security/T10_2_telegram_input_sanitization.md` + T10.6 (`6998f6f`) |
| Telegram exception leak | 2 site fixed (`force_settle_handler:206`, `ai_handler:354`) | `docs/security/T10_7_batch2_exception_leak.md` |
| `pip-audit` CVE | **0** (24 → 0 via 3 upgrade: `aiohttp 3.10.0 → 3.13.4`, `Pillow 11.0.0 → 12.2.0`, `python-dotenv 1.0.1 → 1.2.2`) | `docs/security/T10_3_pip_audit.md` |
| `.env` ↔ `.env.example` hygiene | `.env ⊆ .env.example` (F2 fixed: 4 undocumented prod key eklendi) | `docs/security/T10_4_env_sync.md` |
| `.env` gitignore durumu | `.env` hiç commit edilmemiş (`git check-ignore` + `git ls-files` verified) | T10.1 |
| Fresh > stale price (WS cache) | `get_live_price` malformed entry → `None` + `logger.debug` | `docs/security/T10_5_get_live_price_fresh_over_stale.md` |
| `eval()` / `exec()` / `shell=True` kullanımı | **0** (baseline solid) | T10.2 baseline audit |
| HTML escaping (Telegram output) | `html.escape(str(v))` `esc()` helper kullanımı | T10.2 |
| SQL parameter binding | `?` placeholder (aiosqlite), f-string injection yok | T10.2 baseline |

---

## 6. Outstanding / Non-Blocker Backlog

Aşağıdakiler Epic 11 `T11.4-T11.8` içine veya yerel Windows telemetry
backlog'una alındı. Hiçbiri mainnet bloklayıcı değil; pre-mainnet checklist
T11.2 ve T11.3 ile tamamlanır.

### 6.1 Yerel Windows / 24h Telemetry (Epic 4 çocuğu)

| ID | Task | Gerekçe |
|---|---|---|
| **T4.5** | `fill_model.py` empirical slippage kalibrasyonu (1417 trade p10/p50/p90) | 24h bot uptime + DB erişimi sandbox'ta yok |
| **T4.6** | Backtest sweep parity — eski heuristic vs kalibre değer PnL delta <5% kanıtı | T4.5 sonrası |
| **T4.7** | REST RTT 24h telemetry + `core/observability/rest_timing.py` → real p50/p_iqr | `REST_TIMING_TELEMETRY=true` + 24h bot uptime |
| **T4.8** | `/dump_rest_timing` admin Telegram cmd | T4.7 için trigger |
| **T4.9** | `live_trader.py` + `polymarket_client.py` HTTP çağrılarını `time_call(label)` ile sar | T4.7 önkoşulu |

### 6.2 Bare Except Aşama C (T7.6 Faz 3)

146 HIGH-risk blok × 4 dosya: `ai_brain` 47 + `auto_optimizer` 22 + `engine` 35
+ `engine_signals` 42. Windows final olarak işaretlendi — sandbox'ta test
harness yetersiz (asyncio engine + live state). **Not:** Epic 8 T8.1 zaten
bu bloklara dokundu (LLM rate-limit + genel narrowing); Aşama C "kalan bare
except'lerin final temizliği".

### 6.3 Test Regression (Windows real-asyncio)

| ID | Task | Gerekçe |
|---|---|---|
| **T9.8-REG** | Real asyncio `engine.start()` + live WS + shadow-paper divergence probe | Sandbox'ta live WS yok |
| **T7.6-REG** | `run_t76_asama_a_regression.bat` 16 modül import smoke + `pytest tests\unit -q` | Windows baseline teyidi |

### 6.4 Epic 11 Defense-in-Depth (T11.4-T11.8)

Hepsi "mainnet açıldıktan sonra yapılabilir" kategorisinde; mainnet stability
sağlandıktan sonra 2-3 haftalık hygiene sprint'i:

- **T11.4** Coverage CI gate + pre-commit hook (21.2% ratchet baseline +
  `detect-secrets` 13-pattern baseline + `pip-audit` quarterly gate)
- **T11.5** Test env-leak hygiene (`os.environ[]` → `monkeypatch.setenv` × 5 dosya)
- **T11.6** User-facing exception render policy (wider `esc(str(e))` sweep
  ~20 site × 12 handler)
- **T11.7** `docs/env_reference.md` AST-gen + `.env.example` linter pre-commit
- **T11.8** `except Exception: pass` pre-commit grep + fresh>stale genelleme

---

## 7. Bilinen Riskler ve Mitigations

### 7.1 Shadow Live Credential-Ready, LIVE_ENABLED=false

**Durum:** CLOB API creds populated + EOA type 0 + ApiCreds signature fix
uygulandı; `LIVE_ENABLED=false` çünkü T11.2 canlı doğrulama yapılmadı.
**Mitigation:** T11.2 kill-switch + budget + daily loss + divergence monitor
canlı kanıtı. `LIVE_ENABLED=true` öncesi T11.3 rollback plan test edilmiş
olmalı.
**Referans:** `.auto-memory/project_live_trader_state.md`,
`.auto-memory/project_polypaper_status.md` (shadow live: $1.49 USDC,
$1/trade, 3 strateji).

### 7.2 Gitignored Kod Sync (sandbox → Windows)

**Durum:** `data/` dizini gitignored; T10.5 fix (`data/websocket_client.py`
malformed 'ts' narrow) sandbox'ta yapıldı, Windows prod tree'ye **manuel
kopyalama + bot restart** gerekli.
**Mitigation:** TASKS.md `SYNC.1` satırı + checkpoint memory'de uyarı.
Windows side `/h` ile WS status doğrulaması yapılacak.

### 7.3 WSL Git Quirks

**Durum:** `.git/config` ghost-bug + bulk `git add` index corruption
geçmişi var.
**Mitigation:** Atomic tek-komut `git add ... && git commit` pattern'i
uygulanıyor (Epic 10 12 commit'te sorun çıkmadı). Detay:
`.auto-memory/reference_wsl_git_quirks.md`.

### 7.4 Replit Free Tier Limiti

**Durum:** Proje Replit free tier'da; ancak bot **yerel Windows PC'de**
çalışıyor (`.auto-memory/feedback_local_pc.md`). Replit yalnızca
geliştirme/sandbox.
**Mitigation:** Live-side operasyon Windows'ta yürüyor; Replit limiti
production availability'ye etki etmiyor.

### 7.5 Coverage %21.2 Başlangıç

**Durum:** Mainnet için düşük görünebilir; ancak kritik path'ler (engine
boot, WS reconnect, Single Fee Oracle PnL identity, 50 integration test)
Epic 9'da kapatıldı.
**Mitigation:** T11.4 ratchet hook baseline'ı koruyacak. Her PR yeni
`core/` kod ile karşılığında test mecbur — 60% floor 30-gün ramp.

### 7.6 Epic 11 T11.6 Exception Leak (Bilinen Kısmi Kapanış)

**Durum:** T10.7 sadece 2 site kapattı; grep ~20 site × 12 handler
`reply_text(f"... {esc(str(e))}")` hâlâ var.
**Mitigation:** Admin-only handler'larda exception detayı operator
visibility için kasıtlı (diagnostik değer yüksek). Public handler'da
sızma düşük — büyük çoğunluğu admin-gated. T11.6 formal policy sweep
defense-in-depth.

---

## 8. Go/No-Go Karar Kriterleri

**T11.1 bu raporun kendisi** — statik + tarihsel konsolidasyon, Go vermez.
Mainnet geçişi için T11.2 ve T11.3 birlikte tamamlanmalı:

### T11.2 — Canlı Kill-Switch / Budget / Divergence Doğrulaması (HIGH)

Aşağıdakilerin **runtime kanıt gerektirmesi** — doc veya test değil, canlı
bot üzerinde tetiklenmiş ve beklenen davranışı sergilemiş olacak:

1. **Kill switch:** `/stop_all` admin cmd tüm açık pozisyonları force-settle
   eder, yeni trade açılmaz. Stratejiler paused state'e geçer.
2. **Budget guard:** `LIVE_BUDGET` cap aşıldığında yeni trade blocked
   (skip_reason="budget_exceeded" log + Telegram bildirim).
3. **Daily loss:** `LIVE_MAX_DAILY_LOSS` cap'i tetiklendiğinde günün
   sonuna kadar yeni trade blocked.
4. **Paper-shadow divergence monitor:** Shadow live vs paper engine PnL
   farkı eşiği aşarsa alert. Canlı 48h test ile verify.
5. **Rolling WR kill:** `ROLLING_WR_KILL` threshold altına düşen strateji
   otomatik paused.
6. **Price freshness kill:** `WS_STALE_SEC` aşıldığında yeni trade
   blocked + WS reconnect.

### T11.3 — Rollback Planı (HIGH)

`rollback_*.bat` envanterinden hangi senaryo hangi .bat'ı tetikler karar
matrisi + test edilmiş bir dry-run. `data/` gitignored sync yolu
dokümante edilmiş olmalı.

### Go/No-Go Kararı

- ✅ Epic 0-10 kapalı (bu rapor)
- ⏳ T11.2 runtime kanıt tamamlandı mı?
- ⏳ T11.3 rollback plan test edildi mi?
- ⏳ T4.5/T4.6 slippage kalibrasyonu (Windows yerel, 24h telemetry) —
  mainnet'e ideal; ancak blocker değil (shadow ile start, kalibrasyon
  sonrası scale).

**Üç ⏳ item tamamlandığında mainnet açılabilir.** T11.4-T11.8 mainnet
sonrası hygiene sprint'i.

---

## 9. Referans İndeks

### Security doc setleri (Epic 10)

- `docs/security/T10_1_secret_leak_scan.md` — 13-pattern scan + T10.8 extension
- `docs/security/T10_2_telegram_input_sanitization.md`
- `docs/security/T10_3_pip_audit.md` — T10.9 eth-* precision inline
- `docs/security/T10_4_env_sync.md` — T10.10 reproducible grep script inline
- `docs/security/T10_5_get_live_price_fresh_over_stale.md`
- `docs/security/T10_7_batch2_exception_leak.md`

### Memory landmark'ları (`.auto-memory/`)

- `project_checkpoint_epic11_ready.md` — bu raporun kaynak snapshot'ı
- `project_epic10_closure.md` — Epic 10 + post-audit closure
- `project_epic9_t910_closure.md` — Epic 9 closure
- `project_epic9_post_audit_closure.md` — pre-Epic-10 audit
- `project_epic8_t81_progress.md`, `project_epic8_genel_tarama_closure.md`
- `project_epic7_closed.md`, `project_epic7_b6_b3_closure.md`
- `project_t76_asama_a_progress.md`, `project_t76_asama_b_closed.md`,
  `project_t76_post_audit_2026_04_22.md`
- `project_epic6_closed.md`, `project_t61_pnl_pause_runtime.md`,
  `project_t63_brain_flags_parity.md`
- `project_epic4_simulator_audit.md`, `project_single_fee_oracle.md`
- `project_epic1_6_audit_2026_04_21.md`
- `project_cleanup_epic2_closed.md`
- `project_t14_faz1_bare_except.md`, `project_t56_ws_cap_fix.md`
- `feedback_price_freshness_doctrine.md`, `feedback_iterative_workflow.md`,
  `feedback_change_impact_check.md`
- `reference_wsl_git_quirks.md`, `reference_rest_timing_telemetry.md`

### Proje doc'ları

- `docs/ARCHITECTURE.md` — 14+ strategic-gate pipeline, data_feeds /
  calibration / backtest / db / telegram_bot/jobs dizin ağacı
- `docs/STRATEGIES.md` — Classic + AI stratejileri, PROTECTED list
- `docs/TROUBLESHOOTING.md`
- `docs/SECRETS_ROTATION.md`
- `TASKS.md` — aktif backlog (Epic 0-11)
- `CHANGELOG.md` — faz ilerleme tarihçesi

### Kritik runtime kaynağı

- `telegram_bot/version.py` — BOT_VERSION + BOT_CODENAME tek kaynak
- `core/engine.py`, `core/engine_signals.py`, `core/engine_settlement.py`,
  `core/engine_fills.py`
- `core/fees_v2.py` — Single Fee Oracle
- `core/live_trader.py` — CLOB live wrapper
- `core/risk_manager.py`, `core/auto_optimizer.py`, `core/ai_brain.py`
- `data/websocket_client.py` (gitignored — T10.5 fix Windows'a manuel sync)
- `telegram_bot/handlers/strategies.py` — admin gate helper kaynağı

---

## 10. Sonuç

**Epic 0-10 kapsamı tamamen kapalı ve denetimden geçti.** Bilinen mainnet
bloklayan güvenlik veya test item'ı yok. Bu rapor T11.1 statik audit'in
closure belgesidir.

**Sıradaki zorunlu adımlar (sırası önemli):**

1. **T11.2** — Canlı kill-switch / budget / daily loss / divergence monitor
   runtime kanıtı. Shadow live'da 48h test + Telegram alert validation.
2. **T11.3** — Rollback planı: `rollback_*.bat` inventory + senaryo karar
   matrisi + dry-run kanıtı.
3. **Mainnet Go/No-Go toplantısı** — Heddas + bu rapor + T11.2 + T11.3
   birlikte değerlendirilir; `LIVE_ENABLED=true` flag'i bu onay sonrası
   set edilir.

**Post-mainnet hygiene sprint'i (2-3 hafta):** T11.4-T11.8 defense-in-depth
+ T4.5-T4.9 slippage kalibrasyonu + T7.6 Aşama C + T9.8-REG / T7.6-REG
Windows regression.

---

**Audit tarihi:** 2026-04-22
**Audit sahibi:** Claude (baş geliştirici + denetçi; Cowork session
`happy-confident-cannon`).
**Sonraki revizyon:** T11.2 kapanışında güncellenir (Bölüm 8 ⏳ → ✅).
