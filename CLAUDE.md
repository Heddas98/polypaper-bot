# Memory — PolyPaper Bot

> Working memory. Her oturum başında okunur. Tam liste için `memory/` klasörü.
> Son güncelleme: 2026-05-19 (çoklu-oturum konsolidasyonu — detay: Mevcut Faz).
> Audit dosyaları: `docs/audits/2026_05_13_ultra_audit.md` + `docs/audits/2026_05_15_ultra_audit.md` (yeni, 22 bulgu).

## Me

**Heddas** (vfurkanv@gmail.com) — solo developer + operator. PolyPaper Bot'un tek geliştiricisi, denetçisi ve oncall'i. Türkçe konuşur, kod İngilizce yorum + Türkçe progress log karışık.

## Proje

**PolyPaper Bot** — Polymarket binary prediction market'ler için otonom trading botu.
- **Mainnet LIVE: 2026-05-09'dan beri** (6 gün) — gerçek pUSD ile.
- Shadow trading: 2026-05-03'ten beri (12 gün).
- 2 ay solo geliştirme, day 1'den production.
- Tek Telegram chat'ten kontrol (paper + live aynı kod tabanı).
- Klasör: `C:\Users\heddas\Desktop\Heddas\Dersnotu2\Polyscout31`
- GitHub: `Heddas98/polypaper-bot`

## Mevcut Faz

