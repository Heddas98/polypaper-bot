"""
P0-07 (2026-05-09) — Reference price feed gerceklik audit'i.
==============================================================

Polymarket binary Up/Down market'lerinin resolution oracle'i Binance spot
price (kline close at boundary moment). Bu script:

  1. --backfill : historical executions tablosundan settled trade'leri tara,
     external_prices'tan ±5s window'da bot'un local feed degerlerini tut,
     reference_price_audit'e satir ekle (INSERT OR IGNORE; live settle hook
     P0-07-b'nin yazdigi satirlari overwrite etmez).

  2. --fetch-references : Binance public klines API'dan official kline close
     fiyatini cek, audit rows'da official_resolution_price + dev_*_bps
     kolonlarini doldur, data_quality 'missing_resolution' -> 'ok'.

  3. --report : son N gunluk markdown rapor:
        - Per (asset, tf, source) sample count + mean/median/p95/p99 dev_bps
        - Worst 10 deviation orneki + Polymarket URL
        - |mean_bps| > 5 -> sistemik bias alarm
        - Per-asset histogram

  4. --all : 1+2+3 sirayla.

Usage:
  py -3.11 scripts/audit_reference_price.py --backfill --days 7
  py -3.11 scripts/audit_reference_price.py --fetch-references
  py -3.11 scripts/audit_reference_price.py --report --days 7 \\
      --output data_store/audits/ref_audit_$(date +%Y%m%d).md
  py -3.11 scripts/audit_reference_price.py --all --days 7

Bot durdurulmasina gerek yok — sadece audit tablo + external_prices'tan okur,
ana DB schema'sini bozmaz.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data_store" / "polypaper.db"

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# Polymarket boundary -> Binance kline interval mapping
TF_TO_BINANCE_INTERVAL = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "24h": "1d",
}


def _iso_to_ms(iso_str: str) -> int | None:
    if not iso_str:
        return None
    try:
        s = iso_str.rstrip("Z")
        dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# -------------------------------------------------------------
# Backfill
# -------------------------------------------------------------


def cmd_backfill(args: argparse.Namespace) -> int:
    """Walk executions, insert audit rows where missing.

    Backfill is idempotent thanks to INSERT OR IGNORE on the (condition_id,
    settle_ts_ms) PK. Live settle hook rows are preserved untouched.
    """
    cutoff_ms = int((time.time() - args.days * 86400) * 1000)
    print(f"[backfill] window: last {args.days} days (cutoff_ms={cutoff_ms})")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Pull settled trades — closed_at populated, status='claimed'.
    cur = conn.execute(
        """SELECT id, event_slug, market_token_id, direction, result,
                  closed_at, payout, pnl
           FROM executions
           WHERE status = 'claimed'
             AND closed_at IS NOT NULL
             AND event_slug IS NOT NULL""")
    rows = cur.fetchall()
    print(f"[backfill] {len(rows)} closed executions in DB")

    # Group by (slug, settle_ts_ms) — one audit row per market boundary
    seen_keys: set[tuple[str, int]] = set()
    inserted = 0
    skipped = 0
    no_ts = 0

    for r in rows:
        slug = r["event_slug"]
        settle_ts_ms = _iso_to_ms(r["closed_at"])
        if settle_ts_ms is None:
            no_ts += 1
            continue
        if settle_ts_ms < cutoff_ms:
            continue
        key = (slug, settle_ts_ms)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        # asset + tf inference (slug_utils available on PYTHONPATH)
        try:
            sys.path.insert(0, str(REPO_ROOT))
            from core.slug_utils import (
                infer_asset_from_slug, infer_tf_from_slug)
            asset = infer_asset_from_slug(slug) or ""
            tf = infer_tf_from_slug(slug) or ""
        except ImportError:
            asset = tf = ""

        symbol = f"{asset}USD" if asset else None

        # Look up external_prices ±5s
        bot_ws = bot_rest = bot_cl = None
        if symbol:
            ext_cur = conn.execute(
                """SELECT source, price FROM external_prices
                   WHERE symbol = ? AND ts_ms BETWEEN ? AND ?
                   ORDER BY ABS(ts_ms - ?) ASC""",
                (symbol, settle_ts_ms - 5000, settle_ts_ms + 5000,
                 settle_ts_ms))
            seen_src = set()
            for src, price in ext_cur.fetchall():
                if src in seen_src:
                    continue
                seen_src.add(src)
                if src == "binance_spot_ws":
                    bot_ws = float(price)
                elif src == "binance":
                    bot_rest = float(price)
                elif src == "chainlink":
                    bot_cl = float(price)
                if len(seen_src) >= 3:
                    break

        if bot_ws is None and bot_rest is None and bot_cl is None:
            data_quality = "missing_external"
        else:
            data_quality = "missing_resolution"

        outcome_text = (r["result"] or "").lower()
        # Map executions.result -> settle_outcome
        if outcome_text in ("won", "lost"):
            settle_outcome = (
                str(r["direction"]).upper() if outcome_text == "won"
                else (
                    "UP" if str(r["direction"]).lower() == "down"
                    else "DOWN"))
        else:
            settle_outcome = None

        try:
            conn.execute(
                """INSERT OR IGNORE INTO reference_price_audit
                   (settle_ts_ms, condition_id, asset_id, slug, asset,
                    timeframe, official_resolution_price,
                    bot_binance_rest_price, bot_binance_ws_price,
                    bot_chainlink_price, dev_binance_bps, dev_chainlink_bps,
                    settle_outcome, data_quality, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (settle_ts_ms, slug, r["market_token_id"] or "", slug,
                 asset, tf,
                 None,
                 bot_rest, bot_ws, bot_cl,
                 None, None,
                 settle_outcome, data_quality,
                 _ms_to_iso(int(time.time() * 1000))))
            if conn.total_changes > inserted + skipped:
                inserted += 1
            else:
                skipped += 1
        except sqlite3.Error as e:
            print(f"[backfill] insert failed for {slug}: {e}",
                  file=sys.stderr)

    conn.commit()
    conn.close()
    print(f"[backfill] inserted={inserted} skipped(existing)={skipped} "
          f"no_timestamp={no_ts}")
    return 0


