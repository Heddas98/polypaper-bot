# PolyPaper Cleanup Backlog

> **Durum:** 2026-04-20 oluşturuldu (ilk tarama). Son commit sync: **2026-04-21**. Sahibi: Claude (Baş Geliştirici/Denetçi).
> **Kural:** Bu dosya her oturumun başında okunur. Bitenler `[x]`, yeni iş eklenir. Bir Epic bitmeden sıradakine geçilmez.
> **Mod:** STRICT CLEANUP — spekülasyon yok. Her iddia: dosya + satır.
> **Protected:** `core/ai_brain.py::PROTECTED_STRATEGIES` ve `PROTECTED_STRATEGY_TYPES={"classic"}` dokunulmaz.

---

## 🗓️ Son Commit Sync — 2026-04-21

| Commit | Mesaj | Dosya | Kapsam |
|---|---|---|---|
| `34494f1` | chore: add backlog artifacts (analysis/, cleanup plan, smoke tests) | 8 | `analysis/` edge-discovery tooling + `TEMIZLEME_PLANI_2026-04-20.md` + `scripts/smoke_ws_stale_threshold.py` + `tests/test_risk_limits_roundtrip.py` (Epic 3 T3.4) |
| `e9fe9bc` | chore: backlog sync — Epic 2 cleanup + Sprint 5/6 + T1.4 Faz 1 | 28 | Epic 2 (root .bat/py deletions), Sprint 5 HOTFIX v6 Classic fill, Sprint 6 `/env_toggle`, T1.4 Faz 1 bare except (8 core dosya) |
| `3264add` | epic4(audit): fee oracle consolidation + slippage/latency honesty pass | 11 | T4.1 single fee oracle (`core/fees_v2.py`), T4.2 Faz A ENV-overridable slippage, T4.3 Faz A REST latency docstring honesty + `core/observability/rest_timing.py` helper |

**Toplam commit altına alınan dosya:** 47 (11 + 28 + 8). Uncommitted kalan: yalnız `BUGUN_NE_YAPACAGIM.md` (günlük working note, kasıtlı) + `.hyperopt.lock` (runtime state).

**WSL quirks:** `.git/config` ghost-bug + bulk `git add` index corruption → atomic tek-komut `git add ... && git commit` pattern'i kullanıldı. Detay: `/sessions/happy-confident-cannon/mnt/.auto-memory/reference_wsl_git_quirks.md`.

---

## 📊 Ölçümler (baseline vs güncel)

- Python dosyası (archive/backup hariç): **238**
- Aktif kaynak satır sayısı: **~71.891**
- Kök dizinde tek seferlik deploy/rollback/hotfix .bat: ~~46~~ → **0** (59 arşive) ✅ Epic 2
- Kök dizinde eski handoff/audit/roadmap dokümanı: ~~26~~ → **0** (22 arşive) ✅ Epic 2
- Kök dizinde one-shot .py (fix_* + unused): ~~7~~ → **0** (7 arşive) ✅ Epic 2
- Kök dizin toplam dosya: ~~100+~~ → **19** ✅ Epic 2 (mainnet-ready)
- `core/` altında bare `except:` yakalama: ~~341~~ → **276** (65 daraltma) ✅ T1.4 Faz 1
- Var olmayan `core.*` modüllerine yapılan import çağrısı: **40** (10 modül)
- Arşiv klasörü: **8.6 MB** → ~9.2 MB (97 yeni dosya)

---

## Epic 0 — Baseline & Ground Truth  *(PRE-REQ, 1 oturum)*

Hedef: Elimizdeki envanteri kayda geç, sürüm/doküman tutarsızlıklarını kapat. Kod değişmez.

- [x] **T0.1** Bu TASKS.md'yi kullanıcı onayına sun — risk: LOW *(onaylandı 2026-04-20)*
- [x] **T0.2** `BOT_VERSION` tek kaynak kuralını netleştir — risk: LOW *(2026-04-20 tamamlandı, Seçenek A)*
  - ~~`config/settings.py:142` → `BOT_VERSION: str = "9.7.3"` (eski)~~ → kaldırıldı, yerine yorum satırı
  - `telegram_bot/version.py:6` → `BOT_VERSION = "v9.7.9"` tek kaynak
  - `main.py` → artık `from telegram_bot.version import BOT_VERSION` ile okuyor; Sentry release tag'i `polypaper-bot@v9.7.9` olarak düzeldi (önceki: `polypaper-bot@v9.7.3`)
  - Doğrulama: AST parse + dataclass fields assertion + f-string assertion tamam
- [x] **T0.3** `README.md` ve `docs/ARCHITECTURE.md` gerçek mimariyle uyumlu mu, kontrol et (sadece okuma) — risk: LOW ✅ 2026-04-20
  - Denetim tamamlandı. 8 sapma raporlandı; 3 alt-görev oluşturuldu (T0.3.1, T0.3.2, T0.3.3). Detay için CHANGELOG'daki 2026-04-20 T0.3 notuna bakılacak (ileride).
  - README büyük ölçüde güncel (versiyon, faz, bakiye, komut tablosu). ARCHITECTURE.md'de data_feeds/, skills/, calibration/, backtest/, db/, telegram_bot/jobs/ ve top-level dizin listesinde ciddi drift.
- [x] **T0.3.1** `docs/ARCHITECTURE.md` gerçek dizin ağacıyla senkronla (docs-only) — risk: LOW ✅ 2026-04-20
  - Değişiklikler: data_feeds/ (7 dosya iddiası → 1 dosya + arşiv notu), skills/volatility_skill.py isim düzeltmesi, calibration/ 2→3 dosya (surface_2d/coherence/ev_threshold), backtest/ top-level 1→6 dosya (engine_v2, replay_engine, replay_engine_v3, archive_reader, becker_replay, metrics) + hyperopt 1→4 dosya, db/ 3→5 dosya (database, models, migrations.py dosya olarak, migration_phase79, ro_connect), telegram_bot/jobs/ 8 fantom→10 gerçek dosya + maintenance_jobs.py heartbeat notu, top-level +6 dizin (_archive, backups, data, data_store, logs, reports)
  - Doğrulama: 40 yeni path tek tek filesystem'da doğrulandı (40/40 OK). Eski yanlış isimler purged (sadece açıklayıcı T0.3.1 notu ve arşiv listesi kasıtlı kaldı).
- [x] **T0.3.2** `data_feeds/*` ghost import temizliği — risk: MEDIUM · Epic 1'e devredildi ✅ 2026-04-20 (T1.3 ile kapandı)
  - ~~`core/engine_signals.py:1121` → `from data_feeds.spread_signal import analyze_spread`~~ → T1.3 Commit 1'de silindi
  - ~~`core/engine_signals.py:1175` → `from data_feeds.event_waves import assess_market_quality`~~ → T1.3 Commit 1 (engine boost) + Commit 5 (`/market_quality`) sildi
  - ~~`tests/unit/test_phase71.py`~~ → T1.3 Commit 7'de dosya komple silindi
  - Nihai karar: **Remove** (hepsi silindi, archive purge T1.3 Commit 8)
- [x] **T0.3.3** "14-gate pipeline" ifadesini kodla hizala (docs-only) — risk: LOW ✅ 2026-04-20
  - DÜZELTME: Raporumda "README.md:32, 217" dedim ama grep README'de `14-gate` yok — gerçek konum `docs/ARCHITECTURE.md:49, 235`.
  - `docs/ARCHITECTURE.md:49` → "14-gate pipeline" → "çok-gate pipeline (14+ strategic gate, 30+ skip reason)" (engine_signals.py:106 ile birebir hizalı)
  - `docs/ARCHITECTURE.md:235` → "14-gate pipeline" → "14+ strategic-gate pipeline (30+ skip reason)"
  - TROUBLESHOOTING.md, STRATEGIES.md, PHASES.md, CHANGELOG.md, HANDOFF_PROMPT'taki "14-gate bypass" kullanımları `CLASSIC_BYPASS_ALL_GATES` ENV flag bağlamında — KASITLI, dokunulmadı.
