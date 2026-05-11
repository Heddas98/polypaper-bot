"""
PolyPaper Bot - Decision Explainer (Phase 77)
==============================================
Builds human-readable reasoning chains for every trade decision.
Answers: "Neden bu trade'i açtın? Hangi sinyaller tetikledi? Ne bekliyoruz?"

Every signal evaluation produces a ReasoningChain that gets:
1. Stored with the trade (reasoning_json in executions)
2. Sent to admin in enriched fill notification
3. Queryable via /why command

ENV:
    DECISION_EXPLAINER_ENABLED=true
    DECISION_NOTIFY_DETAIL=medium   # minimal/medium/full
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Optional

import aiosqlite

from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.core.decision_explainer")

EXPLAINER_ENABLED = os.getenv("DECISION_EXPLAINER_ENABLED", "true").lower() == "true"
NOTIFY_DETAIL = os.getenv("DECISION_NOTIFY_DETAIL", "medium")  # minimal/medium/full


@dataclass
class ReasoningStep:
    """One step in the decision chain."""

    module: str  # "strategy_plugin", "confluence", "markov", "memory", etc.
    action: str  # "boost", "penalty", "gate_pass", "gate_block", "sizing"
    value: str  # "+0.05 confidence", "3/6 gates passed", etc.
    impact: str  # "positive", "negative", "neutral"


@dataclass
class ReasoningChain:
    """Complete reasoning chain for a trade decision."""

    strategy_id: str = ""
    slug: str = ""
    direction: str = ""
    final_score: float = 0.0
    trade_amount: float = 0.0
    steps: list[ReasoningStep] = field(default_factory=list)
    summary_tr: str = ""  # Turkish summary for Telegram
    summary_en: str = ""  # English summary for logs
    created_at: str = ""
    decision: str = "pending"  # "trade", "skip", "pending"
    skip_reason: str = ""

    def add_step(self, module: str, action: str, value: str, impact: str = "neutral"):
        self.steps.append(ReasoningStep(module=module, action=action, value=value, impact=impact))

    def to_json(self) -> str:
        return json.dumps(
            {
                "strategy": self.strategy_id,
                "slug": self.slug,
                "direction": self.direction,
                "score": round(self.final_score, 4),
                "amount": round(self.trade_amount, 2),
                "decision": self.decision,
                "skip_reason": self.skip_reason,
                "steps": [
                    {"m": s.module, "a": s.action, "v": s.value, "i": s.impact} for s in self.steps
                ],
                "ts": self.created_at,
            },
            ensure_ascii=False,
        )

    def build_summary(self):
        """Build human-readable summaries from steps."""
        pos = [s for s in self.steps if s.impact == "positive"]
        neg = [s for s in self.steps if s.impact == "negative"]

        # Turkish summary
        parts_tr = []
        if pos:
            pos_str = ", ".join(f"{s.module}({s.value})" for s in pos[:3])
            parts_tr.append(f"✅ Güçlü: {pos_str}")
        if neg:
            neg_str = ", ".join(f"{s.module}({s.value})" for s in neg[:3])
            parts_tr.append(f"⚠️ Zayıf: {neg_str}")

        if self.decision == "trade":
            self.summary_tr = (
                f"📊 Karar: TRADE {self.direction.upper()}\n"
                f"Skor: {self.final_score:+.3f} | ${self.trade_amount:.2f}\n" + "\n".join(parts_tr)
            )
        else:
            self.summary_tr = f"⏭️ Karar: SKIP\n" f"Sebep: {self.skip_reason}\n" + "\n".join(
                parts_tr
            )

        # English summary (for logs)
        step_str = " → ".join(f"{s.module}:{s.action}" for s in self.steps[:5])
        self.summary_en = (
            f"{self.decision}({self.direction}) score={self.final_score:.3f} | {step_str}"
        )

    def format_telegram_short(self) -> str:
        """Short format for fill notification enrichment."""
        if NOTIFY_DETAIL == "minimal":
            return f"Skor: {self.final_score:+.3f}"

        pos = [s for s in self.steps if s.impact == "positive"]
        neg = [s for s in self.steps if s.impact == "negative"]

        parts = []
        if pos:
            parts.append("✅ " + ", ".join(s.module for s in pos[:3]))
        if neg:
            parts.append("⚠️ " + ", ".join(s.module for s in neg[:2]))

        return "\n".join(parts) if parts else f"score={self.final_score:+.3f}"

    def format_telegram_full(self) -> str:
        """Full format for /why command."""
        lines = [
            "🔍 <b>Karar Detayı</b>",
            f"Strateji: <code>{esc(self.strategy_id)}</code>",
            f"Market: <code>{esc(self.slug[:40])}</code>",
            f"Yön: {esc(self.direction.upper())} | Skor: {self.final_score:+.3f}",
            f"Miktar: ${self.trade_amount:.2f} | Karar: {esc(self.decision.upper())}",
            "",
            "<b>Adımlar:</b>",
        ]

        for i, s in enumerate(self.steps, 1):
            icon = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}.get(s.impact, "⚪")
            lines.append(f"  {i}. {icon} <b>{esc(s.module)}</b>: {esc(s.action)} = {esc(s.value)}")

        if self.skip_reason:
            lines.append(f"\n❌ Skip Sebebi: {esc(self.skip_reason)}")

        lines.append(f"\n🕐 {self.created_at[:16]}")
        return "\n".join(lines)


class DecisionExplainer:
    """
    Manages reasoning chains for trade decisions.
    Stores recent decisions for /why queries.
    """

    def __init__(self):
        self._recent: list[ReasoningChain] = []  # last N decisions
        self._max_recent = 100
        self.db = None

    async def initialize(self, db):
        """Ensure reasoning_json column exists in executions."""
        self.db = db
        try:
            # Add column if missing (idempotent)
            await db.conn.execute(
                "ALTER TABLE executions ADD COLUMN reasoning_json TEXT DEFAULT NULL"
            )
            await db.conn.commit()
        except aiosqlite.Error:
            # T1.4 Faz 3: ALTER TABLE ADD COLUMN is idempotent by design —
            # sqlite3.OperationalError ("duplicate column name") is the
            # expected re-run path. aiosqlite.Error covers the whole
            # sqlite3.Error family (operational, integrity, programming).
            pass  # Column already exists
        logger.info("🔍 Phase 77: Decision Explainer initialized")

    def new_chain(self, strategy_id: str, slug: str) -> ReasoningChain:
        """Start a new reasoning chain for a trade evaluation."""
        if not EXPLAINER_ENABLED:
            return ReasoningChain()

        return ReasoningChain(
            strategy_id=strategy_id,
            slug=slug,
            created_at=datetime.now(UTC).isoformat()[:19],
        )

    def finalize(self, chain: ReasoningChain):
        """Finalize chain: build summaries and store in recent."""
        if not EXPLAINER_ENABLED:
            return

        chain.build_summary()
        self._recent.append(chain)
        if len(self._recent) > self._max_recent:
            self._recent = self._recent[-self._max_recent :]

    async def persist(self, chain: ReasoningChain, execution_id: str):
        """Persist reasoning to DB alongside execution."""
        if not EXPLAINER_ENABLED or self.db is None:
            return

        try:
            await self.db.conn.execute(
                "UPDATE executions SET reasoning_json = ? WHERE id = ?",
                (chain.to_json(), execution_id),
            )
            await self.db.conn.commit()
        except (aiosqlite.Error, ValueError, TypeError) as e:
            # T1.4 Faz 3: UPDATE executions + commit + chain.to_json() in
            # one try. chain.to_json() can raise TypeError/ValueError if a
            # step holds non-serializable content (defensive — dataclass
            # enforces str, but shield against future additions).
            logger.debug(f"reasoning persist: {e}")

    def get_recent(self, n: int = 5, trades_only: bool = True) -> list[ReasoningChain]:
        """Get recent reasoning chains."""
        if trades_only:
            chains = [c for c in self._recent if c.decision == "trade"]
        else:
            chains = self._recent
        return chains[-n:]

    def get_by_slug(self, slug: str) -> Optional[ReasoningChain]:
        """Find most recent reasoning for a specific market."""
        for chain in reversed(self._recent):
            if slug in chain.slug:
                return chain
        return None

    async def get_from_db(self, execution_id: str) -> Optional[ReasoningChain]:
        """Load reasoning from DB for a past trade."""
        if self.db is None:
            return None

        try:
            rows = await self.db.conn.execute_fetchall(
                "SELECT reasoning_json FROM executions WHERE id = ? AND reasoning_json IS NOT NULL",
                (execution_id,),
            )
            if rows and rows[0][0]:
                data = json.loads(rows[0][0])
                chain = ReasoningChain(
                    strategy_id=data.get("strategy", ""),
                    slug=data.get("slug", ""),
                    direction=data.get("direction", ""),
                    final_score=data.get("score", 0),
                    trade_amount=data.get("amount", 0),
                    decision=data.get("decision", ""),
                    skip_reason=data.get("skip_reason", ""),
                    created_at=data.get("ts", ""),
                )
                for s in data.get("steps", []):
                    chain.add_step(s["m"], s["a"], s["v"], s.get("i", "neutral"))
                return chain
        except (
            aiosqlite.Error,
            json.JSONDecodeError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
        ) as e:
            # T1.4 Faz 3: execute_fetchall + json.loads(rows[0][0]) +
            # dict.get chain + steps loop with s["m"]/s["a"]/s["v"] in
            # one try. Realistic failure modes:
            #   - aiosqlite.Error: executions/column missing
            #   - json.JSONDecodeError: malformed reasoning_json
            #   - ValueError/TypeError: numeric coerce or dataclass field
            #   - KeyError: steps[i] missing required 'm'/'a'/'v' keys
            #   - IndexError: rows[0] guard race (defensive)
            logger.debug(f"reasoning load: {e}")
        return None

    def format_recent_telegram(self, n: int = 5) -> str:
        """Format recent decisions for Telegram."""
        recent = self.get_recent(n, trades_only=False)
        if not recent:
            return "<i>Henüz karar geçmişi yok.</i>"

        lines = ["🔍 <b>Son Kararlar</b>\n"]
        for c in reversed(recent):
            icon = "✅" if c.decision == "trade" else "⏭️"
            d_str = c.direction.upper() if c.direction else "—"
            lines.append(
                f"{icon} {c.strategy_id[:12]} {d_str} "
                f"score={c.final_score:+.3f} "
                f"{'$' + str(round(c.trade_amount, 2)) if c.decision == 'trade' else c.skip_reason[:20]}"
            )
        return "\n".join(lines)


# ── Singleton ──
_instance: Optional[DecisionExplainer] = None


def get_decision_explainer() -> DecisionExplainer:
    global _instance
    if _instance is None:
        _instance = DecisionExplainer()
    return _instance