# -------------------------------------------------------------
# Fetch references (Binance klines)
# -------------------------------------------------------------


async def _fetch_kline_close(httpx_module, client, symbol: str, interval: str,
                             start_ms: int, end_ms: int) -> float | None:
    """Fetch the Binance kline close that brackets start_ms..end_ms.

    Returns the close price of the kline whose openTime >= start_ms.
    """
    try:
        r = await client.get(
            BINANCE_KLINES_URL,
            params={
                "symbol": symbol,
                "interval": interval,
                "startTime": start_ms - 60000,  # 1m before
                "endTime": end_ms + 60000,
                "limit": 5,
            },
            timeout=8.0,
        )
        if r.status_code != 200:
            return None
        klines = r.json()
        # kline = [openTime, open, high, low, close, volume, closeTime, ...]
        # Pick the one whose closeTime is closest (and >=) start_ms
        best = None
        best_dist = None
        for k in klines:
            close_time = int(k[6])
            close_price = float(k[4])
            dist = abs(close_time - start_ms)
            if best is None or dist < best_dist:
                best = close_price
                best_dist = dist
        return best
    except (httpx_module.HTTPError, ValueError, TypeError, IndexError) as e:
        print(f"[fetch] kline {symbol}@{interval}@{start_ms} failed: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return None


async def _fetch_references_async(args) -> int:
    try:
        import httpx
    except ImportError:
        print("[ERROR] httpx not installed", file=sys.stderr)
        return 1

    cutoff_ms = int((time.time() - args.days * 86400) * 1000)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """SELECT settle_ts_ms, condition_id, slug, asset, timeframe,
                  bot_binance_rest_price, bot_binance_ws_price,
                  bot_chainlink_price
           FROM reference_price_audit
           WHERE data_quality = 'missing_resolution'
             AND asset != ''
             AND timeframe != ''
             AND settle_ts_ms >= ?""", (cutoff_ms,))
    rows = cur.fetchall()
    print(f"[fetch] {len(rows)} audit rows need resolution price")

    enriched = 0
    failed = 0
    async with httpx.AsyncClient() as client:
        for r in rows:
            asset = r["asset"]
            tf = r["timeframe"]
            interval = TF_TO_BINANCE_INTERVAL.get(tf, "5m")
            symbol = f"{asset}USDT"

            official = await _fetch_kline_close(
                httpx, client, symbol, interval,
                r["settle_ts_ms"], r["settle_ts_ms"])

            if official is None or official <= 0:
                failed += 1
                continue

            def _bps(local):
                if local is None or local <= 0:
                    return None
                return ((local - official) / official) * 10000.0

            ws_bps = _bps(r["bot_binance_ws_price"])
            rest_bps = _bps(r["bot_binance_rest_price"])
            cl_bps = _bps(r["bot_chainlink_price"])

            # Pick best Binance bps (prefer WS over REST)
            dev_b = ws_bps if ws_bps is not None else rest_bps

            try:
                conn.execute(
                    """UPDATE reference_price_audit
                       SET official_resolution_price = ?,
                           dev_binance_bps = ?,
                           dev_chainlink_bps = ?,
                           data_quality = 'ok'
                       WHERE condition_id = ? AND settle_ts_ms = ?""",
                    (official, dev_b, cl_bps, r["condition_id"],
                     r["settle_ts_ms"]))
                enriched += 1
            except sqlite3.Error as e:
                print(f"[fetch] update failed: {e}", file=sys.stderr)
                failed += 1

            # Throttle: stay well under Binance 1200 req/min
            await asyncio.sleep(0.05)

    conn.commit()
    conn.close()
    print(f"[fetch] enriched={enriched} failed={failed}")
    return 0


