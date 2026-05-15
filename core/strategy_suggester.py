"""
Phase 79b: AI Strategy Suggester — Claude finds niche edges.

Every 4 hours, Claude analyzes all available data and suggests ONE new strategy.
Night (00:00-09:00 TR / 21:00-06:00 UTC): Auto-create if backtest passes.
Day (09:00-00:00 TR / 06:00-21:00 UTC): Telegram approval required.

Usage:
    suggester = StrategySuggester(db, engine, bot_app)
    await suggester.run()  # Called by JobQueue every 4h
"""

import logging
import os
from datetime import UTC, datetime

import aiosqlite

logger = logging.getLogger("polypaper.core.strategy_suggester")

# Timezone: Turkey = UTC+3
NIGHT_START_UTC = 21  # 00:00 TR
NIGHT_END_UTC = 6  # 09:00 TR

SUGGEST_INTERVAL = int(os.getenv("AI_STRATEGY_SUGGEST_INTERVAL", "14400"))  # 4h default

SUGGEST_SYSTEM = """Sen PolyPaper Bot'un strateji keşifçisisin. DERIN POLYMARKET BILGISI ile
TAM 1 ADET yeni niş strateji olusturacaksin.

═══ POLYMARKET DERIN BILGI ═══

MARKET YAPISI:
- Polymarket kripto Up/Down binary prediction market
- Her 5 dakikada yeni market acilir: "BTC 5dk sonra simdiki fiyatin ustunde mi?"
- Her 15 dakikada yeni market acilir: "BTC 15dk sonra simdiki fiyatin ustunde mi?"
- 4 asset: BTC, ETH, SOL, XRP. Her biri 5m ve 15m marketleri var.
- UP token + DOWN token = $1.00 (her zaman). Kazanan taraf $1.00, kaybeden $0.00 alir.
- Settlement: Market suresi bitince, Binance spot fiyatina gore otomatik sonuclanir.

ODDS VE ZONE MEKANIGI:
- Odds = token fiyati = piyasanin tahmini olasilik
- UP odds 0.60 demek = piyasa %60 ihtimalle UP kazanir diyor
- Zone'lar: 0-20c (ucuz, riskli), 20-35c (deger), 35-50c (dengeli), 50-65c (favori ama fee yuksek),
  65-80c (guclu favori), 80-100c (neredeyse kesin)
- FEE YAPISI: Polymarket taker fee = p × (1-p) × 2. Yani:
  50c'de: %2 fee (en dusuk). 30c veya 70c'de: %4.2. 10c veya 90c'de: %1.8.
  KRITIK: 40-60c zone'da fee dusuk AMA edge de dusuk. 20-40c veya 60-80c'de fee yuksek AMA edge potansiyeli var.

MARKET DINAMIKLERI:
- 5m market: Cok hizli, momentum güçlü, Binance spot lead 2-10sn edge
- 15m market: Daha yavas, trend daha belirgin, mean-reversion firsatlari daha fazla
- Gece (00-06 UTC): Az likidite, daha cok mispricing, spread genis
- Ogle (12-18 UTC): Yuksek likidite, daha efficient, spread dar
- Haftasonu: Daha az katilimci, volatilite düsük ama mispricing yuksek

BINANCE-POLYMARKET ILISKISI:
- Polymarket odds'lari Binance spot fiyatini TAKİP EDER (2-10sn gecikme)
- Bu gecikme = GERCEK EDGE. Binance'de hareket basladiysa Polymarket henuz ayarlanmamis olabilir.
- BTC 5m: Binance lead en guclu (en likit market, en hizli fiyat kesfı)
- ETH/SOL/XRP: Binance lead daha yavas (daha az likit, daha gec ayarlanir)
- KORELASYON: BTC yukselince ETH/SOL genelde takip eder (30-120sn gecikmeyle)

BILINEN EDGE KAYNAKLARI:
1. Momentum: Son 60sn Binance spot hareketi → Polymarket henuz yansitmadi
2. Mean-Reversion: Odds asiri ucta (>80c veya <20c) → geri donme egilimi
3. Zone Mispricing: 35-50c zone'da fee dusuk + odds dengeli = iyi risk/odül
4. Time Pattern: Bazi saatlerde belirli asset'ler daha predictable
5. Cross-Asset: BTC hareket etti → ETH/SOL henuz hareket etmedi = lead-lag
6. Orderbook Pressure: Alici/satici baskisi kisa vadede fiyati yon verir
7. Late Window: Market kapanisina 1-2dk kala odds ekstrem → mean-revert veya confirm

STRATEJI TIPLERI (bot'un destekledikleri):
- fusion: 5 sinyalin agirlikli birlesimi (en genel, en esnek)
- momentum: Trend takipcisi (trending market'te iyi, ranging'de kotu)
- contrarian: Mean-reversion (extreme odds'ta iyi, trending'de kotu)
- scalper: Kucuk hareketlerden hizli kar (spread dar olmalı)
- sniper: Cok secici, az trade, yuksek WR hedefli
- highthreshold: Sadece 80c+ zone'da trade (cok yuksek WR ama dusuk kar/trade)
- flashcrash: Ani odds dususlerinde alim (nadir ama karli)
- streak: Ardisik ayni yon sonuclardan sonra ters yonde trade

PARAMETRELER:
- asset: BTC, ETH, SOL, XRP
- timeframe: 5m (hizli, momentum), 15m (yavas, trend)
- direction: up (yukari), down (asagi), any (en guclu tarafi sec)
- odds_threshold: 0.30-0.70 arasi (ne zaman trade ac)
"""

