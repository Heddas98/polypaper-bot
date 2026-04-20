"""
PolyPaper Bot - Kelly Criterion Position Sizing (Phase 27 + Phase 73)
Automatically sizes trades based on verified win rate.

Uses Quarter Kelly (1/4 of full Kelly) for safety:
- Full Kelly has 33% chance of halving bankroll before doubling
- Quarter Kelly retains 51% of growth rate with 9% of variance

Phase 73: Kelly Decay (Regime-Based)
  Dynamically adjusts Kelly fraction based on market regime:
  - TRENDING  → Quarter Kelly (1/4) — full aggression, regime matches
  - RANGING   → Sixth Kelly (1/6) — moderate, less directional edge
  - VOLATILE  → Eighth Kelly (1/8) — conservative, protect capital

ENV controls:
  KELLY_DECAY_ENABLED=true   — enable regime-based Kelly scaling
  KELLY_DECAY_TRENDING=0.25  — fraction in trending regime (default quarter)
  KELLY_DECAY_RANGING=0.167  — fraction in ranging regime (default sixth)
  KELLY_DECAY_VOLATILE=0.125 — fraction in volatile regime (default eighth)

For binary markets at price p:
  b = (1/p - 1) = payout ratio
  f* = (b*win_rate - loss_rate) / b
  Regime-adjusted Kelly = f* × regime_fraction
"""
import logging
import os

logger = logging.getLogger("polypaper.core.kelly")

MIN_TRADES_FOR_KELLY = 15  # Need at least 15 trades for reliable WR
MIN_BET = 1.0              # Polymarket minimum ~$1
MAX_BET_PCT = 0.15         # Never risk more than 15% of bankroll
KELLY_FRACTION = 0.25      # Quarter Kelly — retains 51% of full Kelly growth with only 9% of variance

# ── Phase 73: Kelly Decay (Regime-Based) ──
KELLY_DECAY_ENABLED = os.getenv("KELLY_DECAY_ENABLED", "true").lower() in ("true", "1", "yes")
KELLY_DECAY_FRACTIONS = {
    "trending":  float(os.getenv("KELLY_DECAY_TRENDING", "0.25")),   # Quarter Kelly
    "ranging":   float(os.getenv("KELLY_DECAY_RANGING", "0.167")),   # ~Sixth Kelly
    "volatile":  float(os.getenv("KELLY_DECAY_VOLATILE", "0.125")),  # Eighth Kelly
}


def get_regime_kelly_fraction(regime: str = "ranging") -> float:
    """Get Kelly fraction adjusted for current market regime.

    Args:
        regime: Market regime from RegimeClassifier ("trending", "ranging", "volatile")

    Returns:
        Kelly fraction (0.125-0.25). Falls back to KELLY_FRACTION if decay disabled.
    """
    if not KELLY_DECAY_ENABLED:
        return KELLY_FRACTION
    return KELLY_DECAY_FRACTIONS.get(regime, KELLY_FRACTION)

# Phase 47f.9 (2026-04-09): Kelly now uses the REAL wallet bankroll instead of
# a hardcoded $100. Env caps:
#   KELLY_BANKROLL_MIN  — floor applied if wallet < this (avoid sub-dollar sizing noise).
#   KELLY_BANKROLL_MAX  — hard cap, prevents runaway sizing if a paper wallet drifts high.
KELLY_BANKROLL_MIN = float(os.getenv("KELLY_BANKROLL_MIN", "10.0"))
KELLY_BANKROLL_MAX = float(os.getenv("KELLY_BANKROLL_MAX", "10000.0"))


def _effective_bankroll(bankroll: float) -> float:
    """Clamp the caller's bankroll into the safe Kelly range."""
    try:
        b = float(bankroll)
    except (TypeError, ValueError):
        b = KELLY_BANKROLL_MIN
    if b < KELLY_BANKROLL_MIN:
        return KELLY_BANKROLL_MIN
    if b > KELLY_BANKROLL_MAX:
        return KELLY_BANKROLL_MAX
    return b


