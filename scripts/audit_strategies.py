"""
P1-04-a (2026-05-09) Strategy audit (read-only).
P1-04 re-audit prep (2026-05-11):
  - --days N now actually filters executions by closed_at >= NOW - N days
    (was parsed but ignored). Default 30.
  - --since-prev PATH compares current audit against a previous .md report
    and prints a "regressed/improved/dropped/new" delta section.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data_store" / "polypaper.db"

DEAD_AFTER_DAYS = 7
N_EXPL_TO_EVAL = 20
N_EVAL_TO_PROV = 50
WR_PROVEN_MIN = 0.55


def _iso_to_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.rstrip("Z")).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _classify(n, wr, pnl, age_days, status):
    if n == 0:
        return "no_trades", "ARCHIVE"
    if age_days is not None and age_days > DEAD_AFTER_DAYS:
        return "idle", "ARCHIVE"
    if n < N_EXPL_TO_EVAL:
        if status != "active":
            return "exploration", "ARCHIVE"
        return "exploration", "WATCH"
    if n < N_EVAL_TO_PROV:
        return "evaluation", "WATCH"
    if wr >= WR_PROVEN_MIN and pnl > 0:
        return "proven", "KEEP"
    return "regression", "ARCHIVE"


def _fmt_pct(x):
    return f"{x*100:.1f}%" if x is not None else "?"


def _fmt_pnl(x):
    return f"{x:+.2f}" if x is not None else "?"


def _parse_prev_audit(path: Path) -> dict[str, str]:
    r"""Extract {strategy_id_12char: recommendation} from a prior audit .md.

    Looks for table rows like `| 1 | \`abc123def456\` | Label | ...` in
    each section (KEEP/WATCH/ARCHIVE). Section header determines the rec.
    Returns short-id -> recommendation map.
    """
    out: dict[str, str] = {}
    if not path.exists():
        return out
    section = None
    section_map = {
        "KEEP Proven": "KEEP",
        "WATCH Exploration": "WATCH",
        "ARCHIVE Pruning": "ARCHIVE",
    }
    id_row_re = re.compile(r"\|\s*(?:\d+\s*\|\s*)?`([0-9a-f]{6,32})`")
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if s.startswith("## "):
            section = None
            for key, rec in section_map.items():
                if key in s:
                    section = rec
                    break
            continue
        if not section:
            continue
        m = id_row_re.search(line)
        if m:
            sid = m.group(1)[:12]
            out[sid] = section
    return out


def main():
    p = argparse.ArgumentParser(prog="audit_strategies")
    p.add_argument("--output")
    p.add_argument(
        "--days",
        type=int,
        default=30,
        help="Lookback window in days for execution stats (closed_at filter).",
    )
    p.add_argument(
        "--since-prev",
        type=str,
        default=None,
        help="Path to a previous strategy_audit_*.md to diff against (delta section).",
    )
    args = p.parse_args()

    if not DB_PATH.exists():
        print(f"[ERROR] DB not found: {DB_PATH}", file=sys.stderr)
        return 1

    # SQLite online backup API: consistent snapshot under WAL contention.
    import os
    import tempfile

    snap_path = Path(tempfile.gettempdir()) / f"polypaper_ro_{os.getpid()}.db"
    src_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30.0)
    src_conn.execute("PRAGMA busy_timeout=30000")
    snap_conn = sqlite3.connect(str(snap_path), timeout=30.0)
    src_conn.backup(snap_conn, pages=200, sleep=0.05)
    snap_conn.close()
    src_conn.close()
    conn = sqlite3.connect(f"file:{snap_path}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row

    cur = conn.execute("""SELECT id, label, asset, timeframe, direction, status,
        strategy_type, deploy_stage, odds_threshold, trade_amount,
        started_at, created_at, updated_at FROM strategies ORDER BY created_at DESC""")
    strategies = [dict(r) for r in cur.fetchall()]

    stats = {}
    # P1-04 re-audit fix (2026-05-11): apply --days lookback filter. Older
    # behavior summed across the entire history (silently ignoring --days).
    since_iso = (datetime.now(UTC) - timedelta(days=int(args.days))).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    cur = conn.execute(
        """SELECT strategy_id,
        COUNT(*) AS n,
        SUM(CASE WHEN result='won' THEN 1 ELSE 0 END) AS wins,
        SUM(pnl) AS pnl_sum,
        MAX(closed_at) AS last_closed
        FROM executions
        WHERE strategy_id IS NOT NULL
          AND status='claimed'
          AND closed_at >= ?
        GROUP BY strategy_id""",
        (since_iso,),
    )
    for r in cur.fetchall():
        sid = r["strategy_id"]
        n = r["n"] or 0
        wins = r["wins"] or 0
        wr = wins / n if n > 0 else 0.0
        stats[sid] = {
            "n": n,
            "wins": wins,
            "wr": wr,
            "pnl_sum": r["pnl_sum"] or 0.0,
            "last_closed": r["last_closed"],
        }
    conn.close()

    now = datetime.now(UTC)
    rows = []
    for s in strategies:
        sid = s["id"]
        st = stats.get(sid, {"n": 0, "wins": 0, "wr": 0.0, "pnl_sum": 0.0, "last_closed": None})
        last_dt = _iso_to_dt(st["last_closed"])
        age_days = (now - last_dt).total_seconds() / 86400 if last_dt else None
        lc, rec = _classify(st["n"], st["wr"], st["pnl_sum"], age_days, s["status"])
        rows.append({**s, **st, "age_days": age_days, "lifecycle": lc, "recommendation": rec})

    total = len(rows)
    by_rec = {"KEEP": [], "WATCH": [], "ARCHIVE": []}
    for r in rows:
        by_rec[r["recommendation"]].append(r)
    by_lc = {}
    for r in rows:
        by_lc[r["lifecycle"]] = by_lc.get(r["lifecycle"], 0) + 1
    total_trades = sum(r["n"] for r in rows)
    total_pnl = sum(r["pnl_sum"] for r in rows)

    L = []
    L.append("# Strategy Audit Report (P1-04-a)")
    L.append("")
    L.append(f"**Generated:** {now.strftime('%Y-%m-%d %H:%M UTC')}")
    L.append(f"**Lookback:** last {args.days} days (executions.closed_at >= {since_iso})")
    L.append(f"**Total strategies:** {total}")
    L.append(f"**Total trades (claimed):** {total_trades}")
    L.append(f"**Total realized PnL:** {total_pnl:+.2f}")
    L.append("")
    L.append("## Recommendation Summary")
    L.append("")
    L.append(
        f"- **KEEP** ({len(by_rec['KEEP'])}): proven (n>={N_EVAL_TO_PROV}, WR>={WR_PROVEN_MIN*100:.0f}%, PnL>0)"
    )
    L.append(f"- **WATCH** ({len(by_rec['WATCH'])}): exploration / evaluation")
    L.append(
        f"- **ARCHIVE** ({len(by_rec['ARCHIVE'])}): no-trades / idle ({DEAD_AFTER_DAYS}+d) / regression"
    )
    L.append("")
    L.append("## Lifecycle Distribution")
    L.append("")
    for lc, n in sorted(by_lc.items()):
        pct = 100 * n / total if total else 0
        L.append(f"- `{lc}`: {n} ({pct:.0f}%)")
    L.append("")

    L.append("## KEEP Proven Strategies")
    L.append("")
    if not by_rec["KEEP"]:
        L.append("> No strategy meets PROVEN criteria yet.")
    else:
        L.append("| Rank | ID | Label | Asset/TF | n | WR | PnL | Last | Status |")
        L.append("|--:|---|---|---|--:|--:|--:|---|---|")
        keep_sorted = sorted(
            by_rec["KEEP"], key=lambda r: (r["n"], r["wr"], r["pnl_sum"]), reverse=True
        )
        for i, r in enumerate(keep_sorted, 1):
            sid_short = (r["id"] or "?")[:12]
            label = (r["label"] or "?")[:25]
            atf = f"{r['asset']}/{r['timeframe']}"
            last = (r["last_closed"] or "")[:16]
            L.append(
                f"| {i} | `{sid_short}` | {label} | {atf} | {r['n']} | {_fmt_pct(r['wr'])} | {_fmt_pnl(r['pnl_sum'])} | {last} | {r['status']} |"
            )
    L.append("")

    L.append("## WATCH Exploration / Evaluation")
    L.append("")
    if not by_rec["WATCH"]:
        L.append("> None.")
    else:
        watch_sorted = sorted(by_rec["WATCH"], key=lambda r: (r["n"], r["wr"]), reverse=True)
        L.append("| ID | Label | Asset/TF | n | WR | PnL | Last | Phase |")
        L.append("|---|---|---|--:|--:|--:|---|---|")
        for r in watch_sorted[:20]:
            sid_short = (r["id"] or "?")[:12]
            label = (r["label"] or "?")[:25]
            atf = f"{r['asset']}/{r['timeframe']}"
            last = (r["last_closed"] or "-")[:16]
            L.append(
                f"| `{sid_short}` | {label} | {atf} | {r['n']} | {_fmt_pct(r['wr'])} | {_fmt_pnl(r['pnl_sum'])} | {last} | {r['lifecycle']} |"
            )
        if len(by_rec["WATCH"]) > 20:
            L.append(f"\n*(+ {len(by_rec['WATCH']) - 20} more)*")
    L.append("")

    L.append("## ARCHIVE Pruning Candidates")
    L.append("")
    if not by_rec["ARCHIVE"]:
        L.append("> None.")
    else:
        arch_sorted = sorted(by_rec["ARCHIVE"], key=lambda r: (r["lifecycle"], -r["n"]))
        L.append("| ID | Label | Asset/TF | n | WR | PnL | Last | Why |")
        L.append("|---|---|---|--:|--:|--:|---|---|")
        for r in arch_sorted[:30]:
            sid_short = (r["id"] or "?")[:12]
            label = (r["label"] or "?")[:25]
            atf = f"{r['asset']}/{r['timeframe']}"
            last = (r["last_closed"] or "-")[:16]
            why = r["lifecycle"]
            if why == "idle" and r["age_days"]:
                why = f"idle ({int(r['age_days'])}d)"
            L.append(
                f"| `{sid_short}` | {label} | {atf} | {r['n']} | {_fmt_pct(r['wr'])} | {_fmt_pnl(r['pnl_sum'])} | {last} | {why} |"
            )
        if len(by_rec["ARCHIVE"]) > 30:
            L.append(f"\n*(+ {len(by_rec['ARCHIVE']) - 30} more candidates)*")
    L.append("")
    L.append("## Pruning Decision Template")
    L.append("")
    L.append("- **3 strategies to KEEP active** (Heddas selects from KEEP table above)")
    L.append(f"- **{len(by_rec['ARCHIVE'])} strategies to ARCHIVE** (status='stopped', label kept)")
    L.append(f"- **{len(by_rec['WATCH'])} strategies to keep WATCHING** (no change)")
    L.append("")
    L.append("Live whitelist (LIVE_STRATEGIES) untouched paper noise only.")
    L.append("")
    L.append("Next: P1-04-b (Heddas confirms) -> P1-04-c (idempotent stop migration).")

    # P1-04 re-audit (2026-05-11): diff against previous audit if --since-prev.
    if args.since_prev:
        prev_path = Path(args.since_prev)
        prev_map = _parse_prev_audit(prev_path)
        L.append("")
        L.append("## Delta vs Previous Audit")
        L.append("")
        if not prev_map:
            L.append(f"> Could not parse previous audit at `{prev_path}` (no rows found).")
        else:
            L.append(f"**Compared with:** `{prev_path.name}` ({len(prev_map)} strategies)")
            L.append("")
            cur_map = {(r["id"] or "")[:12]: r["recommendation"] for r in rows}

            regressed = []  # KEEP/WATCH -> ARCHIVE
            improved = []  # ARCHIVE -> KEEP/WATCH
            kept_archive = []  # ARCHIVE -> ARCHIVE (still bad)
            new_ids = []  # in cur, not in prev
            dropped_ids = []  # in prev, not in cur

            for sid12, rec_now in cur_map.items():
                rec_prev = prev_map.get(sid12)
                if rec_prev is None:
                    new_ids.append((sid12, rec_now))
                elif rec_prev != rec_now:
                    if rec_now == "ARCHIVE" and rec_prev in {"KEEP", "WATCH"}:
                        regressed.append((sid12, rec_prev, rec_now))
                    elif rec_prev == "ARCHIVE" and rec_now in {"KEEP", "WATCH"}:
                        improved.append((sid12, rec_prev, rec_now))
                elif rec_now == "ARCHIVE":
                    kept_archive.append(sid12)
            for sid12 in prev_map.keys():
                if sid12 not in cur_map:
                    dropped_ids.append((sid12, prev_map[sid12]))

            L.append(f"- **Regressed** (KEEP/WATCH -> ARCHIVE): {len(regressed)}")
            L.append(f"- **Improved** (ARCHIVE -> KEEP/WATCH): {len(improved)}")
            L.append(f"- **Still ARCHIVE both runs:** {len(kept_archive)}")
            L.append(f"- **New strategies:** {len(new_ids)}")
            L.append(f"- **Dropped (no longer in DB):** {len(dropped_ids)}")
            L.append("")
            if regressed:
                L.append("### Regressed (need attention)")
                L.append("")
                L.append("| ID | Prev | Now |")
                L.append("|---|---|---|")
                for sid12, prev_rec, now_rec in regressed[:20]:
                    L.append(f"| `{sid12}` | {prev_rec} | {now_rec} |")
                L.append("")
            if improved:
                L.append("### Improved")
                L.append("")
                L.append("| ID | Prev | Now |")
                L.append("|---|---|---|")
                for sid12, prev_rec, now_rec in improved[:20]:
                    L.append(f"| `{sid12}` | {prev_rec} | {now_rec} |")
                L.append("")

    md = "\n".join(L) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"[audit] wrote {out}")
    else:
        out = (
            REPO_ROOT
            / "data_store"
            / "audits"
            / f"strategy_audit_{now.strftime('%Y%m%dT%H%M%SZ')}.md"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"[audit] wrote {out}")
        print()
        print(f"  total={total} trades={total_trades} pnl={total_pnl:+.2f}")
        print(
            f"  KEEP={len(by_rec['KEEP'])} WATCH={len(by_rec['WATCH'])} ARCHIVE={len(by_rec['ARCHIVE'])}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