SUGGEST_USER_TEMPLATE = """{gathered_data}

═══ GOREV ═══
Yukaridaki tum verilere dayanarak TAM 1 ADET yeni NIS strateji olustur.

KURALLAR:
1. Mevcut aktif stratejilerle AYNI olmasin (farkli nis)
2. Gecmis basarisiz CREATE denemelerinden KACIN (changelog'a bak)
3. 5m VEYA 15m olabilir — 15m daha az denenmis, firsat olabilir
4. Fee'yi yenecek kadar edge olmali — dusuk edge zone'lardan kacin
5. Skip analizine bak — neden trade acilmiyor? Bu sorunu cozen strateji olustur.
6. Saat bazli performansa bak — en iyi saatlere odaklan
7. Zone bazli performansa bak — karli zone'lari hedefle
8. $1 ile basla (paper trading, risk dusuk)
9. Neden bu stratejiyi onerdigini ACIKLA — hangi veriye dayanıyor?

CIKTI (SADECE JSON):
{{
  "strategy": {{
    "strategy_type": "fusion|momentum|contrarian|scalper|sniper|highthreshold|flashcrash|streak",
    "asset": "BTC|ETH|SOL|XRP",
    "direction": "up|down|any",
    "odds_threshold": 0.30-0.70,
    "timeframe": "5m|15m",
    "label_hint": "kisaca bu stratejinin adi (ornek: ETH_15m_night_mean_revert)"
  }},
  "reasoning": "Turkce detayli aciklama — hangi veriye dayanarak bu stratejiyi oneriyorsun",
  "edge_source": "momentum|mean_reversion|zone_mispricing|time_pattern|spot_lead|cross_asset|orderbook",
  "expected_wr": 50-65,
  "risks": "ne ters gidebilir — hangi market kosulunda basarisiz olur",
  "avoid_because": "gecmis basarisiz denemelerden ne ogrendik, neleri tekrarlamiyoruz"
}}"""


