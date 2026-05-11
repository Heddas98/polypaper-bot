"""
PolyPaper Bot - Trade Journal (Phase 30)
Dual-write: JSONL (yedek) + DB (hizli sorgulama)
DB trade_log tablosu = analytics icin aninda sorgulanabilir.
JSONL = offline analiz + yedeklilik icin devam eder.
"""

import json
import logging
import os
from datetime import UTC, datetime

import aiosqlite

logger = logging.getLogger("polypaper.journal")

JOURNAL_DIR = "data_store"
JOURNAL_FILE = os.path.join(JOURNAL_DIR, "trade_journal.jsonl")
DECISION_LOG = os.path.join(JOURNAL_DIR, "decisions.jsonl")

# Global DB reference — set by engine at startup
_db = None

# T7.6 B1 (2026-04-22): Keep strong references to in-flight _write_db tasks.
# ``loop.create_task()`` only keeps a weak reference; if the caller discards
# the Task, CPython is free to garbage-collect (and cancel) the coroutine
# mid-flight. We add each task to this set and remove it on done-callback,
# so the DB write is guaranteed to run to completion even if the outer
# journal call has returned.
_pending_db_tasks: set = set()


def set_db(db):
    """Set DB reference for dual-write. Called by engine on startup."""
    global _db
    _db = db


def _ensure_dir():
    os.makedirs(JOURNAL_DIR, exist_ok=True)