def cmd_fetch_references(args: argparse.Namespace) -> int:
    return asyncio.run(_fetch_references_async(args))


# -------------------------------------------------------------
# Report
# -------------------------------------------------------------


def _percentile(data, pct):
    if not data:
        return None
    s = sorted(data)
    k = (len(s) - 1) * pct / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def cmd_report(args: argparse.Namespace) -> int:
    cutoff_ms = int((time.time() - args.days * 86400) * 1000)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    cur = conn.execute(
        """SELECT * FROM reference_price_audit
           WHERE settle_ts_ms >= ? ORDER BY settle_ts_ms DESC""",
        (cutoff_ms,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    lines: list[str] = []
    lines.append("# Reference Price Feed Audit")
    lines.append("")
    lines.append(f"**Period:** last {args.days} days  ")
    lines.append(f"**Generated:** {_ms_to_iso(int(time.time() * 1000))}  ")
    lines.append(f"**Total audit rows:** {len(rows)}")
    lines.append("")

    if not rows:
        lines.append("> No audit data in window. Bot may not have settled "
                     "any trades, or settle hook is disabled.")
        _emit_report(args, lines)
        return 0

    # Data quality breakdown
    by_quality: dict[str, int] = {}
    for r in rows:
        by_quality[r["data_quality"]] = by_quality.get(
            r["data_quality"], 0) + 1
    lines.append("## Data Quality")
    lines.append("")
    for q, n in sorted(by_quality.items()):
        pct = (n / len(rows)) * 100
        lines.append(f"- `{q}`: {n} ({pct:.1f}%)")
    lines.append("")

    # Acceptable rows for stats
    ok_rows = [r for r in rows if r["data_quality"] == "ok"]
    if not ok_rows:
        lines.append("## Statistics")
        lines.append("")
        lines.append("> No rows with full data quality. Run "
                     "`--fetch-references` to enrich.")
        _emit_report(args, lines)
        return 0

    # Per-(asset, tf, source) stats
    lines.append("## Per (asset, tf, source) Statistics")
    lines.append("")
    lines.append("| Asset | TF | Source | N | mean_bps | "
                 "median_bps | p95_bps | p99_bps | bias |")
    lines.append("|-------|----|----|---:|---------:|"
                 "---------:|--------:|--------:|----|")

    groups: dict[tuple, list[tuple[str, float]]] = {}
    for r in ok_rows:
        a = r["asset"] or "?"
        tf = r["timeframe"] or "?"
        for src_label, val in (("binance", r["dev_binance_bps"]),
                               ("chainlink", r["dev_chainlink_bps"])):
            if val is None:
                continue
            key = (a, tf, src_label)
            groups.setdefault(key, []).append(val)

    for key in sorted(groups.keys()):
        vals = groups[key]
        a, tf, src = key
        n = len(vals)
        mean_bps = statistics.fmean(vals)
        median_bps = statistics.median(vals)
        p95 = _percentile(vals, 95)
        p99 = _percentile(vals, 99)
        bias = "🔴" if abs(mean_bps) > 5 else (
            "🟡" if abs(mean_bps) > 2 else "🟢")
        lines.append(
            f"| {a} | {tf} | {src} | {n} | {mean_bps:+.2f} | "
            f"{median_bps:+.2f} | {p95:+.2f} | {p99:+.2f} | {bias} |"
        )
    lines.append("")
    lines.append("Bias key: 🟢 ≤2 bps · 🟡 ≤5 bps · 🔴 >5 bps")
    lines.append("")

    # Worst 10 deviations
    worst: list[tuple[float, dict, str]] = []
    for r in ok_rows:
        for src_label, val in (("binance", r["dev_binance_bps"]),
                               ("chainlink", r["dev_chainlink_bps"])):
            if val is not None:
                worst.append((abs(val), r, src_label))
    worst.sort(key=lambda x: x[0], reverse=True)

    lines.append("## Worst 10 Deviations")
    lines.append("")
    lines.append("| When (UTC) | Asset/TF | Source | dev_bps | "
                 "Local | Official | Slug |")
    lines.append("|------------|---------|--------|--------:|"
                 "-------:|---------:|------|")
    for abs_v, r, src in worst[:10]:
        when = _ms_to_iso(r["settle_ts_ms"])
        local = (r["bot_binance_ws_price"]
                 if src == "binance" and r["bot_binance_ws_price"]
                 else r["bot_binance_rest_price"]
                 if src == "binance" else r["bot_chainlink_price"])
        a_tf = f"{r['asset']}/{r['timeframe']}"
        slug_disp = (r["slug"] or "")[:48]
        bps_val = (r["dev_binance_bps"] if src == "binance"
                   else r["dev_chainlink_bps"])
        lines.append(
            f"| {when} | {a_tf} | {src} | {bps_val:+.2f} | "
            f"{local} | {r['official_resolution_price']} | "
            f"`{slug_disp}` |"
        )
    lines.append("")

    # Systemic alarm summary
    alarms: list[str] = []
    for key, vals in groups.items():
        if abs(statistics.fmean(vals)) > 5:
            a, tf, src = key
            alarms.append(f"({a}/{tf}/{src}) mean="
                          f"{statistics.fmean(vals):+.2f} bps")
    lines.append("## Alarms")
    lines.append("")
    if alarms:
        lines.append("> 🔴 **EDGE ESTIMATE INVALID** — systematic bias > 5 bps:")
        lines.append("")
        for a in alarms:
            lines.append(f"- {a}")
    else:
        lines.append("> 🟢 No systematic bias detected (all groups |mean| ≤ 5 bps).")
    lines.append("")

    _emit_report(args, lines)
    return 0


def _emit_report(args, lines):
    md = "\n".join(lines) + "\n"
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"[report] wrote {out}")
    else:
        print(md)


# -------------------------------------------------------------
# CLI
# -------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        prog="audit_reference_price",
        description="P0-07: PolyPaper reference price feed audit.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--backfill", action="store_true")
    g.add_argument("--fetch-references", action="store_true")
    g.add_argument("--report", action="store_true")
    g.add_argument("--all", action="store_true",
                   help="run --backfill + --fetch-references + --report")
    p.add_argument("--days", type=int, default=7,
                   help="lookback window in days (default 7)")
    p.add_argument("--output", help="markdown output path (only --report)")
    args = p.parse_args()

    if not DB_PATH.exists():
        print(f"[ERROR] DB not found: {DB_PATH}", file=sys.stderr)
        return 1

    if args.all:
        rc = cmd_backfill(args)
        if rc != 0:
            return rc
        rc = cmd_fetch_references(args)
        if rc != 0:
            return rc
        return cmd_report(args)
    if args.backfill:
        return cmd_backfill(args)
    if args.fetch_references:
        return cmd_fetch_references(args)
    return cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
