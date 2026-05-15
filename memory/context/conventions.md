# Conventions & Doctrine

> Heddas'ın çalışma tarzı. Her ajan oturumunun başında oku.

## Çalışma İlkeleri

1. **STRICT CLEANUP mod** — Spekülasyon yok. Her iddia → dosya + satır numarası kanıtı.
2. **"Para kazanana kadar para harcamayacağız"** — $0 cost ilkesi. Heddas direktifi 2026-05-11. LLM çağrısı kaçınılmaz değilse stub/mock kullan.
3. **Mainnet protected** — `core/ai_brain.py::PROTECTED_STRATEGIES` ve `PROTECTED_STRATEGY_TYPES={"classic"}` ASLA dokunulmaz.
4. **Mainnet blocker'ı defense-in-depth'ten ayır** — Blocker'lar büyük büyük yazılır, defense-in-depth backlog'a.
5. **Bir Epic bitmeden sıradakine geçilmez** — TASKS.md kuralı.
6. **Her oturumun başında TASKS.md + progress log + CLAUDE.md oku**.

## Commit + Karar

- Commit: `feat/fix/docs/chore/test/deps(scope): <Türkçe özet>`
- Türkçe progress log, Türkçe TASKS.md, Türkçe yorumlar; İngilizce kod + variable name + docstring.
- Her closure'da `data_store/.auto-memory/project_*_closure.md` landmark çıkar.
- Tek seferde küçük commit > büyük blok.

## Karar Verme

- **Sandbox-doable + mainnet blocker olmayan iş** → "defense-in-depth" tagged backlog.
- **Windows-side (user elle) gerektiren iş** → TASKS.md "Windows" section.
- **Kritik bulgu (mainnet bloklayan)** → Big banner closure + memory landmark + immediate fix.
- **API drift / API varsayım hatası** → Test fail → fix forward, test re-write.

## Doğrulama Doktrini

- **Polymarket docs cross-check** — fees/contract/endpoint sabitleri Polymarket MCP docs ile bit-by-bit verify. Sapma → fix.
- **3-seed determinism** — Replay test'leri 42/1337/9001 üçlüsünde identical sonuç.
- **Real-behavior coverage** — Mock değil gerçek path test. Smoke test'ler izole tmp dir'de tam round-trip.
- **Audit trail** — Her runtime patch `/envt` audit log'a yazar.

## Yakalanan Pre-Mainnet Kritik Bug'lar (Doktrin)

1. **LIVE_BUDGET whitelist eksikti** — `/envt` yolu kapalıydı → `d9a143b`
2. **PNL_DIVERGENCE_* whitelist eksikti** (G2 paraleli) → `da11c2f`
3. **daily_db_snapshot atomic write yok** (2 bozuk backup) → `35ae7d0`

**Doktrin**: yeni live guard eklerken **5 adımlı checklist** → helper + site + whitelist + `/live_guards` + test.

## Faz / Epic Adlandırması

- **Faz 0.1, 0.2** vb. → roadmap small slice
- **Epic 0-11** → cleanup epic'leri (HEPSİ KAPALI)
- **T11.1-T11.8** → Epic 11 alt görevleri
- **P0-P3** → priority bucket (yol haritası 27 görev)
- **Wave 1-4** → P1-01 coverage sprint dilimleri
- **Batch 1-N** → progress log iş paketi
- **Aşama A/B/C/D/E** → T11.8-B advisory zone sweep

## Memory Landmarks (Mevcut)

Yer: `data_store/.auto-memory/`

- `project_t11_2_full_closure.md` — Live guards 6/6 PASS
- `project_t11_3_closure.md` — Rollback dry-run 4/4 PASS
- `project_t118b_full_closure.md` — Bare-except advisory zone %100
- `project_t46b_sweep_closure.md` — Sweep fill heuristic
- `project_faz0_1_fee_audit_closure.md` — Fee audit
- `reference_polymarket_fee_rates_2026_05_11.md` — Fee reference snapshot

## İletişim Tarzı (Heddas → Claude)

- Direktifler Türkçe, kısa: "sırada ne kaldıysa oradan devam et", "polymarket connector kullanarak doğrula"
- Onay / kararlar zamanında veriliyor; emin değilse "Heddas onayı bekleniyor" düşülür
- Claude → Heddas: Türkçe progress log entry stili, dosya:satır referanslı

## Yapma Listesi (Dont's)

- ❌ Memory bootstrap'ı tekrar etme — CLAUDE.md var, oradan başla
- ❌ Auto-execute AI action (P0-01 öncesi). Tüm AI kararları approval queue'dan.
- ❌ `core/ai_brain.py::PROTECTED_STRATEGIES` veya `classic` plugin'ine dokunma
- ❌ `.env` dosyasını commit etme; sırrı doğrudan görme
- ❌ `/export_private_key` benzeri komut yazma (P0-03)
- ❌ Mainnet blocker'ı defense-in-depth ile karıştırma
- ❌ Test yazmadan refactor; coverage `fail_under = 43` ratchet altına düşme

## Yapılacaklar Listesi (Do's)

- ✅ TASKS.md + progress log + memory landmark üçlüsünü senkron tut
- ✅ Polymarket docs ile constant cross-check
- ✅ 3-seed deterministik replay
- ✅ `/envt` whitelist'e her yeni guard için entry ekle (5-adımlı checklist)
- ✅ Her closure'da memory landmark
- ✅ "Para kazanana kadar para harcamayacağız" — $0 cost yolunu seç
