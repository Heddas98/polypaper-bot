"""
PolyPaper Bot — Portfolio Kill-Switch
========================================
P0.8 (5AI Yol Haritası §5.1 — Heddas direktifi "drawdown kill-switch hayati")

Üç katmanlı portfolio risk kontrolü:
  1. Daily loss → HALT (-%10/gün)
  2. Consecutive losses → cooldown (5 ardışık → 1h)
  3. Weekly drawdown → emergency stop (-%20/hafta, manuel restart)

Mevcut `PNL_PAUSE_THRESHOLD` (auto_optimizer.py) **strateji-bazlı pause**.
Bu modül **portfolio-bazlı halt** (tüm strategy'leri durdurur, panik kill-switch).

Memory'deki ilgili landmark:
- `auto_optimizer._startup_health_check` — strategy-level pause (-8.0 default)

Bu modül engine'in `should_trade()` gate'inden önce check edilir:
    if kill_switch.is_halted():
        return False
    proceed_with_trade()

ENV (T6.1 hot-tune pattern):
- KILL_DAILY_MAX_LOSS_PCT      (default 0.10 = %10)
- KILL_CONSECUTIVE_LOSS_LIMIT  (default 5)
- KILL_CONSECUTIVE_COOLDOWN_S  (default 3600 = 1h)
- KILL_WEEKLY_MAX_DD_PCT       (default 0.20 = %20)
- KILL_SWITCH_ENABLED          (default true; "false" → bypass)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Optional

logger = logging.getLogger("polypaper.core.portfolio_kill_switch")


def _env_float(name: str, default: float) -> float:
    """Runtime re-read (T6.1 doctrine — /env_toggle hot-tune compatible)."""
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name, "true" if default else "false").strip().lower()
    return val in {"1", "true", "yes", "on"}


# --- Reasons ---
HALT_NONE = "OK"
HALT_DAILY = "DAILY_LOSS_HALT"
HALT_CONSECUTIVE = "CONSECUTIVE_LOSS_COOLDOWN"
HALT_WEEKLY = "WEEKLY_DD_EMERGENCY"
HALT_DISABLED = "KILL_SWITCH_DISABLED"


@dataclass
class KillSwitchState:
    """Persistent state (in-memory; engine.risk DB persist optional)."""

    consecutive_losses: int = 0
    consecutive_cooldown_until: float = 0.0  # epoch seconds
    daily_pnl_baseline: float = 0.0  # equity at day start
    weekly_pnl_baseline: float = 0.0  # equity at week start
    daily_baseline_date: str = ""  # YYYY-MM-DD
    weekly_baseline_week: str = ""  # YYYY-WNN ISO
    weekly_emergency_triggered: bool = False
    last_trigger_reason: str = HALT_NONE
    last_trigger_ts: float = 0.0
    last_check_ts: float = 0.0


@dataclass
class HaltDecision:
    """Output of `evaluate()`."""

    halted: bool
    reason: str
    detail: str
    daily_pnl_pct: float = 0.0
    weekly_pnl_pct: float = 0.0
    consecutive_losses: int = 0
    cooldown_remaining_s: int = 0


class PortfolioKillSwitch:
    """Stateless evaluation, stateful tracking.

    Usage in engine:
        ks = PortfolioKillSwitch()
        # On each trade outcome:
        ks.record_trade(pnl=-1.5)
        # Before opening new trade:
        decision = ks.evaluate(current_equity=1000, daily_baseline=...)
        if decision.halted:
            return SKIP, decision.reason
    """

    def __init__(self, state: Optional[KillSwitchState] = None):
        self.state = state or KillSwitchState()

    # ---------- Tunables (runtime re-read each call) ----------

    @property
    def daily_max_loss_pct(self) -> float:
        return _env_float("KILL_DAILY_MAX_LOSS_PCT", 0.10)

    @property
    def consecutive_limit(self) -> int:
        return _env_int("KILL_CONSECUTIVE_LOSS_LIMIT", 5)

    @property
    def consecutive_cooldown_s(self) -> int:
        return _env_int("KILL_CONSECUTIVE_COOLDOWN_S", 3600)

    @property
    def weekly_max_dd_pct(self) -> float:
        return _env_float("KILL_WEEKLY_MAX_DD_PCT", 0.20)

    @property
    def enabled(self) -> bool:
        return _env_bool("KILL_SWITCH_ENABLED", True)

    # ---------- Trade lifecycle hooks ----------

    def record_trade(self, pnl: float) -> None:
        """Call after every closed trade. pnl > 0 = win, <= 0 = loss."""
        now = time.time()
        if pnl > 0:
            # Win → reset consecutive counter
            self.state.consecutive_losses = 0
        else:
            self.state.consecutive_losses += 1
            if self.state.consecutive_losses >= self.consecutive_limit:
                # Trigger cooldown
                self.state.consecutive_cooldown_until = now + self.consecutive_cooldown_s
                self.state.last_trigger_reason = HALT_CONSECUTIVE
                self.state.last_trigger_ts = now
                logger.warning(
                    f"⚠️ Kill-switch CONSECUTIVE: {self.state.consecutive_losses} losses → "
                    f"cooldown {self.consecutive_cooldown_s}s"
                )

    def reset_consecutive(self) -> None:
        """Manual reset (admin command)."""
        self.state.consecutive_losses = 0
        self.state.consecutive_cooldown_until = 0.0

    def reset_weekly_emergency(self) -> None:
        """Manual restart after weekly emergency (admin only)."""
        self.state.weekly_emergency_triggered = False
        logger.info("✅ Weekly emergency RESET (manual)")

    # ---------- Daily / Weekly baselines ----------

    def _today_str(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def _week_str(self) -> str:
        d = datetime.now(UTC)
        # ISO week (year + week number)
        iso = d.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    def _maybe_rotate_baselines(self, current_equity: float) -> None:
        """Rotate daily/weekly baselines if date/week changed."""
        today = self._today_str()
        week = self._week_str()
        if self.state.daily_baseline_date != today:
            self.state.daily_pnl_baseline = current_equity
            self.state.daily_baseline_date = today
            logger.info(f"📅 Daily baseline rotated: ${current_equity:.2f} ({today})")
        if self.state.weekly_baseline_week != week:
            self.state.weekly_pnl_baseline = current_equity
            self.state.weekly_baseline_week = week
            self.state.weekly_emergency_triggered = False  # auto-reset weekly
            logger.info(f"📅 Weekly baseline rotated: ${current_equity:.2f} ({week})")

    # ---------- Main evaluation ----------

    def evaluate(self, current_equity: float) -> HaltDecision:
        """Return halt decision.

        Args:
            current_equity: Mevcut portfolio value (USD). Bot'un net wealth'i.

        Returns: HaltDecision
        """
        now = time.time()
        self.state.last_check_ts = now

        if not self.enabled:
            return HaltDecision(
                halted=False,
                reason=HALT_DISABLED,
                detail="KILL_SWITCH_ENABLED=false",
            )

        self._maybe_rotate_baselines(current_equity)

        # 1. Weekly emergency (highest priority — manual reset gerek)
        if self.state.weekly_emergency_triggered:
            return HaltDecision(
                halted=True,
                reason=HALT_WEEKLY,
                detail="Weekly emergency triggered — admin /reset_weekly_emergency required",
                weekly_pnl_pct=self._weekly_pct(current_equity),
            )

        weekly_pct = self._weekly_pct(current_equity)
        if weekly_pct <= -self.weekly_max_dd_pct:
            self.state.weekly_emergency_triggered = True
            self.state.last_trigger_reason = HALT_WEEKLY
            self.state.last_trigger_ts = now
            logger.error(
                f"🚨 Weekly drawdown EMERGENCY: {weekly_pct*100:.2f}% "
                f"<= -{self.weekly_max_dd_pct*100:.0f}%"
            )
            return HaltDecision(
                halted=True,
                reason=HALT_WEEKLY,
                detail=f"Weekly drawdown {weekly_pct*100:.2f}% reached threshold "
                f"-{self.weekly_max_dd_pct*100:.0f}%",
                weekly_pnl_pct=weekly_pct,
            )

        # 2. Daily loss halt
        daily_pct = self._daily_pct(current_equity)
        if daily_pct <= -self.daily_max_loss_pct:
            self.state.last_trigger_reason = HALT_DAILY
            self.state.last_trigger_ts = now
            return HaltDecision(
                halted=True,
                reason=HALT_DAILY,
                detail=f"Daily loss {daily_pct*100:.2f}% reached threshold "
                f"-{self.daily_max_loss_pct*100:.0f}%",
                daily_pnl_pct=daily_pct,
                weekly_pnl_pct=weekly_pct,
            )

        # 3. Consecutive loss cooldown
        if self.state.consecutive_cooldown_until > now:
            remaining = int(self.state.consecutive_cooldown_until - now)
            return HaltDecision(
                halted=True,
                reason=HALT_CONSECUTIVE,
                detail=f"{self.state.consecutive_losses} consecutive losses; "
                f"cooldown {remaining}s remaining",
                consecutive_losses=self.state.consecutive_losses,
                cooldown_remaining_s=remaining,
                daily_pnl_pct=daily_pct,
                weekly_pnl_pct=weekly_pct,
            )

        return HaltDecision(
            halted=False,
            reason=HALT_NONE,
            detail="OK",
            daily_pnl_pct=daily_pct,
            weekly_pnl_pct=weekly_pct,
            consecutive_losses=self.state.consecutive_losses,
        )

    def _daily_pct(self, equity: float) -> float:
        if self.state.daily_pnl_baseline <= 0:
            return 0.0
        return (equity - self.state.daily_pnl_baseline) / self.state.daily_pnl_baseline

    def _weekly_pct(self, equity: float) -> float:
        if self.state.weekly_pnl_baseline <= 0:
            return 0.0
        return (equity - self.state.weekly_pnl_baseline) / self.state.weekly_pnl_baseline

    # ---------- Telegram /kill_switch_status ----------

    def status_html(self, current_equity: float) -> str:
        """Format current state as HTML for Telegram."""
        decision = self.evaluate(current_equity)
        emoji = "🛑" if decision.halted else "✅"
        reason_emoji = {
            HALT_NONE: "✅",
            HALT_DAILY: "🔴",
            HALT_CONSECUTIVE: "🟡",
            HALT_WEEKLY: "🚨",
            HALT_DISABLED: "⚪",
        }.get(decision.reason, "❓")

        lines = [
            f"<b>{emoji} Portfolio Kill-Switch</b>",
            "",
            f"Status: {reason_emoji} <b>{decision.reason}</b>",
            f"  {decision.detail}",
            "",
            f"📊 Equity:     ${current_equity:.2f}",
            f"📅 Daily:      {decision.daily_pnl_pct*100:+.2f}% "
            f"(threshold: -{self.daily_max_loss_pct*100:.0f}%)",
            f"📅 Weekly:     {decision.weekly_pnl_pct*100:+.2f}% "
            f"(threshold: -{self.weekly_max_dd_pct*100:.0f}%)",
            f"🔁 Streak:     {decision.consecutive_losses} consecutive losses "
            f"(limit: {self.consecutive_limit})",
        ]
        if decision.cooldown_remaining_s > 0:
            lines.append(f"⏳ Cooldown:   {decision.cooldown_remaining_s}s remaining")
        return "\n".join(lines)


# Module-level singleton (engine'in kullanımı için)
_default_instance: Optional[PortfolioKillSwitch] = None


def get_kill_switch() -> PortfolioKillSwitch:
    """Get or create default instance (singleton pattern)."""
    global _default_instance
    if _default_instance is None:
        _default_instance = PortfolioKillSwitch()
    return _default_instance
