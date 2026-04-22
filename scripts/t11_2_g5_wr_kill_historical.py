#!/usr/bin/env python3
"""
T11.2 G5 Rolling WR Kill — Historical Evidence Query
=====================================================

T11_2_runtime_validation.md G5 için otomatize kanıt: geçmişte
`ROLLING_WR_KILL` guard'ının gerçekten ateşlendiğini DB'den doğrular.

20+ trade & < 40% WR state'ini yapay yaratmak yerine, bu guard
üretimde kendini kanıtlamışsa `strategy_changelog` tablosunda
`action='ROLLING_WR_KILL'` satırı vardır. Böyle satır varsa → guard
canlı ortamda tetiklendi + strateji pause'landı → kanıt tamam.

Kullanım:
    py -3.11 scripts/t11_2_g5_wr_kill_historical.py
    py -3.11 scripts/t11_2_g5_wr_kill_historical.py --json > evidence.json
    py -3.11 scripts/t11_2_g5_wr_kill_historical.py --days 30

Exit code:
    0: en az 1 ROLLING_WR_KILL kaydı var (guard tetiklendiği kanıtlı)
    1: hiç kayıt yok — canlı tetikleme gerekir (shadow live'da bekle)
    2: DB bulunamadı / SQL hatası

NOT: Bu script read-only + sadece history okur; hiç değişiklik yapmaz.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone


def _parse_args():
    p = argparse.ArgumentParser(description="T11.2 G5 rolling WR kill history query")
    p.add_argument("--db", default=None,
                   help="DB path (default: $POLYPAPER_DB or data_store/polypaper.db)")
    p.add_argument("--days", type=int, default=0,
                   help="Look-back days (0 = all history, default: 0)")
    p.add_argument("--limit", type=int, default=50,
                   help="Max rows to show (default: 50)")
    p.add_argument("--json", action="store_true",
                   help="Output structured JSON (for evidence collection)")
    return p.parse_args()


def _resolve_db_path(arg_db: str | None) -> str:
    if arg_db:
        return arg_db
    if os.getenv("POLYPAPER_DB"):
        return os.environ["POLYPAPER_DB"]
    for candidate in ("data_store/polypaper.db", "polypaper.db"):
        if os.path.isfile(candidate):
            return candidate
    return "data_store/polypaper.db"


def _open_ro(db_path: str) -> sqlite3.Connection:
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.execute("PRAGMA query_only=1")
    return conn


def query(db_path: str, days: int, limit: int) -> dict:
    """Query strategy_changelog for ROLLING_WR_KILL entries."""
    conn = _open_ro(db_path)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    try:
        # Total count (all time)
        total = conn.execute(
            "SELECT COUNT(*) FROM strategy_changelog WHERE action = ?",
            ("ROLLING_WR_KILL",)).fetchone()[0]

        # Window filter
        if days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            where_clause = "WHERE action = ? AND created_at >= ?"
            params = ("ROLLING_WR_KILL", cutoff)
        else:
            where_clause = "WHERE action = ?"
            params = ("ROLLING_WR_KILL",)

        # Distinct strategies that ever got killed
        distinct_strats = conn.execute(
            f"SELECT COUNT(DISTINCT strategy_id) FROM strategy_changelog {where_clause}",
            params).fetchone()[0]

        # Fetch recent entries
        rows = conn.execute(
            f"""SELECT created_at, strategy_id, strategy_label, reason, wr_at_time,
                       pnl_at_time, trades_at_time
                FROM strategy_changelog
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ?""",
            (*params, limit)).fetchall()

        entries = []
        for r in rows:
            entries.append({
                "created_at": r[0],
                "strategy_id": (r[1] or "")[:12],
                "strategy_label": r[2] or "",
                "reason": r[3] or "",
                "wr_at_time": r[4],
                "pnl_at_time": r[5],
                "trades_at_time": r[6],
            })
    finally:
        conn.close()

    return {
        "probe_time_utc": now_str,
        "db_path": db_path,
        "days_filter": days,
        "total_ever": total,
        "window_count": len(entries),
        "distinct_strategies": distinct_strats,
        "entries": entries,
        "verdict": "GUARD_HAS_FIRED" if total > 0 else "NEVER_FIRED",
    }


def _format_human(r: dict) -> str:
    days_str = "all history" if r["days_filter"] == 0 else f"last {r['days_filter']} days"
    lines = [
        f"[T11.2 G5 Rolling WR Kill — Historical Evidence]",
        f"{'=' * 60}",
        f"Probe time       : {r['probe_time_utc']}",
        f"DB               : {r['db_path']}",
        f"Window           : {days_str}",
        f"Total ROLLING_WR_KILL (ever)    : {r['total_ever']}",
        f"In window count                 : {r['window_count']}",
        f"Distinct strategies ever killed : {r['distinct_strategies']}",
        f"Verdict          : {r['verdict']}",
        "",
    ]
    if not r["entries"]:
        lines.append("(no rows) → G5 guard canlı ortamda henüz tetiklenmedi.")
        lines.append("  Anlam: 20+ trade × < 40% WR state oluşmadı. Kanıt için")
        lines.append("  /env_toggle ROLLING_WR_KILL 60 yapıp bir stratejinin WR'si")
        lines.append("  60'ın altına düştüğünde guard'ı canlı izle (T11_2 G5 Seçenek B).")
    else:
        lines.append(f"{'created_at (UTC)':<22} {'strat':<14} {'wr%':>6} {'pnl':>8} {'n':>4}  reason")
        lines.append("-" * 100)
        for e in r["entries"]:
            wr = f"{e['wr_at_time']:.1f}" if e["wr_at_time"] is not None else "N/A"
            pnl = f"{e['pnl_at_time']:+.2f}" if e["pnl_at_time"] is not None else "N/A"
            nt = str(e["trades_at_time"]) if e["trades_at_time"] is not None else "N/A"
            reason = (e["reason"] or "")[:60]
            lines.append(
                f"{e['created_at'][:22]:<22} {e['strategy_id']:<14} {wr:>6} {pnl:>8} {nt:>4}  {reason}"
            )
    return "\n".join(lines)


def main() -> int:
    args = _parse_args()
    db_path = _resolve_db_path(args.db)

    if not os.path.isfile(db_path):
        sys.stderr.write(f"ERROR: DB file not found: {db_path}\n")
        return 2

    try:
        result = query(db_path, args.days, args.limit)
    except sqlite3.Error as e:
        sys.stderr.write(f"ERROR: sqlite: {e}\n")
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(_format_human(result))

    return 0 if result["total_ever"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