def calculate_kelly_size(
    win_rate: float,        # 0.0-1.0
    avg_entry_price: float, # Average entry odds (e.g., 0.70)
    bankroll: float,        # Current balance
    min_trades: int = MIN_TRADES_FOR_KELLY,
    trade_count: int = 0,
    fraction: float = KELLY_FRACTION,
) -> dict:
    """Calculate optimal position size using Quarter Kelly.

    Returns:
        {
            "size": float,         # Dollar amount to bet
            "full_kelly_pct": float,  # Full Kelly as % of bankroll
            "quarter_kelly_pct": float,
            "confidence": str,     # "high", "medium", "low"
            "reason": str,
        }
    """
    # Default fallback — size=0 means SKIP (no trade).
    # Phase 52 fix: Previously defaulted to MIN_BET which caused the bot to
    # bleed $1/trade when there was no edge.  Now returns 0 so the engine
    # skips the entry entirely.
    result = {
        "size": 0.0,
        "full_kelly_pct": 0,
        "quarter_kelly_pct": 0,
        "confidence": "low",
        "reason": "",
        "skip": True,
    }

    if trade_count < min_trades:
        # Phase 58: Edge check even during exploration.
        # If strategy already has some trades and WR is below breakeven + margin,
        # don't burn $1/trade on a losing strategy.
        _expl_min_wr = float(os.getenv("KELLY_EXPLORATION_MIN_WR", "0.50"))  # Phase 62: 0.52→0.50 (true breakeven)
        if trade_count >= 5 and win_rate < _expl_min_wr:
            result["reason"] = (f"Exploration WR {win_rate:.0%} < {_expl_min_wr:.0%} "
                                f"after {trade_count} trades — SKIP")
            result["size"] = 0.0
            result["skip"] = True
            return result
        result["reason"] = f"Need {min_trades}+ trades (have {trade_count})"
        result["size"] = MIN_BET          # exploration phase — allow MIN_BET
        result["skip"] = False
        return result

    if win_rate <= 0.50:
        result["reason"] = f"WR {win_rate:.0%} ≤ 50% — no edge, SKIP"
        result["size"] = 0.0
        result["skip"] = True
        return result

    if avg_entry_price <= 0 or avg_entry_price >= 0.999:
        result["reason"] = f"Invalid entry price {avg_entry_price}"
        result["size"] = 0.0
        result["skip"] = True
        return result

    # Binary market Kelly formula
    # b = payout ratio = (1/p) - 1
    # f* = (b * p_win - p_loss) / b
    # Phase 54 P0-02: avg_entry_price > 0 guaranteed by guard above
    b = (1.0 / avg_entry_price) - 1.0  # Net odds (e.g., at 0.70: b = 0.4286)
    if b <= 1e-9:  # Phase 54: near-zero guard for numerical stability
        result["reason"] = "Invalid odds, SKIP"
        result["size"] = 0.0
        result["skip"] = True
        return result

    p_win = win_rate
    p_loss = 1.0 - win_rate

    full_kelly = (b * p_win - p_loss) / b
    if full_kelly <= 0:
        result["reason"] = f"Negative Kelly: edge insufficient at this price, SKIP"
        result["size"] = 0.0
        result["skip"] = True
        return result

    quarter_kelly = full_kelly * fraction
    bet_size = bankroll * quarter_kelly

    # Apply bounds
    max_bet = bankroll * MAX_BET_PCT
    bet_size = max(MIN_BET, min(bet_size, max_bet))
    bet_size = round(bet_size, 2)

    # Confidence level
    if trade_count >= 50 and win_rate >= 0.65:
        confidence = "high"
    elif trade_count >= 25 and win_rate >= 0.55:
        confidence = "medium"
    else:
        confidence = "low"

    result.update({
        "size": bet_size,
        "full_kelly_pct": round(full_kelly * 100, 1),
        "quarter_kelly_pct": round(quarter_kelly * 100, 1),
        "confidence": confidence,
        "skip": False,
        "reason": f"WR={p_win:.0%} b={b:.2f} FK={full_kelly:.1%} QK={quarter_kelly:.1%} → ${bet_size:.2f}",
    })

    return result


