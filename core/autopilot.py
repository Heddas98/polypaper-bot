"""
PolyPaper Bot - AutoPilot v2 (Phase 28)
Semi-autonomous: AI analyzes → proposes actions → user approves via inline button

Relaxed criteria so more strategies get suggestions.
Actions persist to DB to survive restarts.
"""
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("polypaper.core.autopilot")

ACTION_STOP = "ap_stop"
ACTION_SCALE = "ap_scale"
ACTION_TUNE = "ap_tune"


class AutoPilot:
    """Generate and execute approved strategy actions."""

    def __init__(self, db, engine=None):
        self.db = db
        self.engine = engine

    async def generate_actions(self) -> list[dict]:
        """Analyze all strategies and generate proposed actions."""
        actions = []
        try:
            strats = await self.db.conn.execute_fetchall(
                """SELECT s.id, s.label, s.strategy_type, s.trade_amount,
                    s.odds_threshold, s.status,
                    COUNT(CASE WHEN e.result IS NOT NULL THEN 1 END) as t,
                    COALESCE(SUM(CASE WHEN e.pnl>0 AND e.result IS NOT NULL THEN 1 ELSE 0 END),0) as w,
                    COALESCE(SUM(CASE WHEN e.result IS NOT NULL THEN e.pnl ELSE 0 END),0) as pnl,
                    COALESCE(AVG(CASE WHEN e.result IS NOT NULL THEN e.execution_price END),0.5) as avg_p
                FROM strategies s LEFT JOIN executions e ON e.strategy_id=s.id
                GROUP BY s.id""")

            for s in (strats or []):
                sid, label, stype = s[0], s[1] or "?", s[2] or "fusion"
                amount, threshold, status = s[3], s[4], s[5]
                trades, wins, pnl, avg_price = s[6], s[7], s[8], s[9]

                if trades < 5:
                    continue
                wr = wins / trades * 100 if trades > 0 else 0
                ev = pnl / trades if trades > 0 else 0

                # ═══ STOP: losing strategy ═══
                # Active + 8+ trades + WR<50% + negative PnL
                if status == "active" and trades >= 8 and wr < 50 and pnl < -1:
                    actions.append({
                        "type": ACTION_STOP, "sid": sid, "label": label,
                        "stype": stype, "emoji": "🛑",
                        "reason": f"{trades}t {wr:.0f}% WR, PnL:{pnl:+.2f}, EV:{ev:+.3f}",
                        "desc": f"Durdur: {label} ({trades}t {wr:.0f}% {pnl:+.2f})",
                    })

                # ═══ STOP: active but low WR with enough data ═══
                elif status == "active" and trades >= 15 and wr < 45:
                    actions.append({
                        "type": ACTION_STOP, "sid": sid, "label": label,
                        "stype": stype, "emoji": "🛑",
                        "reason": f"{trades}t {wr:.0f}% WR (< 45%), PnL:{pnl:+.2f}",
                        "desc": f"Durdur: {label} ({trades}t {wr:.0f}% kesin edge yok)",
                    })

                # ═══ SCALE: winning strategy ═══
                elif status == "active" and trades >= 12 and wr > 62 and pnl > 1:
                    # Quarter Kelly
                    b = (1.0 / max(avg_price, 0.01)) - 1.0
                    if b > 0:
                        fk = (b * (wr/100) - (1 - wr/100)) / b
                        qk = max(fk * 0.25, 0)
                        kelly_amt = round(100 * qk, 2)
                        kelly_amt = max(1.0, min(kelly_amt, 15.0))
                        if kelly_amt > amount * 1.2:
                            actions.append({
                                "type": ACTION_SCALE, "sid": sid, "label": label,
                                "stype": stype, "emoji": "📈",
                                "old_amount": amount, "new_amount": kelly_amt,
                                "reason": f"{trades}t {wr:.0f}% WR, Kelly=${kelly_amt:.2f}",
                                "desc": f"Scale: {label} ${amount}→${kelly_amt:.2f}",
                            })

                # ═══ RESTART: stopped but now might have edge ═══
                if status == "stopped" and trades >= 10 and wr > 55 and pnl > 0:
                    actions.append({
                        "type": ACTION_TUNE, "sid": sid, "label": label,
                        "stype": stype, "emoji": "🔄", "field": "status",
                        "old_val": "stopped", "new_val": "active",
                        "reason": f"Durmus ama {trades}t {wr:.0f}% WR, PnL:{pnl:+.2f} pozitif",
                        "desc": f"Yeniden baslat: {label} ({wr:.0f}% {pnl:+.2f})",
                    })

                # ═══ TUNE: threshold adjustment ═══
                if status == "active" and trades >= 12:
                    if wr < 55 and threshold and threshold < 0.85:
                        new_thr = min(threshold + 0.05, 0.95)
                        actions.append({
                            "type": ACTION_TUNE, "sid": sid, "label": label,
                            "stype": stype, "emoji": "🎯", "field": "odds_threshold",
                            "old_val": threshold, "new_val": round(new_thr, 2),
                            "reason": f"WR {wr:.0f}% < 55% → threshold yukari",
                            "desc": f"Tune: {label} threshold {threshold}→{new_thr:.2f}",
                        })
                    elif wr > 75 and threshold and threshold > 0.55:
                        new_thr = max(threshold - 0.05, 0.40)
                        actions.append({
                            "type": ACTION_TUNE, "sid": sid, "label": label,
                            "stype": stype, "emoji": "🎯", "field": "odds_threshold",
                            "old_val": threshold, "new_val": round(new_thr, 2),
                            "reason": f"WR {wr:.0f}% > 75% → threshold asagi (daha fazla trade)",
                            "desc": f"Tune: {label} threshold {threshold}→{new_thr:.2f}",
                        })

        except Exception as e:
            logger.error(f"AutoPilot generate: {e}")
        return actions

    async def store_pending(self, action: dict) -> str:
        """Store action in DB to survive restarts."""
        aid = hashlib.md5(
            f"{json.dumps(action, default=str)}{int(time.time()//3600)}".encode()
        ).hexdigest()[:8]
        try:
            now = datetime.now(timezone.utc).isoformat()
            await self.db.conn.execute(
                "INSERT OR REPLACE INTO bot_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (f"ap_pending.{aid}", json.dumps(action), now))
            await self.db.conn.commit()
        except Exception as e:
            logger.debug(f"AP store: {e}")
        return aid

    async def _get_pending(self, action_id: str) -> Optional[dict]:
        """Retrieve pending action from DB."""
        try:
            rows = await self.db.conn.execute_fetchall(
                "SELECT value FROM bot_settings WHERE key=?",
                (f"ap_pending.{action_id}",))
            if rows:
                return json.loads(rows[0][0])
        except Exception:
            pass
        return None

    async def _remove_pending(self, action_id: str):
        try:
            await self.db.conn.execute(
                "DELETE FROM bot_settings WHERE key=?", (f"ap_pending.{action_id}",))
            await self.db.conn.commit()
        except Exception:
            pass

    async def execute_action(self, action_id: str) -> Optional[str]:
        """Execute an approved action."""
        action = await self._get_pending(action_id)
        if not action:
            return "❌ Aksiyon bulunamadi veya suresi doldu."
        try:
            atype = action["type"]
            if atype == ACTION_STOP:
                await self.db.conn.execute(
                    "UPDATE strategies SET status='stopped' WHERE id=?", (action["sid"],))
                await self.db.conn.commit()
                logger.info(f"🤖 AutoPilot STOP: {action['label']}")
                await self._remove_pending(action_id)
                return f"✅ Durduruldu: {action['label']}\n{action['reason']}"
            elif atype == ACTION_SCALE:
                await self.db.conn.execute(
                    "UPDATE strategies SET trade_amount=? WHERE id=?",
                    (action["new_amount"], action["sid"]))
                await self.db.conn.commit()
                logger.info(f"🤖 AutoPilot SCALE: {action['label']} ${action['old_amount']}→${action['new_amount']}")
                await self._remove_pending(action_id)
                return f"✅ Olceklendi: {action['label']}\n${action['old_amount']}→${action['new_amount']:.2f}"
            elif atype == ACTION_TUNE:
                field = action.get("field", "odds_threshold")
                if field == "status":
                    await self.db.conn.execute(
                        "UPDATE strategies SET status=? WHERE id=?",
                        (action["new_val"], action["sid"]))
                else:
                    await self.db.conn.execute(
                        f"UPDATE strategies SET {field}=? WHERE id=?",
                        (action["new_val"], action["sid"]))
                await self.db.conn.commit()
                logger.info(f"🤖 AutoPilot TUNE: {action['label']} {action.get('old_val')}→{action['new_val']}")
                await self._remove_pending(action_id)
                return f"✅ Ayarlandi: {action['label']}\n{field}: {action.get('old_val')}→{action['new_val']}"
            return "❌ Bilinmeyen aksiyon tipi."
        except Exception as e:
            logger.error(f"AutoPilot execute: {e}")
            return f"❌ Hata: {e}"

    async def reject_action(self, action_id: str) -> str:
        action = await self._get_pending(action_id)
        if action:
            await self._remove_pending(action_id)
            logger.info(f"🤖 AutoPilot REJECTED: {action.get('desc', action_id)}")
            return f"⏭ Reddedildi: {action.get('desc', '?')}"
        return "Aksiyon bulunamadi."
