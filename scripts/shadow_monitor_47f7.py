"""
Phase 47f.7 — Live Shadow Monitor for late_convergence + Becker decision-mode.

What it does (read-only, no side effects):
  1. Tails `data_store/polypaper.log` (+ rotated backups) and counts the
     decision-mode footprint:
       * "becker flip:"           -> flip events
       * "becker veto:"           -> veto events
       * "becker[p+k]" / "becker[p]" -> boost evals
       * "late_conv ..."          -> strategy signal log lines
     Splits the log into "before" / "after" the most recent
     `BECKER_DECISION_MODE` flip (heuristic: scans for restart banners).

  2. Reads `data_store/polypaper.db` for late_convergence executions
     (`strategies.strategy_type='late_convergence'`), grouped by
     `created_at < cutoff` vs `>= cutoff`. Reports per-bucket: trades, WR,
     PnL, avg PnL/trade.

  3. Prints an A/B style report on stdout. With `--telegram` it also
     POSTs the same report to Telegram via the bot's chat
     (uses `TELEGRAM_BOT_TOKEN` + `ADMIN_CHAT_ID` from .env). HTML
     parse_mode (project rule).

Usage (Windows):
  py -3.11 -u scripts\shadow_monitor_47f7.py [--cutoff "2026-04-09 12:00"]
                                              [--strategy late_convergence]
                                              [--telegram]
                                              [--log-dir data_store]

Defaults:
  --cutoff   = the timestamp of the most recent "Phase 47f.7" log line
               that mentions decision-mode != boost; falls back to "now -1d"
  --strategy = late_convergence
  --log-dir  = data_store

Exit codes:
  0  report generated
  1  DB or log access failure
  2  no late_convergence rows found at all
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR_DEFAULT = ROOT / "data_store"
LOG_NAME = "polypaper.log"

# Log line patterns
RE_FLIP = re.compile(r"becker flip:")
RE_VETO = re.compile(r"becker veto:")
RE_BOOST = re.compile(r"becker\[(p\+k|p)\]")
RE_LATE = re.compile(r"late_conv ")
RE_RESTART = re.compile(r"PolyPaper Bot v\S+ - Mainnet Ready")
# 2026-04-09 12:34:56 [name] LEVEL: msg
RE_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})")


def p(msg: str = "") -> None:
    print(msg, flush=True)


def _iter_log_files(log_dir: Path) -> list[Path]:
    """Return main log + rotated backups in chronological order (oldest first)."""
    base = log_dir / LOG_NAME
    files: list[Path] = []
    # Rotated backups: polypaper.log.3, .2, .1 (oldest -> newest)
    for i in range(3, 0, -1):
        fp = log_dir / f"{LOG_NAME}.{i}"
        if fp.exists():
            files.append(fp)
    if base.exists():
        files.append(base)
    return files


def _read_lines(files: Iterable[Path]):
    for fp in files:
        try:
            with fp.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    yield line.rstrip("\n")
        except OSError as e:
            p(f"[warn] could not read {fp}: {e}")


def _parse_ts(line: str) -> Optional[datetime]:
    m = RE_TS.match(line)
    if not m:
        return None
    raw = m.group(1).replace("T", " ")
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def scan_log(log_dir: Path, cutoff: datetime) -> dict:
    counts = {
        "before": {"flip": 0, "veto": 0, "boost": 0, "late_sig": 0, "lines": 0},
        "after":  {"flip": 0, "veto": 0, "boost": 0, "late_sig": 0, "lines": 0},
        "first_ts": None, "last_ts": None,
        "restarts_after_cutoff": 0,
    }
    for line in _read_lines(_iter_log_files(log_dir)):
        ts = _parse_ts(line)
        bucket = "after" if (ts and ts >= cutoff) else "before"
        b = counts[bucket]
        b["lines"] += 1
        if ts:
            if counts["first_ts"] is None or ts < counts["first_ts"]:
                counts["first_ts"] = ts
            if counts["last_ts"] is None or ts > counts["last_ts"]:
                counts["last_ts"] = ts
            if bucket == "after" and RE_RESTART.search(line):
                counts["restarts_after_cutoff"] += 1
        if RE_FLIP.search(line):
            b["flip"] += 1
        if RE_VETO.search(line):
            b["veto"] += 1
        if RE_BOOST.search(line):
            b["boost"] += 1
        if RE_LATE.search(line):
            b["late_sig"] += 1
    return counts


def query_executions(db_path: Path, strategy_type: str,
                     cutoff: datetime) -> dict:
    """Return {before:{...}, after:{...}} stats for executions on `strategy_type`."""
    if not db_path.exists():
        raise FileNotFoundError(f"db not found: {db_path}")
    # Strategy 0: normal sqlite3.connect (participates in WAL like any
    # reader). Works when the bot is on the same filesystem with proper
    # POSIX locking. This is the FAST and CORRECT path.
    con = None
    try:
        con = sqlite3.connect(str(db_path), timeout=30.0)
        con.execute("PRAGMA query_only=1").fetchall()
        con.execute("SELECT 1 FROM strategies LIMIT 1").fetchall()
    except Exception:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
            con = None
    # Strategy 1: Try mode=ro&nolock=1 URI (filesystem with no fcntl).
    if con is None:
        uri = f"file:{db_path.as_posix()}?mode=ro&nolock=1"
        try:
            con = sqlite3.connect(uri, uri=True)
            con.execute("SELECT 1 FROM strategies LIMIT 1").fetchall()
        except Exception:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass
                con = None
    # Strategy 2: raw byte-copy of the .db ONLY (no WAL/SHM) to local
    # sandbox /tmp, then open with immutable=1. immutable=1 tells SQLite
    # to ignore any WAL/journal entirely, which avoids the "malformed
    # image" race that happens when we copy a WAL midstream. Loses the
    # most recent uncommitted writes (those still in WAL) but the bot
    # checkpoints regularly so the snapshot is at most a few minutes
    # behind. Use shutil.copyfileobj in chunks to avoid loading 1.6GB
    # into memory.
    if con is None:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="poly_mon_"))
        tmp_db = tmp / db_path.name
        try:
            with open(db_path, "rb") as fsrc, open(tmp_db, "wb") as fdst:
                while True:
                    buf = fsrc.read(1024 * 1024)  # 1 MiB chunks
                    if not buf:
                        break
                    fdst.write(buf)
        except Exception as _ce:
            raise RuntimeError(
                f"db raw-copy failed: {_ce}"
            ) from _ce
        try:
            uri2 = f"file:{tmp_db.as_posix()}?mode=ro&immutable=1"
            con = sqlite3.connect(uri2, uri=True)
            # Sanity check: hit a known table
            con.execute("SELECT 1 FROM strategies LIMIT 1").fetchall()
        except Exception as _oe:
            raise RuntimeError(
                f"db reopen after raw-copy failed: {_oe}. Try pausing "
                f"the bot briefly so the WAL can checkpoint."
            ) from _oe
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT id FROM strategies WHERE strategy_type = ?",
            (strategy_type,),
        )
        sids = [r["id"] for r in cur.fetchall()]
        if not sids:
            return {"strategy_ids": [], "before": None, "after": None}

        placeholders = ",".join("?" for _ in sids)
        cur.execute(
            f"""
            SELECT created_at, status, pnl, payout, trade_amount, direction, result
            FROM executions
            WHERE strategy_id IN ({placeholders})
              AND status IN ('closed', 'settled', 'won', 'lost')
            """,
            sids,
        )
        rows = cur.fetchall()
        return {
            "strategy_ids": sids,
            "before": _bucket_stats([r for r in rows if _row_before(r, cutoff)]),
            "after":  _bucket_stats([r for r in rows if not _row_before(r, cutoff)]),
        }
    finally:
        con.close()


def _row_before(row: sqlite3.Row, cutoff: datetime) -> bool:
    raw = (row["created_at"] or "").replace("T", " ").split(".")[0]
    try:
        ts = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True  # ambiguous → bucket as 'before' so it doesn't pollute 'after'
    return ts < cutoff


def _bucket_stats(rows: list[sqlite3.Row]) -> dict:
    if not rows:
        return {"trades": 0, "wins": 0, "losses": 0, "wr": 0.0,
                "pnl": 0.0, "avg_pnl": 0.0}
    wins = sum(1 for r in rows if (r["pnl"] or 0) > 0)
    losses = sum(1 for r in rows if (r["pnl"] or 0) < 0)
    pnl = sum(float(r["pnl"] or 0) for r in rows)
    n = len(rows)
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "wr": (wins / n * 100.0) if n else 0.0,
        "pnl": pnl,
        "avg_pnl": (pnl / n) if n else 0.0,
    }


def auto_cutoff(log_dir: Path) -> datetime:
    """Heuristic: most recent restart line that follows a `BECKER_DECISION_MODE`
    line in .env or env-dump. Fallback: now - 1 day."""
    fallback = datetime.now() - timedelta(days=1)
    last_restart = None
    for line in _read_lines(_iter_log_files(log_dir)):
        if RE_RESTART.search(line):
            ts = _parse_ts(line)
            if ts:
                last_restart = ts
    return last_restart or fallback


def render_report(cutoff: datetime, log_counts: dict, db_stats: dict,
                  strategy: str) -> str:
    a = db_stats.get("before") or {"trades": 0, "wins": 0, "losses": 0,
                                   "wr": 0.0, "pnl": 0.0, "avg_pnl": 0.0}
    b = db_stats.get("after") or {"trades": 0, "wins": 0, "losses": 0,
                                  "wr": 0.0, "pnl": 0.0, "avg_pnl": 0.0}
    lc = log_counts
    lines = []
    lines.append(f"<b>Phase 47f.7 Shadow Monitor</b> — <code>{strategy}</code>")
    lines.append(f"cutoff: <code>{cutoff:%Y-%m-%d %H:%M}</code>")
    if lc.get("first_ts") and lc.get("last_ts"):
        lines.append(f"log span: <code>{lc['first_ts']:%m-%d %H:%M}</code> → "
                     f"<code>{lc['last_ts']:%m-%d %H:%M}</code> "
                     f"(restarts after cutoff: {lc['restarts_after_cutoff']})")
    lines.append("")
    lines.append("<b>Decision-mode footprint (log):</b>")
    lines.append(f"  flip events:  before=<b>{lc['before']['flip']}</b>  "
                 f"after=<b>{lc['after']['flip']}</b>")
    lines.append(f"  veto events:  before=<b>{lc['before']['veto']}</b>  "
                 f"after=<b>{lc['after']['veto']}</b>")
    lines.append(f"  boost evals:  before={lc['before']['boost']}  "
                 f"after={lc['after']['boost']}")
    lines.append(f"  late_conv signals: before={lc['before']['late_sig']}  "
                 f"after={lc['after']['late_sig']}")
    lines.append("")
    lines.append("<b>Executions A/B (DB):</b>")
    lines.append(f"  A (before): {a['trades']}t  W={a['wins']}/L={a['losses']}  "
                 f"WR={a['wr']:.1f}%  PnL=<b>{a['pnl']:+.2f}</b>  "
                 f"avg={a['avg_pnl']:+.4f}")
    lines.append(f"  B (after):  {b['trades']}t  W={b['wins']}/L={b['losses']}  "
                 f"WR={b['wr']:.1f}%  PnL=<b>{b['pnl']:+.2f}</b>  "
                 f"avg={b['avg_pnl']:+.4f}")
    if a["trades"] and b["trades"]:
        d_pnl_per = b["avg_pnl"] - a["avg_pnl"]
        d_wr = b["wr"] - a["wr"]
        lines.append(f"  delta:      avg PnL/trade <b>{d_pnl_per:+.4f}</b>  "
                     f"WR <b>{d_wr:+.1f}pp</b>")
    elif b["trades"] == 0:
        lines.append("  (no executions in 'after' bucket yet — run longer)")
    lines.append("")
    lines.append("<b>Promotion gate:</b> ≥50 trades after, WR Δ ±5pp, PnL Δ ≥ 0.")
    return "\n".join(lines)


async def post_to_telegram(report_html: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = (
        os.getenv("ADMIN_CHAT_ID")
        or os.getenv("TELEGRAM_ADMIN_CHAT_ID")
        or os.getenv("ADMIN_TELEGRAM_ID")
        or os.getenv("TELEGRAM_CHAT_ID")
    )
    if not token or not chat_id:
        p("[warn] TELEGRAM_BOT_TOKEN or ADMIN_CHAT_ID/ADMIN_TELEGRAM_ID missing — skip telegram post")
        return False
    try:
        import httpx
    except ImportError:
        p("[warn] httpx not installed — skip telegram post")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": report_html, "parse_mode": "HTML"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.post(url, json=payload)
            if r.status_code == 200:
                return True
            p(f"[warn] telegram post failed: HTTP {r.status_code}: {r.text[:200]}")
            return False
    except Exception as e:
        p(f"[warn] telegram post exception: {e}")
        return False


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default=None,
                    help='ISO timestamp like "2026-04-09 12:00". Default: most recent restart')
    ap.add_argument("--strategy", default="late_convergence")
    ap.add_argument("--log-dir", default=str(LOG_DIR_DEFAULT))
    ap.add_argument("--db", default=str(ROOT / "data_store" / "polypaper.db"))
    ap.add_argument("--telegram", action="store_true")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    log_dir = Path(args.log_dir)
    db_path = Path(args.db)

    if args.cutoff:
        try:
            cutoff = datetime.strptime(args.cutoff, "%Y-%m-%d %H:%M")
        except ValueError:
            p(f"[FATAL] bad --cutoff format: {args.cutoff} (need 'YYYY-MM-DD HH:MM')")
            return 1
    else:
        cutoff = auto_cutoff(log_dir)
        p(f"[info] auto-cutoff = {cutoff:%Y-%m-%d %H:%M} (most recent restart)")

    try:
        log_counts = scan_log(log_dir, cutoff)
    except Exception as e:
        p(f"[FATAL] log scan failed: {e}")
        return 1

    try:
        db_stats = query_executions(db_path, args.strategy, cutoff)
    except FileNotFoundError as e:
        p(f"[FATAL] {e}")
        return 1
    except sqlite3.Error as e:
        p(f"[FATAL] db query failed: {e}")
        return 1

    if not db_stats["strategy_ids"]:
        p(f"[warn] no strategies with strategy_type={args.strategy} found")
        if log_counts["after"]["flip"] == 0 and log_counts["after"]["veto"] == 0:
            p("[warn] also zero flip/veto events in log after cutoff")
            return 2

    report = render_report(cutoff, log_counts, db_stats, args.strategy)
    # Plain-text dump (strip HTML)
    plain = re.sub(r"<[^>]+>", "", report)
    p("=" * 80)
    p(plain)
    p("=" * 80)

    if args.telegram:
        ok = asyncio.run(post_to_telegram(report))
        p(f"[info] telegram post {'OK' if ok else 'SKIPPED/FAILED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