class StrategySuggester:
    """Periodic AI-driven strategy discovery."""

    def __init__(self, db, engine, bot_app=None):
        self.db = db
        self.engine = engine
        self.bot_app = bot_app
        self._last_run = None

    def _is_night_utc(self) -> bool:
        """Check if current UTC hour is in Turkey night window."""
        hour = datetime.now(UTC).hour
        if NIGHT_START_UTC > NIGHT_END_UTC:  # Wraps midnight
            return hour >= NIGHT_START_UTC or hour < NIGHT_END_UTC
        return NIGHT_START_UTC <= hour < NIGHT_END_UTC

    async def run(self):
        """Main entry — called by JobQueue every 4 hours."""
        try:
            logger.info("🔮 Strategy Suggester: starting cycle...")

            # Step 1: Gather data (reuse AI Brain's gather + extras)
            brain = getattr(self.engine, "analyst", None)
            if not brain:
                logger.warning("Strategy Suggester: AI Brain not available")
                return

            base_data = await brain._gather_data()
            if not base_data:
                logger.warning("Strategy Suggester: no data gathered")
                return

            # Step 2: Add niche discovery data
            niche_data = await self._discover_niches()
            full_data = base_data + "\n" + niche_data

            # Step 3: Call Claude with strategy discovery prompt
            user_prompt = SUGGEST_USER_TEMPLATE.format(gathered_data=full_data)
            response = await brain._call_claude(SUGGEST_SYSTEM, user_prompt)
            if not response:
                response = await brain._call_groq(SUGGEST_SYSTEM, user_prompt)
            if not response:
                logger.warning("Strategy Suggester: LLM returned nothing")
                return

            # Step 4: Parse response
            parsed = brain._parse(response)
            if not parsed or "strategy" not in parsed:
                logger.warning(
                    f"Strategy Suggester: parse failed. Response preview: {(response or '')[:200]}"
                )
                return

            strat = parsed["strategy"]
            reasoning = parsed.get("reasoning", "?")
            edge = parsed.get("edge_source", "?")
            expected_wr = parsed.get("expected_wr", "?")
            risks = parsed.get("risks", "?")
            avoid = parsed.get("avoid_because", "")

            logger.info(
                f"🔮 Suggested: {strat.get('label_hint', '?')} [{strat.get('strategy_type')}] "
                f"{strat.get('asset')}/{strat.get('timeframe')} {strat.get('direction')} "
                f"@{strat.get('odds_threshold')} | edge={edge}"
            )

            # Step 5: Mini backtest
            backtest_result = await self._mini_backtest(strat)

            # Step 6: Auto-create (night) or approval (day)
            is_night = self._is_night_utc()

            if is_night and backtest_result and backtest_result.get("wr", 0) >= 50:
                # Night: auto-create if backtest passes
                sid = await self._create_strategy(strat, reasoning, backtest_result)
                if sid:
                    await self._notify(
                        f"🌙 <b>Gece Otomatik Strateji</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Tip: <b>{strat.get('strategy_type')}</b> | {strat.get('asset')}/{strat.get('timeframe')}\n"
                        f"Yon: {strat.get('direction')} | Threshold: {strat.get('odds_threshold')}\n"
                        f"Edge: {edge} | Beklenen WR: {expected_wr}%\n\n"
                        f"Backtest: {backtest_result.get('trades', 0)}t "
                        f"WR={backtest_result.get('wr', 0):.0f}% "
                        f"PnL={backtest_result.get('pnl', 0):+.2f}\n\n"
                        f"Mantik: {reasoning[:300]}\n"
                        f"Risk: {risks[:150]}"
                    )
            else:
                # Day: send approval request
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup

                bt_str = "Backtest yok"
                bt_engine = ""
                if backtest_result:
                    eng = backtest_result.get("engine", "legacy")
                    bt_engine = " (ReplayEngine)" if eng == "ReplayEngine" else " (legacy)"
                    parts = [
                        f"{backtest_result.get('trades', 0)}t",
                        f"WR={backtest_result.get('wr', 0):.0f}%",
                        f"PnL={backtest_result.get('pnl', 0):+.2f}",
                    ]
                    if backtest_result.get("sharpe"):
                        parts.append(f"Sharpe={backtest_result['sharpe']:.2f}")
                    bt_str = " ".join(parts)

                text = (
                    f"🔮 <b>Yeni Strateji Onerisi</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Tip: <b>{strat.get('strategy_type')}</b> | {strat.get('asset')}/{strat.get('timeframe')}\n"
                    f"Yon: {strat.get('direction')} | Threshold: {strat.get('odds_threshold')}\n"
                    f"Edge: {edge} | Beklenen WR: {expected_wr}%\n\n"
                    f"📊 Backtest{bt_engine}: <b>{bt_str}</b>\n\n"
                    f"💡 Mantik: {reasoning[:400]}\n\n"
                    f"⚠️ Risk: {risks[:200]}\n"
                    f"{'📚 Kacinilan: ' + avoid[:150] if avoid else ''}"
                )

                # Store for callback
                self.__class__._pending_suggest = {
                    "strategy": strat,
                    "reasoning": reasoning,
                    "backtest": backtest_result,
                }

                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "✅ Onayla ve Ekle", callback_data="suggest_approve"
                            ),
                            InlineKeyboardButton("❌ Reddet", callback_data="suggest_reject"),
                        ]
                    ]
                )

                admin_id = os.getenv("ADMIN_TELEGRAM_ID")
                if admin_id and self.bot_app:
                    await self.bot_app.bot.send_message(
                        chat_id=admin_id, text=text, parse_mode="HTML", reply_markup=keyboard
                    )

            self._last_run = datetime.now(UTC)

        except Exception as e:  # noqa: BLE001
            # T1.4 Faz 3: JobQueue top-level umbrella (4h cycle). run() LLM
            # (httpx/anthropic), Telegram (InlineKeyboardMarkup +
            # send_message), DB, parse birleşimi içeriyor — tek bir
            # beklenmedik hata cycle'ı çöpe atsın ama bot'u öldürmesin.
            # Bilinçli şemsiye.
            logger.error(f"Strategy Suggester error: {e}", exc_info=True)

    # Class-level storage for pending approval.
    # INTENTIONAL singleton — Telegram callback handlers access via
    # ``StrategySuggester._pending_suggest`` (see ai_handler.py:712,719,734).
    # Writes go through ``self.__class__._pending_suggest = ...`` at L235.
    # Do NOT convert to instance-attr; callbacks are class-bound not instance.
    _pending_suggest: dict = {}

    async def _discover_niches(self) -> str:
        """Find untried asset/timeframe/direction/type combinations."""
        lines = ["\n═══ NIS KESFI — DENENMEMIS KOMBINASYONLAR ═══"]
        try:
            # Get all existing combinations
            existing = await self.db.conn.execute_fetchall(
                "SELECT DISTINCT asset, timeframe, direction, strategy_type FROM strategies"
            )
            existing_set = set()
            for r in existing:
                existing_set.add(f"{r[0]}_{r[1]}_{r[2]}_{r[3]}")

            # Generate all possible combinations
            assets = ["BTC", "ETH", "SOL", "XRP"]
            timeframes = ["5m", "15m"]
            directions = ["up", "down", "any"]
            types = ["fusion", "momentum", "contrarian", "sniper", "scalper"]

            untried = []
            for a in assets:
                for tf in timeframes:
                    for d in directions:
                        for t in types:
                            key = f"{a}_{tf}_{d}_{t}"
                            if key not in existing_set:
                                untried.append(f"  {a}/{tf} {d} [{t}]")

            if untried:
                lines.append(f"  Toplam {len(untried)} denenmemis kombinasyon:")
                # Show a representative sample
                for u in untried[:30]:
                    lines.append(u)
                if len(untried) > 30:
                    lines.append(f"  ... ve {len(untried) - 30} daha")
            else:
                lines.append("  Tum kombinasyonlar denenmis — varyasyonlari dene")

            # Show which 15m strategies exist (probably very few)
            m15_count = sum(1 for e in existing if e[1] == "15m")
            m5_count = sum(1 for e in existing if e[1] == "5m")
            lines.append(f"\n  5m stratejiler: {m5_count} | 15m stratejiler: {m15_count}")
            if m15_count < 3:
                lines.append("  ⚠️ 15m NEREDEYSE HIC DENENMEMIS! Buyuk firsat.")

        except (aiosqlite.Error, IndexError, TypeError, ValueError, AttributeError) as e:
            # T1.4 Faz 3: DISTINCT SELECT + tuple row access (r[0..3]) +
            # set/list building. Realistic modes: aiosqlite.Error (DB),
            # IndexError (r[0] boş tuple), TypeError/ValueError
            # (f-string / sum guard), AttributeError (self.db.conn None).
            lines.append(f"  Nis kesfı hatasi: {e}")

        return "\n".join(lines)

    async def _mini_backtest(self, strat: dict) -> dict:
        """Phase 81: Run REAL ReplayEngine backtest for proposed strategy.

        Eski mini-backtest (odds_history'den basit simülasyon) yerine,
        gerçek L2 orderbook verileriyle ReplayEngine çalıştırır.
        Live strategy adaptör aracılığıyla backtest edilir → TEK STRATEJİ.

        Falls back to legacy mini-backtest if ReplayEngine fails.
        """
        asset = strat.get("asset", "BTC")
        tf = strat.get("timeframe", "5m")
        strat.get("direction", "any")
        strat.get("odds_threshold", 0.50)
        stype = strat.get("strategy_type", "momentum")

        # ── Phase 81: Gerçek ReplayEngine backtest ──
        try:
            from backtest.replay_engine import ReplayConfig, ReplayEngine

            config = ReplayConfig(
                strategy_name=stype,  # Live strateji adı (adaptör otomatik çalışır)
                initial_balance=10000.0,
                trade_amount=1.0,
                asset_filter=asset,
                timeframe_filter=tf,
                last_n=50,  # Son 50 market
            )

            engine = ReplayEngine(self.db, config)
            stats = await engine.run()

            if stats.total_trades >= 5:
                return {
                    "trades": stats.total_trades,
                    "wins": stats.wins,
                    "wr": round(stats.win_rate, 1),
                    "pnl": round(stats.total_pnl, 2),
                    "sharpe": round(stats.sharpe_ratio, 2) if hasattr(stats, "sharpe_ratio") else 0,
                    "markets_checked": getattr(engine, "_markets_processed", 0),
                    "engine": "ReplayEngine",
                }
            # Yetersiz trade → legacy fallback'e düş
            logger.info(
                f"ReplayEngine backtest: sadece {stats.total_trades} trade — legacy fallback"
            )

        except Exception as e:  # noqa: BLE001
            # T1.4 Faz 3: ReplayEngine umbrella. Dinamik import
            # (backtest.replay_engine) + 3rd-party deps (numpy, l2
            # snapshot parsing, asyncio timing) geniş yüzey — "fallback to
            # legacy" pattern bilinçli. Tek bir beklenmedik hata legacy
            # yolu tetiklesin.
            logger.warning(f"ReplayEngine backtest failed: {e} — legacy fallback")

        # ── Legacy fallback: odds_history tabanlı basit simülasyon ──
        return await self._mini_backtest_legacy(strat)

    async def _mini_backtest_legacy(self, strat: dict) -> dict:
        """Legacy mini-backtest: odds_history tablosundan basit simülasyon."""
        try:
            asset = strat.get("asset", "BTC")
            tf = strat.get("timeframe", "5m")
            direction = strat.get("direction", "any")
            threshold = strat.get("odds_threshold", 0.50)

            rows = await self.db.conn.execute_fetchall(
                """SELECT slug, up_odds, down_odds, timestamp
                   FROM odds_history
                   WHERE slug LIKE ? AND slug LIKE ?
                   ORDER BY timestamp DESC
                   LIMIT 1000""",
                (f"%{asset.lower()}%", f"%{tf}%"),
            )

            if not rows or len(rows) < 20:
                return {"trades": 0, "wins": 0, "wr": 0, "pnl": 0, "note": "yetersiz veri"}

            markets: dict[str, list[dict]] = {}
            for r in rows:
                slug = r[0]
                if slug not in markets:
                    markets[slug] = []
                markets[slug].append({"up": r[1], "down": r[2], "ts": r[3]})

            trades = 0
            wins = 0
            total_pnl = 0.0
            market_list = list(markets.items())[-50:]

            for _slug, snapshots in market_list:
                if len(snapshots) < 3:
                    continue

                mid = len(snapshots) // 2
                entry_snap = snapshots[mid]
                up = entry_snap["up"]
                down = entry_snap["down"]

                if direction == "up" and up >= threshold:
                    trade_dir, entry_price = "up", up
                elif direction == "down" and down >= threshold:
                    trade_dir, entry_price = "down", down
                elif direction == "any":
                    if up >= down and up >= threshold:
                        trade_dir, entry_price = "up", up
                    elif down >= threshold:
                        trade_dir, entry_price = "down", down
                    else:
                        continue
                else:
                    continue

                last = snapshots[-1]
                settled_up = last["up"] >= 0.80

                won = (trade_dir == "up" and settled_up) or (trade_dir == "down" and not settled_up)

                shares = 1.0 / entry_price if entry_price > 0 else 0
                fee = entry_price * (1 - entry_price) * 2 * 1.0
                pnl = (shares * 1.0 - 1.0 - fee) if won else (-1.0 - fee)

                trades += 1
                if won:
                    wins += 1
                total_pnl += pnl

            wr = (wins / trades * 100) if trades > 0 else 0
            return {
                "trades": trades,
                "wins": wins,
                "wr": round(wr, 1),
                "pnl": round(total_pnl, 2),
                "markets_checked": len(market_list),
                "engine": "legacy",
            }

        except (aiosqlite.Error, IndexError, KeyError, TypeError, ValueError, AttributeError) as e:
            # T1.4 Faz 3: odds_history SELECT + tuple row access (r[0..3])
            # + dict building + fee/pnl arithmetic. Realistic modes:
            # aiosqlite.Error (DB), IndexError (r[0] boş), KeyError
            # (entry_snap["up"]), TypeError/ValueError (arithmetic on None /
            # float coerce), AttributeError (self.db.conn None).
            logger.debug(f"Legacy mini backtest error: {e}")
            return {"trades": 0, "wins": 0, "wr": 0, "pnl": 0, "error": str(e)}

    async def _create_strategy(self, strat: dict, reasoning: str, bt: dict) -> str:
        """Create the strategy in DB. Returns strategy ID or None."""
        try:
            import uuid

            user = await self.db.conn.execute_fetchall("SELECT id FROM users LIMIT 1")
            wallet = await self.db.conn.execute_fetchall("SELECT id FROM wallets LIMIT 1")
            if not user or not wallet:
                return None

            stype = strat.get("strategy_type", "fusion")
            asset = strat.get("asset", "BTC").upper()
            tf = strat.get("timeframe", "5m")
            direction = strat.get("direction", "any").lower()
            threshold = strat.get("odds_threshold", 0.50)
            hint = strat.get("label_hint", "")

            label = (
                f"AI_{hint}"
                if hint
                else f"AI_{stype[:1].upper()}_{asset}_{tf}_{direction}_{threshold}"
            )
            label = label[:50]  # Truncate

            # Check duplicate
            existing = await self.db.conn.execute_fetchall(
                "SELECT id FROM strategies WHERE label=?", (label,)
            )
            if existing:
                logger.info(f"Strategy Suggester: {label} already exists, skipping")
                return None

            sid = str(uuid.uuid4())
            now = datetime.now(UTC).isoformat()
            await self.db.conn.execute(
                """INSERT INTO strategies (id,user_id,wallet_id,label,asset,timeframe,
                    direction,trade_amount,odds_threshold,strategy_type,status,
                    minutes_before_end,max_executions_per_event,
                    created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,1.0,?,?,'active',0.5,1,?,?)""",
                (
                    sid,
                    user[0][0],
                    wallet[0][0],
                    label,
                    asset,
                    tf,
                    direction,
                    threshold,
                    stype,
                    now,
                    now,
                ),
            )
            await self.db.conn.commit()

            # Log to changelog
            from core.changelog import log_change

            bt_str = (
                f"BT:{bt.get('trades', 0)}t WR={bt.get('wr', 0):.0f}% PnL={bt.get('pnl', 0):+.2f}"
            )
            await log_change(
                self.db,
                sid,
                "CREATE",
                "strategy_suggester",
                new={
                    "strategy_type": stype,
                    "asset": asset,
                    "timeframe": tf,
                    "direction": direction,
                    "odds_threshold": threshold,
                },
                reason=f"{reasoning[:200]} | {bt_str}",
                label=label,
            )

            logger.info(f"🔮 Created: {label} [{stype}] {asset}/{tf} {direction} @{threshold}")
            return sid

        except (
            ImportError,
            aiosqlite.Error,
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            AttributeError,
        ) as e:
            # T1.4 Faz 3: dinamik uuid/changelog import + users/wallets/
            # strategies DB yazımı + tuple row access (user[0][0]).
            # Realistic modes: ImportError (core.changelog gecikmeli),
            # aiosqlite.Error (INSERT/SELECT), IndexError (empty users/
            # wallets), KeyError/TypeError/ValueError (strat dict access),
            # AttributeError (self.db.conn None).
            logger.error(f"Strategy create error: {e}")
            return None

    async def _notify(self, text: str):
        """Send notification to admin."""
        admin_id = os.getenv("ADMIN_TELEGRAM_ID")
        if not admin_id or not self.bot_app:
            return
        try:
            # Sanitize HTML
            import re

            safe = re.sub(
                r"<(?!/?(b|i|code|pre|a)\b)[^>]*>",
                lambda m: m.group().replace("<", "&lt;").replace(">", "&gt;"),
                text,
            )
            for i in range(0, len(safe), 4000):
                try:
                    await self.bot_app.bot.send_message(
                        chat_id=admin_id, text=safe[i : i + 4000], parse_mode="HTML"
                    )
                except Exception:  # noqa: BLE001
                    # T1.4 Faz 3: HTML parse_mode fallback. telegram modülü
                    # burada local import yok; telegram.error.BadRequest /
                    # TimedOut / NetworkError namespace dışı — tip bazlı
                    # narrow yapılamaz. Bilinçli retry-without-HTML pattern.
                    await self.bot_app.bot.send_message(chat_id=admin_id, text=text[i : i + 4000])
        except Exception as e:  # noqa: BLE001
            # T1.4 Faz 3: notify best-effort umbrella. re.sub + chunked
            # telegram send — network/regex/telegram hataları bildirimi
            # boş geçsin, bir üst çağrı (run) bozulmasın.
            logger.debug(f"Suggest notify: {e}")