**`/live` trade istasyonu + mode-first UX redesign tamamlandı** (2026-05-18/19) — kokpit, 4 panel, mode-first tek-kapı, on-chain PnL hep canlı. Aktif backlog: P0-04 (Budget 2FA), M-08 (test monolith böl). P1-01 (coverage) + P1-02 (AI Brain microservice) sürüyor.
- P1-01: Coverage source genişletme (%42 → %60 ratchet, şu an %44.06 toplam ama kritik path <%30).
- P1-02: AI Brain microservice (Wave 1+2a+2b+2c kapalı, Wave 3 approval queue backlog).
- Açık P0 eski: P0-02 (keyring), P0-04 (LIVE_BUDGET 2FA), P0-08 (5m default OFF) — mainnet blocker değil.
- Audit 2026-05-13: P0-10 (fees precision) ✅, P0-14 (1h cycle log) ✅ kapalı; P0-11..P0-13, P0-15 backlog.
- **Audit 2026-05-15 Wave 1 ✅** (4 fix): C-01 is_admin backdoor, C-02 slug prompt-injection sanitize, H-03 /buy inf/nan reject, L-01 ruff F401.
- **REG-01 ✅** (`8b13226`): `services/ai_advisor/app.py` 2026-05-13 v2 zincirinde truncate (412→333 satır). v1 `ca6ff41`'den restore. test 29/29 PASS.
- **Audit 2026-05-15 Wave 2 ✅** (6 fix): H-01 AI Advisor cost-tiered auth (`fc04d1c`), H-02 budget race lock (`4820636`), H-04 deduct rowcount false-positive (`8959905`), H-05 live_handler admin gate + M-01 exception leak (`88573b9`), H-06 zaten C-01'de kapanmış.
- **REG-02 ✅** (2026-05-17): Kök neden — Heddas ana dizini (`Polyscout31`) origin/main'in 12+ commit gerisindeydi + bozuk working tree (`config/env_whitelist.py` `list_groups`'suz). `main.py` ImportError. Test/kod bug'ı DEĞİLDİ — working environment desync. Çözüm: stale `.git/index.lock` sil → `git stash` → `git reset --hard origin/main`. Ana dizin → `6b8e670`, bot import OK, 41/41 test PASS.
- **Log audit 2026-05-18 ✅** (bot ilk temiz boot sonrası): M-09 per_market_exposure unbounded growth/827 stale entry (`39e5bf3`), H-07 reconciliation log mesajı dinamik (`35ccb7b`), L-07 chainlink CHAINLINK_RPC_URL env override (`a9deec4`).
- **`.env` ✅ (2026-05-18)**: OP-01 duplikat `LIVE_ENABLED` satır 46 silindi (tek kaynak `true`), L-07 `CHAINLINK_RPC_URL=ethereum.publicnode.com` eklendi. OP-02 (stale Polymarket creds) açık — yeni API key Heddas almalı.
- **Wave 3 ✅ (2026-05-18, C-03)**: `test_live_trader_e2e.py` 9 test (`maybe_mirror` success path, `b1f219a`) + `test_ai_brain_cycle_e2e.py` 5 test (`run_brain_cycle` P0-01 invariant, `7ea5674`). Kritik-path e2e boşluğu kapandı.
- **Wave 3 kalan ✅ (2026-05-18)**: M-07 mypy_baseline UTF-16→UTF-8 regen (`f8c23bd`, mypy 0 hata doğrulandı), M-03 ai_brain `_BrainResponse` Pydantic LLM schema (`0ca08cb`, +2 test). M-08 (555 class / 24.5k satır monolith) — bölme planı audit ADDENDUM 5'te, tam uygulama backlog (test-kaybı riski, ayrı oturum).
- **OP-02 ✅ (2026-05-18)**: `.env` Polymarket creds stale değil artık. API key sorunlu DEĞİLDİ (Polymarket'te duruyordu) — sadece `.env` kopyaları eskiydi. `scripts/refresh_polymarket_creds.py` ile `POLYGON_PRIVATE_KEY`'den geçerli L2 creds türetilip `.env` güncellendi. Bot restart → PATH 1 stored creds PASS.
- **Wave 4 ✅ (2026-05-18)**: M-04 httpx verify, M-05 `_place` operator notify, M-06 Sentry PII guard doktrin (`9b0b346`). M-02 & L-05 incelendi → FALSE POSITIVE (fix yapılmadı). L-02 risk>değer (yapma). L-03/L-04/L-06 düşük-değer backlog.
- **Audit durumu**: Tüm Critical/High + anlamlı Medium bulgular kapandı. Backlog: M-08 (test monolith böl, plan hazır), L-03/L-04/L-06 (düşük-değer hijyen).
- **`/live` redesign (2026-05-18, Heddas direktifi "trade istasyonu")**: Faz 1 kokpit ✅ (`e39042f` bilgi-yoğun panel: risk-bar, Binance momentum, regime, streak; `f99b0b0` streak PAPER etiketi). Budget Reset butonu ✅ (`1b2f506`, 2-tık onaylı). Faz 2A veri katmanı ✅: `live_trades` boş bug — `execute_market_order` INSERT eksikti (`9803037` fix), `pusd_allowance` ♾️ UI (`d574924`).
- **`/live` Faz 2B+3 ✅ (2026-05-19, `c60ab5d`)**: Trade istasyonu epic'i KAPANDI. 4 bağlı panel + yenileme butonları, hepsi mevcut veri kaynağına bağlı (yeni veri uydurulmadı). **📡 Piyasa Tara** — `scanner.active_markets` in-memory cache, coin/tf bazlı up/down odds, doğal tf sırası. **🛡 Guards** — `live_guards_handler.build_guards_text()` ayrıldı, `/lg` komutu + `/live` paneli ortak builder (UI drift yok). **⚙️ Risk** — `RiskManager.get_status()` halt/maruziyet-bar/loss-streak/asset limitleri. **📈 Performans** (Faz 3) — on-chain BOT LIVE PnL + paper×real kalibrasyon + son trade'ler tek panelde (`_build_compare`+`_build_history` reuse). `_build_history` footer yanıltıcı `_total_pnl` → "Risk limiti kalan". `_panel_nav_kb` helper. `test_live_panels.py` 13 test.
- **Mode-first tek-kapı redesign ✅ (2026-05-19, `0628474`)**: Heddas direktifi "bot ikiye bölünsün — PAPER/LIVE iki dünya". Mod-first tasarım `main_dashboard.py`'de zaten vardı (2026-05-06) ama yarım/tutarsızdı — tamamlandı. **Tek kapı**: `/start`+`/main`+`/dashboard`+`/d` HEPSİ mode-seçim ekranını açar (bot.py). **Mode-select**: her mod için açıklama + canlı özet + admin gate (gerçek pUSD bakiyesi sızdırır). **PAPER MODE**: `paper_dashboard` artık `dashboard._build()` detaylı içeriğini gösterir + paper menü. **LIVE MODE**: eski ince `live_dashboard` kaldırıldı → doğrudan `/live` trade istasyonu kokpiti (`live_handler._build_main`); kokpit keyboard'una "◀️ Mode Seçimi" butonu. **Güvenlik**: mod seçimi = yalnız navigasyon, LIVE MODE'a girmek gerçek trading'i BAŞLATMAZ (`live_toggle` ayrı 2-tık onaylı). `test_mode_first.py` 11 test.
- **Yeni-kod audit fix-pass ✅ (2026-05-19, `dd7e2ef`)**: Son 3 oturum kodunun (LIVE PnL + trade istasyonu + mode-first) acımasız audit'i — **kritik bug YOK**. 6 bulgu kapatıldı: **B1** refresh "message not modified" → duplicate mesaj (`_safe_edit` helper — "not modified" sessizce yutulur); **D1** Performans paneli çelişkili PnL (on-chain vs DB-kaynaklı) → ayırıcı açıklama; **A8** mode-select "bugün PnL" yanlış etiket → gerçek `compute_live_pnl` net'i; **C1** `/mode` footgun (tek-tık `LIVE_ENABLED` toggle = gerçek trading açıyordu) → `mode_handler.py` navigasyon alias'a indirildi, canlı trading yalnız kokpit 2-tık `live_toggle`; **A11** docstring; **E1** `dashboard_command` legacy notu. **P0-15** `dashboard.html` gitignore. **P0-13** `PROTECTED_STRATEGIES` denetlendi → **değişiklik YOK** (doctrine dokunulmaz + veri: kazanan strateji yok). `test_live_panels.py` +4 test, 2583 PASS.
- **Paper PnL drift araştırması ✅ KAPALI (2026-05-19, `20536ba`)**: Memory'deki "1417 trade / +$355 PnL / baseline $10k / WR %57" = `docs/MASTER_PLAN_2026_04_30.md` + `YOL_HARITASI_5AI_SYNTHESIS_2026_04_30.md`'nin **2026-04-30 pre-mainnet snapshot'ı**, ~15 dokümana doğrulanmadan kopyalanmış (audit'ler "DB erişimi sandbox'ta yok" diye not düşmüş — hiç verifiye edilmemiş). **Kanıt**: tüm trade artefaktları (`executions`, `trade_log`, `trade_journal.jsonl`, `decisions.jsonl`, `trade_memory`) 2026-05-08'de başlıyor — `schema_version` #13 (`p0_08_e2`) migration'ı 2026-05-08T22:30, mainnet (2026-05-09) öncesi temiz başlangıç için trade geçmişi sıfırlanmış; hiçbir backup (en eski 05-11) ya da JSONL pre-reset veri tutmuyor → 1417 sayısı doğrulanamaz. Reset'ten sağ çıkan `wallets` (primary, oluşturma 2026-04-05): bakiye **$9.966,99** = $10k baseline'ın ALTINDA → +$355 doğrudan çürük. **Gerçek paper performansı**: `executions` 251 trade / **-$54,28** / WR **%45,8** (115K/136L), `trade_log` SETTLEMENT 54/-$28,52, tüm stratejiler net negatif (`Conservative Sniper` aktif 78 trade -$6,26). Düzeltme: `main_dashboard._get_paper_summary` artık `wallets.balance` okuyor (sahte `PAPER_BUDGET_DISPLAY=10386` env default kaldırıldı). `PROTECTED_STRATEGIES` 2 stratejisi `executions`'da 0 trade — DOKUNULMADI (doctrine). 2026-04-30 dated docs tarihsel snapshot olduğu için düzeltilmedi.
- **Ölü buton fix + PnL zenginliği ✅ (2026-05-19, `c8ca05f`)**: Heddas raporu — LIVE trade istasyonu panel butonları (Piyasa Tara/Guards/Performans/Risk) TAMAMEN TEPKİSİZdi. **Kök neden (Faz 2B regresyonu)**: `bot.py` `live_callback`'i açık izin-listesiyle kayıt ediyor; Faz 2B'de eklenen `live_scan`/`live_guards`/`live_perf`/`live_risk` callback'leri listede YOKtu → hiçbir handler eşleşmiyor → buton ölü. 4 callback kaydedildi. **Buton audit'i**: PAPER MODE menüsünde 6 ölü callback daha (`strategies`/`ai_brain`/`stats`/`bt_v2_main`/`trades_page:0`/`suggest` — hiçbiri kayıtlı değildi) → kanıtlanmış `build_main_hub_keyboard`'a (tüm `menu_*` kayıtlı) geçildi; `env_toggle_main` ölü buton kaldırıldı. Market BUY/SELL alt-akışı sağlam (`^live_market_`). **Heddas isteği**: market onay ekranına "🔄 Fiyatı Yenile" butonu. **PnL zenginliği**: `compute_live_pnl` `per_market` listesi döndürür (her market title/ts/outcome/entry_price/cost/payout/net/result); Performans paneline "İŞLEM DÖKÜMÜ" bloğu (`_live_pnl_detail_block` — tarih·market·giriş·net·sonuç); eski DB-kaynaklı `_build_history` kaldırıldı (D1). +9 test, 2591 PASS. **Ders**: yeni callback eklerken `bot.py` registration ZORUNLU — if/elif branch tek başına yetmez.
- **"no match" likidite fix ✅ (2026-05-19, `36a00bf`)**: Heddas raporu — live trade'de `PolyException: no match` + traceback. **Neden**: V2 SDK `calculate_market_price` order book'ta FOK $ tutarını dolduracak karşı-taraf likiditesi bulamadı — **kod bug'ı DEĞİL** (Polymarket 5m/15m crypto market'lerde ince/boş orderbook olağan; order post edilmedi, para gitmedi). 3 fix: (1) `_sync_order` "no match" exception'ı → temiz `skip:no_liquidity` status + WARNING (ERROR+traceback yerine); (2) `_execute_market_trade` `skip:*` → ⏭️ "ATLANDI" + kullanıcı-dostu açıklama (❌ "BAŞARISIZ" değil — başarısızlık değil); (3) onay ekranı — `_peek_market_info` `has_asks`/`has_bids` döndürür, `_show_market_confirm` orderbook boşsa onaylamadan ÖNCE uyarır. 2426 regresyon PASS. (`_sync_order` projenin "unit-test edilemez ağ katmanı" sınırında — yeni test yok.)
- **Market likidite filtresi ✅ (2026-05-19, `447c5a3`)**: Heddas direktifi — market seçim filtresi denetlendi (Polymarket docs MCP ile). **Teşhis**: bot doğru market TÜRÜNÜ (crypto Up/Down) hedefliyor ama likidite FİLTRESİ bozuktu — `has_liquidity` SAHTEYDİ (`polymarket_client.get_market_odds` `if up_odds and not has_liquidity: has_liquidity=True` ile midpoint üzerine zorla True; `engine_signals.py:373` `NO_LIQ` gate etkisiz → ince/boş defterli market trade'e geçip "no match" alıyordu). 3 fix: **A** sahte override kaldırıldı (client + `market_scanner` WS-tick path); **B** `_compute_liquidity` — `get_orderbook` ile iki-yanlı defter derinliği (ask+bid USDC) `MIN_BOOK_DEPTH_USD` ($2 default) eşiği, `MARKET_DEPTH_CHECK` env-gated; **C** `_market_tradeable` — discovery `enableOrderBook`/`acceptingOrders=False` market'leri eler (docs: "Markets can only be traded via CLOB if enableOrderBook is true"). `test_market_filter.py` 14 test, 2555 PASS. **Ek bulgu (backlog)**: `core/uma_dispute.py::should_block_new_position` (UMA dispute + `acceptingOrders` gate) production trade yoluna bağlı DEĞİL — yalnız testler import ediyor.
- **Fiyat-hareketi (delta) verisi ✅ (2026-05-19, `5f506e6`)**: Heddas direktifi — market'lerde "fiyat farkı" bilgisi (önceki pencere açılış→kapanış delta + son 5/10/24s ortalama hareket — işlem alırken volatilite/eğilim göstergesi). `data/candle_collector.py::compute_price_deltas` saf fonksiyon (son pencere delta $+%, ort.|hareket| 5/10/tümü, net yön, up/down pencere sayısı; `drop_last` ile devam-eden pencere atılır) + `candles_24h_count(tf)`. **Veri kaynağı `candles_ext`** (CandleCollector Binance OHLC — zaten toplanıyor, YENİ API yükü YOK). `live_handler._fetch_price_deltas` + `_price_delta_block`. Market BUY onay ekranında tam blok, Piyasa Tara panelinde kompakt inline (her coin/tf: 24s ort|hareket| + son pencere yön oku). PAPER MODE'a "📡 Piyasa Tara" butonu — delta verisi mod-bağımsız (hem live hem paper). `test_price_delta.py` 12 test, 2423 PASS.
- **PAPER özet kartı stale-status fix ✅ (2026-05-19, `2c06bdd`)**: Heddas raporu — `main_dashboard.py::_get_paper_summary` PAPER MODE özet kartında iki "stale status string" bug'ı; ikisi de hiçbir satır eşleştiremiyor → kart yapısal yanlış değer. **BUG 1** (`daily_pnl` hep $0): sorgu `executions WHERE status='filled'` arıyordu — `ExecutionStatus` enum'ında (`db/models.py:78-82`: pending/bet_placed/claimed/failed) `filled` YOK. Settle olan execution `status='claimed'` (`engine_settlement.py:593` + `engine.py:881`, `closed_at`+`pnl`+`result` ile birlikte yazılır) → `'filled'`→`'claimed'`. **BUG 2** (`open_strategies` hep 0): sorgu `strategies WHERE status='started'` — `db/migrations.py:46` tüm statü alias'larını `'active'`'e normalize eder, `'started'` hiç geçerli değil → `'started'`→`'active'`. 2026-05-08 tarihli yanıltıcı yorum da tek-satır doğru hâliyle değiştirildi. `test_mode_first.py` +4 test (`_get_paper_summary` ilk testleri; gerçek `:memory:` bot DB, parent user/wallet seed'li). 3'ü gerçek regresyon — literal'ler ampirik olarak eski hâline çevrilip koşuldu: FAIL (`daily_pnl=0.0`, `open_strategies=0`), fix geri uygulanınca PASS. 4.'sü engine-yok koruması. 15/15 PASS, ruff temiz. **Ders**: statü string'lerini varsayma — kaynaktan (`ExecutionStatus` enum + `db/migrations`) doğrula; önceki "FIX" yorumu yanlış literal getirmişti.
- **Fiyat-delta revize ✅ (2026-05-19, `4220d16`)**: Heddas — güncel (canlı) delta ekle + blok okunabilir olsun. `compute_price_deltas` artık `current_*` alanları döndürür (devam eden pencere — `drop_last`'le atılan mum). `_fetch_price_deltas` `external_feed.get_price` ile CANLI spot çeker → `live_delta` = canlı fiyat − devam eden pencere açılışı (stale ise `candles_ext`'e düşer). `_price_delta_block` yeniden tasarlandı — 4 net etiketli satır: ▶️ Şu an (canlı) açılış→canlı fiyat · ⏹ Önceki pencere (kapandı) · 📊 pencere başına ort. hareket (son 5/10/24s, tam kelime) · 🧭 24s yön dengesi + insan-okunur yorum. Kriptik "5p/10p/\|Δ\|/net%0.000" kaldırıldı. `test_price_delta.py` 14 test, 2414 PASS.
- **BOT LIVE PnL ✅ (2026-05-18, `c72ddfc`)**: Kokpit "9 trade · $0 PnL" gösteriyordu — `live_trader._total_pnl` `check_settlement`'tan beslenir, manuel `/live` trade'lerini kaçırır. `data/polymarket_portfolio.py::compute_live_pnl` — Polymarket on-chain `activity` feed'inden gerçek PnL (TRADE maliyeti + REDEEM payout). Market-bazlı conditionId filtresi (bot-öncesi market'in sınır-aşan redeem'i PnL'i şişirmez) + pending guard (redeem'siz + 900s grace içi trade = pending, kayıp değil). fee = `usdc_size−price×size`, fees_v2 crypto `0.07×(1−p)` ile cent-cent doğrulandı. `_build_main` kokpite "BOT LIVE PnL" bloğu; yanıltıcı "Toplam PnL: $0.0000" satırı silindi. **Production sonuç: 9 trade, 9/9 KAZANDI, net +$3.55, fee $0.14, ROI +%38.8**. Manuel-trade settle eşleştirme bu yolla çözüldü (on-chain kaynak — `closed_positions` belirsizliğine gerek kalmadı). `test_live_pnl.py` 14 test.
- **Veri bulgusu (2026-05-18, KESİN)**: `engine.risk` streak = PAPER (sadece `engine_settlement.py:633`'ten beslenir). Polymarket `closed_positions` (50 kayıt) — filtre testi: bot dönemi (≥2026-05-09 + crypto updown) = **0 kayıt**, hepsi (50/50) Heddas'ın bot-ÖNCESİ kişisel geçmişi (Ocak-Mart: `tur-ala-fen`/`nba`/`uel` spor + eski bitcoin, toplam `realized_pnl` $19.7k). `polymarket_portfolio.py` parse DOĞRU — sadece cüzdan eski kullanıldığından. **Faz 2B panel doktrini**: "bot performansı" için `closed_positions` HİÇ KULLANMA (0 bot kaydı, 50-limit eski geçmişle dolu). Bot verisi → `live_trades` (artık `9803037` ile kaydeder) + Polymarket `recent_trades`/`activity` (Mayıs, güncel).
- **5m resolution + flip edge soruşturması ✅ (2026-05-19)**: 5m **ve** 15m BTC up/down marketleri **Chainlink BTC/USD data stream**'e settle olur — spot/Binance DEĞİL (Gamma API market kuralı + 500+ market doğrulandı). Eski "5m=Binance" varsayımı (`price_feed_divergence` + `rtds_chainlink_subscribe` audit'leri) YANLIŞ — kod + 6 doküman düzeltildi, 2 kaynak-audit'e banner. **Ofansif edge (in-sample, 502 market):** token trajektorisi son-60sn flip'i öngörür — T-60→T-30'da zayıflayan favori çok daha sık çöker (up-odds yükselen DOWN-favori → **%69 UP'a döner**; düşen UP-favori %57 tutar). Ekstrem fiyat (≥0.90/≤0.10) güvenli (206 örnek, 0 flip). **RTDS feed bağlandı (P1.10):** `data/polymarket_rtds.py` (11 gün uykudaydı) → main.py, `external_prices` source `rtds_chainlink`/`rtds_binance`, `RTDS_ENABLED` env default açık, aktivasyon=restart. Açık bug'lar: `chainlink_oracle.py` yanlış Chainlink ürünü (on-chain Data Feed, stale 42 fiyat/10gün); `reference_price_audit` 0/48 kullanılabilir. **Edge OOS backtest ✅ (2026-05-19):** trajektori sinyali out-of-sample DOĞRULANMADI — in-sample +$0.22/trade (thr=0.10) ama OOS yarı tüm eşiklerde negatif (−$0.06..−$0.09/trade), in-sample overfit. Ofansif edge deploy EDİLMEDİ. **Tick-level flip kataloğu ✅ (519 market, `data_store/flip_catalog_5m.csv`):** endgame flip %8 — %82'si son-10sn, 30-60sn'de tespit yok; flip'ler ekstrem değil belirsiz (~0.65) marketlere oluyor — karar-verilmiş pozisyon çalınmıyor, "manipülasyon" tezi çürüdü. Scalp güvenliği tick-level: ekstrem fiyat (T-10 ≥0.95/≤0.05, T-5 ≥0.97/≤0.03) → **689/689 flip'siz**. T-10 kalibrasyon: ≥0.85→%100, 0.6-0.85→%76 (tehlike bandı). **Footprint analizi ✅ (2026-05-19, `data_store/flip_features_5m.csv`, 519 market):** flip vs stable trade-tape — flip-enriched ~10 cüzdan kümesi (4-7× base-rate testini geçti) bulundu AMA OOS copy-trade backtest NEGATİF (−$0.32/trade); korelasyon edge değil. **KESİN SONUÇ: 3 ofansif açı (trajektori sinyali / naif 95c scalp / cüzdan-kopya) üçü de OOS'ta çöktü → 5m'de sömürülebilir manipülasyon-alfası YOK. Naif 95c scalp bile edge değil (0.95 giriş = adil fiyat, başabaş %95 winrate gerek). Soruşturma KAPANDI. Kazanım: RTDS feed + 5m=Chainlink düzeltmesi (kod, main'de), kaybettiren strateji deploy EDİLMEDİ.**