async def get_strategy_kelly(db, strategy_id: str, bankroll: float,
                             regime: str = "ranging") -> dict:
    """Calculate Kelly size using REALIZED trade data (not theoretical b).
    This fixes the bug where 92% WR at 0.93 entry gives negative Kelly.
    Phase 47f.9: uses the caller-supplied wallet bankroll (clamped to
    KELLY_BANKROLL_MIN/MAX env bounds) instead of a hardcoded $100.
    Phase 73: regime parameter adjusts Kelly fraction via Kelly Decay."""
    effective_bankroll = _effective_bankroll(bankroll)
    # Phase 73: regime-aware fraction
    fraction = get_regime_kelly_fraction(regime)
    try:
        rows = await db.conn.execute_fetchall(
            """SELECT COUNT(*) as t,
                COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) as w,
                COALESCE(AVG(CASE WHEN pnl>0 THEN pnl END),0) as avg_win,
                COALESCE(AVG(CASE WHEN pnl<0 THEN ABS(pnl) END),0) as avg_loss,
                COALESCE(AVG(execution_price),0.5) as avg_price,
                COALESCE(AVG(trade_amount),1) as avg_amount
            FROM executions WHERE strategy_id=? AND result IS NOT NULL""",
            (strategy_id,))

        if not rows or rows[0][0] < 5:
            # Exploration phase — allow MIN_BET so strategy can gather data
            return {"size": MIN_BET, "full_kelly_pct": 0, "quarter_kelly_pct": 0,
                    "confidence": "low", "skip": False,
                    "reason": f"Need 5+ trades (have {rows[0][0] if rows else 0})"}

        t, w, avg_win, avg_loss, avg_price, avg_amount = rows[0]
        wr = w / t if t > 0 else 0.5

        if wr <= 0.50 or t < MIN_TRADES_FOR_KELLY:
            # Phase 52: NO EDGE → SKIP.  Previously returned MIN_BET which
            # bled $1/trade indefinitely.
            return {"size": 0.0, "full_kelly_pct": 0, "quarter_kelly_pct": 0,
                    "confidence": "low", "skip": True,
                    "reason": f"WR={wr:.0%} {'≤ 50% no edge' if wr<=0.5 else ''} t={t}, SKIP"}

        # Realized Kelly: f* = (p*W - q*L) / (W*L)
        # where W=avg_win, L=avg_loss, p=win_rate, q=1-p
        # Simplified: f* = p/L - q/W (fraction of bankroll)
        if avg_loss <= 0 or avg_win <= 0:
            # All wins or all losses — use simple fraction
            if wr >= 0.90 and avg_win > 0:
                full_kelly = wr - 0.5  # Simple edge-based
            else:
                return {"size": 0.0, "full_kelly_pct": 0, "quarter_kelly_pct": 0,
                        "confidence": "low", "skip": True, "reason": "No loss data, SKIP"}
        else:
            # Standard Kelly with realized W/L
            b = avg_win / avg_loss  # Realized win/loss ratio
            full_kelly = (b * wr - (1 - wr)) / b

        if full_kelly <= 0:
            # Kelly negative but check if EV is positive (asymmetric payoff)
            ev_per_trade = (wr * avg_win) - ((1 - wr) * avg_loss)
            if ev_per_trade > 0 and t >= 10:
                # EV-based sizing: bet = EV / max_loss × bankroll × 0.25
                ev_fraction = (ev_per_trade / avg_loss) * 0.25
                bet_size = effective_bankroll * ev_fraction
                bet_size = max(MIN_BET, min(bet_size, effective_bankroll * MAX_BET_PCT))
                bet_size = round(bet_size, 2)
                confidence = "medium" if t >= 20 and wr >= 0.80 else "low"
                return {
                    "size": bet_size,
                    "full_kelly_pct": round(ev_fraction * 400, 1),
                    "quarter_kelly_pct": round(ev_fraction * 100, 1),
                    "confidence": confidence,
                    "skip": False,
                    "reason": f"EV-based: WR={wr:.0%} EV=${ev_per_trade:.2f}/trade → ${bet_size:.2f}",
                }
            return {"size": 0.0, "full_kelly_pct": 0, "quarter_kelly_pct": 0,
                    "confidence": "low", "skip": True,
                    "reason": f"No edge: WR={wr:.0%} EV=${ev_per_trade:.2f}, SKIP"}

        # Phase 73: use regime-aware fraction instead of fixed KELLY_FRACTION
        regime_kelly = full_kelly * fraction
        bet_size = effective_bankroll * regime_kelly
        max_bet = effective_bankroll * MAX_BET_PCT
        bet_size = max(MIN_BET, min(bet_size, max_bet))
        bet_size = round(bet_size, 2)

        if t >= 50 and wr >= 0.65:
            confidence = "high"
        elif t >= 25 and wr >= 0.55:
            confidence = "medium"
        else:
            confidence = "low"

        # Label reflects regime fraction
        frac_label = f"{fraction:.0%}" if fraction != 0.25 else "QK"

        return {
            "size": bet_size,
            "full_kelly_pct": round(full_kelly * 100, 1),
            "quarter_kelly_pct": round(regime_kelly * 100, 1),
            "confidence": confidence,
            "skip": False,
            "regime_fraction": fraction,
            "reason": f"WR={wr:.0%} W=${avg_win:.2f} L=${avg_loss:.2f} {frac_label}={regime_kelly:.1%} → ${bet_size:.2f}",
        }
    except Exception as e:
        logger.error(f"Kelly calc: {e}")
        return {"size": MIN_BET, "reason": f"Error: {e}", "confidence": "low",
                "skip": False, "full_kelly_pct": 0, "quarter_kelly_pct": 0}
