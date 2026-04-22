"""
PolyPaper Bot - AI Brain v3 (Phase 32: True Learning)

FIXES from Phase 31:
- DELETE bug: filter out already-stopped strategies
- Threshold protection: max ±0.05 for existing, ±0.10 for AI-created
- AI strategies tracked SEPARATELY in prompt
- Mini-backtest before CREATE
- Real learning: outcome measurement + explicit feedback to Claude
- 10-minute cycle (was 5min)

Architecture:
  GATHER (DB + Binance) → BACKTEST (historical) → CLAUDE (JSON actions)
  → VALIDATE (safety rules) → EXECUTE → MEASURE (24h) → LEARN (feed back)
"""
import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiosqlite  # Epic 8 T8.1: narrow DB exception handling

from core.bg_task import safe_create_task  # Phase 82e Sprint 2.1

logger = logging.getLogger("polypaper.core.ai_brain")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GROK_API_KEY = os.getenv("GROK_API_KEY", "")
MAX_BUDGET = 15.0

# Phase 32: Tighter safety + 10min cycle
# Phase 75+: CHANGED to 6 hour cooldown + 50 trade minimum (GPT recommendation)
MAX_ACTIONS = 8
MAX_SCALE_HUMAN = 3.0     # Human strategies: max 3x
MAX_SCALE_AI = 5.0        # AI strategies: max 5x (experimental)
MAX_THR_DELTA_HUMAN = 0.05  # Human strategies: tight threshold protection
MAX_THR_DELTA_AI = 0.15     # AI strategies: more freedom
MAX_TRADE_AMOUNT = 25.0
# PROTECTED_STRATEGIES: label → preferred odds_threshold.
# Purpose: shield against LLM-driven AI Brain mistakes (STOP + TUNE actions).
#   - A proven winner can weather a short bad streak; we don't want the LLM
#     acting on noise. Format: {label: target_threshold}.
#   - NOT a defense against `auto_optimizer` — that runs deterministic
#     PnL/WR/loss-streak rules which apply to ALL non-classic strategies
#     (paper AND live). Parity is intentional: self-healing design assumes
#     live mirrors paper, so if a strategy stops paying in paper, live
#     should stop too.
#   - Not tied to LIVE_STRATEGIES (live_trader.py:40). A strategy can be
#     LIVE without being PROTECTED (e.g. AI_F_* experimental strategies:
#     they trade real $1 but AI Brain may stop/tune them based on fresh
#     performance data — deliberate).
# Decision log: 2026-04-20 Epic 4 T4.4 parity audit (kept as-is).
PROTECTED_STRATEGIES = {"M_BTC_5m_any_0.92": 0.92, "BTC High-Threshold Pure": 0.80}
# Sprint 3 S3-01: ENV-configurable cycle. Default 1h (was 6h).
# More strategies = more trades = AI can act more often.
CYCLE_INTERVAL = int(os.getenv("AI_BRAIN_CYCLE", "3600"))
ACTIVE_INTERVAL = CYCLE_INTERVAL  # Same in active mode
MIN_TRADES_FOR_ACTION = int(os.getenv("AI_MIN_TRADES", "15"))  # Was 50 — lowered for 11+ strategies

# Phase 69: 2-Agent mode prompts
OPTIMIST_SYSTEM = """Sen bir Polymarket analisti ve IYIMSER perspektiften bakiyorsun.
Gorevin: Bu markette neden trade etmeliyiz? Firsatlari bul, potansiyeli goster.

Fermi Decompozisyon kullan:
1. Base rate: Benzer marketlerin gecmis WR'si nedir?
2. Ozel faktor: Bu marketi farkli kilan ne var?
3. Timing: Simdi mi girmeli, beklemeli mi?
4. Tahmini WR: Parcalari birlestirip olas kazanma oranini soyle.

CIKTI (JSON): {"bullish_case": "neden girilmeli", "estimated_wr": 0.55-0.80,
"best_strategies": ["strat1","strat2"], "conviction": 0.0-1.0, "fermi_steps": [...]}"""

CRITIC_SYSTEM = """Sen bir Polymarket risk yoneticisi ve SKEPTIK perspektiften bakiyorsun.
Gorevin: Bu trade neden basarisiz olabilir? Riskleri goster, kayiplari tahmin et.

Analiz et:
1. Yanilma senaryolari: Ne olursa kaybederiz?
2. Likidite riski: Cikabilir miyiz?
3. Manipulation: Bu fiyat manipule edilmis olabilir mi?
4. Timing: Gecmiste bu saatte/gunde performans nasil?

CIKTI (JSON): {"bearish_case": "neden girilmemeli", "risk_score": 0.0-1.0,
"kill_strategies": ["strat1","strat2"], "concerns": [...]}"""

BRAIN_SYSTEM = """Sen PolyPaper Bot'un otonom trading beynisin. GERCEK OGRENME yapiyorsun.

PROJE: Polymarket 5dk Up/Down kripto paper trading. BTC/ETH/SOL/XRP.

KRITIK KURALLAR (IHLAL ETME):
1. M_BTC_5m_any_0.92 threshold'u ASLA degistirme (0.92 sabit, bu stratejinin gucu)
2. BTC High-Threshold Pure threshold'u ASLA degistirme (0.80 sabit)
3. Zaten STOPPED olan stratejiyi tekrar DELETE etme — sadece ACTIVE olanlari isle
4. AI_ stratejileri $1 ile basla, 20+ trade ve %55+ WR olmadan scale ETME
5. Human stratejilerin threshold'unu max ±0.05 degistir
6. Her CREATE walk-forward backtest ile dogrulanir — FAIL olursa deploy edilmez
7. 6-sinyal fusion aktif: odds, ema, momentum, volatility, time, ORDERBOOK IMBALANCE
8. Thompson Sampling aktif: dusuk performansli stratejiler otomatik bloke ediliyor
9. Regime detection aktif: trending/ranging/volatile → uyumsuz stratejiler atlanir

KANITLANMIS VERILER:
- Zone 35-50c: +$152 (en karli zone)
- Zone 65-80c: +$19 (guvenli zone)
- Zone 50-65c: -$48 (TEHLIKELI)
- Fusion: 122t %65 WR, EV +1.15 (en iyi tip)
- Momentum: 135t %67 WR, EV +0.36
- Scalper/Martingale: KAYBEDIYOR
- UP yonu: %60 WR vs DOWN %53

FERMI DECOMPOZISYON (Phase 69 — her karar icin kullan):
1. Base rate: Benzer market/strateji gecmis WR → baz oran
2. Ozel faktor: Mevcut durum farkli mi? (volatilite, saat, zone)
3. Sinyal gucu: Confluence gate kac sinyal uyumlu? (4+/6 = iyi)
4. Risk: Bayesian posterior vs market fiyat fark > 2c mi?
Son tahmin = base × ozel × sinyal × risk

AKSIYON TIPLERI:
- DELETE: SADECE active + kaybeden strateji (stopped olanlari IGNORE ET)
- CREATE: Yeni strateji ($1 ile basla, reason'a neden olusturdugun yaz)
  Ornek: {"type":"CREATE","strategy_type":"fusion","asset":"ETH","direction":"any","odds_threshold":0.50,"reason":"ETH 5m fusion 65-80c zone'da karli"}
- SCALE: stake artir (human max 3x, AI max 5x ama 20+ trade gerekli)
- TUNE: threshold degistir (human max ±0.05, AI max ±0.15)
  Ornek: {"type":"TUNE","id":"df8902ba","field":"odds_threshold","value":0.50,"reason":"WR yuksek, threshold dusur daha cok trade ac"}
- RESTART: Durmus karli stratejiyi baslat
  Ornek: {"type":"RESTART","id":"4da8cbee","reason":"market trending'e dondu, momentum calisabilir"}
- OPTIMIZE: Stratejiyi Optuna ile optimize et (arka planda calisir, sonuc sonraki cycle'da gorunur)
  Ornek: {"type":"OPTIMIZE","strategy_name":"hour_edge","reason":"WR dusuk, parametre taramasi gerekli"}
- APPLY_HYPEROPT: Bekleyen HyperOpt sonucunu stratejiye uygula
  Ornek: {"type":"APPLY_HYPEROPT","result_id":7,"reason":"overfit degil, test score iyi"}
- INSIGHT: Gozlem (aksiyon degil, sadece not)

KARAR VERIRKEN DIKKAT ET:
1. SKIP ANALIZI bloğuna bak — neden trade acilmiyor? SIG_WEAK coksa threshold dusur. REGIME coksa o strateji tipini durdur.
2. FEE ANALIZI'ne bak — fee/PnL > %50 ise daha yuksek edge gereken trade'ler ac, dusuk edge'li trade'leri engellet.
3. SAAT BAZLI PERFORMANS'a bak — en iyi saatlerde daha agresif ol, en kotu saatlerde dikkatli ol.
4. STRATEJI BAZLI PERFORMANS'a bak — tp_exit ve settle_win sayilari yuksek olan stratejileri koru.
5. ANLIK MARKET DURUMU'na bak — spot momentum guclu ise o yone CREATE yap.
6. BOT KONFIGURASYONU'na bak — hangi sinyaller kapali, agirliklar ne. Buna gore strateji olustur.
7. SADECE 4-5 AKTIF STRATEJI varsa ONCELIKLE CREATE veya RESTART yap. Fazla DELETE yapma.
8. Paper trading'de cesur ol ama NEDEN kaybettigini ANALIZ et.
9. HYPEROPT SONUCLARI bloğuna bak — bekleyen (BEKLIYOR) ve overfit olmayan sonuclar varsa APPLY_HYPEROPT yap.
   Eger bir strateji kotu gidiyorsa ve son 7 gunde optimize edilmediyse OPTIMIZE action ver.
   TUNE ile elle ayar yerine OPTIMIZE ile Optuna'ya birak — 100 deneme yapar, daha iyi sonuc bulur.

CIKTI (SADECE JSON, baska bir sey yazma):
{"actions": [...], "confidence": 0.0-1.0, "market_view": "bullish/bearish/sideways",
 "reasoning": "turkce — skip analizini, fee durumunu, momentum'u yorumla",
 "lessons_learned": "gecmis kararlardan ne ogrendi — oversize, fee_trap, wrong_direction vb"}
"""

TRADE_SYSTEM = """Kisa trade analizi. Max 3 satir turkce.
[emoji] [Strateji] → [Sonuc] [PnL] | Analiz: [1 cumle]"""

MISTAKE_SYSTEM = """Sen bir trade hata analisti sin. Kaybeden trade'leri analiz et.

Her kayip trade icin:
1. mistake_type: early_exit | wrong_direction | ignored_signal | bad_timing | oversize | fee_trap | low_edge
2. lesson_learned: 1 cumle (Turkce), spesifik ve olculebilir
3. applied_fix: Bu hatadan kacinmak icin parametrik oneri

SADECE JSON ciktisi ver:
{"mistake_type": "...", "lesson_learned": "...", "applied_fix": "..."}
"""


OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

class ModelRouter:
    """Phase 59→69: 4-tier model routing with OpenRouter fallback.

    Tier 1: Groq (FREE) — routine tasks
    Tier 2: OpenRouter (FREE/CHEAP) — mid-tier tasks, Groq fallback
    Tier 3: Claude (PAID) — complex reasoning
    Tier 4: OpenRouter premium — Claude/GPT-4o via OpenRouter as final fallback
    """
    TASK_MODEL_MAP = {
        # Tier 1: FREE — routine tasks (Groq Llama 70B)
        "market_scan":       ("groq", "llama-3.3-70b-versatile"),
        "data_summary":      ("groq", "llama-3.1-8b-instant"),
        "alert_format":      ("groq", "llama-3.1-8b-instant"),
        "trade_analysis":    ("groq", "llama-3.3-70b-versatile"),
        "mistake_analysis":  ("groq", "llama-3.3-70b-versatile"),
        # Phase 75: OpenRouter has no balance → route to Groq free
        "optimist_agent":    ("groq", "llama-3.3-70b-versatile"),
        "data_enrichment":   ("groq", "llama-3.1-8b-instant"),
        # Tier 3: PAID — complex reasoning (Claude)
        "strategy_decision": ("claude", "claude-sonnet-4-6"),
        "risk_assessment":   ("claude", "claude-sonnet-4-6"),
        "brain_cycle":       ("claude", "claude-sonnet-4-6"),
        "critic_agent":      ("claude", "claude-sonnet-4-6"),
    }

    # Fallback chain: skip openrouter (no balance), groq→claude only
    FALLBACK_CHAIN = os.getenv("AI_BRAIN_FALLBACK_CHAIN", "groq,claude").split(",")

    @classmethod
    def get(cls, task_type: str) -> tuple[str, str]:
        return cls.TASK_MODEL_MAP.get(task_type, ("groq", "llama-3.3-70b-versatile"))