- [x] **T0.4** TEMIZLEME_PLANI_2026-04-20.md ile TASKS.md'yi senkronize et (duplikasyon yok, tek doğru kaynak) — risk: LOW ✅ 2026-04-20
  - Yaklaşım: Seçenek α (iki dosyanın rolü ayrıldı, çakışma temizlendi). TEMIZLEME_PLANI = meta-plan (metodoloji, açılış promptu, modül şablonu). TASKS.md = aktif backlog (Epic 0-11, somut task'lar).
  - TEMIZLEME_PLANI değişiklikleri: Başlık "META-PLAN" notu + T0.4 uyarısı (aktif backlog TASKS.md); Bölüm 3 eski Epic 1-7 örneği kaldırıldı, sadece format standardı + TASKS.md pointer kaldı; Bölüm 5 Faz A-H → Faz A-L ve TASKS Epic 0-11 ile birebir tablo eşleştirmesi yapıldı.
  - TASKS.md değişiklikleri: **Yeni Epic 10 — Security Pass** eklendi (T10.1 secret leak, T10.2 Telegram input sanitization, T10.3 dependency CVE scan, T10.4 .env/.env.example sync) — PLANI'nın eski Epic 7 Security'sini somutlaştırır; eski Epic 10 (Mainnet Go/No-Go) → **Epic 11** renumbered (T10.1-3 → T11.1-3, "Epic 0-9" ref → "Epic 0-10").
  - Doğrulama: TASKS.md Epic 0-11 (12 başlık doğru); T10.x=4, T11.x=3 (beklenen sayılar); TEMIZLEME_PLANI'da eski Epic 1-7 örnek listesi grep=0; markdown fence balance OK.
- [x] **T0.5** Codename + faz string'i senkronla — risk: LOW, bağımlılık: T0.2 ✅ 2026-04-20
  - `telegram_bot/version.py:7` → `BOT_CODENAME = "Phase 82e Sprint 6 — /env_toggle hot-tune"` (CHANGELOG top ile birebir)
  - `telegram_bot/bot.py:856` hardcoded `"Phase 79"` literal'i kaldırıldı → `{BOT_CODENAME}` f-string referansı
  - Tek kaynak felsefesi (T0.2 gibi): bundan sonra faz ilerlediğinde sadece `version.py` güncellenir; `/help` paneli + startup log + Sentry release otomatik hizalanır
  - Doğrulama: AST parse OK × 2 dosya; exec(version.py) → BOT_VERSION=v9.7.9, BOT_CODENAME=Phase 82e Sprint 6; runtime'da "Phase 57"/"Phase 79" sıfır

---

## Epic 1 — Ghost Modules & Dead Imports  *(HIGH öncelik)*

Hedef: `core/engine.py` içinde try/except ile "soft-fail" edilen ama dosyası hiç olmayan modüller var. Ya dosyayı yaz, ya importu sil. Sessiz degradation kabul edilmez.

- [x] **T1.1** Ghost modül envanteri çıkar — risk: LOW ✅ 2026-04-20
  - **Kapsam:** 16 ghost modül (10 `core.*` + 5 `data_feeds.*` + `core.copy_leader` bonus), ~47 import site, hepsi `_archive/sprint4_modules/` altında mevcut (~127KB ghost kod). **Yani "hiç yazılmamış" değil — Sprint 4 temizliğinde arşive taşınmış, aktif kod import'u hâlâ yapıyor.**
  - **Tarama metodu:** `core.<mod>` + `from core.<mod>` + `import <mod>` regex, project-wide grep. T0.3.2 kapsamı da dahil edildi.

  **Core ghost envanteri:**
  | # | Modül | Aktif site | Bağlam | Arşiv |
  |---|---|---|---|---|
  | 1 | `core.cascade_detector` | engine.py:204 | try/except soft-fail (Phase 60) | 8.9 KB |
  | 2 | `core.lag_arbitrage` | engine.py:213 | try/except + `LAG_ARB_ENABLED` gate (Phase 60) | 10.6 KB |
  | 3 | `core.whale_signal` | engine.py:225 | try/except + `WHALE_SIGNAL_ENABLED` gate. ⚠️ `core/signals/whale_flow.py` aktif kodda mevcut — isim/kavram çakışması | 7.7 KB |
  | 4 | `core.markov_estimator` | 7 site: engine.py:278 (soft), engine_signals.py:1137 (soft), **phase76_handler.py:67 (⚠️ try/except YOK)**, tests/test_phase77.py × 4 (fonksiyon-içi) | `MARKOV_ENABLED` default=`true` → sessiz import fail | 10.8 KB |
  | 5 | `core.capital_allocator` | 5 site: engine.py:288 (soft + `CAPITAL_ALLOCATOR_ENABLED` default=`true`), tests/test_phase77.py × 4 | Phase 76, engine runtime'ında fiilen init edilmeye çalışılıyor | 13.5 KB |
  | 6 | `core.majority_voting` | 9 site: roadmap_handler.py:192 (try/except, `/vote`), tests/unit/test_phase72.py × 8 | Phase 72 consensus | 5.7 KB |
  | 7 | `core.evolutionary` | 7 site: roadmap_handler.py:156 (try/except, `/breed`), tests/unit/test_phase72.py × 6 | Phase 72 GA | 10.3 KB |
  | 8 | `core.pnl_verification` | 6 site: roadmap_handler.py:230 (try/except, `/drift_check`), tests/unit/test_phase72.py × 5 | Shadow↔paper drift | 8.5 KB |
  | 9 | `core.wf_validator` | 2 site: ai_brain.py:1420 (try/except, yorum: *"Phase 79b: wf_validator archived — make optional"*), ai_handler.py:528 (try/except, `/validate`) | Bilinçli archive, yorum var | 4.7 KB |
  | 10 | `core.strategy_correlation` | 1 site: roadmap_handler.py:437 (try/except, `/correlation_check`) | Phase 75+ | 6.3 KB |
  | 11 | `core.copy_leader` | **0 aktif site** — sadece arşiv dosyası duruyor | Dead, referans dahi yok | 9.9 KB |

  **Data feeds ghost envanteri (T0.3.2 kapsamı):**
  | # | Modül | Aktif site | Bağlam | Arşiv |
  |---|---|---|---|---|
  | 12 | `data_feeds.spread_signal` | 7 site: engine_signals.py:1121 (try/except + `SPREAD_SIGNAL_ENABLED` default=`false`), tests/unit/test_phase71.py × 6 | Phase 71 | 5.4 KB |
  | 13 | `data_feeds.event_waves` | 7 site: engine_signals.py:1175 (try/except + `EVENT_WAVES_ENABLED` default=`false`), roadmap_handler.py:384 (try/except, `/market_quality`), tests/unit/test_phase71.py × 5 | Phase 71 | 5.8 KB |
  | 14 | `data_feeds.pmxt_bridge` | 7 site: **aktif kodda 0 ref**, tests/unit/test_phase71.py × 7 | Phase 71 Kalshi bridge | 9.1 KB |
  | 15 | `data_feeds.whale_tracker` | 6 site: roadmap_handler.py:252 (try/except, `/whale`), tests/unit/test_phase71.py × 5 | Phase 71 | 8.7 KB |
  | 16 | `data_feeds.latency_monitor` | 5 site: **aktif kodda 0 ref**, tests/unit/test_phase71.py × 5 | Phase 71 | 6.5 KB |

  **Kritik bulgular:**
  - **(A) Hardening deliği:** `phase76_handler.py:67` → `from core.markov_estimator import (MARKOV_ENABLED, ...)` try/except dışında, `/markov` komutu slug-siz çağrıldığında `ImportError` atacak (fonksiyonun ortasındaki import olduğu için uygulamayı çökertmez ama handler exception olarak biter). Diğer 46 site try/except ya da fonksiyon-içi testler.
  - **(B) Sessiz ENV default'ları:** `MARKOV_ENABLED=true` ve `CAPITAL_ALLOCATOR_ENABLED=true` default — engine her boot'ta bu iki modülü init etmeyi deniyor ve sessiz fail ediyor (logger.debug). `SPREAD_SIGNAL_ENABLED=false`, `EVENT_WAVES_ENABLED=false` — zaten kapalı, etkisiz.
  - **(C) Dead files:** `copy_leader.py`, `pmxt_bridge`, `latency_monitor` aktif kodda hiç çağrılmıyor. `copy_leader` zaten silinmiş (arşivde duruyor), diğer ikisi sadece `tests/unit/test_phase71.py`'de.
  - **(D) Test fallout:** `test_phase71.py`, `test_phase72.py`, `test_phase77.py` fonksiyon-içi import kullanıyor → pytest collection geçer, test çalışırsa `ModuleNotFoundError` ile FAIL. Toplam ~40 test vakası ghost'a bağlı.
  - **(E) İsim çakışması:** `core.whale_signal` (arşiv) vs `core/signals/whale_flow.py` (aktif). İkisi de "whale" konseptini kapsıyor, biri aktif biri ölü — kafa karıştırıcı.
  - **(F) Bilinçli ghost var:** `ai_brain.py:1417` yorumu `wf_validator`'ı açıkça "Phase 79b archived — make optional" diye belgelemiş. Temizlemeye hazır.

  **Karar matrisi taslağı (T1.2'de onaya sunulacak):**
  | Modül | Önerilen karar | Gerekçe |
  |---|---|---|
  | cascade_detector | Remove | 1 site, Phase 60 deneysel; sonraki fazlarda terk edilmiş |
  | lag_arbitrage | Remove | 1 site, ENV-gated; kullanılmıyor |
  | whale_signal | Remove | Aktif `whale_flow.py` ile çakışıyor, isim temizliği |
  | **markov_estimator** | **Kullanıcı kararı** | 7 site + default=true. Restore (Phase 76 özelliği iade) veya Remove + ENV default=false + phase76_handler:67 hardening |
  | **capital_allocator** | **Kullanıcı kararı** | Phase 76/77 feature, 5 site + default=true |
  | majority_voting | Remove | `/vote` komut kullanılmıyor |
  | evolutionary | Remove | `/breed` komut kullanılmıyor |
  | **pnl_verification** | **Kullanıcı kararı** | `/drift_check` shadow↔paper divergence için değerli olabilir |
  | wf_validator | Remove | Yorum zaten bilinçli archive'ı belgeliyor |
  | strategy_correlation | Remove | `/correlation_check` kullanılmıyor |
  | copy_leader | Purge (arşiv dosyası dahil) | 0 ref |
  | spread_signal | Remove | default=false |
  | event_waves | Remove | default=false, 2 Telegram komutu feda edilir |
  | pmxt_bridge | Purge (test dahil) | 0 aktif ref |
  | whale_tracker | Remove | `/whale` kullanılmıyor |
  | latency_monitor | Purge (test dahil) | 0 aktif ref |

  Sonraki adım: T1.2 — bu matris üzerinden kullanıcı onayı + 3 "Kullanıcı kararı" rozeti olan modüle nihai verdikt.
- [x] **T1.2** Her ghost modül için karar matrisi üret — risk: MED ✅ 2026-04-20
  - T1.1 karar matrisi taslağı kullanıcıya sunuldu (üstte). Kullanıcı onayı: "komple onay. markov gereksiz sanırım bizim için şu an... yük olmasın bize" → 16 modülün tamamı **Remove/Purge**. 3 "Kullanıcı kararı" rozetli modül (markov_estimator, capital_allocator, pnl_verification) da Remove'a çevrildi.
  - Nihai verdikt tablosu = T1.1 matrisi + 3 kullanıcı-kararı → Remove (değişmedi: cascade_detector, lag_arbitrage, whale_signal, majority_voting, evolutionary, wf_validator, strategy_correlation, spread_signal, event_waves, whale_tracker = Remove; copy_leader, pmxt_bridge, latency_monitor = Purge)
- [x] **T1.3** Onaylanan kaldırmaları uygula (her modül ayrı commit) — risk: MED — **TAMAMLANDI 2026-04-20**
  - [x] **Commit 1** (engine.py + engine_signals.py) — Phase 60 Cascade/LagArb/Whale + Phase 71 Spread + Phase 76 Markov boost blokları + `capital_allocator.initialize` çağrısı silindi; py_compile OK, 0 live ref.
  - [x] **Commit 3** (ai_brain.py + ai_handler.py + bot.py) — Phase 33 walk-forward validation bloğu + wf label + `/validate` komutu silindi; py_compile OK, 0 live ref.
  - [x] **Commit 4** (phase76_handler.py + bot.py + menu_handler.py) — `phase76_handler.py` dosyası silindi; `/markov` + `/capital` CommandHandler + InlineKeyboard butonları + CMD_MAP girişleri + menu_cmd_markov/capital callback'leri temizlendi; py_compile OK, tek kalan ref testlerde (Commit 7 kapsamında).
  - [x] **Commit 5** (roadmap_handler.py + bot.py) — 6 ghost komut silindi: `/breed`, `/vote`, `/drift_check`, `/whale`, `/market_quality`, `/correlation_check`. bot.py import + registration temizlendi. Aktif kalanlar: `/ev_stats`, `/metrics`, `/surface`, `/latency`. py_compile OK.
  - [x] **Commit 6** (bot.py + menu_handler.py) — `menu_cmd_breed/vote/whale_callback` fonksiyonları silindi; bot.py imports + CallbackQueryHandler registrations + menu_advanced_callback UI metni/butonları temizlendi. CMD_MAP'te sadece `mistakes` kaldı. py_compile OK, 0 canlı ref.
  - [x] **Commit 7** (tests) — `tests/unit/test_phase71.py` (303 sat) + `tests/unit/test_phase72.py` (241 sat) silindi; `tests/test_phase77.py` içinden `TestMarkovEstimator` + `TestCapitalAllocator` + `TestHandlerImports.test_phase76_handler` silindi. Korunanlar: `TestTradeMemory`, `TestDecisionExplainer`, `TestExperimentRunner`, `TestBondingYield`, `TestHandlerImports.test_phase77_handler`. py_compile OK, 0 ghost import ref.
  - [x] **Commit 8** (`_archive/sprint4_modules/` purge) — 16 ghost modül dosyası (core/ 11 + data_feeds/ 5, ~168 KB) fiziksel silindi. Canlı kod artık _archive altındaki ghost modüllere hiçbir referans taşımıyor. TASKS.md'de kalan eski dokümantasyon satırları T1.3 kapatma notu olarak bırakıldı.
- [x] **T1.4** `except Exception` bloklarını spesifik hata tipiyle daralt — risk: MED, bağımlılık: T1.3 — **Faz 1 TAMAMLANDI 2026-04-20 ✅**
  - **Kapsam ayrımı (2026-04-20):** `core/` altında 284 bare `except Exception` bloğu (baseline 341'den T1.3 sonrası). Risk bazlı 3 faza bölündü; sadece **Faz 1** Epic 1'de yapıldı. Faz 2-3 ilgili Epic'lere ertelendi (aşağı bakınız).
  - **Faz 1 toplam çıktı:** 65 → 17 bare Exception (48 narrow + 17 gerekçeli catch-all); 30 satır dead code silindi (2 ghost orphan pattern: capital_allocator reserve/release + Phase 60 whale_signal/cascade_detector); 19 debug log upgrade (type(e).__name__ + logger.exception/warning); 3 import eklemesi (aiosqlite × 4 dosya, telegram.error × 2, json modül seviyesine); 4 dosya py_compile + AST doğrulama geçti.
  - **T1.4 Faz 1 — CRITICAL 4 dosya, 65 blok:** mainnet-kritik para/order/risk yollarında dar hata tipleri.
    - [x] `core/live_trader.py` (16 → 7 blok) — 2026-04-20 ✅
      - Commit A (narrow): 9 blok daraltıldı. `aiosqlite.Error` (L303/422/521), `RuntimeError` (L92/327 — executor wrap, CancelledError re-raise güvende), `(aiosqlite.Error, TypeError)` (L436 _save_state), `(aiosqlite.Error, json.JSONDecodeError, KeyError, IndexError)` (L453 _restore_state), `TelegramBadRequest` + `TelegramError` (L492/495 _notify). Üst-dosyaya `import aiosqlite` + `from telegram.error import BadRequest, TelegramError` eklendi (ikisi de requirements.txt'te mevcut).
      - Commit B (debug upgrade): 7 catch-all blok korundu (py-clob-client wraps, _place/_sync_order geniş yollar), her birine `type(e).__name__` hata mesajı + `logger.exception` (L256 _place, L388 _sync_order) ile traceback eklendi. Silent pass'ler `logger.warning`'e yükseltildi (L436/453).
      - Doğrulama: `py_compile` OK × 2, AST imports OK, `grep "except Exception"` 16 → 7 (bekleneceği gibi).
    - [x] `core/engine_settlement.py` (28 → 8 blok + 1 dead code delete) — 2026-04-20 ✅
      - Commit C1 (dead code): L433-439 `_capital_allocator.release()` bloğu silindi — T1.3 Commit 1'de modül kalkmıştı, `getattr(self, "_capital_allocator", None)` her zaman `None` döndüğünden branch hiç execute olmuyordu. Yorumla yerine tarihsel not bırakıldı.
      - Commit C2 (narrow): 19 blok daraltıldı. `aiosqlite.Error` (classic fills select/update, AI fills select/update, journal log UPDATE, trade UPDATE finalize yolları), `(ValueError, TypeError)` (price parsing/hesap kısımları), `TelegramError` (`_notify` çağrıları 2 yerde), `(KeyError, IndexError)` (row dict erişimleri), `(ImportError, AttributeError, TypeError)` (adaptive tracker soft-fail), compound tuple 5 yerde. Üst-dosyaya `import aiosqlite` + `from telegram.error import TelegramError` eklendi.
      - Commit C3 (debug upgrade): 8 catch-all blok korundu (classic exit notify outer, classic resolution notify outer, `_ai_trade_analysis` httpx wrap, `_close` transaction outer, `log_decision_close` journal I/O, micro_weight/becker_weight adaptive trackers, `live.check_settlement` CLOB+DB+telegram wrap). 3 tanesine `logger.exception` traceback + kalanlara `type(e).__name__` loglama eklendi.
      - Doğrulama: `py_compile` OK, AST inspect → 28 toplam handler (8 Exception + 20 narrowed/specific) olarak doğrulandı. `grep "except Exception"` 28 → 8 (bekleneceği gibi).
    - [x] `core/engine_fills.py` (12 → 2 blok + 2 dead code delete) — 2026-04-20 ✅
      - Commit D1 (dead code): `on_real_trade` içinden L146-161 Phase 60 `_whale_signal` + `_cascade_detector` orphan (16 satır) silindi — T1.3 Commit 1'de modüller kalkmıştı, `getattr(self, "_whale_signal", None)` ve `getattr(self, "_cascade_detector", None)` her zaman `None` döndüğünden dallar dead code. Yorumla tarihsel not bırakıldı.
      - Commit D2 (dead code): `_fill` içinden L428-434 Phase 76 `_capital_allocator.reserve()` orphan (7 satır) silindi — engine_settlement.py C1 kardeşi (release). Aynı ghost modül pattern'i, aynı sebeple ölü.
      - Commit D3 (narrow): 9 blok daraltıldı. `(TypeError, ValueError)` × 3 (orderbook imbalance bid/ask loops, `_rest_latency_sleep`), `aiosqlite.Error` × 3 (slippage UPDATE, reasoning_json UPDATE, strategy label SELECT), `(aiosqlite.Error, AttributeError)` × 1 (reasoning_chain persist), `(TypeError, ValueError, AttributeError)` × 1 (`on_real_trade` outer), `ValueError` × 1 (cancel loop `list.remove`). Üst-dosyaya `import aiosqlite` eklendi (telegram.error burada kullanılmıyor).
      - Commit D4 (debug upgrade): 2 catch-all blok korundu (L297 `_check_pending` per-order loop — 97 satır genişliğinde CLOB/WS/scanner/settings zinciri, silent `pass` → `logger.warning(type(e).__name__)`; L465 `live.maybe_mirror` — CLOB+DB+telegram zinciri, `logger.debug` → `logger.exception`). İkisi de `# noqa: BLE001` + T1.4 yorumu ile işaretlendi.
      - Doğrulama: `py_compile` OK, AST inspect → 14 total handler (2 Exception + 9 narrowed + 3 pre-existing). `grep "except Exception"` 12 → 2 (bekleneceği gibi).
    - [x] `core/risk_manager.py` (9 → 0 blok) — 2026-04-20 ✅
      - Commit E1 (narrow): 9 blok daraltıldı, tek bare `except Exception` kalmadı. `(ValueError, TypeError, AttributeError)` × 4 (Gate 7 streak cooldown L248, loss-streak alert L360, pnl soft alert L376, boot streak cooldown L608), `(aiosqlite.Error, TypeError, ValueError)` × 1 (save_state), `(json.JSONDecodeError, TypeError, ValueError, AttributeError)` × 1 (per_market_exposure restore), `(aiosqlite.Error, ValueError, TypeError, KeyError)` × 1 (load_state outer), `(aiosqlite.Error, ValueError, TypeError, IndexError)` × 1 (_rebuild_per_asset_exposure), `(aiosqlite.Error, IndexError)` × 1 (_rebuild_strategy_market_open). Üst-dosyaya `import json` (modül seviyesine taşındı, önce 2 fonksiyon içinde `import json as _json` vardı) + `import aiosqlite` eklendi.
      - Commit E2 (debug upgrade): 2 kritik blok'ta log seviyesi yükseltildi — L574 `save_state` `logger.debug` → **`logger.warning`** (crash-restart cycle halted state + daily_pnl kaybı görünür olsun); L635 `load_state` outer `logger.warning` → **`logger.exception`** (boot-time issue'lar tam traceback ile). 7 debug blok `type(e).__name__` prefix ile zenginleştirildi.
      - Dead code: YOK (risk_manager self-contained, T1.3 orphan tespit edilmedi).
      - Doğrulama: `py_compile` OK, AST inspect → 11 total handler (9 narrowed + 2 pre-existing). `grep "except Exception"` 9 → 0 (hedef).
  - **Yaklaşım:** Her bloğu araştır → spesifik exception tipini belirle (I/O, network, DB, JSON decode, API auth, vb.) → kullanıcı onayı → dosya-dosya commit. `except Exception as e: logger.exception(...)` catch-all kalması uygun bulunanlar açıkça yorumla işaretlenir.

  **T1.4 Faz 2 → Epic 8 T8.1 altına devredildi** (engine_signals.py 42 + engine.py 34 + ai_brain.py 47 = 123 blok, HIGH risk). Gerekçe: 14-gate signal pipeline + engine orchestration + AI decision path hep birlikte denetlenmeli çünkü exception tipi bağlamı paylaşıyor (ör. CLOB/HTTP/DB/telegram chain'i). Faz 1 metodolojisi (research → narrow/dead code → debug upgrade → py_compile + AST) aynen uygulanacak.
  **T1.4 Faz 3 → Epic 7 T7.6 altına devredildi** (auto_optimizer.py 22 + 19 MED-LOW dosya ≈ 96 blok). Gerekçe: önce T7.1-T7.3 dead code purge'ü bitsin, silinmiş dosyalarda boşuna daraltma yapmayalım. Faz 1 metodolojisi aynen uygulanacak.

---

## Epic 2 — Kök Klasör Arınması  *(LOW risk ama büyük temizlik)*

Hedef: Kök dizinde 63 deploy/rollback/hotfix .bat + 32 handoff/audit .md/.html var. Mainnet öncesi sadece `start.bat`, `stop_bot.bat`, `backup.bat`, `watchdog.bat` kalmalı; gerisi arşive.

- [x] **T2.1** 59 .bat kök dizinden `_archive/` alt klasörlerine taşındı (2026-04-20) — risk: LOW
  - `_archive/deploy_superseded/` → 33 deploy_*.bat (1 çakışma `deploy_phase82b5_later.bat` olarak ayırıldı, toplam 34)
  - `_archive/hotfix_superseded/` → 7 (hotfix_*, fix_classic_threshold, quickfix_82b2, restart_classic_bypass_pin)
  - `_archive/rollback_superseded/` → 6 (generic rollback + 5 versiyonlu)
  - `_archive/diag_oneshot/` → 6 (analyze_ob_snapshots, diag_*, diagnose_*, verify_*)
  - `_archive/operational_unused/` → 3 (run_tests, vacuum_db, reset_and_start — kullanıcı kullanmıyor)
  - `_archive/db_utilities_oneshot/` → 4 (clean_overfit×2, create_covering_index, create_split_backtest_index — one-shot)
  - Kökte kalan: `start.bat`, `stop_bot.bat`, `backup.bat`, `watchdog.bat` ✅
- [x] **T2.2** 22 .md kök dizinden arşive taşındı (2026-04-20) — risk: LOW
  - `_archive/handoff_superseded/` → 6 (HANDOFF_NEXT_CHAT, HANDOFF_PHASE82B3, HANDOFF_PHASE82B5, HANDOFF_PROMPT, HANDOFF_PROMPT_V2, HANDOFF_SPRINT4)
  - `_archive/roadmap_superseded/` → 6 (ROADMAP_GUNCEL_2026-04-17, PHASE62, PHASE64, PHASE66_ONWARDS, PHASE79_ULTRA, RESURRECTION)
  - `_archive/audit_snapshots/` → 6 (ACMASIZ_AUDIT_2026-04-15, AUDIT_REPORT_PHASE62, BRAIN_TRANSFER_PHASE62, EXTERNAL_ANALYSIS_REPORT, GITHUB_REPOS_DEEP_ANALYSIS, POLYPAPER_BOT_FULL_ANALYSIS)
  - `_archive/phase_snapshots/` → 2 (MEGA_DIAGNOSIS_PHASE79, PHASE_75_IMPLEMENTATION)
  - `_archive/doc_superseded/` → 2 (DEPLOYMENT.md [eski 03-14, DEPLOY_INSTRUCTIONS üstün tutuldu], BUGFIX_CHANGELOG.md [CHANGELOG'a merge olmuş])
- [x] **T2.3** Kökte kalacak 10 doküman ✅ (2026-04-20):
  - Üretim: `README.md`, `CHANGELOG.md`, `SECURITY.md`, `DEPLOY_INSTRUCTIONS.md`, `WATCHDOG_SETUP.md`, `EDGE_DISCOVERY_GUIDE.md`
  - Günlük çalışma: `HANDOFF_PROMPT_2026-04-20.md` (en güncel), `TASKS.md`, `TEMIZLEME_PLANI_2026-04-20.md`, `BUGUN_NE_YAPACAGIM.md`
  - Not: `LICENSE` dosyası kökte yok (spec'de vardı, düşülmüş). DEPLOYMENT.md → DEPLOY_INSTRUCTIONS.md ikame edildi (kullanıcı onayıyla).
- [x] **T2.4** `.githooks/`, `.github/` dokunulmadan bırakıldı (CI/CD otomatik pas)
- [x] **T2.5** Kök .py + .html + .txt + .docx temizliği (2026-04-20) — risk: LOW:
  - `.py`: 3 `fix_*.py` → `_archive/hotfix_superseded/`; 4 kullanılmayan (test_data_sources, verify_setup, wal_checkpoint, wipe_all_strategies) → `_archive/operational_unused/`. Kökte sadece `main.py` (bot entry, `start.bat` → `py -3.11 main.py`).
  - `.html` (6): AUDIT_PHASE62, POLYPAPER_LIVE_READINESS_AUDIT, X_MAKALELER_ULTRA_ANALIZ(+V2) → `_archive/audit_snapshots/`; PHASE60_ROADMAP, ROADMAP_PHASE58 → `_archive/roadmap_superseded/`. Kökte 0 .html.
  - `.txt` (2): AUDIT_REPORT_2026-04-11.txt → `_archive/audit_snapshots/`; deploy_ws_stale_threshold_log.txt → `_archive/deploy_superseded/`. Kökte sadece `requirements.txt`.
  - `.docx`: PolyPaper_Ultra_Analiz_Raporu.docx → `_archive/audit_snapshots/`.
  - **Referans doğrulama yapıldı**: `main.py` start.bat tarafından aktif, 10+ referans var — korundu. `verify_setup.py` scripts/setup_github_*.bat tarafından referanslı ama one-time setup, kullanıcı onayı ile arşive taşındı.
- **EPİK 2 KAPANIŞI (2026-04-20)**: Kök = **19 dosya** (önce 100+). 4 .bat + 10 .md + 1 .py + 1 .txt + LICENSE + polypaper.db + watchdog.vbs. Arşiv yapısı: 11 nested subfolder. Mainnet'e hazır.

---

## Epic 3 — Risk Manager & Bypass Denetimi  *(CLOSED — 2026-04-20)*

Hedef: `core/engine_signals.py` içinde "classic bypass" ifadesi çok yerde geçiyor. Hangi gate'leri atlıyor, hangi mantıkla, 9-gate RiskManager hâlâ çalışıyor mu — doğrula.

**Özet (closure)**: 9 bypass noktası haritalandı, 9-gate RiskManager Classic için ÇALIŞIYOR ✅ (check_trade L1211 _classic_free ile atlanmıyor). Classic free-trade design intent kullanıcı onayıyla kilitlendi. Docstring tutarsızlıkları (engine_signals.py:105-123 FEE_TAIL/TOKEN_CAP + risk_manager.py:21-22 stale BUG-10 uyarısı) düzeltildi. BUG-10 Phase 54 P0-04'te zaten fixed (test coverage dahil). T3.4 round-trip test suite eklendi (22 test, all green).

- [x] **T3.1** Bypass haritası tamamlandı (2026-04-20) — risk: LOW
  - `_eval_market_checks`: engine_signals.py:341-351 (TOO_LATE) [CLASSIC_BYPASS_ALL_GATES]
  - `_eval_signal`: engine_signals.py:616-621 (EMA) / 673-676 (LOW_VOL) [CLASSIC_BYPASS_ALL_GATES]
  - `_eval_signal`: engine_signals.py:761-766 (ALLOWED_ZONES) [CLASSIC_RESPECT_ZONES opt-in]
  - `_eval_signal_boosters`: engine_signals.py:813-817 (ORACLE_PARITY) [CLASSIC_BYPASS_ALL_GATES]
  - `_eval_gates`: engine_signals.py:1107-1117 (SLIPPAGE) [CLASSIC_BYPASS_ALL_GATES]
  - `_eval_gates`: engine_signals.py:1179-1187 (UNSELLABLE) [CLASSIC_RESPECT_UNSELLABLE opt-in]
  - `_eval_place_order`: engine_signals.py:1626-1629 (FEE_TAIL) [CLASSIC_RESPECT_FEE_TAIL opt-in]
  - `_eval_place_order`: engine_signals.py:1679 (TOKEN_CAP) [CLASSIC_RESPECT_TOKEN_CAP opt-in]
  - Master flag: `_classic_free_mode()` @ L116-127 (`CLASSIC_BYPASS_ALL_GATES`, default true)
- [x] **T3.2** `RiskManager.check_trade` doğrulandı (2026-04-20) — risk: LOW
  - **Çağrı satırı**: engine_signals.py:1211 (baseline 1321 → drift güncellendi) içinde `_eval_gates`
  - **`_classic_free` ile bypass EDİLMİYOR** — Classic strategiler 9-gate RiskManager'dan geçiyor ✅
  - `verdict.approved==False` → `skips.record("RISK")` → return None
- [x] **T3.3** Kullanıcı onayı alındı (2026-04-20) — risk: HIGH
  - **Karar**: "Classic tip planının hiçbir filtreye ve sıfırlamaya maruz kalmaması. Özgürce trade etmekti." (kasıtlı design intent)
  - Tüm 9 bypass → **KALSIN** (HOTFIX v3/v5/v6 design intent)
  - **Dokümantasyon fix uygulandı**: engine_signals.py:105-123 docstring güncellendi — FEE_TAIL + TOKEN_CAP "HARD SAFETY" listesinden "OPT-IN RESPECT" grubuna taşındı (yanlış documentation düzeltildi). Kod davranışı DEĞİŞMEDİ.
  - Design intent notu eklendi: "Classic strategies must trade freely based on user-directed triggers. Hard safety + check_trade only."
- [x] **T3.4** `RiskLimits.to_dict/from_dict` round-trip testi eklendi (2026-04-20) — risk: MED
  - **Oluşturulan**: `tests/test_risk_limits_roundtrip.py` (22 test, 7 test class, 335 satır).
  - **Kapsanan edge case'ler**: default değerler, custom scalar'lar, `per_asset_limits` dict, empty dict davranışı (documented fallback-to-default quirk), Unicode/Türkçe asset adları (BİTCOİN/ЕТН/€UR), float precision (12.3456789), büyük integer (999_999), negative (-1.0 kill-switch), corrupt scalar (ValueError/TypeError → default), corrupt per_asset value (skip), empty input dict (defaults), missing fields (partial dict), unknown keys (forward-compat silent drop), `per_market_limit` ↔ `per_asset_limits` key collision regression, double round-trip idempotency.
  - **Sonuç**: 22/22 PASSED (0.14s). Tüm üretimdeki `RiskLimits` persist/restart davranışı locked.
  - **Not**: Empty `per_asset_limits` quirk keşfedildi — `if per_asset:` guard (L85) nedeniyle "explicitly cleared" davranışı "not persisted" ile ayırt edilemiyor. Test davranışı kilitliyor; gelecek değişiklik bilinçli olmalı.
- [x] **T3.5** BUG-10 araştırma + docstring fix tamamlandı (2026-04-20) — risk: MED
  - **Araştırma sonucu**: BUG-10 Phase 54 P0-04'te ZATEN DÜZELTİLDİ (operator `<=`, L215/L220/L391 üçünde de tutarlı). Test coverage var: `tests/test_phase55_critical.py::TestRiskDailyLossBoundary`.
  - **Stale docstring uyarısı temizlendi**: risk_manager.py:21-22'deki "⚠️ BUG-10 ... Toplu patch bekleniyor" silindi, yerine "✅ Daily loss boundary uses <= operator (Phase 54 P0-04 fix, 2026-04-20 audited)" + test path eklendi.
  - **L217 margin behavior belgelendi**: Kullanıcı onayıyla (mevcut davranış doğru) — margin check REJECTS trade ama halt=True SET ETMEZ; daily_pnl hâlâ limit içinde, sadece bu specific trade riski var. Küçük trade'ler geçebilsin. Açıklayıcı 5-satır yorum eklendi (risk_manager.py:220 öncesi).
  - **Kod davranışı değişmedi**, sadece dokümantasyon + belge.

---

## Epic 4 — Simulator Doğruluğu (Fee / Slippage / Latency)  *(CRITICAL)*

Hedef: Paper+shadow trading'in canlıya parite vermesi için simülasyon girdileri gerçekçi olmalı. Mainnet'e geçmeden mutlaka denetlenmeli.

- [x] **T4.1** Fee modeli denetimi — `core/fees.py` (v1) vs `core/fees_v2.py` + canlı Gamma API cross-check — tamamlandı (2026-04-20/21) — risk: LOW
  - **Faz A — Statik denetim:**
    - `core/fees.py` (v1 quadratic) production'da kullanılmıyor. Tek consumer: `tests/unit/test_fees_v2.py::test_legacy_category_matches_quadratic` (regresyon oracle).
    - `core/fees.py` header'ına "LEGACY REFERENCE ONLY" docstring block eklendi (fees_v2 regresyon oracle rolü açık).
    - `backtest/simulation/fee_model_v3.py:11-18` yanıltıcı docstring ("older strategies reference core.fees") düzeltildi.
    - `test_fees_v2` 13/13 PASS — legacy oracle davranışı korundu.
  - **Faz B — Canlı Gamma API doğrulaması (ETH 5m + SOL 15m, feeType=crypto_fees_v2):**
    - ✅ `feeSchedule.rate: 0.072` ↔ `fees_v2.crypto.taker_rate: 0.072` eşleşiyor.
    - ✅ `feeSchedule.exponent: 1` ↔ `fees_v2.crypto.taker_exp: 1` eşleşiyor.
    - ✅ `feeSchedule.takerOnly: true` ↔ v2 taker-only tasarımı.
    - ✅ 5m/15m schedule identical → `fee_model.py:74` `and False` (DYNAMIC_15M multiplier off) kararı doğru, korundu. G5 kapandı.
    - ✅ `feeType: "crypto_fees_v2"` market-level discriminator (category field market'te yok; event `tags[].slug` içinde `crypto`/`crypto-prices`). G2 kapandı.
    - ⚠️ **Drift**: live `feeSchedule.rebateRate: 0.2` vs fees_v2 `crypto.maker_rebate_pct: 0.25` → %5 fark. Faz C'ye ertelendi.
    - ⚠️ **Semantic netlendi**: `orderMinSize: 5` → birim **shares** (USDC değil, `engine.py:1031-1036` yorumu zaten belgelemiş). Paper'da `MIN_ORDER_SHARES=1.0` ile gevşetilmiş. Live'da p > 0.20 pazarlarda $1 emirler reddedilir.
  - **Faz A (bonus) — Log tarama (polypaper.log + .1/.2/.3, ~18 MB, 2026-04-13 → 04-20):**
    - `LIVE_ENABLED=true` hit: **0** | `✅ CLOB` hit: **0** | `orderID` hit: **0** | `MIN_SHARES` reject: **0**.
    - `LIVE_ENABLED=false / STANDBY` hit: **62** (her restart'ta).
    - Sonuç: Bot bu dönem hiç live CLOB emri göndermedi. `orderMinSize=5 shares` şu an hiçbir şeyi bloklamıyor (hiç live emir yok). Memory "shadow live ON" = "auth hazır + budget okundu", canlı emir gönderdiği anlamına gelmiyor.
  - **Ertelenenler (Faz C / mainnet-öncesi):**
    - C1: `maker_rebate_pct: 0.25 → 0.20` + `test_fees_v2` maker assertion recalibration (1 satır config + test update).
    - C2: `engine_signals.py:1620` MIN_SHARES check'ini `LIVE_ENABLED=true` iken 5.0'a yükselt (paper=live parity, opsiyonel).
    - C3: Runtime per-market `feeSchedule` fetch (hardcoded constant yerine; `market_sync.py` veya benzeri).
- [x] **T4.2** Slippage/fill modelini denetle: `backtest/simulation/fill_model.py` (484 satır) — risk: MED — **Faz A tamamlandı (2026-04-21)**
  - **Statik audit (484 satır, 7 mode):** Mimari sağlam — `_real_orderbook_walk` (VWAP) ve `_maker_fill` (Phase 57 depth-bucketed) iyi belgelenmiş, dokunulmadı. 4 alanda kalibrasyonsuz heuristic bulundu.
  - **Faz A — Statik düzeltmeler (✅ done):**
    - `SPREAD_COST = 0.005` → ENV `FILL_SPREAD_COST` ile override, docstring'e Polymarket gerçek aralık (1-5c) + Faz B referansı.
    - `_orderbook_walk` tier'ları (0.2/0.5/1.5/3.0%) → docstring "SYNTHETIC — NOT calibrated" + `REAL_ORDERBOOK` canonical olduğu açıklandı.
    - `_market_impact_fill` 0.01 scale → `IMPACT_SCALE` class attr + ENV `FILL_IMPACT_SCALE`/`FILL_IMPACT_MIN_FLOOR`. Docstring "Almgren-Chriss approximation, NOT calibrated".
    - Latency drift `0.08 bps/ms` → ENV `FILL_LATENCY_DRIFT_BPS_PER_MS`, docstring "HEURISTIC placeholder, NOT empirically calibrated".
    - **Cross-check**: `engine_fills.py:290-291` live fallback de `cur * 0.002` (0.2% adverse) — backtest'le tutarlı.
    - **Test**: smoke_phase51 + test_fees_v2 → 12/12 PASS. ENV override sanity → 3/3 wired.
- [x] **T4.3** REST latency: `config/settings.py:44` `REST_LATENCY_MS=200`, jitter=80 — canlı p50 ile kıyasla — risk: LOW — **Faz A tamamlandı (2026-04-21)**
  - **Statik audit:** Implementation temiz (`engine_fills.py:_rest_latency_sleep` 18 satır), validator clamp `[0,5000]`/`[0,2000]` mevcut. **Ama 200ms / 80ms değerleri kanıtsız** — Phase 39 docstring iddiası, log'larda hiç REST RTT ölçümü yok.
  - **Backtest drift:** `replay_engine.py:98` 250ms kullanıyor (settings'ten 50ms farklı) — kasıtlı pessimism notu eklendi.
  - **Faz A — Statik düzeltmeler (✅ done):**
    - `engine_fills.py:_rest_latency_sleep` docstring → "HEURISTIC, NOT EMPIRICALLY MEASURED" + Faz B referansı.
    - `config/settings.py:41-50` docstring → dürüst "plausible-median estimates" notu + replay_engine 50ms drift açıklaması.
    - `replay_engine.py:98` ConfigLatency → docstring "HEURISTIC pessimism, pending Faz B alignment".
    - **Yeni modül**: `core/observability/rest_timing.py` (~150 satır) + `__init__.py`. ENV-gated (`REST_TIMING_TELEMETRY=true`), `time_call(label)` async ctx manager + `record_ms()` API + `get_summary()` p10/p50/p90/p99 + `dump_to_file()`. Default OFF = zero overhead.
    - **Test**: AST 5/5 OK + smoke (direct record + async ctx + disable = noop) PASS.
- [ ] **T4.7** *(Epic 4 T4.3 Faz B — yerel Windows iş)* — REST RTT empirical kalibrasyon — risk: LOW
  - Bot'u 24h `REST_TIMING_TELEMETRY=true` ile çalıştır.
  - `live_trader.py` + `polymarket_client.py` HTTP çağrılarını `time_call("clob.create_order")`, `time_call("clob.cancel_order")`, `time_call("gamma.get_market")` vb. ile sar.
  - 24h sonra `/dump_rest_timing` admin command (yeni eklenecek) → `data_store/rest_timing_24h.json`.
  - p50/p_iqr değerlerinden `REST_LATENCY_MS` / `REST_LATENCY_JITTER_MS` / `replay_engine.latency_mean_ms` defaults'larını yeniden ata.
  - Output: `.env.production` veya `config/settings.py` literal güncellemesi + commit notu.
- [ ] **T4.5** *(Epic 4 T4.2 Faz B — yerel Windows iş)* — Empirical slippage kalibrasyonu — risk: MED
  - 1417 trade'lik live `executions.realized_slippage` kolonunu sorgula (paper + shadow mix).
  - Percentile breakdown çıkar (p10/p50/p90 + bucket: depth tier, market_type, hour_utc).
  - `fill_model.py` 4 heuristic (SPREAD_COST, _orderbook_walk tier'ları, IMPACT_SCALE, LATENCY_DRIFT) için real-data değerleri belirle.
  - Output: `backtest/calibration/slippage_2026q2.json` + `.env.example` ENV override önerileri.
  - Bağımlılık: 9.3GB live DB sandbox'ta okunamıyor → kullanıcı yerel Windows'ta `scripts/calibrate_slippage.py` ile çalıştırır.
- [ ] **T4.6** *(Epic 4 T4.2 Faz C — T4.5 sonrası)* — Backtest sweep parity — risk: LOW
  - Eski heuristic değerlerle 1 ay backtest → PnL baseline.
  - Yeni kalibre edilmiş değerlerle aynı backtest → PnL delta.
  - Kabul kriteri: |delta| < 5% (overfit yok) ve direction tutarlı (real fills daha kötüyse backtest de kötüleşmeli).
  - Output: `backtest/results/slippage_calibration_compare_v1_v2.html`.
  - Bağımlılık: T4.5 tamamlanmalı.
- [x] **T4.4** LIVE vs PROTECTED parity audit tamamlandı (2026-04-20) — risk: MED
  - **Bulgu**: 3 ayrı set farklı işlevlere sahip, tutarsızlık DEĞİL — kasıtlı:
    - `LIVE_STRATEGIES` (live_trader.py:40, 3 item): gerçek parayla trade eden whitelist.
    - `ai_brain.PROTECTED_STRATEGIES` (ai_brain.py:41, 2 item): LLM-driven AI Brain STOP/TUNE eylemlerinden korur (label-based).
    - `auto_optimizer.PROTECTED_STRATEGY_TYPES` (auto_optimizer.py:45, type="classic"): user-managed Classic tipi determinsitik auto-pause kurallarından korur.
  - **Parity kararı**: paper ve live aynı governance'tan geçsin (self-healing design). auto_optimizer'ın deterministik kurallarıyla paper'da kötü giden live'da da durur → mirror doğru.
  - **AI_F_BTC_5m_up_0.38 LIVE ama PROTECTED değil — kasıtlı**: deneysel, AI Brain stop/tune edebilmeli.
  - **Kod davranışı DEĞİŞMEDİ**. Yalnız dokümantasyon güçlendirildi:
    - `ai_brain.py:41` üstüne 12 satır docstring eklendi (PROTECTED semantiği, LLM-shield vs quantitative-rules ayrımı)
    - `live_trader.py:40` üstüne 13 satır docstring eklendi (parity principle + LIVE ≠ PROTECTED netliği)
    - Her live stratejinin yanına `[PROTECTED]` / `[experimental]` etiketi

---

## Epic 5 — Concurrency & State Hygiene  *(HIGH)*

Hedef: `asyncio.Lock` kullanımları, atomik bakiye düşme, WAL locking, WS reconnect flush — race condition testleri.

- [x] **T5.1** ✅ 2026-04-21 — `_trade_lock` audit tamamlandı. 4 acquire site ✅ doğru. 7 `_pending` mutation site denetlendi; 1 lock-free: `engine.py:922 _pending.clear()` in sync `_check_ws_health`. **Option B**: fn `async def`'e çevrildi + `async with self._trade_lock` wrap (commit `270de36`). AST test geçti: TradingEngine 7 async/6 sync, `_check_ws_health` async listede.
- [x] **T5.2** ✅ 2026-04-21 — `db/database.py::atomic_deduct_balance` (isim: audit notasyonu `get_and_deduct_balance` yanlıştı). **Ana bulgu**: SELECT+UPDATE race YOK — fonksiyon zaten `UPDATE ... WHERE balance >= amount` single-SQL pattern kullanıyor, atomic. **Secondary fix**: "database is locked" retry wrapper eksikti → concurrent commit sırasında trade silent-drop riski vardı. Fix A uygulandı: 3x retry + 100/200/300ms exponential backoff. Test C: `tests/unit/test_atomic_deduct.py` (6 test, 10 concurrent deduct race dahil). 6/6 pass. — commit: `73637df`
- [x] **T5.3** ✅ 2026-04-21 — `pending_reserved` audit. **Bulgu**: Tek hesap sitesi (`engine_signals.py:1232`), `engine.py`'de paralel hesap yok (docstring referansı var sadece). Aktif race YOK (evals `for s in strats:` sequential). **Fix B**: `_compute_pending_reserved(wallet_id)` helper extract (DRY). **Fix A**: locked append scope'u içine defensive re-check eklendi, yeni skip reason `RESERVED_OVERFLOW` — future-proof (paralel eval introduced olursa overdraw catch). **Test C**: `tests/unit/test_pending_reserved.py` 11 test (multi-wallet isolation + overflow predicate). 11/11 pass. — commit: `174dd37`
- [x] **T5.4** ✅ 2026-04-21 — WS reconnect + live_prices staleness audit. **Bulgu**: `_loop()` reconnect başarılı olduğunda `_subscribed` re-subscribe ediyor ama `live_prices` cache pre-drop değerlerle doluydu, `get_live_price()` sadece `WS_STALE_SEC` yaş kontrolü yapıyordu → bir reconnect sonrası 60 saniyeye kadar pre-drop tick servisi yapılabiliyordu (trade risk). **Fix A**: `data/websocket_client.py`'a `_connected_since: float` epoch marker eklendi, reconnect başarılı olduğunda `time.time()`'a set, `get_live_price()` entry_ts < `_connected_since` olan cache'leri `None` döndürüyor. **Fix B**: `core/engine.py::_check_ws_health` reconnect edge (offline→online) algılıyor + yeni `_backfill_prices_on_reconnect` helper `asyncio.gather` ile tüm subscribed tokenlar için REST `/midpoint` çağrısı yapıyor, valid sonuçları fresh timestamp ile `ws.live_prices`'a yazıyor → stale-gap 2-15s'den ~500ms'ye düşüyor. Sanity filter (0.005 < p < 0.995) ve exception-safe (`return_exceptions=True`). **Test C**: `tests/unit/test_ws_reconnect.py` 10 test (Fix A 4 test: legacy, pre-reconnect invalidated, post-reconnect accepted, age-independent; Fix B 6 test: populate, skip None, empty subscribed, exception-safe, out-of-range reject, end-to-end integration). 10/10 pass. Regresyon: T5.2+T5.3+T5.4 birlikte 27/27 ✅.
- [x] **T5.5** ✅ 2026-04-21 — WAL autocheckpoint audit. **Ana bulgu**: `wal_autocheckpoint=5000` PASSIVE modda çalışıyor → writer bloke etme riski **YOK** ✅ (LOW risk etiketi doğru). **Secondary bulgular**: (1) Canlı durum WAL=79MB (threshold=20MB, ~4x bloat) — long-read connections (`daily_db_snapshot_job`, `ro_connect`) autocheckpoint'i ilerletemiyor; (2) Kod tabanında manuel `PRAGMA wal_checkpoint(TRUNCATE)` yok → WAL monotonik büyüyor; (3) Büyük WAL = crash recovery gecikmesi. **Fix A**: `telegram_bot/jobs/maintenance_jobs.py::wal_checkpoint_job` (~75 satır) + `bot.py` JobQueue kaydı — her 6 saatte bir TRUNCATE, `ENABLE_WAL_CHECKPOINT`/`WAL_CHECKPOINT_INTERVAL_HOURS` ENV kontrollü, log'ta before/after MB + busy/log/ckpt pages. **Fix B**: `tests/unit/test_wal_checkpoint.py` 6 test (idle no-op, shrink-after-writes, idempotent, data-survives, concurrent-reader, return-shape). 6/6 pass. Regresyon: Epic 5 T5.2+T5.3+T5.4+T5.5 birlikte 33/33 ✅. Admin command (`/wal_checkpoint`) atlandı — periodic job + `/env_toggle` emergency ayarı yeterli.
- [x] **T5.6** ✅ 2026-04-21 — WS subscription cap (`MAX_WS_TOKENS=200`) overflow fix. **Ana bulgular**: (1) `prune_stale_tokens` tanımlı AMA sıfır call-site → dead function, `_subscribed` monotonik büyüyor; (2) `market_scanner._subscribed_ws_tokens` de asla küçülmüyor → cap-skipped tokenlar "ghost subscribed" olarak işaretleniyor, reconnect'te re-sub edilmiyor (HIGH risk state drift); (3) Cap slicing `set(list(new)[:avail])` nondeterministic (hash-seed) → protected/shadow-live tokenlar rastgele düşebilirdi (MED); (4) Cap hit WARN telemetrisi yok (LOW). **Fix A**: `data/market_scanner.py::_do_scan` sonunda `live_token_ids` setine her market'in up_token+down_token'ı toplanıyor, cycle sonunda `ws.prune_stale_tokens(live_token_ids)` + scanner-side `_subscribed_ws_tokens &= live_token_ids` + `_token_slug` shrink; API hiccup koruması (live count <50% of prev → skip). **Fix B**: `ws.subscribe()` yeni imza `(token_ids, priority_first=None)` — deterministic caller-ordered dedupe, `priority_first` önce kapasiteye giriyor, aşım TAIL'den düşüyor. **Fix C**: `_cap_hit_count`, `_cap_skipped_total`, `_last_cap_hit_ts` sayaçları + `get_status()` expose. **Test**: `tests/unit/test_ws_subscribe_cap.py` 14 test (prune contract, below-cap, partial-cap order, full-cap, priority win, dedupe, already-subscribed filter, priority-overflow tail drop, empty/None, telemetry, scan-cycle integration). 14/14 pass. Regression: Epic 5 T5.3+T5.4+T5.5+T5.6 birlikte 41/41 ✅. **Default cap 200→400 yükseltme yapılmadı** (Skip D) — A+B sızıntıyı kökten çözüyor, cap artırma semptom maskeleme olurdu. `data/websocket_client.py` + `data/market_scanner.py` gitignore'lu (Windows-side sync gerekli); test commit: `3e3f531`.

---

## Epic 6 — Ghost Parametreler (UI ↔ Engine parite)

Hedef: UI'da (Telegram butonları / komutlar) kullanıcıya gösterilen ama engine'in okumadığı ayarları bul.

- [x] **T6.1** `/env_toggle` whitelisted 24 env — `config/env_whitelist.py` listesini engine okuyan env'lerle kıyasla — risk: LOW ✅ 2026-04-21
    - **Bulgu**: `PNL_PAUSE_THRESHOLD` whitelisted ama `core/auto_optimizer.py` module-top'ta `PNL_PAUSE_THRESHOLD = float(os.getenv(...))` → import-time donmuş. `/env_toggle` `os.environ`'ı patch'lerdi ama engine yine eski değeri kullanırdı = **silent ghost toggle**.
    - **Ek bulgu**: whitelist default `-3.0`, code default `-8.0` (Sprint 0 loosening sonrası drift). UI'da yanlış default gösteriliyordu.
    - **Fix (Option A)**: Module-top constant'ı `_get_pnl_pause_threshold()` runtime helper'a çevirdim. `_adaptive_pnl_threshold` artık her çağrıda fresh env okuyor. Malformed env için fallback `-8.0`.
    - **Default fix**: `config/env_whitelist.py` `PNL_PAUSE_THRESHOLD.default` `-3.0` → `-8.0`.
    - **Tests**: `tests/unit/test_pnl_pause_runtime.py` 8 test (runtime fresh read, default fallback, malformed fallback, adaptive uses runtime base, floor guard, disabled path, ghost-toggle regression). 8/8 PASS.
    - **Regression**: Phase 56 `TestAdaptivePnlThreshold` 8/8 + Epic 5 T5.6 14/14 + T6.1 8/8 = **82/82 ✅**. `tests/test_phase56_engine.py` setUp/tearDown güncellendi — artık `os.environ` patch'liyor.
    - **Touched**: `core/auto_optimizer.py`, `config/env_whitelist.py`, `tests/test_phase56_engine.py`, `tests/unit/test_pnl_pause_runtime.py` (NEW).
- [x] **T6.2** ✅ 2026-04-21 — Tüm handler dosyalarının (28 handler) kapsamlı ghost audit'i tamamlandı. **Bulgular**:
    - **3 TRUE GHOST** (UI toggle'ı var, engine hiç okumuyor): `brain_flags['drift_monitor']` (hiç module/reader yok), `brain_flags['autopilot']` (AutoPilot sınıfı flag check etmiyor), `brain_flags['kelly_sizing']` (engine `_kelly_mode` okuyor, flag ayrı).
    - **1 REVERSE GHOST** (engine okuyor, UI expose etmiyor): `brain_flags['market_recorder']` (engine `mr._enabled` set ediyor ama `valid_features` setinde yok).
    - **Sonuç**: AI Brain panel toggle'larından 4'ü yanıltıcı; kullanıcı bir şeyi açıp/kapattığını sanıyor ama engine davranışı değişmiyor. → T6.3 fix paketi.
- [x] **T6.3** AI Brain parity — RED tests + 4 atomik fix — risk: LOW-MED (audit sonrası)
    - [x] **T6.3a** ✅ 2026-04-21 — `tests/unit/test_brain_flags_parity.py` (276 satır, 12 test) — AST-driven regression baseline. `engine.brain_flags` ↔ `ai_handler.valid_features` set-equality, her flag için engine consumer varlığı (direct `brain_flags[k]` veya sibling-gate `self._enabled` read). Pre-fix: 5 fail (ghost flags) + 7 pass → RED baseline doğru. — commit: `e1924a5`
    - [x] **T6.3b** ✅ 2026-04-21 — `drift_monitor` ghost kaldırıldı. `core/engine.py` `self.brain_flags` dict'inden sökme + DB boot loader filter (retired flag resurrection koruması) + `ai_handler.py` status text/keyboard/valid_features temizliği. **Önemli**: `core/regime.py::DriftDetector` always-on aktif bir özellik — ghost toggle yanıltıcıydı (kullanıcı "drift detection'ı kapattım" sanıyordu, gerçekte kapanmıyordu). — commit: `1c94141`
    - [ ] **T6.3c** `autopilot` brain_flag gate — `core/autopilot.py::generate_actions()` başına `if not self.engine.brain_flags.get('autopilot', True): return []` + (opsiyonel) `execute_action()` benzeri gate. Hedef: `test_no_true_ghost_flags[autopilot]` GREEN. — risk: LOW
    - [ ] **T6.3d** `kelly_sizing` unified toggle — `brain_flags['kelly_sizing']` engine dict'inden sökülür, AI Brain panel Kelly butonu tek kaynağa (`engine._kelly_mode`) retarget. Strategies handler toggle'ı ile aynı state. — risk: LOW
    - [ ] **T6.3e** `market_recorder` UI exposure — `ai_handler.valid_features` setine eklenir + status text satırı + keyboard button. Engine side (mr._enabled) zaten wired. Reverse ghost temizliği. — risk: LOW
    - [ ] **T6.3 closure** — parity test suite full GREEN + `test_brain_flags_init_matches_expected_set` pin (6 flag canonical set) + closure memory + Epic 6 kapanış.
- [ ] **T6.4** `core/auto_optimizer.py` kalan module-top env constant'ları (MIN_TRADES_FOR_EVAL, ROLLING_WR_WINDOW, ROLLING_WR_KILL_THRESHOLD, ADAPTIVE_PNL_*) — whitelist'e eklenirse T6.1 pattern ile runtime helper yap — risk: LOW (şu an whitelist'te yok, ghost değil, backlog)

---

## Epic 7 — Dead Code & Duplicate Logic

Hedef: `_archive/` dışındaki dead code ve aynı işi yapan iki modül.

- [ ] **T7.1** `backtest/replay_engine.py` (1030 satır) ve `backtest/replay_engine_v3.py` (199 satır) — v1 hâlâ kullanılıyor mu? — risk: LOW
- [x] **T7.2** `backtest/simulation/fee_model.py` vs `fee_model_v3.py` — import grep — risk: LOW *(2026-04-21 ✅ Epic 4 T4.1 ile birlikte kapatıldı — fee_model.py → `_archive/fee_consolidation_2026_04_21_T41/fee_model_legacy_v1.py`, 4 import sitesi `FeeCalculatorV3 as FeeCalculator` aliasıyla v3'e taşındı)*
- [x] **T7.3** `core/fees.py` vs `core/fees_v2.py` (Epic 4 T4.1 ile overlap) — risk: LOW *(2026-04-21 ✅ Yol B agresif silme — `core/fees.py` v1 + `tests/unit/test_fees.py` + `category="legacy"` branch + `test_legacy_category_matches_quadratic` → arşive. **Tek fee oracle kaldı: `core/fees_v2.py`**, live Gamma feeSchedule'a karşı doğrulanmış (rate=0.072 / exp=1 / rebateRate=0.2 crypto). Test: 34/34 passing.)*
- [ ] **T7.4** `scripts/smoke_*.py` 13 dosya — son 30 günde `py scripts/smoke_*.py` çağrıları hangi smoke hâlâ aktif? — risk: LOW
- [ ] **T7.5** `fix_canary_streak.py`, `fix_filter_override.py`, `fix_slippage.py` — kök dizindeki tek seferlik scriptler, arşive taşınmalı — risk: LOW
- [ ] **T7.6** T1.4 Faz 3 — bare `except Exception` daraltma (MED-LOW, ~96 blok) — risk: MED, bağımlılık: **T1.4 Faz 1 ✅ (2026-04-20)** + T7.1-T7.3 (önce dead code temizliği) *(T1.4'ten devredildi, 2026-04-20)*
  - **Hedef:** core/ altındaki 96 MED-LOW bare `except Exception` bloğu. Paper trading yan yolları (adaptive tracker, calibration, journal, archive reader) + auto-optimizer. Mainnet para yolu değil ama silent failure telemetryi bozuyor.
  - **Ana dosya:** `core/auto_optimizer.py` (22 blok) — Optuna tuning loop + PnL pause guard + scheduled optimization runs. HIGH risk alt-dalı: T8.3'le örtüşüyor (`_startup_health_check` protected strategy safety).
  - **Diğer 19 MED-LOW dosya:** Epic 7 T7.1-T7.5 dead code temizliği bittikten sonra kalan dosya listesi grep ile yeniden çıkarılacak — şu an tahmini dağılım: `core/` altındaki adaptive_exposure/becker_weight/micro_weight (adaptive trackers), `core/sizing_*`, `core/calibration/*`, `core/signals/*`, `core/trade_journal.py`, `core/engine_support.py`, `backtest/*` analiz yolları.
  - **Metodoloji:** Faz 1 ile aynı — research (Grep -B/-A + fonksiyon haritası) → blok tablosu (line/fonksiyon/gerçek hata tipi/öneri) → kullanıcı onayı → commit'ler (dead code sil → narrow → debug upgrade) → py_compile + AST + grep doğrulama.
  - **Beklenen kazanım:** Tüm Epic 0-8 sonrası `core/` altında bare `except Exception` ≤ 20 kalacak (hepsi gerekçeli catch-all, # noqa: BLE001 işaretli).

---

## Epic 8 — AI Brain & Auto-Optimizer Denetimi

Hedef: `core/ai_brain.py` (1932 satır, proje içindeki en büyük dosya) ve `core/auto_optimizer.py` (685 satır). Claude Sonnet 10dk döngüsü + PnL-pause otomasyonu.

- [ ] **T8.1** T1.4 Faz 2 — `ai_brain.py` + `engine_signals.py` + `engine.py` bare except daraltma (123 blok, HIGH risk) — bağımlılık: **T1.4 Faz 1 ✅ (2026-04-20)**
  - **T1.4 Faz 2 ile kapsam birleştirildi (2026-04-20):** `ai_brain.py` (47 blok, baseline 54'ten T1.3 sonrası) + `core/engine_signals.py` (42 blok) + `core/engine.py` (34 blok) = **123 blok HIGH risk**. Sadece listeleme değil, bu Epic'te daraltma da yapılacak. AI decision path + 14-gate signal pipeline + engine orchestration → hep birlikte denetlenir çünkü exception tipi bağlamı paylaşıyor (httpx → Anthropic API, CLOB REST, WS reconnect, DB transaction, telegram notify zincirleri bu üç dosya arasında geçiyor).
  - **Metodoloji:** Faz 1 ile birebir aynı workflow — (1) dosya başına grep `except Exception` + context; (2) blok tablosu (line/fonksiyon/gerçek hata tipi/narrow önerisi + dead code tespiti); (3) kullanıcı onayı; (4) commit-per-concern (dead code sil → narrow tipler → debug upgrade); (5) py_compile + AST handler count + grep doğrulama.
  - **Alt-bağımlılık:** T8.3 `_startup_health_check` protected strategy denetimi bu görevle birlikte yapılabilir (ai_brain.py zaten inceleniyor olacak). T1.3'te silinen 16 ghost modülün ai_brain.py/engine_signals.py/engine.py'de kalan orphan pattern'i varsa (capital_allocator/whale_signal/cascade_detector gibi) engine_settlement/engine_fills'deki gibi dead code olarak silinecek.
  - **Beklenen çıktı:** 123 → ~30-40 catch-all (gerekçeli) + ~85 narrow, 3 dosya py_compile + AST temiz.
- [ ] **T8.2** `ANTHROPIC_API_KEY` kullanım yerleri, rate-limit guard var mı — risk: MED
- [ ] **T8.3** `auto_optimizer.py::_startup_health_check` — PNL_PAUSE_THRESHOLD protected strategy'yi yanlışlıkla pause ediyor mu? — risk: HIGH

---

## Epic 9 — Test Kaplaması

- [ ] **T9.1** `tests/` klasörü envanteri, hangi test hangi faz için, hangi hâlâ anlamlı — risk: LOW
- [ ] **T9.2** `pytest` tam koşu — kaç tanesi yeşil, kaç tanesi sarı — risk: LOW
- [ ] **T9.3** Risk manager, balance deduction, ghost module regression testleri yaz — risk: MED, bağımlılık: Epic 1,3,5

---

## Epic 10 — Security Pass  *(CRITICAL, mainnet öncesi son güvenlik denetimi)*

Hedef: Hiçbir API key/secret log'a, commit'e, Telegram çıktısına sızmıyor. Telegram kullanıcı girdileri SQL/shell injection'a kapalı. Bağımlılıklar güncel ve bilinen CVE yok.

- [ ] **T10.1** Log + git history secret leak taraması — risk: HIGH
  - `logs/*.log`, `reports/*.md`, `backups/*.db` dosyalarında `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `CLOB_SECRET`, `POLYMARKET_*`, `OPENROUTER_*`, `GROQ_*`, `GEMINI_*`, `sk-ant-`, `sk-or-`, `gsk_`, seed phrase patterns grep
  - `git log -p --all` içinde aynı pattern'ler
  - Bulunursa: rotate + `.gitignore` güçlendir + gerekirse `git filter-repo`
- [ ] **T10.2** Telegram input sanitization denetimi — risk: HIGH
  - `telegram_bot/handlers/*.py` içinde `update.message.text`, callback_data, update.effective_user.id alan fonksiyonlar → `_trim`, HTML escape, SQL parametreli query kullanılıyor mu?
  - Admin komutları (`/force_settle`, `/env_toggle`, `/kill`) için ADMIN_TELEGRAM_ID kontrolü **her** handler başında var mı?
  - `re.compile` kullanan regex'lerin ReDoS'a karşı kontrolü
- [ ] **T10.3** Dependency CVE scan — risk: MED
  - `pip install pip-audit --break-system-packages` → `pip-audit -r requirements.txt`
  - py-clob-client, httpx, websockets, aiosqlite, python-telegram-bot, optuna için bilinen CVE varsa upgrade planı
- [ ] **T10.4** `.env` ↔ `.env.example` senkron denetimi — risk: LOW
  - .env.example'da olmayan ama .env'de olan değişken var mı (yeni, dokumante edilmemiş)?
  - .env.example'daki placeholder değerler gerçek değer içeriyor mu (kopya kalıntısı)?

---

## Epic 11 — Mainnet Go/No-Go Çek Listesi  *(SON EPIC)*

- [ ] **T11.1** Tüm Epic 0-10 kapandığında final audit rapor
- [ ] **T11.2** `LIVE_ENABLED=true` öncesi: kill switch, budget guard, daily loss, paper-shadow divergence monitor aktif mi
- [ ] **T11.3** Rollback planı (`rollback_*.bat` hangisi canlı) — risk: HIGH

---

## 🟡 Ertelenenler — Yerel Windows / Live Telemetry Backlog

> **Bağlam:** Sandbox'ta yapılamayan işler (büyük WAL DB okunamıyor + canlı API erişimi yok + 24h telemetry gerekli). Hepsi *yerel Windows PC'de* `.bat` script veya `/admin command` ile koşturulacak. Mainnet Go/No-Go ÖNCESİ tamamlanmalı.
>
> **Epic eşleştirmesi:** Hepsi Epic 4 (Simulator Doğruluğu) çocuğu. Epic 11 (Mainnet Go/No-Go) tamamlanmadan önce kapanmalı.

| ID | Task | Ana Epic | Sahibi | ETA |
|---|---|---|---|---|
| T4.5 | **fill_model.py empirical slippage kalibrasyonu** — 1417 trade `realized_slippage` p10/p50/p90 → `backtest/calibration/slippage_2026q2.json` + `.env.example` | Epic 4 (T4.2 Faz B) | Heddas (yerel) | 1 oturum |
| T4.6 | **Backtest sweep parity** — eski heuristic vs yeni kalibre değerler PnL delta < 5% kanıtı | Epic 4 (T4.2 Faz C) | Heddas (yerel, T4.5 sonrası) | 1 oturum |
| T4.7 | **REST RTT 24h telemetry** — `REST_TIMING_TELEMETRY=true` 24h çalıştır, `core/observability/rest_timing.py:dump_to_file` → real p50/p_iqr ile `REST_LATENCY_MS`/`REST_LATENCY_JITTER_MS`/`replay_engine.latency_mean_ms` defaults'ı yeniden ata | Epic 4 (T4.3 Faz B) | Heddas (yerel, 24h bot uptime gerekli) | 24h + 1 oturum |
| T4.8 | **`/dump_rest_timing` admin Telegram cmd** — T4.7 için trigger; settings_handler veya admin_handler altına ekle | Epic 4 (T4.3 Faz B) | Sonraki oturum | <30dk |
| T4.9 | **`live_trader.py` + `polymarket_client.py` HTTP çağrılarını `time_call(label)` ile sar** — T4.7 önkoşulu | Epic 4 (T4.3 Faz B) | Sonraki oturum | <30dk |

**Sıralama:** T4.8 + T4.9 → 24h bot çalışması → T4.7 → T4.5 → T4.6 → Epic 11 Go/No-Go.

---

### 📦 Gitignored Kod Sync (sandbox → Windows)

> **Bağlam:** `data/` dizini `.gitignore`'lu (büyük DB + secret risk). Sandbox'ta yapılan fix'lerin Windows production'a elle aktarılması gerekiyor. `git pull` sadece tracked dosyaları getiriyor — aşağıdaki dosyalar kullanıcının manuel kopyalaması gerek. **Aktarım yapılmadan fix'ler devreye girmez.**

| ID | Task | Fix Kaynağı | Sandbox Yolu | Hedef (Windows) | Durum |
|---|---|---|---|---|---|
| SYNC.1 | `data/websocket_client.py` kopyala | T5.4 Fix A + T5.6 Fix A/B/C | `/sessions/happy-confident-cannon/mnt/Polyscout31/data/websocket_client.py` | `data/websocket_client.py` | ⏳ manuel bekliyor |
| SYNC.2 | `data/market_scanner.py` kopyala | T5.6 Fix A (prune wiring) | `/sessions/happy-confident-cannon/mnt/Polyscout31/data/market_scanner.py` | `data/market_scanner.py` | ⏳ manuel bekliyor |

**Doğrulama adımları (Windows'ta):**
1. İki dosyayı kopyala
2. `py -3.11 -c "import ast; ast.parse(open('data/websocket_client.py').read()); ast.parse(open('data/market_scanner.py').read()); print('OK')"`
3. Bot restart (Ctrl+C + yeniden `py -3.11 main.py` veya start script)
4. `/h` ile WS status kontrol et — yeni alanlar var mı: `cap_hits`, `cap_skipped`, `last_cap_hit_age`
5. İlk scan cycle log'unda `Scanner pruned N tokens` görmeli (N değişken)

**Test'ler tracked** — `tests/unit/test_ws_reconnect.py` + `tests/unit/test_ws_subscribe_cap.py` `git pull` ile geliyor, kod sync'ten sonra yerel test çalıştırılabilir.

---

## Yeni İş Kuyruğu (analiz sırasında çıkarsa buraya eklenir)

*(ilk tarama sırasında bulunan ek konular)*

- [ ] Version drift: `config/settings.py:142` vs `telegram_bot/version.py:7` — Epic 0'a alındı (T0.2)
- [ ] `core/engine.py:204-290` → 4 ghost modül silent-fail — Epic 1'e alındı
- [ ] `core/engine_signals.py` 9 farklı "classic bypass" — Epic 3'e alındı
- [x] `core/` altında 341 bare except — **3 faza bölündü (2026-04-20, T1.3 sonrası 284 blok):**
  - **Faz 1 ✅ TAMAMLANDI 2026-04-20** (65 → 17 bare, T1.4 Epic 1 altında): `live_trader.py` (16→7), `engine_settlement.py` (28→8), `engine_fills.py` (12→2), `risk_manager.py` (9→0). Bonus: 30 satır dead code (2 ghost orphan pattern) silindi, 19 debug log upgrade, 4 dosya py_compile + AST temiz.
  - Faz 2 (123 blok, HIGH): Epic 8 T8.1 altında (ai_brain + engine_signals + engine) — bağımlılık: Faz 1 ✅
  - Faz 3 (96 blok, MED-LOW): Epic 7 T7.6 altında (auto_optimizer + 19 dosya) — bağımlılık: Faz 1 ✅ + T7.1-T7.3 dead code temizliği
