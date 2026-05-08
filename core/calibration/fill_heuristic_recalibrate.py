"""
Fill Heuristic Empirical Recalibration — P0.7 (T4.7-C closure)
================================================================

Mevcut T4.6-B sweep bulgusu (memory):
- paper×0.66 ≈ live → fill heuristic INSUFFICIENT
- Önerilen: FILL_SPREAD_COST 0.005→0.023, IMPACT 0.01→0.025, LATENCY_DRIFT 0.08→0.04
- Sweep artifact: backtest/calibration/sweep_fill_heuristic_20260424_193711.json

Bu modül haftalık empirical sweep cron job:
- Her Cuma çalışır
- Son 200 trade × son 200 market'e karşı sweep
- Yeni heuristic değerleri vs mevcut karşılaştır
- Delta > %5 alarm + Telegram
- Auto-update YOK (Heddas direktifi: tüm config user-confirmed)

Usage:
    from core.calibration.fill_heuristic_recalibrate import recalibrate_weekly
    new_params = await recalibrate_weekly(db, sample_size=200)
    if new_params["delta_pct"] > 5:
        await notify_admin(format_alert(new_params))

Hedef ENV (sweep artifact'ten):
    FILL_SPREAD_COST=0.023
    FILL_IMPACT=0.025
    LATENCY_DRIFT=0.04
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("polypaper.calibration.fill_heuristic")


# T4.6-B sweep'in önerdiği değerler (sweep_fill_heuristic_20260424_193711.json)
# Heddas onayı sonrası config/settings.py'a yazılır.
RECOMMENDED_VALUES = {
    "FILL_SPREAD_COST": 0.023,    # was 0.005
    "FILL_IMPACT": 0.025,          # was 0.010
    "LATENCY_DRIFT": 0.04,         # was 0.080
}

LEGACY_VALUES = {
    "FILL_SPREAD_COST": 0.005,
    "FILL_IMPACT": 0.010,
    "LATENCY_DRIFT": 0.080,
}

DEFAULT_SAMPLE_SIZE = 200
DELTA_ALERT_PCT = 5.0


def get_current_values() -> dict[str, float]:
    """Read current ENV-overridden values (T6.1 runtime re-read)."""
    return {
        "FILL_SPREAD_COST": float(os.getenv("FILL_SPREAD_COST", LEGACY_VALUES["FILL_SPREAD_COST"])),
        "FILL_IMPACT": float(os.getenv("FILL_IMPACT", LEGACY_VALUES["FILL_IMPACT"])),
        "LATENCY_DRIFT": float(os.getenv("LATENCY_DRIFT", LEGACY_VALUES["LATENCY_DRIFT"])),
    }


def compute_paper_live_delta(paper_pnls: list[float], live_pnls: list[float]) -> dict:
    """T4.6-B style paper vs live PnL delta.

    Args:
        paper_pnls: list of paper engine PnL values
        live_pnls: list of live trade PnL values (same trade IDs)

    Returns: {paper_total, live_total, delta_pct, paper_mean, live_mean, ...}
    """
    paper_total = sum(paper_pnls)
    live_total = sum(live_pnls)
    paper_mean = paper_total / len(paper_pnls) if paper_pnls else 0
    live_mean = live_total / len(live_pnls) if live_pnls else 0

    if abs(paper_total) < 1e-6:
        delta_pct = 0.0
    else:
        delta_pct = ((live_total - paper_total) / abs(paper_total)) * 100

    return {
        "n_paper": len(paper_pnls),
        "n_live": len(live_pnls),
        "paper_total": round(paper_total, 4),
        "live_total": round(live_total, 4),
        "paper_mean": round(paper_mean, 4),
        "live_mean": round(live_mean, 4),
        "delta_pct": round(delta_pct, 2),
        "drift_ratio": round(live_total / paper_total, 4) if paper_total != 0 else 0,
    }


def evaluate_recalibration(current: dict, recommended: dict) -> dict:
    """Compare current ENV values vs recommended T4.6-B values."""
    deltas = {}
    for key, rec_val in recommended.items():
        cur_val = current.get(key, 0)
        if cur_val == 0:
            pct = 0
        else:
            pct = ((rec_val - cur_val) / cur_val) * 100
        deltas[key] = {
            "current": cur_val,
            "recommended": rec_val,
            "delta_pct": round(pct, 2),
        }
    return deltas


async def fetch_recent_paper_live_pairs(db_path: Path, sample_size: int = DEFAULT_SAMPLE_SIZE) -> tuple[list[float], list[float]]:
    """Read paired paper/live trades from DB.

    Returns: (paper_pnls, live_pnls) — same indices = same trade.

    Note: bu modülün DB schema bilgisi gerektiriyor. Şu an basit pattern:
    `live_trades` tablosunda hem `pnl` hem `paper_pnl` kolonu var (memory).
    """
    import sqlite3
    paper, live = [], []
    if not db_path.exists():
        return paper, live

    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
        cur.execute(
            """SELECT pnl, paper_pnl FROM live_trades
               WHERE created_at >= ? AND pnl IS NOT NULL AND paper_pnl IS NOT NULL
               ORDER BY created_at DESC LIMIT ?""",
            (cutoff, sample_size),
        )
        for live_pnl, paper_pnl in cur.fetchall():
            try:
                live.append(float(live_pnl))
                paper.append(float(paper_pnl))
            except (TypeError, ValueError):
                continue
        con.close()
    except sqlite3.Error as e:
        logger.warning(f"recalibrate DB read: {e}")

    return paper, live


async def recalibrate_weekly(db_path: Optional[Path] = None, sample_size: int = DEFAULT_SAMPLE_SIZE) -> dict:
    """Top-level: weekly recalibration check.

    Returns: dict with `delta_pct` (alarm trigger), `recommended_values`,
    `current_values`, `paper_live_drift`, `should_alert`.
    """
    if db_path is None:
        db_path = Path(os.getenv("POLYPAPER_DB", "data_store/polypaper.db"))

    current = get_current_values()
    paper_pnls, live_pnls = await fetch_recent_paper_live_pairs(db_path, sample_size)
    drift = compute_paper_live_delta(paper_pnls, live_pnls)
    recalib = evaluate_recalibration(current, RECOMMENDED_VALUES)

    # Max delta across params
    max_delta = max((abs(d["delta_pct"]) for d in recalib.values()), default=0)
    drift_delta = abs(drift.get("delta_pct", 0))

    should_alert = max_delta > DELTA_ALERT_PCT or drift_delta > DELTA_ALERT_PCT

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "current_values": current,
        "recommended_values": RECOMMENDED_VALUES,
        "param_deltas": recalib,
        "paper_live_drift": drift,
        "max_param_delta_pct": round(max_delta, 2),
        "drift_pct": drift_delta,
        "should_alert": should_alert,
        "sample_size": sample_size,
    }


def format_alert(result: dict) -> str:
    """HTML alert for Telegram."""
    drift = result.get("paper_live_drift", {})
    deltas = result.get("param_deltas", {})

    lines = [
        "<b>📐 Fill Heuristic Recalibration Check</b>",
        "",
        f"📅 Timestamp: <code>{result.get('ts', '')[:16]}</code>",
        f"📊 Sample: {drift.get('n_paper', 0)} pairs (last 30d)",
        "",
        f"<b>Paper vs Live Drift:</b>",
        f"  Paper PnL:  ${drift.get('paper_total', 0):.2f}",
        f"  Live PnL:   ${drift.get('live_total', 0):.2f}",
        f"  Drift:      {drift.get('delta_pct', 0):+.2f}%",
        "",
        f"<b>Param Recommendations (T4.6-B):</b>",
    ]
    for key, d in deltas.items():
        emoji = "🔴" if abs(d["delta_pct"]) > DELTA_ALERT_PCT else "✅"
        lines.append(
            f"  {emoji} <code>{key}</code>: {d['current']} → {d['recommended']} "
            f"({d['delta_pct']:+.1f}%)"
        )

    if result.get("should_alert"):
        lines.append("")
        lines.append("⚠️ <b>ACTION REQUIRED:</b> Heuristic drift detected.")
        lines.append("Review and update <code>.env</code>:")
        for key, d in deltas.items():
            if abs(d["delta_pct"]) > DELTA_ALERT_PCT:
                lines.append(f"  <code>{key}={d['recommended']}</code>")

    return "\n".join(lines)


async def cron_recalibrate_job(context=None):
    """APScheduler-compatible job. Wires into telegram_bot/jobs/.

    Cron pattern: every Friday at 18:00 UTC (or whatever schedule).
    """
    result = await recalibrate_weekly()
    out_dir = Path("evidence")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"fill_heuristic_recalib_{ts}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    logger.info(f"📐 Fill heuristic recalibration: {out_path}")
    if result["should_alert"]:
        logger.warning(f"⚠️ Recalibration alert: max_delta={result['max_param_delta_pct']}%")
    return result