def log_trade(event_type: str, data: dict):
    """Dual-write: JSONL file + DB trade_log table."""
    _ensure_dir()
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event_type,
        **data,
    }

    # 1. JSONL (yedek — her zaman yaz)
    try:
        with open(JOURNAL_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
    except (OSError, TypeError, ValueError) as e:
        # T1.4 Faz 3: file append + json.dumps. Realistic modes:
        # OSError (disk full / permission / FNF), TypeError (non-
        # serializable record), ValueError (NaN/Infinity).
        logger.debug(f"Journal JSONL: {e}")

    # 2. DB (hizli sorgulama)
    if _db and _db.conn:
        try:
            import asyncio

            loop = asyncio.get_running_loop()  # BUG-08 fix: get_event_loop() deprecated
            # T7.6 B1: keep strong ref so task is not GC'd mid-write.
            task = loop.create_task(_write_db(record))
            _pending_db_tasks.add(task)
            task.add_done_callback(_pending_db_tasks.discard)
        except RuntimeError as e:
            # Event loop closed/unavailable (bot shutdown). JSONL backup already written.
            logger.debug(f"Journal DB skip (loop closed): {e}")  # T7.6 B4
        except (ImportError, AttributeError, TypeError) as e:
            # T1.4 Faz 3: asyncio create_task fallback. Realistic modes:
            # ImportError (asyncio module — gerçekte olmaz ama sandbox
            # güvencesi), AttributeError (loop None / closed),
            # TypeError (create_task coroutine argümanı). RuntimeError
            # zaten üstteki handler'da yakalı.
            # T7.6 B4: add debug log so silent failures leave an audit trail.
            logger.debug(f"Journal DB task create: {e}")


async def _write_db(record: dict):
    """Write log entry to trade_log DB table."""
    if not _db or not _db.conn:
        return
    try:
        await _db.conn.execute(
            """INSERT INTO trade_log (event, slug, direction, strategy_id,
               price, amount, pnl, fee, reason, metadata, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.get("event", ""),
                record.get("slug", ""),
                record.get("direction", ""),
                record.get("strategy_id", ""),
                record.get("price") or record.get("entry_price") or record.get("fill_price"),
                record.get("amount") or record.get("trade_amount"),
                record.get("pnl"),
                record.get("fee"),
                record.get("reason", ""),
                json.dumps(
                    {
                        k: v
                        for k, v in record.items()
                        if k
                        not in (
                            "event",
                            "slug",
                            "direction",
                            "strategy_id",
                            "price",
                            "amount",
                            "pnl",
                            "fee",
                            "reason",
                            "ts",
                        )
                    }
                ),
                record.get("ts", datetime.now(UTC).isoformat()),
            ),
        )
        await _db.conn.commit()
    except (aiosqlite.Error, TypeError, ValueError, AttributeError) as e:
        # T1.4 Faz 3: INSERT + json.dumps(metadata). Realistic modes:
        # aiosqlite.Error (DB), TypeError/ValueError (json.dumps non-
        # serializable record values), AttributeError (_db.conn None).
        logger.debug(f"Journal DB: {e}")


# ═══ CONVENIENCE FUNCTIONS ═══


def log_entry(slug, direction, price, amount, shares, fee, strategy_id, token_id):
    log_trade(
        "ENTRY",
        {
            "slug": slug,
            "direction": direction,
            "price": price,
            "amount": amount,
            "shares": shares,
            "fee": fee,
            "strategy_id": strategy_id,
            "token_id": token_id,
        },
    )


def log_exit(slug, direction, reason, entry_price, exit_price, pnl, payout):
    log_trade(
        "EXIT",
        {
            "slug": slug,
            "direction": direction,
            "reason": reason,
            "entry_price": entry_price,
            "price": exit_price,
            "pnl": pnl,
            "payout": payout,
        },
    )


def log_settlement(slug, direction, resolution, won, pnl, payout, last_odds):
    log_trade(
        "SETTLEMENT",
        {
            "slug": slug,
            "direction": direction,
            "resolution": resolution,
            "won": won,
            "pnl": pnl,
            "payout": payout,
            "last_odds": last_odds,
        },
    )


def log_rejection(slug, reason, details, strategy_id=None):
    log_trade(
        "REJECTION",
        {
            "slug": slug,
            "reason": reason,
            "details": details,
            "strategy_id": strategy_id or "",
        },
    )


def log_heartbeat(cycle, strats, positions, markets):
    # Heartbeats only to JSONL (too frequent for DB)
    _ensure_dir()
    try:
        with open(DECISION_LOG, "a") as f:
            f.write(
                json.dumps(
                    {
                        "ts": datetime.now(UTC).isoformat(),
                        "event": "HEARTBEAT",
                        "cycle": cycle,
                        "strategies": strats,
                        "positions": positions,
                        "active_markets": len(markets) if isinstance(markets, list) else 0,
                    }
                )
                + "\n"
            )
    except (OSError, TypeError, ValueError):
        # T1.4 Faz 3: decisions.jsonl append + json.dumps heartbeat.
        # Realistic: OSError (FS), TypeError/ValueError (json.dumps).
        pass


# ═══ SPRINT 2: DECISION LOGGING ═══

# "Important" skip reasons — these are near-trade events worth logging individually
_IMPORTANT_SKIPS = {
    "EDGE_GATE",
    "LOW_EDGE_VS_FEE",
    "RISK",
    "BRIER_ALARM",
    "LOW_CONVICTION",
    "EV_NEGATIVE",
    "KELLY_NO_EDGE",
    "SLIPPAGE",
    "CAPITAL_BUDGET",
    "UNSELLABLE",
    "TOKEN_CAP",
}


def log_decision_open(
    strategy_id, slug, direction, signal_score, signal_reason, price, amount, fee, regime=None
):
    """Log trade OPEN decision to decisions.jsonl. Called when VirtualOrder is placed."""
    _ensure_dir()
    try:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "event": "OPEN",
            "strategy_id": strategy_id[:8] if strategy_id else "",
            "slug": slug,
            "direction": direction,
            "signal_score": round(signal_score, 4) if signal_score else 0,
            "signal_reason": signal_reason or "",
            "price": round(price, 4) if price else 0,
            "amount": round(amount, 2) if amount else 0,
            "fee": round(fee, 4) if fee else 0,
        }
        if regime:
            record["regime"] = regime
        with open(DECISION_LOG, "a") as f:
            f.write(json.dumps(record) + "\n")
    except (OSError, TypeError, ValueError):
        # T1.4 Faz 3: decision OPEN append + round/json coerce.
        # Realistic: OSError (FS), TypeError/ValueError (round on non-
        # numeric / json.dumps non-serializable).
        pass


def log_decision_skip(strategy_id, slug, reason, signal_score=None, price=None, extra=None):
    """Log important SKIP decision to decisions.jsonl. Only for near-trade events."""
    if reason not in _IMPORTANT_SKIPS:
        return  # noisy skips (MARKET_HALT, NO_LIQ etc.) only in cycle summary
    _ensure_dir()
    try:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "event": "SKIP",
            "strategy_id": strategy_id[:8] if strategy_id else "",
            "slug": slug or "",
            "reason": reason,
        }
        if signal_score is not None:
            record["signal_score"] = round(signal_score, 4)
        if price is not None:
            record["price"] = round(price, 4)
        if extra:
            record["extra"] = str(extra)[:200]
        with open(DECISION_LOG, "a") as f:
            f.write(json.dumps(record) + "\n")
    except (OSError, TypeError, ValueError):
        # T1.4 Faz 3: decision SKIP append. Realistic: OSError (FS),
        # TypeError/ValueError (round/str/json coerce).
        pass


def log_decision_close(strategy_id, slug, result, pnl, duration_sec=None):
    """Log trade CLOSE event to decisions.jsonl."""
    _ensure_dir()
    try:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "event": "CLOSE",
            "strategy_id": strategy_id[:8] if strategy_id else "",
            "slug": slug or "",
            "result": result or "",
            "pnl": round(pnl, 4) if pnl is not None else 0,
        }
        if duration_sec is not None:
            record["duration_sec"] = int(duration_sec)
        with open(DECISION_LOG, "a") as f:
            f.write(json.dumps(record) + "\n")
    except (OSError, TypeError, ValueError):
        # T1.4 Faz 3: decision CLOSE append + int coerce. Realistic:
        # OSError (FS), TypeError/ValueError (round/int coerce on None).
        pass


def log_decision_cycle_summary(cycle, skip_counts):
    """Log cycle-end skip summary to decisions.jsonl. Called once per heartbeat."""
    if not skip_counts:
        return
    _ensure_dir()
    try:
        with open(DECISION_LOG, "a") as f:
            f.write(
                json.dumps(
                    {
                        "ts": datetime.now(UTC).isoformat(),
                        "event": "CYCLE_SKIPS",
                        "cycle": cycle,
                        "skips": skip_counts,  # {"MARKET_HALT": 12, "NO_LIQ": 5, ...}
                        "total": sum(skip_counts.values()),
                    }
                )
                + "\n"
            )
    except (OSError, TypeError, ValueError, AttributeError):
        # T1.4 Faz 3: cycle summary append + sum(dict.values()) + json.
        # Realistic: OSError (FS), TypeError/ValueError (sum non-numeric
        # / json non-serializable), AttributeError (skip_counts .values
        # yok, guard üstte ama belt+suspenders).
        pass
