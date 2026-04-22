#!/usr/bin/env python3
"""
T11.2 G4 PnL Divergence — Standalone Runtime Probe
===================================================

T11_2_runtime_validation.md G4 için "opsiyonel #1" — 48h beklemeden
PnL divergence job'ının gördüğü state'i ayna eder. `pnl_divergence_job`
ile birebir aynı SQL + aynı hesaplamayı yapar; Telegram'a göndermez,
console'a basar.

Amaç: Bot çalışırken (veya duruyorken — sadece DB okur) "şu anda
PnL_DIVERGENCE_ALERT_PCT'yi aşar mıyız?" sorusuna tek komutla cevap.

Kullanım:
    py -3.11 scripts/t11_2_g4_divergence_probe.py
    py -3.11 scripts/t11_2_g4_divergence_probe.py --db data_store/polypaper.db
    py -3.11 scripts/t11_2_g4_divergence_probe.py --window-h 24 --alert-pct 5.0
    py -3.11 scripts/t11_2_g4_divergence_probe.py --json > evidence.json

Exit code:
    0: yeterli data + divergence_pct < alert_pct  (guard OK)
    1: yeterli data + divergence_pct >= alert_pct  (guard ALERT tetiklenirdi)
    2: yetersiz data (paper_trades veya shadow_trades < min_trades)

Evidence format: T11_2_runtime_validation.md G4 kanıt slot'una yapıştırılabilir.

NOT: Bu script DB'yi read-only açar — bot çalışırken güvenli.
NOT: Script, pnl_divergence_job.py'daki canlı SQL mantığıyla 1:1 eş.
     Her değişiklik iki tarafı da günceller (job + probe).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone


def _parse_args():
    p = argparse.ArgumentParser(description="T11.2 G4 PnL divergence standalone probe")
    p.add_argument("--db", default=None,
                   help="DB path (default: $POLYPAPER_DB or data_store/polypaper.db)")
    p.add_argument("--window-h", type=float, default=None,
                   help="Look-back hours (default: $PNL_DIVERGENCE_WINDOW_H or 24)")
    p.add_argument("--alert-pct", type=float, default=None,
                   help="Alert threshold %% (default: $PNL_DIVERGENCE_ALERT_PCT or 5.0)")
    p.add_argument("--min-trades", type=int, default=None,
                   help="Min trades per bucket (default: $PNL_DIVERGENCE_MIN_TRADES or 5)")
    p.add_argument("--json", action="store_true",
                   help="Output structured JSON (for evidence collection)")
    return p.parse_args()


def _resolve_db_path(arg_db: str | None) -> str:
    """Resolve DB path: CLI → env → conventional locations."""
    if arg_db:
        return arg_db
    if os.getenv("POLYPAPER_DB"):
        return os.environ["POLYPAPER_DB"]
    for candidate in ("data_store/polypaper.db", "polypaper.db"):
        if os.path.isfile(candidate):
            return candidate
    # Fallback — let sqlite3 raise a clear error
    return "data_store/polypaper.db"


def _get_env_float(key: str, default: float, cli_val: float | None) -> float:
    if cli_val is not None:
        return cli_val
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_env_int(key: str, default: int, cli_val: int | None) -> int:
    if cli_val is not None:
        return cli_val
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _open_ro(db_path: str) -> sqlite3.Connection:
    """Open DB in read-only mode (URI filename). Safe while bot holds write lock."""
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    # Required for WAL-mode read while writer is active
    conn.execute("PRAGMA query_only=1")
    return conn


def probe(db_path: str, window_h: float, alert_pct: float, min_trades: int) -> dict:
    """Mirror of pnl_divergence_job(): same SQL, same math, no Telegram."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_h)).isoformat()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    conn = _open_ro(db_path)
    try:
        # ═══ Paper PnL (executions) ═══
        cur = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(pnl), 0),
                      COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0)
               FROM executions
               WHERE result IS NOT NULL AND closed_at >= ?""",
            (cutoff,))
        prow = cur.fetchone()
        paper_trades = int(prow[0] or 0)
        paper_pnl = float(prow[1] or 0.0)
        paper_wins = int(prow[2] or 0)
        paper_wr = (paper_wins / paper_trades * 100.0) if paper_trades > 0 else 0.0

        # ═══ Shadow PnL (live_trades.paper_pnl) ═══
        cur = conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(paper_pnl), 0),
                      COALESCE(SUM(CASE WHEN paper_pnl > 0 THEN 1 ELSE 0 END), 0)
               FROM live_trades
               WHERE paper_pnl IS NOT NULL AND settled_at >= ?""",
            (cutoff,))
        srow = cur.fetchone()
        shadow_trades = int(srow[0] or 0)
        shadow_pnl = float(srow[1] or 0.0)
        shadow_wins = int(srow[2] or 0)
        shadow_wr = (shadow_wins / shadow_trades * 100.0) if shadow_trades > 0 else 0.0
    finally:
        conn.close()

    has_enough = paper_trades >= min_trades and shadow_trades >= min_trades
    pnl_delta = abs(shadow_pnl - paper_pnl)
    base_pnl = max(abs(paper_pnl), abs(shadow_pnl), 1.0)
    divergence_pct = (pnl_delta / base_pnl) * 100.0
    wr_delta = abs(shadow_wr - paper_wr)

    if has_enough and (divergence_pct >= alert_pct or wr_delta >= 10.0):
        level = "ALERT_RED" if divergence_pct >= 10.0 else "ALERT_YELLOW"
    elif has_enough:
        level = "OK"
    else:
        level = "INSUFFICIENT"

    return {
        "timestamp_utc": now_str,
        "db_path": db_path,
        "config": {
            "window_h": window_h,
            "alert_pct": alert_pct,
            "min_trades": min_trades,
        },
        "paper": {
            "trades": paper_trades,
            "pnl": round(paper_pnl, 4),
            "wins": paper_wins,
            "wr_pct": round(paper_wr, 2),
        },
        "shadow": {
            "trades": shadow_trades,
            "pnl": round(shadow_pnl, 4),
            "wins": shadow_wins,
            "wr_pct": round(shadow_wr, 2),
        },
        "divergence": {
            "pnl_delta": round(pnl_delta, 4),
            "divergence_pct": round(divergence_pct, 2),
            "wr_delta_pp": round(wr_delta, 2),
        },
        "verdict": level,
        "has_enough_data": has_enough,
    }


