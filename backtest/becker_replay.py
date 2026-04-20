"""
Phase 50 — Becker Historical Replay Harness
===========================================

Walk-forward historical backtest against the Jon-Becker Polymarket dataset.

The Becker calibration DB (``data_store/becker_calibration.db``, ~849 MB)
already contains crypto Up/Down trades with:
  - poly_crypto        : raw trades (timestamp, maker/taker amounts, slug)
  - poly_crypto_markets: slug, side (yes/no), outcome_prices, end_date

This harness reconstructs each historical market from the trade stream,
walks it forward one trade at a time, and gives a pluggable strategy
function the chance to open/close positions — exactly like the live
engine, but against historical data. Result: a PnL curve / Sharpe / max
drawdown over a multi-year crypto Up/Down dataset (the thing Heddas
specifically asked for in Phase 50).

Design notes
------------
1. **DuckDB read-only**: the harness queries the existing calibration DB,
   so it does NOT require the 36 GB raw tarball at runtime (only for the
   one-time calibration build). This means the harness can run on a
   Replit free tier or the Samsung Tab S10 Ultra with just the 849 MB DB.

2. **Market selector**: ``select_markets(limit, min_trades)`` returns
   a list of (slug, end_date, resolved_yes) for the N most active
   resolved markets — a sane default universe.

3. **Strategy protocol**: any callable matching
       ``strategy(ctx: ReplayContext) -> ReplayDecision | None``
   plugs in. ``ReplayContext`` exposes slug, current mid yes_price,
   seconds to resolution, and the engine's open position for the slug.

4. **Maker/taker toggle**: PnL accounts for Polymarket's 2% taker fee
   and 0% maker fee (Phase 43a parity). Strategy can signal maker intent.

5. **Walk-forward**: trades are visited in ascending timestamp within a
   market, so there is NO look-ahead bias. Resolution (``outcome_prices``)
   is consulted ONLY at market end — never during the walk.

Usage (CLI)
-----------
    python -m backtest.becker_replay --markets 100 --strategy threshold_70
    python -m backtest.becker_replay --markets 50 --strategy threshold_55 --maker

Output
------
Prints a summary table and writes ``data_store/becker_replay_<ts>.json``
with the full trade log for post-hoc analysis.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

logger = logging.getLogger("polypaper.backtest.becker_replay")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CALIB_DB = PROJECT_ROOT / "data_store" / "becker_calibration.db"

TAKER_FEE = float(os.getenv("POLY_TAKER_FEE", "0.02"))
MAKER_FEE = float(os.getenv("POLY_MAKER_FEE", "0.0"))


# ───────────────────────── dataclasses ─────────────────────────

@dataclass
class MarketRef:
    slug: str
    end_date: str                  # ISO8601
    resolved_yes: float            # 0.0 / 1.0 / 0.5 push
    trade_count: int


@dataclass
class ReplayTrade:
    slug: str
    timestamp: int
    yes_price: float               # mid yes probability at this tick
    maker_amount: float
    taker_amount: float


@dataclass
class OpenPosition:
    slug: str
    side: str                      # "YES" / "NO"
    entry_price: float
    stake_usd: float
    shares: float
    is_maker: bool
    opened_at: int


@dataclass
class ClosedTrade:
    slug: str
    side: str
    entry_price: float
    exit_price: float              # settlement price for the chosen side
    stake_usd: float
    shares: float
    pnl_usd: float
    fee_usd: float
    is_maker: bool
    opened_at: int
    closed_at: int


@dataclass
class ReplayDecision:
    """Returned by a strategy function when it wants to act."""
    action: str                    # "open" | "close" | "skip"
    side: Optional[str] = None     # required for "open"
    stake_usd: float = 1.0         # for "open"
    is_maker: bool = False


@dataclass
class ReplayContext:
    slug: str
    now_ts: int
    yes_price: float
    seconds_to_end: float
    open_position: Optional[OpenPosition]
    resolved_yes: float            # exposed so strategies can debug, NOT to cheat


@dataclass
class ReplayResult:
    trades: list[ClosedTrade] = field(default_factory=list)
    markets_seen: int = 0
    markets_traded: int = 0

    # ── metrics ──
    def summarize(self) -> dict:
        n = len(self.trades)
        if n == 0:
            return {
                "trades": 0,
                "markets_seen": self.markets_seen,
                "markets_traded": self.markets_traded,
                "total_pnl": 0.0,
                "win_rate": 0.0,
                "avg_pnl": 0.0,
                "sharpe": 0.0,
                "max_dd": 0.0,
            }
        pnls = [t.pnl_usd for t in self.trades]
        wins = sum(1 for p in pnls if p > 0)
        total_pnl = sum(pnls)
        mean = total_pnl / n
        var = sum((p - mean) ** 2 for p in pnls) / max(n - 1, 1)
        std = math.sqrt(var) if var > 0 else 0.0
        sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0.0

        # Max drawdown on equity curve
        eq = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            eq += p
            peak = max(peak, eq)
            dd = peak - eq
            max_dd = max(max_dd, dd)

        return {
            "trades": n,
            "markets_seen": self.markets_seen,
            "markets_traded": self.markets_traded,
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(100 * wins / n, 2),
            "avg_pnl": round(mean, 4),
            "sharpe": round(sharpe, 3),
            "max_dd": round(max_dd, 2),
        }


# ──────────────────────── data access ────────────────────────

class BeckerReplayDB:
    """Read-only DuckDB client against becker_calibration.db."""

    def __init__(self, db_path: Path = CALIB_DB):
        if not db_path.exists():
            raise FileNotFoundError(
                f"becker_calibration.db not found at {db_path}. "
                "Run /becker_build or scripts/download_becker.bat first.")
        try:
            import duckdb  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "duckdb not installed. pip install duckdb (~25MB, zero deps)."
            ) from e
        self.con = duckdb.connect(str(db_path), read_only=True)

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass

    # ── market selection ──
    def select_markets(self, limit: int = 100, min_trades: int = 20,
                       only_resolved: bool = True,
                       asset_filter: Optional[str] = None) -> list[MarketRef]:
        """Top-N resolved crypto Up/Down markets by trade count.

        Phase 57: asset_filter — filter by asset slug substring.
          "btc"  → only BTC markets (slug LIKE '%btc%')
          "eth"  → only ETH markets
          "sol"  → only SOL markets
          None   → all crypto markets (original behaviour)
        """
        asset_clause = ""
        if asset_filter:
            # Sanitise: lowercase, alphanumeric only
            safe = "".join(c for c in asset_filter.lower() if c.isalnum())
            if safe:
                asset_clause = f"AND LOWER(m.slug) LIKE '%{safe}%'"

        rows = self.con.execute(f"""
            WITH m AS (
                SELECT DISTINCT slug, end_date, outcome_prices
                FROM poly_crypto_markets
                WHERE outcome_prices IS NOT NULL
                  AND end_date IS NOT NULL
                  {"AND closed = TRUE" if only_resolved else ""}
                  {asset_clause}
            ),
            counts AS (
                SELECT slug, COUNT(*) AS n
                FROM poly_crypto
                GROUP BY slug
            )
            SELECT m.slug, m.end_date, m.outcome_prices, counts.n
            FROM m
            JOIN counts USING (slug)
            WHERE counts.n >= {int(min_trades)}
            ORDER BY counts.n DESC
            LIMIT {int(limit)}
        """).fetchall()
        out: list[MarketRef] = []
        for slug, end_date, outcome_prices, n in rows:
            try:
                # outcome_prices is a JSON string like '["1","0"]'
                import json as _json
                parts = _json.loads(outcome_prices)
                resolved_yes = float(parts[0])
            except Exception:
                continue
            out.append(MarketRef(
                slug=str(slug or ""),
                end_date=str(end_date or ""),
                resolved_yes=resolved_yes,
                trade_count=int(n or 0),
            ))
        return out

    # ── trade stream per market ──
    def iter_trades(self, slug: str) -> Iterator[ReplayTrade]:
        """Ordered trade stream for a single market.

        Reconstructs yes_price at each tick via poly_crypto_markets.side:
            token_in_maker → yes_price = taker_amount / maker_amount
            token_in_taker → yes_price = maker_amount / taker_amount
            if side == 'no' → flip to 1 - token_price
        """
        rows = self.con.execute("""
            WITH priced AS (
                SELECT t.timestamp,
                       t.maker_amount, t.taker_amount,
                       m.side,
                       CAST(t.taker_amount AS DOUBLE)
                         / NULLIF(t.maker_amount, 0) AS tp
                FROM poly_crypto AS t
                JOIN poly_crypto_markets AS m
                  ON t.maker_asset_id = m.token_id
                WHERE t.slug = ?
                  AND t.maker_amount > 0
                  AND t.taker_amount > 0
                UNION ALL
                SELECT t.timestamp,
                       t.maker_amount, t.taker_amount,
                       m.side,
                       CAST(t.maker_amount AS DOUBLE)
                         / NULLIF(t.taker_amount, 0) AS tp
                FROM poly_crypto AS t
                JOIN poly_crypto_markets AS m
                  ON t.taker_asset_id = m.token_id
                WHERE t.slug = ?
                  AND t.maker_amount > 0
                  AND t.taker_amount > 0
            )
            SELECT timestamp,
                   CASE WHEN side = 'yes' THEN tp ELSE 1.0 - tp END AS yes_price,
                   maker_amount, taker_amount
            FROM priced
            WHERE tp BETWEEN 0.0 AND 1.0
            ORDER BY timestamp
        """, [slug, slug]).fetchall()
        for ts, yes_price, mk, tk in rows:
            yield ReplayTrade(
                slug=slug,
                timestamp=int(ts or 0),
                yes_price=float(yes_price or 0.0),
                maker_amount=float(mk or 0.0),
                taker_amount=float(tk or 0.0),
            )


# ──────────────────────── strategies ────────────────────────

def _end_ts_from_iso(iso: str) -> int:
    """Parse an ISO8601 end_date → unix epoch seconds."""
    from datetime import datetime as _dt
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return int(_dt.strptime(iso.replace("Z", ""), fmt).timestamp())
        except ValueError:
            continue
    try:
        return int(_dt.fromisoformat(iso.replace("Z", "")).timestamp())
    except Exception:
        return 0


def threshold_strategy(threshold: float = 0.70,
                       min_seconds_left: float = 60.0,
                       max_seconds_left: float = 600.0,
                       stake_usd: float = 1.0,
                       side_above: str = "YES"):
    """Classic threshold buy:
      - open YES at first tick where yes_price >= threshold and
        min_seconds_left <= seconds_to_end <= max_seconds_left
      - hold to resolution
    Can also flip to NO if threshold direction is inverted via side_above.
    """
    def _strategy(ctx: ReplayContext) -> Optional[ReplayDecision]:
        if ctx.open_position is not None:
            return None
        if not (min_seconds_left <= ctx.seconds_to_end <= max_seconds_left):
            return None
        if side_above == "YES" and ctx.yes_price >= threshold:
            return ReplayDecision(action="open", side="YES", stake_usd=stake_usd)
        if side_above == "NO" and (1.0 - ctx.yes_price) >= threshold:
            return ReplayDecision(action="open", side="NO", stake_usd=stake_usd)
        return None
    return _strategy


STRATEGY_REGISTRY: dict[str, Callable] = {
    "threshold_70": threshold_strategy(threshold=0.70),
    "threshold_65": threshold_strategy(threshold=0.65),
    "threshold_60": threshold_strategy(threshold=0.60),
    "threshold_55": threshold_strategy(threshold=0.55),
    "threshold_50": threshold_strategy(threshold=0.50),
    # Contrarian NO taker
    "contra_70": threshold_strategy(threshold=0.70, side_above="NO"),
}


# ──────────────────────── harness ────────────────────────

def replay_market(db: BeckerReplayDB, market: MarketRef,
                  strategy: Callable[[ReplayContext], Optional[ReplayDecision]],
                  maker_default: bool = False,
                  latency_ms: int = 0,
                  fill_fraction: float = 1.0) -> list[ClosedTrade]:
    """Walk a single market trade-by-trade. Returns list of closed trades
    (currently 0 or 1 per market — harness holds to settlement).

    Phase 50 P1-07/P1-08 knobs:
      - ``latency_ms``: signal→fill delay. Entry price is taken from the first
        trade whose timestamp ≥ signal_ts + latency_ms (fallback: last seen).
      - ``fill_fraction``: 0<f≤1 — partial fill multiplier on shares/stake.
    """
    end_ts = _end_ts_from_iso(market.end_date)
    if end_ts == 0:
        return []

    open_pos: Optional[OpenPosition] = None
    last_trade: Optional[ReplayTrade] = None

    # Buffer trades so we can look ahead for latency-delayed fills.
    trades_iter = list(db.iter_trades(market.slug))
    n = len(trades_iter)

    for i, trade in enumerate(trades_iter):
        last_trade = trade
        if trade.timestamp >= end_ts:
            break  # market closed
        seconds_left = end_ts - trade.timestamp

        ctx = ReplayContext(
            slug=market.slug,
            now_ts=trade.timestamp,
            yes_price=trade.yes_price,
            seconds_to_end=seconds_left,
            open_position=open_pos,
            resolved_yes=market.resolved_yes,
        )
        decision = strategy(ctx)
        if decision is None or decision.action == "skip":
            continue

        if decision.action == "open" and open_pos is None and decision.side:
            # P1-07: advance to the first trade at-or-after signal+latency_ms
            fill_trade = trade
            if latency_ms > 0:
                target_ts_ms = trade.timestamp * 1000 + latency_ms
                for j in range(i + 1, n):
                    if trades_iter[j].timestamp * 1000 >= target_ts_ms:
                        fill_trade = trades_iter[j]
                        break
                if fill_trade.timestamp >= end_ts:
                    continue  # latency pushed fill past market close
            entry = (fill_trade.yes_price if decision.side == "YES"
                     else 1.0 - fill_trade.yes_price)
            if entry <= 0 or entry >= 1:
                continue
            is_maker = decision.is_maker or maker_default
            # P1-08: partial fill fraction (clamped to (0,1])
            frac = max(0.01, min(1.0, float(fill_fraction)))
            effective_stake = decision.stake_usd * frac
            shares = effective_stake / entry
            open_pos = OpenPosition(
                slug=market.slug,
                side=decision.side,
                entry_price=entry,
                stake_usd=effective_stake,
                shares=shares,
                is_maker=is_maker,
                opened_at=fill_trade.timestamp,
            )
        elif decision.action == "close" and open_pos is not None:
            # Early close at current price
            exit_p = (trade.yes_price if open_pos.side == "YES"
                      else 1.0 - trade.yes_price)
            closed = _settle_position(open_pos, exit_p, trade.timestamp,
                                      market.slug)
            return [closed]

    # Settle open position at resolution
    if open_pos is not None:
        yes_settle = market.resolved_yes
        exit_p = yes_settle if open_pos.side == "YES" else (1.0 - yes_settle)
        closed_ts = end_ts if last_trade is None else max(last_trade.timestamp,
                                                           end_ts)
        closed = _settle_position(open_pos, exit_p, closed_ts, market.slug)
        return [closed]
    return []


def _settle_position(pos: OpenPosition, exit_price: float, closed_ts: int,
                     slug: str) -> ClosedTrade:
    fee_rate = MAKER_FEE if pos.is_maker else TAKER_FEE
    gross = pos.shares * (exit_price - pos.entry_price)
    # Fee applies on notional (entry + exit) at taker rate
    fee = 2 * pos.shares * pos.entry_price * fee_rate if not pos.is_maker else 0.0
    pnl = gross - fee
    return ClosedTrade(
        slug=slug,
        side=pos.side,
        entry_price=pos.entry_price,
        exit_price=exit_price,
        stake_usd=pos.stake_usd,
        shares=pos.shares,
        pnl_usd=pnl,
        fee_usd=fee,
        is_maker=pos.is_maker,
        opened_at=pos.opened_at,
        closed_at=closed_ts,
    )


def run_replay(strategy_name: str = "threshold_70",
               markets: int = 100,
               min_trades: int = 20,
               maker: bool = False,
               only_resolved: bool = True,
               latency_ms: int = 0,
               fill_fraction: float = 1.0,
               asset_filter: Optional[str] = None) -> ReplayResult:
    """Phase 57: asset_filter kwarg pipes through to select_markets.
    Examples: asset_filter="btc", "eth", "sol", None (all).
    """
    strat = STRATEGY_REGISTRY.get(strategy_name)
    if strat is None:
        raise ValueError(
            f"Unknown strategy {strategy_name!r}. Known: "
            f"{list(STRATEGY_REGISTRY.keys())}")
    db = BeckerReplayDB()
    result = ReplayResult()
    try:
        refs = db.select_markets(limit=markets, min_trades=min_trades,
                                 only_resolved=only_resolved,
                                 asset_filter=asset_filter)
        result.markets_seen = len(refs)
        for ref in refs:
            closed_list = replay_market(db, ref, strat, maker_default=maker,
                                        latency_ms=latency_ms,
                                        fill_fraction=fill_fraction)
            if closed_list:
                result.markets_traded += 1
                result.trades.extend(closed_list)
    finally:
        db.close()
    return result


def save_report(result: ReplayResult, strategy_name: str,
                out_dir: Path = PROJECT_ROOT / "data_store") -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"becker_replay_{strategy_name}_{ts}.json"
    payload = {
        "strategy": strategy_name,
        "summary": result.summarize(),
        "trades": [asdict(t) for t in result.trades],
    }
    out.write_text(json.dumps(payload, indent=2))
    return out


# ──────────────────────── CLI ────────────────────────

def _format_summary(summary: dict) -> str:
    lines = [
        "═══ Becker Replay Summary ═══",
        f"Markets seen    : {summary['markets_seen']}",
        f"Markets traded  : {summary['markets_traded']}",
        f"Trades          : {summary['trades']}",
        f"Total PnL       : ${summary['total_pnl']:+.2f}",
        f"Win rate        : {summary['win_rate']:.2f}%",
        f"Avg PnL / trade : ${summary['avg_pnl']:+.4f}",
        f"Sharpe (annual) : {summary['sharpe']:.3f}",
        f"Max drawdown    : ${summary['max_dd']:.2f}",
    ]
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(
        prog="becker_replay",
        description="Walk-forward historical backtest against the Becker "
                    "Polymarket calibration DB.",
    )
    p.add_argument("--strategy", default="threshold_70",
                   choices=sorted(STRATEGY_REGISTRY.keys()))
    p.add_argument("--markets", type=int, default=100,
                   help="Max markets to replay (default: 100)")
    p.add_argument("--min-trades", type=int, default=20,
                   help="Only markets with >= N trades (default: 20)")
    p.add_argument("--maker", action="store_true",
                   help="Treat all opens as maker (0 fee)")
    p.add_argument("--include-unresolved", action="store_true",
                   help="Include markets where closed != TRUE")
    p.add_argument("--save", action="store_true",
                   help="Write report JSON under data_store/")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    t0 = time.time()
    result = run_replay(
        strategy_name=args.strategy,
        markets=args.markets,
        min_trades=args.min_trades,
        maker=args.maker,
        only_resolved=not args.include_unresolved,
    )
    elapsed = time.time() - t0
    summary = result.summarize()
    print(_format_summary(summary))
    print(f"Elapsed         : {elapsed:.2f}s")
    if args.save:
        path = save_report(result, args.strategy)
        print(f"Report saved    : {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