class AIBrain:
    def __init__(self, db, engine=None, bot_app=None, settings=None):
        self.db = db
        self.engine = engine
        self.bot_app = bot_app
        self.settings = settings
        self._running = False
        self._spent = 0.0
        self._last_run = ""
        self._cycle_count = 0
        # Phase 66: Brier Score tracker for prediction calibration
        self._brier_tracker = None
        try:
            from utils.brier_tracker import BrierTracker
            self._brier_tracker = BrierTracker(db)
        except (ImportError, AttributeError) as _bt_err:
            # Epic 8 T8.1: narrow — BrierTracker is optional; skip if module
            # missing or db API shape mismatches, everything else must bubble.
            logger.debug(f"BrierTracker init: {_bt_err}")

    async def start(self):
        if not ANTHROPIC_API_KEY and not GROK_API_KEY:
            logger.warning("🧠 AI Brain: No API keys")
            return
        self._running = True
        await self._load_budget()
        await self._ensure_tables()
        remaining = MAX_BUDGET - self._spent
        logger.info(f"🧠 AI Brain v3: 10min cycle | ${self._spent:.2f}/{MAX_BUDGET:.2f} "
                    f"(${remaining:.2f} remaining)")
        # Phase 82e Sprint 2.1: scheduler death = no 10min cycles = stale AI
        safe_create_task(self._scheduler(), name="ai_brain_scheduler")

    async def _ensure_tables(self):
        try:
            await self.db.conn.executescript("""
                CREATE TABLE IF NOT EXISTS ai_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL, input_hash TEXT,
                    actions_proposed TEXT, actions_executed TEXT,
                    outcome_24h TEXT, was_correct INTEGER,
                    cost REAL DEFAULT 0, provider TEXT, notes TEXT);
                CREATE TABLE IF NOT EXISTS trade_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event TEXT NOT NULL, slug TEXT, direction TEXT,
                    strategy_id TEXT, price REAL, amount REAL,
                    pnl REAL, fee REAL, reason TEXT, metadata TEXT, ts TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_ai_ts ON ai_decisions(ts);
                CREATE INDEX IF NOT EXISTS idx_tl_ts ON trade_log(ts);

                CREATE TABLE IF NOT EXISTS trade_mistakes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER,
                    strategy_label TEXT,
                    mistake_type TEXT,
                    market_context TEXT,
                    lesson_learned TEXT,
                    applied_fix TEXT,
                    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));
                CREATE INDEX IF NOT EXISTS idx_tm_type ON trade_mistakes(mistake_type);
            """)
            # Phase 59: reasoning_json column on executions (safe ALTER — ignores if exists)
            try:
                await self.db.conn.execute(
                    "ALTER TABLE executions ADD COLUMN reasoning_json TEXT")
                await self.db.conn.commit()
                logger.info("🧠 Added reasoning_json column to executions")
            except aiosqlite.Error:
                pass  # Epic 8 T8.1: narrow — "duplicate column" OperationalError expected
            await self.db.conn.commit()
        except aiosqlite.Error as _et_err:
            # Epic 8 T8.1: narrow — DDL is idempotent; log if we hit an actual
            # sqlite failure (e.g. permission, corruption) instead of swallowing.
            logger.debug(f"_ensure_tables: {_et_err}")

    # ═══ SCHEDULER ═══
    async def _scheduler(self):
        await asyncio.sleep(120)
        # Measure outcomes first
        await self._measure_outcomes()
        while self._running:
            try:
                # Phase 35: Check if AI Brain master toggle is enabled
                if not self.engine.brain_flags.get("ai_brain", True):
                    await asyncio.sleep(CYCLE_INTERVAL)
                    continue

                now = datetime.now(timezone.utc)
                cycle_key = f"{now.strftime('%Y-%m-%d-%H')}-{now.minute // 10}"
                if self._last_run != cycle_key:
                    self._cycle_count += 1
                    await self.run_brain_cycle()
                    self._last_run = cycle_key
            except Exception as e:  # noqa: BLE001
                # Epic 8 T8.1 KEEP: scheduler is infinite-loop supervisor —
                # any exception leak kills AI cycles until restart. Must
                # catch-all and log. Do not narrow. 2026-04-22 audit.
                logger.error(f"AI Brain: {e}", exc_info=True)
            await asyncio.sleep(CYCLE_INTERVAL)

    # ═══ MAIN BRAIN CYCLE ═══
    async def run_brain_cycle(self) -> Optional[str]:
        if self._spent >= MAX_BUDGET:
            logger.warning(f"🧠 Budget: ${self._spent:.2f}/{MAX_BUDGET:.2f}")
            return "Budget exhausted"

        # Phase 75+: Minimum trade gate — prevents overfitting from low sample size
        trade_count = await self.db.conn.execute_fetchall(
            "SELECT COUNT(*) FROM executions WHERE result IS NOT NULL AND created_at > datetime('now', '-1 day')"
        )
        recent_trades = trade_count[0][0] if trade_count else 0
        if recent_trades < MIN_TRADES_FOR_ACTION:
            logger.info(f"🧠 Not enough trades for AI decision: {recent_trades}/{MIN_TRADES_FOR_ACTION}")
            return f"Minimum trades not met: {recent_trades}/{MIN_TRADES_FOR_ACTION}"

        # Step 1: Measure past decisions
        await self._measure_outcomes()

        # Step 1b: Analyze recent losses → mistakes journal (Phase 59)
        await self._analyze_losses()

        # Step 2: Gather all data
        data = await self._gather_data()
        if not data:
            return "No data"

        # Step 3: Call LLM — Phase 69: 2-Agent mode or single-agent
        _two_agent = os.getenv("AI_TWO_AGENT_MODE", "true").lower() == "true"
        if _two_agent:
            response = await self._two_agent_cycle(data)
        else:
            # Fallback: single-agent (Phase 59 routing)
            provider, model = ModelRouter.get("brain_cycle")
            if provider == "claude":
                response = await self._call_claude(BRAIN_SYSTEM, data, model)
                if not response:
                    response = await self._call_groq(BRAIN_SYSTEM, data)
            else:
                response = await self._call_groq(BRAIN_SYSTEM, data)
                if not response:
                    response = await self._call_claude(BRAIN_SYSTEM, data)
        if not response:
            return "LLM failed"

        # Step 4: Parse (with retry on failure)
        parsed = self._parse(response)
        if not parsed:
            logger.warning("🧠 Parse failed on first attempt, retrying with Groq fallback...")
            # Retry: try the other provider
            retry_response = await self._call_groq(BRAIN_SYSTEM, data)
            if retry_response:
                parsed = self._parse(retry_response)
            if not parsed:
                logger.error(f"🧠 Parse failed after retry. Response preview: {(response or '')[:200]}")
                await self._send("⚠️ <b>AI Brain Parse Hatasi</b>\n\nJSON parse 2 denemede de basarisiz. Bir sonraki cycle'da tekrar denenecek.")
                return "Parse failed (notified)"

        actions = parsed.get("actions", [])[:MAX_ACTIONS]
        lessons = parsed.get("lessons_learned", "")
        if lessons:
            logger.info(f"🧠 LESSONS: {lessons[:100]}")

        # Sprint 3 S3-04: Confidence gate — low confidence actions need approval
        confidence = parsed.get("confidence", 0.5)
        _auto_threshold = float(os.getenv("AI_AUTO_CONFIDENCE", "0.70"))

        if confidence >= _auto_threshold or not actions:
            # High confidence → auto-execute
            results = await self._execute(actions)
            await self._save_decision(data[:500], actions, results)
            await self._notify(actions, results, parsed)
        else:
            # Low confidence → queue for Telegram approval
            logger.info(f"🧠 Low confidence {confidence:.0%} < {_auto_threshold:.0%} — "
                        f"queuing {len(actions)} actions for approval")
            await self._queue_for_approval(actions, parsed, data[:500])

        return f"Cycle #{self._cycle_count}: {len(actions)} actions (conf={confidence:.0%})"

    # ═══ Phase 69: 2-AGENT AI CYCLE ═══
    async def _two_agent_cycle(self, data: str) -> Optional[str]:
        """
        2-Agent consensus: Optimist (Groq) + Critic (Claude) → synthesis.
        Source: A8 Inquisitor 3-Agent Architecture (simplified to 2).

        Flow:
          1. Groq (free) → Optimist: "Why should we trade?"
          2. Claude (paid) → Critic: "Why might this fail?"
          3. If consensus → high conviction → proceed
          4. If disagreement → lower conviction or skip
        """
        logger.info("🧠 2-Agent mode: Optimist + Critic")

        # Agent 1: Optimist (Groq — free tier)
        optimist_resp = await self._call_groq(OPTIMIST_SYSTEM, data)
        optimist = None
        if optimist_resp:
            try:
                optimist = json.loads(self._extract_json(optimist_resp))
            except Exception:  # noqa: BLE001
                # Epic 8 T8.1 audit: LLM responses are arbitrary text — JSON
                # repair may raise ValueError/KeyError/RecursionError. Fall
                # back to a best-effort stub rather than killing the cycle.
                optimist = {"bullish_case": optimist_resp[:200], "conviction": 0.5}
            logger.info(f"🟢 Optimist: conv={optimist.get('conviction', '?')}")

        # Agent 2: Critic (Claude — paid, more careful)
        critic_resp = await self._call_claude(
            CRITIC_SYSTEM, data, "claude-sonnet-4-6")
        critic = None
        if critic_resp:
            try:
                critic = json.loads(self._extract_json(critic_resp))
            except Exception:  # noqa: BLE001
                # Epic 8 T8.1 audit: see optimist above — LLM JSON repair is
                # best-effort; keep catch-all to avoid cycle abort.
                critic = {"bearish_case": critic_resp[:200], "risk_score": 0.5}
            logger.info(f"🔴 Critic: risk={critic.get('risk_score', '?')}")

        # Fallback: if both agents fail, use single brain
        if not optimist and not critic:
            logger.warning("🧠 Both agents failed → single brain fallback")
            return await self._call_claude(BRAIN_SYSTEM, data)

        # Synthesis: build enhanced prompt with both perspectives
        synthesis_data = f"""{data}

═══ 2-AGENT ANALIZ SONUCLARI ═══
IYIMSER (Groq): {json.dumps(optimist, ensure_ascii=False) if optimist else 'Yanitlamadi'}
SKEPTIK (Claude): {json.dumps(critic, ensure_ascii=False) if critic else 'Yanitlamadi'}

CONSENSUS KURALI:
- Iyimser conviction > 0.6 VE Skeptik risk < 0.4 → YUKSEK CONVICTION (scale up)
- Iyimser conviction > 0.5 VE Skeptik risk < 0.6 → NORMAL (standart islem)
- Diger durumlarda → DUSUK CONVICTION (sadece $1 veya skip)
"""
        # Final decision: Claude synthesizes
        response = await self._call_claude(BRAIN_SYSTEM, synthesis_data)
        if not response:
            response = await self._call_groq(BRAIN_SYSTEM, synthesis_data)

        return response

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract JSON object from text that may contain markdown."""
        if not text:
            return "{}"
        # Find first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return text[start:end + 1]
        return "{}"

    # ═══ DATA GATHERING ═══
    async def _gather_data(self) -> Optional[str]:
        try:
            # Active strategies only (fix DELETE bug)
            active = await self.db.conn.execute_fetchall(
                """SELECT s.id, s.label, s.strategy_type, s.trade_amount,
                    s.odds_threshold, s.direction,
                    COUNT(CASE WHEN e.result IS NOT NULL THEN 1 END) as t,
                    COALESCE(SUM(CASE WHEN e.pnl>0 AND e.result IS NOT NULL THEN 1 ELSE 0 END),0) as w,
                    COALESCE(SUM(CASE WHEN e.result IS NOT NULL THEN e.pnl ELSE 0 END),0) as pnl
                FROM strategies s LEFT JOIN executions e ON e.strategy_id=s.id
                WHERE s.status='active' GROUP BY s.id ORDER BY pnl DESC""")

            stopped = await self.db.conn.execute_fetchall(
                """SELECT s.label, s.strategy_type,
                    COUNT(CASE WHEN e.result IS NOT NULL THEN 1 END) as t,
                    COALESCE(SUM(CASE WHEN e.result IS NOT NULL THEN e.pnl ELSE 0 END),0) as pnl
                FROM strategies s LEFT JOIN executions e ON e.strategy_id=s.id
                WHERE s.status='stopped' GROUP BY s.id HAVING t > 5 ORDER BY pnl DESC""")

            bal = await self.db.conn.execute_fetchall("SELECT balance FROM wallets LIMIT 1")
            at = await self.db.conn.execute_fetchall(
                "SELECT COALESCE(SUM(pnl),0), COUNT(*) FROM executions WHERE result IS NOT NULL")

            # Binance
            market = await self._get_binance()

            # AI decision history WITH outcomes
            history = await self.db.conn.execute_fetchall(
                """SELECT ts, actions_executed, outcome_24h, was_correct, notes
                FROM ai_decisions ORDER BY ts DESC LIMIT 10""")

            # AI strategies separate tracking
            ai_strats = await self.db.conn.execute_fetchall(
                """SELECT s.label, s.strategy_type, s.trade_amount, s.odds_threshold,
                    COUNT(CASE WHEN e.result IS NOT NULL THEN 1 END) as t,
                    COALESCE(SUM(CASE WHEN e.pnl>0 AND e.result IS NOT NULL THEN 1 ELSE 0 END),0) as w,
                    COALESCE(SUM(CASE WHEN e.result IS NOT NULL THEN e.pnl ELSE 0 END),0) as pnl
                FROM strategies s LEFT JOIN executions e ON e.strategy_id=s.id
                WHERE s.label LIKE 'AI_%' GROUP BY s.id ORDER BY pnl DESC""")

            # Mini backtest: zone WR from real data
            zones = {}
            for lo, hi, lbl in [(0,0.35,'0-35c'),(0.35,0.50,'35-50c'),(0.50,0.65,'50-65c'),(0.65,0.80,'65-80c'),(0.80,1.0,'80c+')]:
                r = await self.db.conn.execute_fetchall(
                    'SELECT COUNT(*),SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),SUM(pnl) FROM executions WHERE result IS NOT NULL AND execution_price>=? AND execution_price<?',(lo,hi))
                if r and r[0][0] > 0:
                    zones[lbl] = {"t": r[0][0], "wr": r[0][1]/r[0][0]*100, "pnl": r[0][2]}

            # Build prompt
            lines = [
                f"BAKIYE: ${bal[0][0]:.2f}" if bal else "?",
                f"ALL-TIME: {at[0][0]:+.2f} / {at[0][1]} trade" if at else "",
                f"CYCLE: #{self._cycle_count} | BUTCE: ${self._spent:.2f}/{MAX_BUDGET:.2f}",
                "", "MARKET:", market or "  unavailable",
                "",
                "═══ AKTIF STRATEJILER (sadece bunlari degistir) ═══"
            ]
            for s in (active or []):
                wr = s[7]/s[6]*100 if s[6] > 0 else 0
                ev = s[8]/s[6] if s[6] > 0 else 0
                ai_tag = "[AI]" if "AI_" in (s[1] or "") else "[HUMAN]"
                protected = " ⚠️KORUNMALI" if s[1] in PROTECTED_STRATEGIES else ""
                lines.append(f"  id={s[0][:12]} {s[1]} {ai_tag} [{s[2]}] ${s[3]}@{s[4]} {s[5]} "
                           f"{s[6]}t {wr:.0f}% PnL:{s[8]:+.2f} EV:{ev:+.3f}{protected}")

            if ai_strats:
                lines.append("\n═══ SENIN (AI) STRATEJILERIN — PERFORMANSIN ═══")
                ai_total_pnl = 0
                for s in ai_strats:
                    wr = s[5]/s[4]*100 if s[4] > 0 else 0
                    ai_total_pnl += s[6]
                    lines.append(f"  {s[0]} [{s[1]}] ${s[2]}@{s[3]} {s[4]}t {wr:.0f}% PnL:{s[6]:+.2f}")
                lines.append(f"  TOPLAM AI PnL: {ai_total_pnl:+.2f}")
                if ai_total_pnl < 0:
                    lines.append(f"  ⚠️ AI stratejileri KAYBEDIYOR! Daha temkinli ol.")

            if stopped:
                lines.append("\n═══ DURMUS STRATEJILER (bilgi icin — AKSIYON ALMA!) ═══")
                for s in stopped:
                    lines.append(f"  [STOPPED] {s[0]} [{s[1]}] {s[2]}t PnL:{s[3]:+.2f} — zaten kapali")

            # Phase 59: Mistakes feedback — last 20 lessons for learning
            mistakes = await self.db.conn.execute_fetchall(
                """SELECT mistake_type, lesson_learned, strategy_label, created_at
                FROM trade_mistakes ORDER BY created_at DESC LIMIT 20""")
            if mistakes:
                lines.append("\n═══ GECMIS HATALAR VE DERSLER (OGREN VE TEKRARLAMA!) ═══")
                type_counts = {}
                for m in mistakes:
                    mtype = m[0] or "unknown"
                    type_counts[mtype] = type_counts.get(mtype, 0) + 1
                    lines.append(f"  [{mtype}] {m[1] or '?'} ({m[2] or '?'})")
                top_mistake = max(type_counts, key=type_counts.get)
                lines.append(f"  ⚠️ EN SIK HATA: {top_mistake} ({type_counts[top_mistake]}x)")

            lines.append("\n═══ ZONE BACKTEST (gercek veri) ═══")
            for lbl, z in zones.items():
                lines.append(f"  {lbl}: {z['t']}t {z['wr']:.0f}% PnL:{z['pnl']:+.2f}")

            if history:
                lines.append("\n═══ GECMIS KARARLARIN VE SONUCLARI ═══")
                correct = sum(1 for h in history if h[3] == 1)
                wrong = sum(1 for h in history if h[3] == 0)
                pending = sum(1 for h in history if h[3] is None)
                lines.append(f"  Skor: {correct} dogru, {wrong} yanlis, {pending} bekliyor")
                for h in history[:5]:
                    icon = "✅" if h[3]==1 else ("❌" if h[3]==0 else "⏳")
                    outcome = h[2] or "bekliyor"
                    executed = str(h[1] or "")[:80]
                    lines.append(f"  {str(h[0])[:16]} {icon} {outcome} | {executed}")

            # Phase 75: Project context — so AI knows our history
            lines.append("\n═══ PROJE BAGLAMI ═══")
            lines.append("  Paper trading botu. Gercek veri, simulasyon para.")
            lines.append("  Hedef: her strateji kendi basina kar etsin.")
            lines.append("  Her stratejinin lifecycle fazı var (exploration/evaluation/proven).")
            lines.append("  Exploration: 0-20 trade, gevsek filtreler. Evaluation: 20-50. Proven: 50+.")
            lines.append("  Kaybeden strat = filtreler sikilasir. Kazanan = gevsesir + size artar.")
            lines.append("  Rakipler: diger botlar ve insanlar. Nis stratejiler gerekli.")
            lines.append("  ONEMLI: paper modda zarardan korkmadan dene. Ama neden kaybettigini analiz et.")

            # Phase 75: Per-strategy lifecycle params
            try:
                if hasattr(self.engine, 'lifecycle') and self.engine.lifecycle._cache:
                    lines.append("\n═══ STRATEJI LIFECYCLE DURUMLARI ═══")
                    for sid, p in self.engine.lifecycle._cache.items():
                        label = sid[:8]
                        try:
                            r = await self.db.conn.execute_fetchall(
                                "SELECT label FROM strategies WHERE id=?", (sid,))
                            if r and r[0][0]:
                                label = r[0][0]
                        except (aiosqlite.Error, IndexError, TypeError):
                            # Epic 8 T8.1: narrow — lifecycle label lookup is
                            # cosmetic; fall back to truncated sid on DB or
                            # row-shape errors. Any other error bubbles.
                            pass
                        lines.append(
                            f"  {label} [{p.phase}] comp={p.min_composite:.2f} "
                            f"conv={p.conviction_min:.2f} edge={p.edge_gate_mult:.2f} "
                            f"size={p.trade_amount_mult:.1f}x"
                        )
                        if p.adjustment_reason:
                            lines.append(f"    └ {p.adjustment_reason}")
            except Exception:  # noqa: BLE001
                # Epic 8 T8.1 audit: lifecycle cache may raise AttributeError
                # (missing engine.lifecycle), TypeError (stale _cache dict),
                # or aiosqlite.Error mid-loop. Block is best-effort context —
                # partial LLM prompt is acceptable, cycle must continue.
                pass

            # Phase 75: Recent trade journal — per-strategy why won/lost
            try:
                journal = await self.db.conn.execute_fetchall(
                    """SELECT s.label, e.result, e.pnl, e.execution_price,
                        e.signal_score, e.signal_reason, e.event_slug
                    FROM executions e JOIN strategies s ON s.id=e.strategy_id
                    WHERE e.result IS NOT NULL
                    ORDER BY e.settled_at DESC LIMIT 30""")
                if journal:
                    lines.append("\n═══ SON 30 TRADE ANALIZI ═══")
                    wins = sum(1 for j in journal if (j[2] or 0) > 0)
                    losses = len(journal) - wins
                    lines.append(f"  Win: {wins} | Loss: {losses} | WR: {wins/len(journal)*100:.0f}%")
                    for j in journal[:15]:
                        icon = "✅" if (j[2] or 0) > 0 else "❌"
                        label = j[0] or "?"
                        pnl = j[2] or 0
                        price = j[3] or 0
                        sig = j[4] or 0
                        reason = (j[5] or "")[:60]
                        lines.append(f"  {icon} {label} pnl={pnl:+.2f} @{price:.2f} sig={sig:.2f} {reason}")
            except Exception:  # noqa: BLE001
                # Epic 8 T8.1 audit: SQL + row iteration can raise aiosqlite.Error,
                # TypeError (None arithmetic), IndexError (short rows).
                # Best-effort LLM context — fall through with partial data.
                pass

            # ═══ Phase 79b: STRATEJI DEGISIKLIK GECMISI ═══
            try:
                from core.changelog import get_changelog_for_ai
                changelog_lines = await get_changelog_for_ai(self.db)
                if changelog_lines:
                    lines.extend(changelog_lines)
            except Exception:  # noqa: BLE001
                # Epic 8 T8.1 audit: changelog import/query is optional LLM
                # context. ImportError + aiosqlite.Error + TypeError all
                # possible; skip block on any failure.
                pass

            # ═══ Phase 79b: ZENGINLESTIRILMIS VERI BLOKLARI ═══

            # ── BLOK 1: Anlık Market Durumu (son 5dk odds + spot momentum) ──
            try:
                lines.append("\n═══ ANLIK MARKET DURUMU ═══")
                if hasattr(self, 'engine') and self.engine:
                    scanner = getattr(self.engine, 'scanner', None)
                    ext_feed = getattr(self.engine, 'external_feed', None)
                    if scanner and scanner.active_markets:
                        for key, mkt in list(scanner.active_markets.items())[:8]:
                            slug = mkt.get("slug", "")
                            up = mkt.get("up_odds", 0.5)
                            down = mkt.get("down_odds", 0.5)
                            lines.append(f"  {key}: UP={up:.2f} DOWN={down:.2f} slug={slug[:40]}")
                    if ext_feed and ext_feed.is_available:
                        for asset in ("BTC", "ETH", "SOL", "XRP"):
                            mom = ext_feed.get_spot_momentum(asset, lookback_seconds=60)
                            if mom:
                                lines.append(
                                    f"  {asset} spot 60sn: {mom['change_pct']:+.4f}% "
                                    f"yon={mom['direction']} guc={mom['strength']:.2f}")
                            else:
                                price = ext_feed.get_price(asset)
                                if price:
                                    lines.append(f"  {asset} spot: ${price:,.0f} (momentum verisi yok)")
            except Exception:  # noqa: BLE001
                # Epic 8 T8.1 audit: scanner/ext_feed shape is fluid across
                # engine versions — AttributeError, KeyError, TypeError all
                # possible. Block is LLM context only; skip on failure.
                pass

            # ── BLOK 2: Bot Konfigurasyonu (aktif sinyaller + agirliklar) ──
            try:
                lines.append("\n═══ BOT KONFIGURASYONU ═══")
                lines.append(f"  MIN_COMPOSITE: {os.getenv('MIN_COMPOSITE', '0.30')}")
                lines.append(f"  SINYAL AGIRLIKLARI: odds={os.getenv('SIGNAL_W_ODDS','0.05')} "
                             f"ema={os.getenv('SIGNAL_W_EMA','0.25')} "
                             f"momentum={os.getenv('SIGNAL_W_MOMENTUM','0.30')} "
                             f"time={os.getenv('SIGNAL_W_TIME','0.10')} "
                             f"orderbook={os.getenv('SIGNAL_W_ORDERBOOK','0.20')}")
                lines.append(f"  KAPALI SINYALLER: whale={os.getenv('WHALE_SIGNAL_ENABLED','false')} "
                             f"bayes={os.getenv('BAYESIAN_UPDATER_ENABLED','false')} "
                             f"technical={os.getenv('TECHNICAL_INDICATORS_ENABLED','false')} "
                             f"calendar={os.getenv('CALENDAR_MULT_ENABLED','false')}")
                lines.append(f"  CONFLUENCE: K={os.getenv('CONFLUENCE_K','3')} "
                             f"penalty={os.getenv('CONFLUENCE_PENALTY','0.3')}")
                lines.append(f"  SMART_EXIT: {os.getenv('SMART_EXIT_ENABLED','true')}")
                lines.append(f"  ALLOWED_ZONES: {os.getenv('ALLOWED_ZONES','(tumu)')}")
                lines.append(f"  ADAPTIVE_MAX_THRESHOLD: {os.getenv('ADAPTIVE_MAX_THRESHOLD','0.85')}")
                lines.append(f"  Regime: {self.engine.regime.regime if hasattr(self.engine, 'regime') else '?'}")
            except Exception:  # noqa: BLE001
                # Epic 8 T8.1 audit: os.getenv + self.engine.regime attr —
                # AttributeError / TypeError possible. Block prints static
                # config; partial output OK for LLM.
                pass

            # ── BLOK 3: Skip Breakdown (neden trade acilmiyor) ──
            try:
                if hasattr(self, 'engine') and hasattr(self.engine, 'skips'):
                    skip_counts = self.engine.skips.get_counts()
                    if skip_counts:
                        lines.append("\n═══ SKIP ANALIZI (neden trade ACILMIYOR) ═══")
                        total = sum(skip_counts.values())
                        lines.append(f"  Son heartbeat'ten bu yana: {total} skip")
                        for reason, count in sorted(skip_counts.items(), key=lambda x: -x[1])[:8]:
                            pct = count / total * 100 if total > 0 else 0
                            lines.append(f"  {reason}: {count}x ({pct:.0f}%)")
                        # Specific advice
                        if skip_counts.get("SIG_WEAK", 0) > total * 0.5:
                            lines.append("  ⚠️ SORUN: Cok fazla SIG_WEAK → sinyal gucunu artir veya threshold dusur")
                        if skip_counts.get("REGIME", 0) > total * 0.3:
                            lines.append("  ⚠️ SORUN: REGIME block → market ranging, momentum/trend stratejileri calismaz")
                        if skip_counts.get("EMA_BLOCK", 0) > total * 0.2:
                            lines.append("  ⚠️ SORUN: EMA_BLOCK → EMA yonu sinyal yonuyle uyusmuyor")
                        if skip_counts.get("ZONE_BLOCKED", 0) > total * 0.2:
                            lines.append("  ⚠️ SORUN: ZONE_BLOCKED → fiyat izin verilen zone'da degil")
            except Exception:  # noqa: BLE001
                # Epic 8 T8.1 audit: engine.skips API varies; AttributeError
                # on missing methods, ZeroDivisionError on empty counts.
                # Block is advisory LLM context; skip on any failure.
                pass

            # ── BLOK 4: Trade Detayları (TP/SL/fee/duration/entry-exit) ──
            try:
                detailed = await self.db.conn.execute_fetchall(
                    """SELECT s.label, e.result, e.pnl, e.execution_price, e.direction,
                        e.trade_amount, e.fee_amount, e.signal_score,
                        e.duration_sec, e.max_favorable_move, e.max_adverse_move,
                        e.event_slug, e.created_at
                    FROM executions e JOIN strategies s ON s.id=e.strategy_id
                    WHERE e.result IS NOT NULL
                    ORDER BY e.closed_at DESC LIMIT 20""")
                if detailed:
                    lines.append("\n═══ SON 20 TRADE DETAYI (entry/exit/fee/sure) ═══")
                    for d in detailed:
                        label = d[0] or "?"
                        result = d[1] or "?"
                        pnl = d[2] or 0
                        entry = d[3] or 0
                        direction = d[4] or "?"
                        amount = d[5] or 0
                        fee = d[6] or 0
                        sig = d[7] or 0
                        dur = d[8]
                        max_fav = d[9]
                        max_adv = d[10]
                        icon = "✅" if pnl > 0 else "❌"
                        dur_str = f"{dur}sn" if dur and dur < 120 else (f"{dur//60}dk" if dur else "?")
                        fav_str = f"max_fav={max_fav:+.4f}" if max_fav else ""
                        adv_str = f"max_adv={max_adv:+.4f}" if max_adv else ""
                        fee_pct = (fee / amount * 100) if amount > 0 else 0
                        lines.append(
                            f"  {icon} {label} {direction.upper()} @{entry:.3f} "
                            f"pnl={pnl:+.3f} fee=${fee:.3f}({fee_pct:.1f}%) "
                            f"sig={sig:.2f} sure={dur_str} {fav_str} {adv_str}")
            except Exception:  # noqa: BLE001
                # Epic 8 T8.1 audit: SQL + tuple unpacking — aiosqlite.Error,
                # TypeError (None formatting), IndexError all possible.
                # Best-effort block for LLM context.
                pass

            # ── BLOK 5: Strateji Bazli Performans Ozeti ──
            try:
                strat_perf = await self.db.conn.execute_fetchall(
                    """SELECT s.label, s.strategy_type, s.status,
                        COUNT(CASE WHEN e.result IS NOT NULL THEN 1 END) as trades,
                        COALESCE(SUM(CASE WHEN e.pnl>0 AND e.result IS NOT NULL THEN 1 ELSE 0 END),0) as wins,
                        COALESCE(SUM(CASE WHEN e.result IS NOT NULL THEN e.pnl ELSE 0 END),0) as total_pnl,
                        COALESCE(SUM(e.fee_amount),0) as total_fees,
                        COALESCE(AVG(e.duration_sec),0) as avg_duration,
                        SUM(CASE WHEN e.result='won' THEN 1 ELSE 0 END) as settlement_wins,
                        SUM(CASE WHEN e.result IN ('tp_exit','smart_exit_edge') THEN 1 ELSE 0 END) as tp_exits,
                        SUM(CASE WHEN e.result IN ('sl_exit','smart_exit_stoploss','force_exit') THEN 1 ELSE 0 END) as sl_exits
                    FROM strategies s LEFT JOIN executions e ON e.strategy_id=s.id
                    GROUP BY s.id HAVING trades > 3
                    ORDER BY total_pnl DESC""")
                if strat_perf:
                    lines.append("\n═══ STRATEJI BAZLI PERFORMANS ═══")
                    for sp in strat_perf:
                        label = sp[0] or "?"
                        stype = sp[1] or "?"
                        status = sp[2] or "?"
                        trades = sp[3] or 0
                        wins = sp[4] or 0
                        pnl = sp[5] or 0
                        fees = sp[6] or 0
                        avg_dur = sp[7] or 0
                        settle_w = sp[8] or 0
                        tp_exits = sp[9] or 0
                        sl_exits = sp[10] or 0
                        wr = (wins / trades * 100) if trades > 0 else 0
                        fee_ratio = (fees / (trades * 1.0) * 100) if trades > 0 else 0
                        status_icon = "🟢" if status == "active" else "🔴"
                        lines.append(
                            f"  {status_icon} {label} [{stype}] {trades}t WR={wr:.0f}% PnL={pnl:+.2f} "
                            f"fees=${fees:.2f}({fee_ratio:.1f}%) "
                            f"settle_win={settle_w} tp={tp_exits} sl={sl_exits} "
                            f"avg_dur={avg_dur:.0f}sn")
            except Exception:  # noqa: BLE001
                # Epic 8 T8.1 audit: heavy aggregate SQL — aiosqlite.Error,
                # ZeroDivisionError on empty trades. Best-effort for LLM.
                pass

            # ── BLOK 6: Saatlik/Gunluk Performans Trendi ──
            try:
                hourly = await self.db.conn.execute_fetchall(
                    """SELECT strftime('%H', created_at) as hour,
                        COUNT(*) as trades,
                        SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                        ROUND(SUM(pnl), 2) as pnl
                    FROM executions WHERE result IS NOT NULL
                    GROUP BY hour ORDER BY hour""")
                if hourly:
                    lines.append("\n═══ SAAT BAZLI PERFORMANS (UTC) ═══")
                    best_hour = max(hourly, key=lambda x: x[3] or 0)
                    worst_hour = min(hourly, key=lambda x: x[3] or 0)
                    for h in hourly:
                        wr = (h[2] / h[1] * 100) if h[1] > 0 else 0
                        tag = " ← EN IYI" if h == best_hour else (" ← EN KOTU" if h == worst_hour else "")
                        lines.append(f"  {h[0]}:00 UTC: {h[1]}t WR={wr:.0f}% PnL={h[3]}{tag}")
            except Exception:  # noqa: BLE001
                # Epic 8 T8.1 audit: strftime + aggregate — aiosqlite.Error,
                # ValueError possible. Best-effort LLM context.
                pass

            # ── BLOK 7: Fee Analizi ──
            try:
                fee_stats = await self.db.conn.execute_fetchall(
                    """SELECT
                        COUNT(*) as total,
                        ROUND(SUM(fee_amount), 2) as total_fees,
                        ROUND(AVG(fee_amount), 4) as avg_fee,
                        ROUND(SUM(pnl), 2) as total_pnl,
                        ROUND(SUM(fee_amount) / NULLIF(ABS(SUM(pnl)), 0) * 100, 1) as fee_to_pnl_ratio
                    FROM executions WHERE result IS NOT NULL""")
                if fee_stats and fee_stats[0][0] > 0:
                    f = fee_stats[0]
                    lines.append("\n═══ FEE ANALIZI ═══")
                    lines.append(f"  Toplam fee: ${f[1]} ({f[0]} trade)")
                    lines.append(f"  Ortalama fee/trade: ${f[2]}")
                    lines.append(f"  Fee/PnL orani: {f[4]}% ← {'SORUNLU (>50%)' if (f[4] or 0) > 50 else 'kabul edilebilir'}")
                    if (f[4] or 0) > 100:
                        lines.append("  ⚠️ KRITIK: Fee'ler kazanctan fazla! Daha yuksek edge gereken trade'ler ac.")
            except Exception:  # noqa: BLE001
                # Epic 8 T8.1 audit: NULLIF + division — aiosqlite.Error,
                # ZeroDivisionError, TypeError all possible. Best-effort.
                pass

            # ── BLOK 8: Onemli Uyarilar ──
            try:
                lines.append("\n═══ ONEMLI UYARILAR VE TAVSIYELER ═══")
                # Active strategy count warning
                active_count = len(active) if active else 0
                if active_count < 4:
                    lines.append(f"  🚨 SADECE {active_count} AKTIF STRATEJI! CREATE ile yeni ekle veya RESTART ile eskiyi canlandir.")
                # Check if all strategies are same asset
                if active:
                    assets = set(s[1].split("_")[1] if "_" in (s[1] or "") else "?" for s in active)
                    if len(assets) == 1:
                        lines.append(f"  ⚠️ TUM STRATEJILER AYNI ASSET ({assets.pop()})! Diversifikasyon gerekli.")
                # Check loss streak from risk
                if hasattr(self, 'engine') and hasattr(self.engine, 'risk'):
                    streak = getattr(self.engine.risk, '_loss_streak', 0)
                    if streak >= 3:
                        lines.append(f"  ⚠️ KAYIP SERISI: {streak} ardisik kayip! Dikkatli ol.")
                # Budget warning
                remaining = MAX_BUDGET - self._spent
                if remaining < 3:
                    lines.append(f"  ⚠️ AI BUTCE AZALIYOR: ${remaining:.2f} kaldi (${MAX_BUDGET} toplam)")
            except Exception:  # noqa: BLE001
                # Epic 8 T8.1 audit: engine.risk + set comprehension —
                # AttributeError, KeyError, TypeError possible. Best-effort.
                pass

            # ── BLOK 9: Son HyperOpt Sonuclari ──
            try:
                hopt_rows = await self.db.conn.execute_fetchall(
                    """SELECT strategy_name, best_params, best_score, metric,
                            train_score, test_score, overfit_ratio, is_overfit,
                            applied, source, id, created_at
                    FROM hyperopt_results
                    WHERE created_at > datetime('now', '-7 days')
                    ORDER BY created_at DESC LIMIT 10""")
                if hopt_rows:
                    lines.append("\n═══ SON HYPEROPT SONUCLARI (7 gun) ═══")
                    for h in hopt_rows:
                        name, params, score, metric = h[0], h[1], h[2], h[3]
                        train, test, ofit_r, is_ofit = h[4], h[5], h[6], h[7]
                        applied_st, src, rid = h[8], h[9], h[10]
                        ofit_tag = "OVERFIT" if is_ofit else "OK"
                        app_tag = {0: "BEKLIYOR", 1: "UYGULANDI", 2: "REDDEDILDI"}.get(applied_st, "?")
                        lines.append(f"  [{rid}] {name} | {metric}={score:.3f} "
                                     f"train={train:.3f} test={test:.3f} ratio={ofit_r:.2f} "
                                     f"{ofit_tag} | {app_tag} | src={src}")
                        if applied_st == 0 and not is_ofit:
                            lines.append(f"      ^ UYGULANABILIR — params: {params}")
                    lines.append("  OPTIMIZE action ile yeni strateji optimize edebilirsin.")
                    lines.append("  APPLY_HYPEROPT action ile bekleyen sonucu uygulayabilirsin.")
                else:
                    lines.append("\n═══ HYPEROPT: Son 7 gunde sonuc yok. OPTIMIZE action kullan. ═══")
            except Exception:  # noqa: BLE001
                # Epic 8 T8.1 audit: hyperopt_results SQL — aiosqlite.Error,
                # TypeError on None formatting. Best-effort LLM context.
                pass

            return "\n".join(lines)
        except Exception as e:  # noqa: BLE001
            # Epic 8 T8.1 KEEP: outer _gather_data supervisor — 16 nested
            # blocks already guarded; this catches anything that escapes
            # (e.g. global memoryerror, unexpected type from engine state).
            # Returning None triggers safe "No data" path in run_brain_cycle.
            logger.error(f"Gather: {e}", exc_info=True)
            return None

    async def _get_binance(self) -> str:
        try:
            import httpx as _httpx
            lines = []
            async with _httpx.AsyncClient(timeout=5.0) as client:
                for sym, name in [("BTCUSDT","BTC"),("ETHUSDT","ETH"),("SOLUSDT","SOL"),("XRPUSDT","XRP")]:
                    try:
                        r = await client.get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}")
                        if r.status_code == 200:
                            d = r.json()
                            price = float(d.get("lastPrice", 0))
                            change = float(d.get("priceChangePercent", 0))
                            lines.append(f"  {name}: ${price:,.0f} 24h:{change:+.1f}%")
                    except Exception:  # noqa: BLE001
                        # Epic 8 T8.1 audit: per-symbol fetch — HTTPError,
                        # TimeoutError, ValueError (json parse) all possible.
                        # Skip symbol, try next.
                        pass
            return "\n".join(lines) if lines else "  unavailable"
        except Exception:  # noqa: BLE001
            # Epic 8 T8.1 audit: AsyncClient construction / import errors —
            # rare but we degrade gracefully to "error" string for LLM.
            return "  error"

    # ═══ PARSE (Phase 79b: robust JSON recovery) ═══
    def _parse(self, response):
        """Parse LLM response to JSON with multi-stage recovery.

        Stages:
        1. Extract JSON from markdown code blocks or raw text
        2. Brace-balanced extraction
        3. Repair common LLM JSON issues (unterminated strings, trailing commas)
        4. Regex fallback for partial JSON
        """
        import re
        if not response:
            return None

        # Stage 1: Extract from code blocks or find first {
        try:
            clean = response.strip()
            if "```" in clean:
                for p in clean.split("```"):
                    p = p.strip()
                    if p.startswith("json"):
                        p = p[4:].strip()
                    if p.startswith("{"):
                        clean = p
                        break
            if not clean.startswith("{"):
                idx = clean.find("{")
                if idx >= 0:
                    clean = clean[idx:]
                else:
                    logger.warning("Parse: no JSON object found in response")
                    return None

            # Stage 2: Brace-balanced extraction
            depth = 0
            end_idx = len(clean) - 1
            for i, c in enumerate(clean):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                if depth == 0:
                    end_idx = i
                    break
            clean = clean[:end_idx + 1]

            # Stage 2b: If braces never balanced, close them
            if depth > 0:
                logger.info(f"Parse: unbalanced braces (depth={depth}), auto-closing")
                clean += "}" * depth

            # Try direct parse first (fast path)
            try:
                return json.loads(clean)
            except json.JSONDecodeError:
                pass

            # Stage 3: Repair common LLM JSON issues
            repaired = clean

            # 3a: Fix unterminated strings — find last valid position
            # Remove everything after the last complete key-value pair
            # Try progressively shorter versions
            for trim_target in ['"actions"', '"market_view"', '"reasoning"', '"confidence"']:
                idx = repaired.rfind(trim_target)
                if idx > 0:
                    # Find the end of this key's value
                    break

            # 3b: Remove trailing commas before } or ]
            repaired = re.sub(r',\s*([}\]])', r'\1', repaired)

            # 3c: Fix unterminated strings by finding unmatched quotes
            # Strategy: remove the last incomplete key-value pair
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

            # 3d: Aggressive truncation — find last valid closing brace
            # Walk backwards to find a position where JSON is valid
            for i in range(len(repaired) - 1, max(len(repaired) // 2, 50), -1):
                if repaired[i] == '}':
                    candidate = repaired[:i + 1]
                    # Ensure braces are balanced
                    d = 0
                    for c in candidate:
                        if c == '{':
                            d += 1
                        elif c == '}':
                            d -= 1
                    if d == 0:
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            continue
                    elif d > 0:
                        try:
                            return json.loads(candidate + "}" * d)
                        except json.JSONDecodeError:
                            continue

            # Stage 4: Regex fallback — extract key fields individually
            logger.warning("Parse: all JSON repair attempts failed, trying field extraction")
            result = {}
            # Extract actions array
            actions_match = re.search(r'"actions"\s*:\s*\[(.*?)\]', repaired, re.DOTALL)
            if actions_match:
                try:
                    actions_str = "[" + actions_match.group(1) + "]"
                    # Fix trailing commas in array
                    actions_str = re.sub(r',\s*\]', ']', actions_str)
                    result["actions"] = json.loads(actions_str)
                except Exception:  # noqa: BLE001
                    # Epic 8 T8.1 audit: regex-extracted JSON fragment may be
                    # malformed in many ways; empty fallback is intentional.
                    result["actions"] = []

            # Extract scalar fields
            for field in ["market_view", "reasoning", "lessons_learned"]:
                m = re.search(rf'"{field}"\s*:\s*"((?:[^"\\]|\\.){{0,500}})"', repaired)
                if m:
                    result[field] = m.group(1).replace('\\"', '"')

            for field in ["confidence"]:
                m = re.search(rf'"{field}"\s*:\s*([\d.]+)', repaired)
                if m:
                    try:
                        result[field] = float(m.group(1))
                    except ValueError:
                        pass

            if result.get("actions") is not None or result.get("market_view"):
                logger.info(f"Parse: recovered {len(result)} fields via regex extraction")
                return result

            logger.warning(f"Parse: complete failure, response length={len(response)}")
            return None

        except Exception as e:  # noqa: BLE001
            # Epic 8 T8.1 audit: multi-stage LLM JSON recovery pipeline can
            # raise ValueError, TypeError, RecursionError, re.error, or
            # RecursionError from pathological input. Catch-all prevents a
            # single bad cycle from killing AI Brain.
            logger.warning(f"Parse: {e}")
            return None

    # ═══ EXECUTE WITH SAFETY ═══
    async def _execute(self, actions) -> list[str]:
        from core.changelog import log_change
        MAX_STOPS_PER_CYCLE = int(os.getenv("MAX_STOPS_PER_CYCLE", "2"))
        stop_count = 0
        results = []
        for action in actions:
            try:
                atype = action.get("type","").upper()
                sid = action.get("id","")
                reason = action.get("reason","?")

                if atype == "DELETE" or atype == "STOP":
                    # SAFETY: Check if already stopped
                    cur = await self.db.conn.execute_fetchall(
                        "SELECT status, label FROM strategies WHERE id LIKE ?", (f"{sid}%",))
                    if not cur:
                        results.append(f"⚠️ {sid[:8]} bulunamadi")
                        continue
                    if cur[0][0] == "stopped":
                        results.append(f"⏭ {cur[0][1]} zaten stopped")
                        continue
                    # SAFETY: Never stop protected strategies
                    label = cur[0][1] or ""
                    if label in PROTECTED_STRATEGIES:
                        results.append(f"🛡️ {label} KORUMALI — durdurulamaz")
                        continue
                    # SAFETY: Max STOP per cycle limiti (7-strateji-katliami onlemi)
                    if stop_count >= MAX_STOPS_PER_CYCLE:
                        results.append(f"⏸ STOP LIMIT: {label} — cycle limiti ({MAX_STOPS_PER_CYCLE}) doldu, sonraki cycle'a ertelendi")
                        logger.warning(f"🧠 STOP BLOCKED (limit): {label} — {reason}")
                        continue
                    await self.db.conn.execute(
                        "UPDATE strategies SET status='stopped' WHERE id LIKE ?", (f"{sid}%",))
                    await self.db.conn.commit()
                    stop_count += 1
                    results.append(f"🛑 STOP: {label} — {reason}")
                    logger.info(f"🧠 STOP: {label} ({stop_count}/{MAX_STOPS_PER_CYCLE})")
                    await log_change(self.db, sid, "STOP", "ai_brain",
                                     old={"status": "active"}, new={"status": "stopped"},
                                     reason=reason, label=label)

                elif atype == "CREATE":
                    result = await self._create(action)
                    results.append(result)

                elif atype == "SCALE" and sid:
                    cur = await self.db.conn.execute_fetchall(
                        "SELECT trade_amount, label FROM strategies WHERE id LIKE ?", (f"{sid}%",))
                    if not cur: continue
                    old_amt, label = cur[0][0], cur[0][1] or ""
                    is_ai = "AI_" in label
                    new_amt = min(action.get("new_amount",old_amt), MAX_TRADE_AMOUNT)
                    max_scale = MAX_SCALE_AI if is_ai else MAX_SCALE_HUMAN

                    # AI strategies need 20+ trades to scale
                    if is_ai:
                        trades = await self.db.conn.execute_fetchall(
                            "SELECT COUNT(*) FROM executions WHERE strategy_id LIKE ? AND result IS NOT NULL", (f"{sid}%",))
                        if trades and trades[0][0] < 20 and new_amt > 3:
                            results.append(f"⚠️ {label}: 20+ trade gerek (simdi {trades[0][0]}t)")
                            continue

                    if new_amt <= old_amt * max_scale:
                        await self.db.conn.execute(
                            "UPDATE strategies SET trade_amount=? WHERE id LIKE ?", (new_amt, f"{sid}%"))
                        await self.db.conn.commit()
                        results.append(f"📈 SCALE: {label} ${old_amt}→${new_amt:.1f} — {reason}")
                        logger.info(f"🧠 SCALE: {label} ${old_amt}→${new_amt}")
                        await log_change(self.db, sid, "SCALE", "ai_brain",
                                         old={"trade_amount": old_amt}, new={"trade_amount": new_amt},
                                         reason=reason, label=label)
                    else:
                        results.append(f"⚠️ SCALE RED: {label} ${new_amt}>${old_amt}×{max_scale}")

                elif atype == "TUNE" and sid:
                    field = action.get("field","odds_threshold")
                    value = action.get("value")
                    if field != "odds_threshold" or value is None: continue
                    cur = await self.db.conn.execute_fetchall(
                        "SELECT odds_threshold, label FROM strategies WHERE id LIKE ?", (f"{sid}%",))
                    if not cur: continue
                    old_thr, label = cur[0][0] or 0.5, cur[0][1] or ""

                    # SAFETY: Protected strategies
                    if label in PROTECTED_STRATEGIES:
                        results.append(f"🛡️ {label} threshold KORUNMALI")
                        continue
                    is_ai = "AI_" in label
                    max_delta = MAX_THR_DELTA_AI if is_ai else MAX_THR_DELTA_HUMAN
                    if abs(old_thr - value) <= max_delta:
                        await self.db.conn.execute(
                            "UPDATE strategies SET odds_threshold=? WHERE id LIKE ?", (value, f"{sid}%"))
                        await self.db.conn.commit()
                        results.append(f"🎯 TUNE: {label} {old_thr}→{value} — {reason}")
                        logger.info(f"🧠 TUNE: {label} {old_thr}→{value}")
                        await log_change(self.db, sid, "TUNE", "ai_brain",
                                         old={"odds_threshold": old_thr}, new={"odds_threshold": value},
                                         reason=reason, label=label)
                    else:
                        results.append(f"⚠️ TUNE RED: {label} delta {abs(old_thr-value):.2f}>{max_delta}")

                elif atype == "RESTART" and sid:
                    cur = await self.db.conn.execute_fetchall(
                        "SELECT label FROM strategies WHERE id LIKE ?", (f"{sid}%",))
                    label = cur[0][0] if cur else sid[:8]
                    await self.db.conn.execute(
                        "UPDATE strategies SET status='active' WHERE id LIKE ?", (f"{sid}%",))
                    await self.db.conn.commit()
                    results.append(f"🔄 RESTART: {label} — {reason}")
                    logger.info(f"🧠 RESTART: {label}")
                    await log_change(self.db, sid, "RESTART", "ai_brain",
                                     old={"status": "stopped"}, new={"status": "active"},
                                     reason=reason, label=label)

                elif atype == "INSIGHT":
                    results.append(f"💡 {action.get('message','')}")

                elif atype == "OPTIMIZE":
                    strat_name = action.get("strategy_name", "").strip()
                    if not strat_name:
                        results.append("⚠️ OPTIMIZE: strategy_name gerekli")
                        continue
                    # Phase 82b.5 — GHOST STRAT GUARD:
                    # AI Brain bazen `AI_F_SOL_5m_up_0.53` gibi strateji INSTANCE
                    # isimleri öneriyor. HyperOpt sadece base TYPE'ları bilir
                    # (momentum, fusion, contrarian, ...). Aşağıdaki mapping
                    # hem instance → type çevirisi yapar, hem de registry'de
                    # olmayan tamamen uydurma isimleri reddeder.
                    mapped_name = self._map_to_hyperopt_type(strat_name)
                    if not mapped_name:
                        results.append(
                            f"⏭ OPTIMIZE: '{strat_name}' bilinen bir "
                            f"strateji tipi değil, atlandı")
                        logger.warning(
                            f"🧠 OPTIMIZE skipped — unknown strategy "
                            f"'{strat_name}' (not in HyperOpt registry)")
                        continue
                    if mapped_name != strat_name:
                        logger.info(
                            f"🧠 OPTIMIZE: mapped '{strat_name}' → "
                            f"'{mapped_name}' (base type)")
                    # Non-blocking: spawn background task
                    # Phase 82e Sprint 2.1: guarded with safe_create_task
                    safe_create_task(
                        self._run_hyperopt_bg(mapped_name, reason),
                        name=f"ai_hyperopt_{mapped_name}")
                    results.append(
                        f"🔬 OPTIMIZE: {mapped_name} baslatildi (arka plan)")
                    logger.info(f"🧠 OPTIMIZE: {mapped_name} — {reason}")

                elif atype == "APPLY_HYPEROPT":
                    result_id = action.get("result_id")
                    if not result_id:
                        results.append("⚠️ APPLY_HYPEROPT: result_id gerekli")
                        continue
                    msg = await self._apply_hyperopt_result(int(result_id), reason)
                    results.append(msg)

            except Exception as e:  # noqa: BLE001
                # Epic 8 T8.1 audit: per-action fault isolation — LLM-proposed
                # actions have unpredictable shapes (dict.get/cast/DB write all
                # in one branch). Catch-all so one malformed action doesn't
                # abort the whole batch; append error and continue with rest.
                results.append(f"❌ {e}")
        return results

    # ═══ HYPEROPT INTEGRATION (Phase 80) ═══

    def _map_to_hyperopt_type(self, name: str) -> Optional[str]:
        """
        Phase 82b.5 — Strategy instance adını HyperOpt'un bildiği base TYPE'a
        çevir. Bilinmiyor ise None dön (OPTIMIZE skip edilir).

        Örnekler:
          "momentum"                       → "momentum"     (zaten base type)
          "AI_F_SOL_5m_up_0.53"            → "fusion"        (AI Fusion prefix)
          "AI_F_XRP_5m_any_0.52"           → "fusion"
          "ETH Momentum Trend"             → "momentum"      (label → tip)
          "foo_bar_baz"                    → None            (bilinmiyor)
        """
        try:
            from core.strategy_plugins import StrategyRegistry
            known = set(StrategyRegistry().names)
        except (ImportError, AttributeError, TypeError) as _reg_err:
            # Epic 8 T8.1: narrow — registry import/construction failure
            # falls back to hard-coded list below. Any other error bubbles.
            logger.debug(f"_map_to_hyperopt_type registry err: {_reg_err}")
            # Fallback hard-coded list (registry import'u patlarsa):
            known = {
                "hour_edge", "streak_reversal", "late_convergence",
                "taker_flow", "orderbook_imbalance", "fade_rip",
                "cross_coin", "opening_breakout", "funding_rate",
                "calibration_arb", "composite", "momentum", "contrarian",
                "scalper", "sniper", "martingale", "flashcrash", "streak",
                "highthreshold", "penny_contract", "bonding_yield", "fusion",
            }

        if not name:
            return None
        raw = name.strip()
        lower = raw.lower()

        # 1) Zaten base type ise direkt dön
        if raw in known:
            return raw
        if lower in known:
            return lower

        # 2) AI Fusion prefix → "fusion"
        if lower.startswith("ai_f_") or lower.startswith("ai_fusion"):
            if "fusion" in known:
                return "fusion"

        # 3) Label içinden tip yakalama ("ETH Momentum Trend" → "momentum")
        for token in lower.replace("-", " ").replace("_", " ").split():
            if token in known:
                return token

        # 4) Bulunamadı
        return None

    async def _run_hyperopt_bg(self, strategy_name: str, reason: str):
        """Phase 82e: run HyperOpt in a SUBPROCESS, never inline.

        Why
        ---
        Phase 82b moved the Telegram /hyperopt path to a subprocess to protect
        the main event loop. AI Brain's OPTIMIZE action still called
        ``HyperOptPipeline.optimize`` inline via ``asyncio.create_task``. Even
        though that runs as a bg task, it shares the engine's event loop —
        any heavy SQL inside discovery priming (>= 90 s) trips the engine
        stall watchdog. Observed on 2026-04-18: cycles 120 + 121 both frozen
        for 90 s right after AI Brain fired OPTIMIZE.

        Fix: delegate to ``backtest.hyperopt_launcher.launch_hyperopt_subprocess``
        which uses the same worker + IPC + mutex + stderr pump as the
        Telegram path. The worker owns the heavy SQL; the engine's event
        loop sees only short awaits on stdout.readline. Result row is
        written by the worker itself (``--source launcher:ai_brain``) so
        this coroutine does no DB writes.
        """
        try:
            from backtest.hyperopt_launcher import launch_hyperopt_subprocess

            n_trials = int(os.getenv("AI_HYPEROPT_TRIALS", "30"))
            info = await launch_hyperopt_subprocess(
                strategy_name=strategy_name,
                n_trials=n_trials,
                source="ai_brain",
            )
            if info is None:
                logger.warning(
                    f"🧠 OPTIMIZE {strategy_name}: launcher returned None "
                    f"(lock busy, stall, or worker failed)")
                return

            logger.info(
                f"🔬 OPTIMIZE done: {info.name} "
                f"score={info.best_value:.4f} trials={info.trial_count} "
                f"elapsed={info.elapsed_sec:.1f}s")

            # Notify admin via Telegram (best-effort — never block on this)
            if getattr(self, "bot_app", None):
                admin_id = os.getenv("ADMIN_CHAT_ID") or os.getenv("ADMIN_TELEGRAM_ID")
                if admin_id:
                    text = (
                        f"🔬 <b>AI HyperOpt Tamamlandi</b>\n"
                        f"Strateji: {info.name}\n"
                        f"Skor: {info.best_value:.4f}\n"
                        f"Trial: {info.trial_count} · {info.elapsed_sec:.0f}s\n"
                        f"Neden: {reason}\n"
                        f"Sonraki cycle'da APPLY_HYPEROPT ile uygulanabilir."
                    )
                    try:
                        await self.bot_app.bot.send_message(
                            int(admin_id), text, parse_mode="HTML")
                    except Exception as _notify_err:  # noqa: BLE001
                        # Epic 8 T8.1 audit: Telegram network/auth errors —
                        # notification is best-effort; do not propagate.
                        logger.debug(f"HyperOpt notif send failed: {_notify_err}")
        except ImportError as _imp_err:
            logger.warning(f"OPTIMIZE failed: launcher import error: {_imp_err}")
        except Exception as e:  # noqa: BLE001
            # Epic 8 T8.1 audit: subprocess launch + IPC + mutex — many
            # failure modes (OSError, asyncio.TimeoutError, CalledProcessError,
            # FileNotFoundError). bg task, so any leak is silent — log w/ trace.
            logger.error(f"OPTIMIZE bg failed: {e}", exc_info=True)

    async def _apply_hyperopt_result(self, result_id: int, reason: str) -> str:
        """Apply a pending HyperOpt result to its matching strategy.

        Phase 82e Sprint 5 (FINAL): reads asset/timeframe from the
        hyperopt_results row (set by worker --asset/--timeframe). When set,
        UPDATE targets ALL live strategies matching (strategy_type, asset,
        timeframe) — previously rows[0]-only was silently applying Fusion×29
        hyperopts to a single instance.
        """
        try:
            rows = await self.db.conn.execute_fetchall(
                "SELECT strategy_name, best_params, best_score, is_overfit, "
                "       applied, asset, timeframe "
                "FROM hyperopt_results WHERE id=?", (result_id,))
            if not rows:
                return f"⚠️ APPLY_HYPEROPT: result_id={result_id} bulunamadi"

            name, params_json, score, is_ofit, applied, r_asset, r_tf = rows[0]
            if applied == 1:
                return f"⏭ APPLY_HYPEROPT: #{result_id} zaten uygulandi"
            if is_ofit:
                return f"⚠️ APPLY_HYPEROPT: #{result_id} OVERFIT — uygulama riskli"

            import json as _json
            best_params = _json.loads(params_json) if isinstance(params_json, str) else params_json

            r_asset = (r_asset or "").strip().upper()
            r_tf = (r_tf or "").strip()

            # Phase 82e Sprint 5 (FINAL): granular match when the hyperopt
            # row has (asset, tf) tags — otherwise fall back to legacy LIKE.
            if r_asset and r_tf:
                strat_rows = await self.db.conn.execute_fetchall(
                    "SELECT id, label FROM strategies "
                    "WHERE strategy_type = ? AND asset = ? AND timeframe = ? "
                    "AND status='active'",
                    (name, r_asset, r_tf))
                match_scope = f"type={name} asset={r_asset} tf={r_tf}"
            else:
                strat_rows = await self.db.conn.execute_fetchall(
                    "SELECT id, label FROM strategies WHERE "
                    "(strategy_type LIKE ? OR label LIKE ?) AND status='active'",
                    (f"%{name}%", f"%{name}%"))
                match_scope = f"type/label~={name}"

            if not strat_rows:
                return (f"⚠️ APPLY_HYPEROPT: '{name}' icin aktif strateji "
                        f"bulunamadi (scope={match_scope})")

            # Apply allowed params to EVERY matching strategy
            _allowed = {"odds_threshold", "trade_amount", "stop_loss_percent",
                        "stop_loss_odds", "take_profit_percent", "take_profit_odds"}
            applied_params: dict = {}
            for param, value in best_params.items():
                # Common params from Optuna use _ prefix
                clean = param.lstrip("_")
                if clean in _allowed:
                    applied_params[clean] = value

            if applied_params:
                for sid_row in strat_rows:
                    sid_i = sid_row[0]
                    for k, v in applied_params.items():
                        await self.db.conn.execute(
                            f"UPDATE strategies SET {k}=? WHERE id=?", (v, sid_i))

                # Mark result as applied (single hyperopt row)
                await self.db.conn.execute(
                    "UPDATE hyperopt_results SET applied=1 WHERE id=?", (result_id,))
                await self.db.conn.commit()

                # Changelog: one entry per updated strategy
                from core.changelog import log_change
                for sid_row in strat_rows:
                    await log_change(
                        self.db, sid_row[0], "HYPEROPT_APPLY", "ai_brain",
                        old=None, new=applied_params,
                        reason=f"HyperOpt #{result_id} score={score:.4f} "
                               f"scope={match_scope} matched={len(strat_rows)} — {reason}",
                        label=sid_row[1])

                labels_str = ", ".join(s[1] for s in strat_rows[:3])
                if len(strat_rows) > 3:
                    labels_str += f" ... (+{len(strat_rows)-3})"
                logger.info(
                    f"🧠 APPLY_HYPEROPT: #{result_id} → {len(strat_rows)} strategy(ies) "
                    f"[{match_scope}] {applied_params}")
                return (f"✅ APPLY_HYPEROPT: #{result_id} → {len(strat_rows)} strategy "
                        f"({labels_str}) — "
                        f"{', '.join(f'{k}={v}' for k,v in applied_params.items())}")
            else:
                return f"⚠️ APPLY_HYPEROPT: #{result_id} uygulanacak param yok (izin: {_allowed})"

        except Exception as e:  # noqa: BLE001
            # Epic 8 T8.1 audit: multi-stage DB reads + UPDATE + changelog —
            # aiosqlite.Error, ValueError (json.loads), KeyError, TypeError
            # all possible. Returns user-visible error string to Telegram;
            # AI cycle must continue even if one APPLY fails.
            return f"❌ APPLY_HYPEROPT: {e}"

    async def _create(self, action) -> str:
        try:
            user = await self.db.conn.execute_fetchall("SELECT id FROM users LIMIT 1")
            wallet = await self.db.conn.execute_fetchall("SELECT id FROM wallets LIMIT 1")
            if not user or not wallet: return "❌ No user"

            stype = action.get("strategy_type","fusion")
            asset = action.get("asset","BTC").upper()
            direction = action.get("direction","any").lower()
            amount = 1.0  # AI strategies ALWAYS start at $1
            threshold = action.get("odds_threshold",0.50)
            tp = action.get("take_profit_odds")
            sl = action.get("stop_loss_odds")
            reason = action.get("reason","AI created")

            type_short = {"fusion":"F","momentum":"M","contrarian":"C","scalper":"S",
                         "sniper":"N","highthreshold":"HT"}.get(stype,"?")
            label = f"AI_{type_short}_{asset}_5m_{direction}_{threshold}"

            # Check duplicate
            existing = await self.db.conn.execute_fetchall(
                "SELECT id FROM strategies WHERE label=?", (label,))
            if existing:
                return f"⏭ {label} zaten var"

            # T1.3 Commit 3 (2026-04-20): Phase 33 walk-forward validation
            # bloğu silindi — core.wf_validator ghost modül (archive'da, Phase
            # 79b yorumuyla "archived — make optional"). Artık create öncesi WF
            # doğrulama yok. wf label'ı (log+reason+return) da kaldırıldı.

            sid = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            await self.db.conn.execute(
                """INSERT INTO strategies (id,user_id,wallet_id,label,asset,timeframe,
                    direction,trade_amount,odds_threshold,strategy_type,status,
                    take_profit_odds,stop_loss_odds,minutes_before_end,max_executions_per_event,
                    created_at,updated_at)
                VALUES (?,?,?,?,?,'5m',?,?,?,?,'active',?,?,0.5,1,?,?)""",
                (sid,user[0][0],wallet[0][0],label,asset,direction,amount,threshold,stype,
                 tp,sl,now,now))
            await self.db.conn.commit()
            logger.info(f"🧠 CREATE: {label} [{stype}] ${amount}@{threshold}")
            from core.changelog import log_change
            await log_change(self.db, sid, "CREATE", "ai_brain",
                             new={"strategy_type": stype, "asset": asset, "direction": direction,
                                  "odds_threshold": threshold, "trade_amount": amount},
                             reason=reason, label=label)
            return f"🆕 CREATE: {label} ${amount}@{threshold} — {reason}"
        except (aiosqlite.Error, ValueError, KeyError, TypeError, AttributeError) as e:
            # Epic 8 T8.1: narrow — DB insert + dict.get + UUID/strftime.
            # Unexpected error types (e.g. ImportError, OSError) bubble so we
            # see them in tests instead of silent "❌ CREATE: ..." swallowing.
            return f"❌ CREATE: {e}"

    # ═══ MEMORY + LEARNING ═══
    async def _measure_outcomes(self):
        """Measure 24h-old decisions and mark correct/wrong.
        Phase 66: Also record Brier Scores for settled trades."""
        try:
            # Use timezone-naive ISO for SQLite string comparison
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
            pending = await self.db.conn.execute_fetchall(
                "SELECT id, ts FROM ai_decisions WHERE was_correct IS NULL AND ts < ?", (cutoff,))
            for row in (pending or []):
                # PnL since this decision
                pnl_after = await self.db.conn.execute_fetchall(
                    "SELECT COALESCE(SUM(pnl),0) FROM executions WHERE result IS NOT NULL AND created_at>=?",
                    (row[1],))
                pnl = pnl_after[0][0] if pnl_after else 0
                correct = 1 if pnl > 0 else 0
                await self.db.conn.execute(
                    "UPDATE ai_decisions SET outcome_24h=?, was_correct=? WHERE id=?",
                    (f"PnL:{pnl:+.2f}", correct, row[0]))
            await self.db.conn.commit()
            if pending:
                logger.info(f"🧠 Measured {len(pending)} past decisions")
        except (aiosqlite.Error, ValueError, TypeError, IndexError) as e:
            # Epic 8 T8.1: narrow — measurement is best-effort; any DB or
            # row-shape error just skips this cycle's measurement. Brier
            # scoring below is independent and still runs.
            logger.debug(f"Measure outcomes: {e}")

        # Phase 66: Record Brier Scores for recently settled trades
        await self._record_brier_scores()

    async def _record_brier_scores(self):
        """Phase 66: Record Brier Scores for settled trades.
        prediction = execution_price (market-implied prob at entry)
        outcome = 1 if trade was correct (pnl > 0), 0 otherwise.
        Source: A2 (Superforecasting) + A7 (Game Theory)."""
        if not self._brier_tracker:
            return
        try:
            # Find recently settled trades not yet scored
            # Use a marker: we'll check for trades settled in last 25 hours
            # that haven't been Brier-scored yet (tracked via context_json)
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
            rows = await self.db.conn.execute_fetchall(
                """SELECT e.id, e.execution_price, e.pnl, e.direction,
                          s.label, s.strategy_type
                   FROM executions e
                   JOIN strategies s ON s.id = e.strategy_id
                   WHERE e.result IS NOT NULL
                     AND e.closed_at >= ?
                     AND e.execution_price IS NOT NULL
                   ORDER BY e.closed_at DESC LIMIT 50""",
                (cutoff,))

            if not rows:
                return

            # Check which trade IDs are already scored
            trade_ids = [str(r[0]) for r in rows]
            existing = set()
            try:
                placeholders = ",".join("?" * len(trade_ids))
                scored = await self.db.conn.execute_fetchall(
                    f"SELECT context_json FROM brier_scores "
                    f"WHERE context_json IN ({placeholders})",
                    [json.dumps({"trade_id": tid}) for tid in trade_ids])
                # This won't work perfectly but is a safety check
            except aiosqlite.Error:
                # Epic 8 T8.1: narrow — "no such table" until BrierTracker
                # initialises. Swallow only sqlite errors; other problems
                # should surface.
                pass  # table may not exist yet, ensure_table will create it

            scored_count = 0
            for row in rows:
                eid, exec_price, pnl, direction, label, stype = row
                if exec_price is None or pnl is None:
                    continue

                # prediction = confidence in trade direction
                # If direction is "up" and price is 0.65, we predicted 65% chance
                prediction = float(exec_price)
                # outcome = 1 if trade was profitable (we were right)
                outcome = 1 if float(pnl) > 0 else 0

                await self._brier_tracker.record(
                    prediction=prediction,
                    outcome=outcome,
                    source="signal_fusion",
                    context={"trade_id": eid, "label": label, "stype": stype,
                             "direction": direction}
                )
                scored_count += 1

            if scored_count > 0:
                logger.info(f"📊 Brier: scored {scored_count} trades")
        except (aiosqlite.Error, ValueError, TypeError, AttributeError) as e:
            # Epic 8 T8.1: narrow — DB fetch + float/int casting + optional
            # BrierTracker attr. Score recording is best-effort; any other
            # error bubbles so test failures remain visible.
            logger.debug(f"Brier scoring: {e}")

    async def _analyze_losses(self):
        """Phase 59: Auto-analyze recent losing trades, classify mistakes."""
        try:
            # Find recent losing trades NOT yet analyzed
            losses = await self.db.conn.execute_fetchall(
                """SELECT e.id, e.pnl, e.execution_price, e.direction, e.strategy_id,
                    s.label, s.strategy_type, s.odds_threshold
                FROM executions e
                JOIN strategies s ON s.id = e.strategy_id
                WHERE e.result IS NOT NULL AND e.pnl < 0
                AND e.id NOT IN (SELECT trade_id FROM trade_mistakes WHERE trade_id IS NOT NULL)
                ORDER BY e.created_at DESC LIMIT 5""")
            if not losses:
                return

            for loss in losses:
                eid, pnl, price, direction, sid, label, stype, threshold = loss
                context = (f"Trade #{eid}: {label} [{stype}] {direction} @{price:.3f} "
                           f"thr={threshold} pnl={pnl:+.2f}")

                # Use Groq for routine analysis (FREE)
                provider, model = ModelRouter.get("mistake_analysis")
                if provider == "groq":
                    resp = await self._call_groq(MISTAKE_SYSTEM, context)
                else:
                    resp = await self._call_claude(MISTAKE_SYSTEM, context, model)

                if not resp:
                    # Fallback: rule-based classification
                    if price and 0.48 <= price <= 0.65:
                        mtype, lesson = "fee_trap", f"50-65c zone trade, fee %3.6 yedi"
                    elif pnl and pnl < -0.5:
                        mtype, lesson = "oversize", f"Buyuk kayip: {pnl:+.2f}"
                    else:
                        mtype, lesson = "wrong_direction", f"Yon hatasi: {direction} @{price:.3f}"
                    applied = ""
                else:
                    parsed = self._parse(resp)
                    if parsed:
                        mtype = parsed.get("mistake_type", "unknown")
                        lesson = parsed.get("lesson_learned", "")
                        applied = parsed.get("applied_fix", "")
                    else:
                        mtype, lesson, applied = "unknown", resp[:200], ""

                await self.db.conn.execute(
                    """INSERT INTO trade_mistakes
                    (trade_id, strategy_label, mistake_type, market_context, lesson_learned, applied_fix)
                    VALUES (?,?,?,?,?,?)""",
                    (eid, label, mtype, context, lesson, applied))
            await self.db.conn.commit()
            if losses:
                logger.info(f"🧠 Analyzed {len(losses)} losing trades for mistakes journal")
        except (aiosqlite.Error, ValueError, TypeError, KeyError, AttributeError) as e:
            # Epic 8 T8.1: narrow — DB reads + LLM parse + UPDATE/INSERT.
            # ModelRouter/LLM path raises httpx errors (already-guarded in
            # _call_claude/_call_groq); only shape + DB errors land here.
            logger.debug(f"Analyze losses: {e}")

    async def _save_decision(self, input_summary, actions, results):
        try:
            now = datetime.now(timezone.utc).isoformat()
            await self.db.conn.execute(
                "INSERT INTO ai_decisions (ts,input_hash,actions_proposed,actions_executed,cost,provider) VALUES (?,?,?,?,?,?)",
                (now, hashlib.md5(input_summary.encode()).hexdigest()[:12],
                 json.dumps(actions,default=str), json.dumps(results), 0.015, "claude-sonnet"))
            await self.db.conn.commit()
            self._spent += 0.015
            await self._save_budget()
        except (aiosqlite.Error, ValueError, TypeError, AttributeError) as _sd_err:
            # Epic 8 T8.1 ALARM fix: silent pass → logger.debug. Decision
            # logging is audit trail — if DB write fails, _spent still bumps
            # and _save_budget is attempted, but we log the lost audit entry.
            logger.debug(f"_save_decision failed (decision audit lost): {_sd_err}")

    # ═══ BUDGET ═══
    async def _load_budget(self):
        try:
            r = await self.db.conn.execute_fetchall("SELECT value FROM bot_settings WHERE key='ai_brain.spent'")
            if r: self._spent = float(r[0][0])
        except (aiosqlite.Error, ValueError, TypeError, IndexError) as _lb_err:
            # Epic 8 T8.1 ALARM fix: silent pass → logger.debug. If budget
            # load fails, _spent stays at __init__ default (0.0) which risks
            # bypassing MAX_BUDGET cap after bot restart — must be visible.
            logger.warning(f"_load_budget failed, _spent reset to 0.0: {_lb_err}")

    async def _save_budget(self):
        try:
            await self.db.conn.execute(
                "INSERT OR REPLACE INTO bot_settings (key,value,updated_at) VALUES ('ai_brain.spent',?,?)",
                (str(self._spent), datetime.now(timezone.utc).isoformat()))
            await self.db.conn.commit()
        except (aiosqlite.Error, ValueError, TypeError) as _sb_err:
            # Epic 8 T8.1 ALARM fix: silent pass → logger.debug. Budget
            # persistence failure means next restart may lose accumulated
            # _spent — visible log so we notice drift.
            logger.warning(f"_save_budget failed, _spent not persisted: {_sb_err}")

    # ═══ LLM ═══
    async def _call_claude(self, system, user, model="claude-sonnet-4-6"):
        if not ANTHROPIC_API_KEY: return None
        payload = json.dumps({
            "model": model, "max_tokens": 2000,
            "system": [{"type":"text","text":system,"cache_control":{"type":"ephemeral"}}],
            "messages": [{"role":"user","content":user}]
        })
        try:
            loop = asyncio.get_event_loop()
            r = await loop.run_in_executor(None, self._do_claude, payload)
            if r:
                cost = {"sonnet":0.015,"haiku":0.004,"opus":0.06}.get(
                    model.split("-")[1] if "-" in model else "sonnet", 0.015)
                self._spent += cost
                await self._save_budget()
                logger.info(f"🧠 Claude OK (${self._spent:.3f}/{MAX_BUDGET})")
            return r
        except Exception:  # noqa: BLE001
            # Epic 8 T8.1 audit: run_in_executor surfaces arbitrary worker
            # exceptions (httpx errors, cancellation, any downstream bug in
            # _do_claude). Brain cycle tolerates None and falls back to Groq.
            # TODO T8.2: detect 429 / budget overflow explicitly.
            return None

    def _do_claude(self, payload):
        import httpx as _httpx
        try:
            r = _httpx.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY,
                         "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                content=payload, timeout=60.0)
            d = r.json()
            if "content" in d and d["content"]: return d["content"][0].get("text","")
            if "error" in d: logger.warning(f"Claude: {d['error'].get('message','')[:100]}")
            return None
        except Exception:  # noqa: BLE001
            # Epic 8 T8.1 audit: sync HTTP call — httpx.HTTPError, TimeoutError,
            # ConnectionError, JSONDecodeError. Caller treats None as soft
            # failure. T8.2 will replace this with explicit 429 handling.
            return None

    async def _call_groq(self, system, user):
        if not GROK_API_KEY: return None
        payload = json.dumps({"model":"llama-3.3-70b-versatile",
            "messages":[{"role":"system","content":system},{"role":"user","content":user}],
            "max_tokens":1500,"temperature":0.3})
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._do_groq, payload)
        except Exception:  # noqa: BLE001
            # Epic 8 T8.1 audit: executor exceptions — any worker failure
            # degrades to None, caller (brain cycle / two-agent) chooses
            # next provider. T8.2: add 429 rate-limit detection.
            return None

    def _do_groq(self, payload):
        import httpx as _httpx
        try:
            r = _httpx.post("https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROK_API_KEY}",
                         "Content-Type": "application/json"},
                content=payload, timeout=30.0)
            d = r.json()
            ch = d.get("choices",[])
            return ch[0].get("message",{}).get("content","") if ch else None
        except Exception:  # noqa: BLE001
            # Epic 8 T8.1 audit: sync HTTP — httpx.HTTPError, TimeoutError,
            # JSONDecodeError. None is soft failure. T8.2: 429 handling.
            return None

    # ═══ Phase 69: OpenRouter SDK ═══
    async def _call_openrouter(self, system: str, user: str,
                               model: str = "meta-llama/llama-3.3-70b-instruct:free"):
        """Call OpenRouter API — supports 100+ models, free tier available."""
        if not OPENROUTER_API_KEY:
            return None
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 1500,
            "temperature": 0.3,
        })
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._do_openrouter, payload)
        except Exception:  # noqa: BLE001
            # Epic 8 T8.1 audit: executor exceptions — tertiary provider,
            # degrade silently. T8.2: add 429 handling + backoff.
            return None

    def _do_openrouter(self, payload):
        import httpx as _httpx
        try:
            r = _httpx.post("https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://polypaper-bot.local",
                    "X-Title": "PolyPaper Bot",
                },
                content=payload, timeout=45.0)
            d = r.json()
            ch = d.get("choices", [])
            return ch[0].get("message", {}).get("content", "") if ch else None
        except Exception:  # noqa: BLE001
            # Epic 8 T8.1 audit: sync HTTP — OpenRouter free tier is flaky.
            # httpx.HTTPError / TimeoutError / JSONDecodeError all normal.
            # T8.2 will add explicit rate-limit detection.
            return None

    # ═══ PER-TRADE ═══
    async def analyze_trade(self, trade_data: dict):
        prompt = (f"{trade_data.get('label','?')} {trade_data.get('direction','?')} "
                  f"@{trade_data.get('price',0):.3f} pnl={trade_data.get('pnl',0):+.2f}")
        # Phase 59: Route to Groq (FREE) for per-trade analysis
        provider, model = ModelRouter.get("trade_analysis")
        if provider == "groq":
            response = await self._call_groq(TRADE_SYSTEM, prompt)
        else:
            response = await self._call_claude(TRADE_SYSTEM, prompt, model)
        if response:
            await self._send(response)

    async def manual_analyze(self, mode="daily"):
        if mode == "brain":
            return await self.run_brain_cycle()
        data = await self._gather_data()
        if not data:
            return None
        response = await self._call_claude(BRAIN_SYSTEM, data) or await self._call_groq(BRAIN_SYSTEM, data)
        return response

    async def manual_analyze_parsed(self, mode="daily"):
        """Like manual_analyze but returns (display_text, parsed_dict) tuple.

        parsed_dict contains actions that can be executed via execute_pending_actions().
        """
        if mode == "brain":
            result = await self.run_brain_cycle()
            return result, None  # brain cycle auto-executes
        data = await self._gather_data()
        if not data:
            return None, None
        response = await self._call_claude(BRAIN_SYSTEM, data) or await self._call_groq(BRAIN_SYSTEM, data)
        if not response:
            return None, None
        parsed = self._parse(response)
        return response, parsed

    # ═══ Phase 79b: Execute pending analyze actions ═══
    _pending_analyze: dict = {}  # {msg_id_str: {"actions": [...], "parsed": {...}, "data": str}}

    async def execute_analyze_actions(self, msg_id: str) -> str:
        """Execute actions from a /analyze result that was queued for approval."""
        pending = self.__class__._pending_analyze.pop(msg_id, None)
        if not pending:
            return "⚠️ Bu analiz sonucu bulunamadi veya zaten uygulanmis."
        actions = pending.get("actions", [])
        if not actions:
            return "⚠️ Uygulanacak aksiyon yok."
        results = await self._execute(actions)
        data_summary = pending.get("data", "")[:500]
        await self._save_decision(data_summary, actions, results)
        text = "✅ <b>Analiz Aksiyonlari Uygulandi</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
        for r in results:
            text += f"{r}\n"
        return text

    # ═══ Phase 79 S3-08: Backtest feedback analysis ═══
    async def analyze_strategy_backtest(self, strategy_label: str, results: dict) -> str:
        """Analyze backtest results and generate recommendations.

        Args:
            strategy_label: Human-readable strategy name
            results: Dict with keys: wr, pnl, trades, ev_per_trade, zones (dict zone->wr)

        Returns:
            Analysis text (HTML-safe) or empty string on failure.
        """
        wr = results.get("wr", 0)
        pnl = results.get("pnl", 0)
        trades = results.get("trades", 0)
        ev = results.get("ev_per_trade", 0)
        zones = results.get("zones", {})

        zone_lines = "\n".join(
            f"  {z}: {d.get('trades',0)}t {d.get('wr',0):.0f}% WR PnL={d.get('pnl',0):+.2f}"
            for z, d in zones.items()
        ) or "  (zone verisi yok)"

        prompt = (
            f"Strateji: {strategy_label}\n"
            f"Backtest: {trades} trade, {wr:.1f}% WR, PnL={pnl:+.2f}, EV/trade={ev:+.4f}\n"
            f"Zone dagilimi:\n{zone_lines}\n\n"
            "Kisa analiz yap (maks 4 cuemle Turkce):\n"
            "1. Bu strateji karli mi?\n"
            "2. Hangi zone'larda iyi/kotu?\n"
            "3. Ne degistirilmeli?\n"
            "4. Paper trade'e baslansin mi?"
        )

        try:
            provider, model = ModelRouter.get("trade_analysis")
            if provider == "groq":
                response = await self._call_groq("Kisa ve net analiz yap.", prompt)
            else:
                response = await self._call_claude("Kisa ve net analiz yap.", prompt, model)
            return response or ""
        except Exception as e:  # noqa: BLE001
            # Epic 8 T8.1 audit: ModelRouter shape + LLM call — returns "" on
            # any failure so caller can skip AI feedback rather than crash.
            logger.warning(f"AI backtest analysis failed: {e}")
            return ""

    # ═══ NOTIFY ═══
    async def _notify(self, actions, results, parsed):
        remaining = MAX_BUDGET - self._spent
        view = parsed.get("market_view","?")
        reasoning = parsed.get("reasoning","?")
        conf = parsed.get("confidence",0)
        lessons = parsed.get("lessons_learned","")

        text = (f"🧠 <b>AI Brain #{self._cycle_count}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 Market: <b>{view}</b> | Guven: {conf:.0%}\n"
                f"💭 {reasoning}\n\n")
        for r in results:
            text += f"{r}\n"
        if lessons:
            text += f"\n📚 Ders: {lessons}\n"
        text += f"\n💰 ${self._spent:.2f}/{MAX_BUDGET:.2f} (${remaining:.2f})"
        await self._send(text)

    async def _send(self, text):
        admin_id = getattr(self.settings,'ADMIN_TELEGRAM_ID',None) if self.settings else None
        if not admin_id or not self.bot_app: return
        # Sanitize: escape < > that aren't valid HTML tags
        import re
        safe = re.sub(r'<(?!/?(b|i|code|pre|a)\b)[^>]*>', lambda m: m.group().replace('<','&lt;').replace('>','&gt;'), text)
        try:
            for i in range(0,len(safe),4000):
                try:
                    await self.bot_app.bot.send_message(chat_id=admin_id,text=safe[i:i+4000],parse_mode="HTML")
                except Exception as _html_err:  # noqa: BLE001
                    # Epic 8 T8.1 ALARM fix: HTML parse failure fallback to
                    # plaintext; log first-chunk error so we can fix entity
                    # escaping instead of silently losing HTML formatting.
                    logger.debug(f"_send HTML fallback: {_html_err}")
                    await self.bot_app.bot.send_message(chat_id=admin_id,text=text[i:i+4000])
        except Exception as _send_err:  # noqa: BLE001
            # Epic 8 T8.1 audit: outer Telegram send — BadRequest / NetworkError
            # / chat blocked. Notification is best-effort; AI cycle continues.
            logger.debug(f"_send outer failed: {_send_err}")

    def get_status(self):
        return {"active":self._running,"spent":self._spent,"budget":MAX_BUDGET,
                "remaining":MAX_BUDGET-self._spent,"cycle":self._cycle_count,
                "last_run":self._last_run,"providers":["claude-sonnet","groq"]}

    # ═══ Sprint 3 S3-04: APPROVAL QUEUE ═══
    _pending_approval: dict = {}  # {msg_id: {"actions": [...], "parsed": {...}, "data": str}}

    async def _queue_for_approval(self, actions, parsed, data_summary):
        """Send low-confidence AI actions to Telegram with Approve/Reject buttons."""
        try:
            from telegram import InlineKeyboardMarkup, InlineKeyboardButton
            confidence = parsed.get("confidence", 0)
            view = parsed.get("market_view", "?")
            reasoning = parsed.get("reasoning", "?")

            text = (f"⚠️ <b>AI Brain — Onay Bekliyor</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Guven: <b>{confidence:.0%}</b> (esik: {os.getenv('AI_AUTO_CONFIDENCE','0.70')})\n"
                    f"Gorunum: {view}\n"
                    f"Mantik: {reasoning[:200]}\n\n"
                    f"<b>Onerilen aksiyonlar:</b>\n")
            for a in actions:
                atype = a.get("type", "?")
                sid = a.get("id", "?")[:8]
                reason = a.get("reason", "")[:80]
                text += f"  • {atype} {sid} — {reason}\n"

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Onayla", callback_data="ai_approve"),
                 InlineKeyboardButton("❌ Reddet", callback_data="ai_reject")]
            ])

            admin_id = getattr(self.settings, 'ADMIN_TELEGRAM_ID', None) if self.settings else None
            if admin_id and self.bot_app:
                msg = await self.bot_app.bot.send_message(
                    chat_id=admin_id, text=text, parse_mode="HTML",
                    reply_markup=keyboard)
                # Store pending actions keyed by a simple counter
                self.__class__._pending_approval[str(msg.message_id)] = {
                    "actions": actions, "parsed": parsed, "data": data_summary}
                logger.info(f"🧠 Approval request sent (msg_id={msg.message_id})")
            else:
                # No Telegram → auto-execute as fallback
                results = await self._execute(actions)
                await self._save_decision(data_summary, actions, results)
        except Exception as e:  # noqa: BLE001
            # Epic 8 T8.1 audit: Telegram keyboard + send_message + pending
            # dict mutation — any failure falls back to auto-execute so we
            # never drop low-confidence actions silently. Catch-all preserves
            # this safety net across PTB API changes.
            logger.error(f"Approval queue: {e}", exc_info=True)
            # Fallback: execute anyway
            results = await self._execute(actions)
            await self._save_decision(data_summary, actions, results)

    async def handle_approval(self, approved: bool, msg_id: str) -> str:
        """Called by Telegram callback handler when user approves/rejects."""
        pending = self.__class__._pending_approval.pop(msg_id, None)
        if not pending:
            return "⚠️ Bu istek artik gecerli degil"
        if approved:
            results = await self._execute(pending["actions"])
            await self._save_decision(pending["data"], pending["actions"], results)
            return "✅ AI aksiyonlari uygulandi:\n" + "\n".join(results)
        else:
            await self._save_decision(pending["data"], pending["actions"], ["❌ Admin tarafindan REDDEDILDI"])
            return "❌ AI aksiyonlari reddedildi"

    def stop(self):
        self._running = False