**Test durumu (2026-05-19):** oturum boyunca live/mode/wave test alt-kümeleri **2595 pass / 0 fail** (tekrarlı doğrulandı). Yeni test dosyaları: `test_live_pnl.py` (17), `test_live_panels.py` (22), `test_mode_first.py` (15). Ruff 0 violation, mypy `core/` Success (55 dosya). Coverage %44.06 (kritik path <%30). Not: 2026-05-15'teki "12 fail" çözüldü — 9 REG-01 ✅, 3 REG-02 env-desync'ti (kod bug'ı değil). Tam suite (~3,600) bu oturum komple koşulmadı.

**✅ Commit zinciri (2026-05-15):** Önceki "39 modified + 18 untracked" CLOSED. 8 duplikat → 4 thematic + 3 follow-up commit (push: `1de180c..3289636`). Ultra-audit + Wave 1: `6e4b9ba` docs(audit) · `93136c6` fix(c-01) · `bda186a` fix(c-02) · `9c01bca` fix(h-03) · `47c7e25` fix(l-01) · `0a8acbd` chore(memory). REG-01: `8b13226` fix(regression) app.py truncation restore.

## Tech Stack

- **Python 3.11** (Windows 10/11 local, Docker P1-05 roadmap)
- **Polymarket V2 SDK**: `py-clob-client-v2==1.0.0` + `py-builder-relayer-client` (gasless)
- **AI**: Claude Sonnet 4.6 (Critic) + Groq Llama 70B (Optimist), 2-agent loop, $15 budget cap
- **DB**: SQLite + WAL (PostgreSQL migration = P1-08)
- **Bot**: Telegram (python-telegram-bot), 40+ komut, inline keyboards
- **Observability**: Sentry custom transactions (env-gated), reality_gap_job
- **Test**: pytest + pytest-cov, 3-seed determinism (42/1337/9001)

## Kişiler

Solo proje — sadece **Heddas**. Dış ekip yok.

## Çalışma Tarzı (Doktrin)

- **STRICT CLEANUP mod**: Spekülasyon yok. Her iddia → dosya + satır numarası.
- **"Para kazanana kadar para harcamayacağız"** — $0 cost ilkesi, her batch budget-aware.
- **Mainnet protected**: `core/ai_brain.py::PROTECTED_STRATEGIES` ve `PROTECTED_STRATEGY_TYPES={"classic"}` dokunulmaz.
- **Her commit + her PR**: Türkçe `feat/fix/docs/chore/test/deps` prefix.
- **Memory landmarks**: Büyük closure'larda `data_store/.auto-memory/project_*.md` doss çıkarılır.
- **Yapı**: Roadmap (`02_POLYPAPER_YOL_HARITASI.md`) → progress log (`03_POLYPAPER_PROGRESS_LOG.md`) + cleanup TASKS (`TASKS.md`) → memory landmark.
- **Memory drift kontrolü (yeni 2026-05-13)**: Her oturum açılışında `CLAUDE.md` / `memory/status.md` / `TASKS.md` üçü ile gerçek kod kanıtını karşılaştır. Tutarsızsa **koda güven, memory'yi güncelle**. 2026-05-13 audit: 4 P0 closed ama memory open gösteriyordu.
- **Closure kaydı ZORUNLU (Heddas direktifi 2026-05-18)**: Her modül / faz / anlamlı değişiklik **biter bitmez**, kapanışla **aynı turda** `CLAUDE.md` (Mevcut Faz bölümü) + `memory/status.md` üst snapshot'ına tek satır closure kaydı düş — biçim: `commit-hash` · ne yapıldı · sonuç/sayı. Ertelenmez. Gerekçe: auto-compact konuşma context'ini sildiğinde geriye **sadece bu kayıt** kalır; yazılmazsa yapılan iş hafızadan tamamen düşer.

## Anahtar Komutlar (Telegram)

`/start` `/main` `/dashboard` `/d` (hepsi → mode-seçim ekranı, 2026-05-19 tek-kapı) · `/live` (LIVE trade istasyonu kokpiti) · `/strategies` `/s` · `/buy` `/sell` · `/envt` `/env_toggle` (37 whitelist param) · `/lg` `/live_guards` (6 guard snapshot) · `/rg` `/reality_gap` (paper×0.66 vs live) · `/ra` `/ref_audit` (reference price audit) · `/recon` `/rc` (pUSD on-chain vs DB) · `/drt` (REST timing).

## Kritik Açık İşler (P0 — 2026-05-13 audit gerçek)

### Gerçek açık (kod-kanıtlı)

- **P0-02** POLYGON_PRIVATE_KEY plaintext → Windows DPAPI / keyring (`config/settings.py:94-96` hâlâ plaintext)
- **P0-04** LIVE_BUDGET 2-faktör + 24h cooldown (`core/live_trader.py:107-116` tek faktör)
- **P0-08** 5m binary'ler default OFF (`config/settings.py:32` BTC default ENABLED — direktif değişti mi onayla)

### Yeni öneri (2026-05-13 audit, detay: `docs/audits/2026_05_13_ultra_audit.md`)

- **P0-10** `fees_v2.py` precision 4 → 5 decimal (docs: smallest 0.00001 USDC)
- **P0-11** AI Advisor service auth (X-Internal-Key, `services/ai_advisor/app.py` hiç auth yok)
- **P0-12** Polymarket constant drift CI guard (haftalık docs MCP karşılaştır)
- **P0-13** `PROTECTED_STRATEGIES` audit (`core/ai_brain.py:102` sadece 2 entry)
- **P0-14** AI Brain "10min cycle" log → "1h cycle" (`core/ai_brain.py:163` vs `:105`)
- **P0-15** `dashboard.html` git'e ekle (97KB untracked)

### KAPALI (kod-kanıtlı, 2026-05-13 audit ile doğrulandı)

- **P0-01** ✅ 2026-05-08 (`ai_brain.py:319-326,1993-2002,2011-2017` "NO auto-execute fallback")
- **P0-03** ✅ (grep `export_private_key` → 0 hit; `portfolio_handler.py:113` "PK access now via OS keychain")
- **P0-05** ✅ 2026-05-09
- **P0-06** ✅ 2026-05-08 (`requirements.txt:39`)
- **P0-07** ✅ 2026-05-09
- **P0-09** ✅ 2026-05-08 (`core/kelly.py:38-52`)

## Son Closure'lar (referans)

- **P0-05** Atomic backup + SHA256 + manifest + restore CLI ✅ 2026-05-09
- **P0-07** Reference price audit (Binance kline ground-truth) ✅ 2026-05-09
- **Crypto fee fix** 0.072 → 0.07 (Polymarket docs cross-check) ✅ 2026-05-11
- **P1-01 Wave 1+1b+2+3+3b** Coverage tests ✅ 2026-05-11
- **P1-02 Wave 1** AI Advisor microservice scaffold (FastAPI + 6 test) ✅ 2026-05-11
- **P1-07** mypy strict — 0 hata, baseline regen ✅ 2026-05-11
- **Epic 11 FULL CLOSURE** Mainnet-ready (T11.1-T11.8 + 3 kritik pre-mainnet bug fix) ✅ 2026-04-24

## Preferences

- Türkçe konuş (kod İngilizce, log/karar Türkçe karışık)
- Bullet list yerine progress log entry stili (tarihli, dosya:satır referanslı)
- Her closure'da `memory/landmarks/` altına özet bırak
- Mainnet blocker varsa BÜYÜK BÜYÜK uyar; defense-in-depth'i mainnet blocker'dan ayrı tut
- `Plan` mode → küçük tek soru sor → büyük blok atma, küçük adım küçük commit
