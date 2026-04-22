"""
Epic 11 T11.2 [A] — Manual PnL Divergence Trigger
==================================================

Standalone script: pnl_divergence_job'ın canlı ALERT path'ini, bot'u
durdurmadan, manuel olarak tetikler. G4 Seçenek B (canlı ALERT
kanıtı) için Heddas/Windows workflow'u.

Farkı probe'dan:
  * scripts/t11_2_g4_divergence_probe.py → read-only probe, output
    dosyaya, Telegram'a hiçbir şey atmaz (Seçenek C happy-path).
  * scripts/trigger_pnl_divergence.py    → probe sonucunu değerlendirir,
    eğer ALERT seviyesine ulaşmışsa Telegram HTTP API'ye direkt
    sendMessage atar. Job ile paralel koşar (bot runtime'a dokunmaz).

Kullanım
--------
Dry-run (sadece hesapla, ne atacağını göster):
    py -3.11 scripts/trigger_pnl_divergence.py --dry-run

Gerçek Telegram alert (divergence threshold aşıldıysa):
    py -3.11 scripts/trigger_pnl_divergence.py --send

Test harness — threshold düşürülmüş simulation:
    py -3.11 scripts/trigger_pnl_divergence.py --send --alert-pct 0.1

Force alert (debug — threshold'u by-pass, gerçek sayılarla RED mesaj at):
    py -3.11 scripts/trigger_pnl_divergence.py --send --force-level ALERT_RED

ENV gereksinimleri (--send için):
    TELEGRAM_BOT_TOKEN     (Bot API token)
    ADMIN_TELEGRAM_ID      veya ADMIN_CHAT_ID veya TELEGRAM_ADMIN_ID

Exit kodları:
    0 → success (ister no-alert, ister sent, ister dry-run)
    1 → DB/env error veya Telegram send fail
    2 → argument hatası

Güvenlik:
    * LIVE_ENABLED=false durumunda da çalışır (mesaj sadece info).
    * HIÇBIR emir/trade etkilemez — saf okuma + mesaj.
    * Token + admin ID sadece ENV'den; kod içinde hardcode YOK.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any


# ══════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════

def _resolve_db_path() -> str:
    if os.getenv("POLYPAPER_DB"):
        return os.environ["POLYPAPER_DB"]
    for candidate in ("data_store/polypaper.db", "polypaper.db"):
        if os.path.isfile(candidate):
            return candidate
    return "data_store/polypaper.db"


def _resolve_admin_id() -> int | None:
    for key in ("ADMIN_TELEGRAM_ID", "ADMIN_CHAT_ID", "TELEGRAM_ADMIN_ID"):
        val = os.getenv(key)
        if val:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None


def _resolve_bot_token() -> str | None:
    return os.getenv("TELEGRAM_BOT_TOKEN")


# ══════════════════════════════════════════════════════════════════════
# DB probe (1:1 mirror of telegram_bot/jobs/pnl_divergence_job.py)
# ══════════════════════════════════════════════════════════════════════

def _open_ro(db_path: str) -> sqlite3.Connection:
    """Open in read-only URI mode — safe while bot holds WAL write lock."""
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.execute("PRAGMA query_only=1")
    return conn


def _probe_divergence(
    db_path: str,
    window_h: float,
    alert_pct: float,
    min_trades: int,
) -> dict[str, Any]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_h)).isoformat()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    conn = _open_ro(db_path)
    try:
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
        "window_h": window_h,
        "alert_pct": alert_pct,
        "min_trades": min_trades,
        "paper_trades": paper_trades,
        "paper_pnl": paper_pnl,
        "paper_wr": paper_wr,
        "shadow_trades": shadow_trades,
        "shadow_pnl": shadow_pnl,
        "shadow_wr": shadow_wr,
        "divergence_pct": divergence_pct,
        "wr_delta": wr_delta,
        "level": level,
        "has_enough": has_enough,
    }


# ══════════════════════════════════════════════════════════════════════
# Telegram message formatting (1:1 mirror of the job)
# ══════════════════════════════════════════════════════════════════════

def _build_alert_text(r: dict[str, Any], level_override: str | None = None) -> str:
    level = level_override or r["level"]
    status_emoji = "🔴" if level == "ALERT_RED" else (
        "🟡" if level == "ALERT_YELLOW" else ("✅" if level == "OK" else "📊"))
    header = "PnL Divergence Alert" if level.startswith("ALERT_") else (
        "PnL Divergence OK" if level == "OK" else "Daily Paper Summary")

    lines = [
        f"{status_emoji} <b>{header}</b> ({int(r['window_h'])}h)",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"📄 <b>Paper:</b> {r['paper_trades']}t | WR {r['paper_wr']:.1f}% | PnL ${r['paper_pnl']:+.2f}",
        f"🔴 <b>Shadow:</b> {r['shadow_trades']}t | WR {r['shadow_wr']:.1f}% | PnL ${r['shadow_pnl']:+.2f}",
        "",
        f"📊 <b>Divergence: {r['divergence_pct']:.1f}%</b> (threshold: {r['alert_pct']}%)",
        f"📊 WR Delta: {r['wr_delta']:.1f}pp",
    ]
    if level == "ALERT_RED":
        lines.append("\n⚠️ Paper sonuclari guvenilir DEGIL! Live scaling durdurun.")
    elif level == "ALERT_YELLOW":
        lines.append("\n⚠️ Divergence yuksek. Paper-live farkini inceleyin.")
    lines.append(f"\n🕐 {r['timestamp_utc']} [MANUAL TRIGGER via T11.2 [A]]")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# Telegram HTTP send (no bot context — raw API)
# ══════════════════════════════════════════════════════════════════════

def _telegram_send(token: str, chat_id: int, text: str) -> tuple[bool, str]:
    """Returns (success, response_body_or_error)."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": str(chat_id),
        "text": text,
        "parse_mode": "HTML",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body)
            if data.get("ok"):
                return True, body
            return False, body
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="DB path (default: $POLYPAPER_DB or data_store/polypaper.db)")
    p.add_argument("--window-h", type=float, default=None, help="Look-back hours (default: $PNL_DIVERGENCE_WINDOW_H or 24)")
    p.add_argument("--alert-pct", type=float, default=None, help="Alert threshold %% (default: $PNL_DIVERGENCE_ALERT_PCT or 5.0)")
    p.add_argument("--min-trades", type=int, default=None, help="Min trades/bucket (default: $PNL_DIVERGENCE_MIN_TRADES or 5)")
    p.add_argument("--send", action="store_true", help="Actually send Telegram message (default is dry-run)")
    p.add_argument("--dry-run", action="store_true", help="Alias for no --send (explicit)")
    p.add_argument("--force-level", choices=["OK", "ALERT_YELLOW", "ALERT_RED", "INSUFFICIENT"],
                   help="Override verdict (useful for alert plumbing test)")
    p.add_argument("--json", action="store_true", help="Emit JSON probe result to stdout")

    args = p.parse_args()

    # Param resolution (CLI > ENV > default)
    def _pick_float(cli_val: float | None, env_key: str, default: float) -> float:
        if cli_val is not None:
            return cli_val
        raw = os.getenv(env_key)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def _pick_int(cli_val: int | None, env_key: str, default: int) -> int:
        if cli_val is not None:
            return cli_val
        raw = os.getenv(env_key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    db_path = args.db or _resolve_db_path()
    window_h = _pick_float(args.window_h, "PNL_DIVERGENCE_WINDOW_H", 24.0)
    alert_pct = _pick_float(args.alert_pct, "PNL_DIVERGENCE_ALERT_PCT", 5.0)
    min_trades = _pick_int(args.min_trades, "PNL_DIVERGENCE_MIN_TRADES", 5)

    if not os.path.isfile(db_path):
        print(f"[ERROR] DB not found: {db_path}", file=sys.stderr)
        return 1

    try:
        r = _probe_divergence(db_path, window_h, alert_pct, min_trades)
    except sqlite3.Error as e:
        print(f"[ERROR] DB probe failed: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(r, indent=2))
        return 0

    effective_level = args.force_level or r["level"]
    msg = _build_alert_text(r, level_override=args.force_level)

    print(f"[T11.2 [A] trigger_pnl_divergence] verdict={effective_level}")
    print("-" * 60)
    print(msg)
    print("-" * 60)

    will_send = args.send and not args.dry_run

    # Only actually ALERT if verdict demands it or explicit --force-level
    # (OK or INSUFFICIENT alone → no-op in send mode, match job behavior)
    is_alert = effective_level in ("ALERT_RED", "ALERT_YELLOW")
    will_actually_send = will_send and (is_alert or args.force_level is not None)

    if not will_send:
        print("\n[DRY-RUN] Not sending. Use --send to actually push to Telegram.")
        return 0

    if not is_alert and args.force_level is None:
        print(f"\n[NO-OP] verdict={effective_level} does not warrant an alert. "
              "Use --force-level to test alert plumbing regardless.")
        return 0

    token = _resolve_bot_token()
    admin_id = _resolve_admin_id()
    if not token:
        print("[ERROR] TELEGRAM_BOT_TOKEN env missing", file=sys.stderr)
        return 1
    if not admin_id:
        print("[ERROR] admin id env missing (ADMIN_TELEGRAM_ID / ADMIN_CHAT_ID / TELEGRAM_ADMIN_ID)", file=sys.stderr)
        return 1

    ok, body = _telegram_send(token, admin_id, msg)
    if ok:
        print(f"\n[OK] Telegram message sent to chat_id={admin_id}")
        return 0
    print(f"\n[ERROR] Telegram send failed: {body}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