def _format_human(r: dict) -> str:
    """Telegram-style formatted output (non-JSON mode)."""
    v = r["verdict"]
    emoji = {"OK": "GREEN", "ALERT_YELLOW": "YELLOW", "ALERT_RED": "RED",
             "INSUFFICIENT": "INSUFFICIENT"}[v]
    lines = [
        f"[T11.2 G4 PnL Divergence Probe] {emoji}",
        f"{'=' * 60}",
        f"Probe time       : {r['timestamp_utc']}",
        f"DB               : {r['db_path']}",
        f"Window           : {r['config']['window_h']}h",
        f"Alert threshold  : {r['config']['alert_pct']}%",
        f"Min trades       : {r['config']['min_trades']}",
        "",
        f"Paper   : {r['paper']['trades']:>4}t | WR {r['paper']['wr_pct']:>5.1f}% | PnL ${r['paper']['pnl']:+.2f}",
        f"Shadow  : {r['shadow']['trades']:>4}t | WR {r['shadow']['wr_pct']:>5.1f}% | PnL ${r['shadow']['pnl']:+.2f}",
        "",
        f"PnL delta        : ${r['divergence']['pnl_delta']:.4f}",
        f"Divergence       : {r['divergence']['divergence_pct']:.2f}%  (threshold: {r['config']['alert_pct']}%)",
        f"WR delta         : {r['divergence']['wr_delta_pp']:.2f}pp   (threshold: 10pp)",
        "",
        f"Verdict          : {v}",
        f"Has enough data  : {r['has_enough_data']}",
    ]
    return "\n".join(lines)


def main() -> int:
    args = _parse_args()

    db_path = _resolve_db_path(args.db)
    window_h = _get_env_float("PNL_DIVERGENCE_WINDOW_H", 24.0, args.window_h)
    alert_pct = _get_env_float("PNL_DIVERGENCE_ALERT_PCT", 5.0, args.alert_pct)
    min_trades = _get_env_int("PNL_DIVERGENCE_MIN_TRADES", 5, args.min_trades)

    if not os.path.isfile(db_path):
        sys.stderr.write(f"ERROR: DB file not found: {db_path}\n")
        return 3

    try:
        result = probe(db_path, window_h, alert_pct, min_trades)
    except sqlite3.Error as e:
        sys.stderr.write(f"ERROR: sqlite: {e}\n")
        return 4

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(_format_human(result))

    # Exit code contract (CI-friendly)
    if not result["has_enough_data"]:
        return 2
    if result["verdict"].startswith("ALERT"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
