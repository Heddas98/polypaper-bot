"""
PolyPaper Bot — Order Status Polling Refinement
====================================================
P2.3 (Phase D Bulgu 12)

Order status transition: matched → mined → confirmed
Mevcut polling agresif (rate limit yer). Bu modül exponential backoff:
5 → 10 → 30 → 60s

Polymarket V2 docs `/trading/orders/overview#trade-statuses`:
- placed       → CLOB accepted, queued
- matched      → matched against resting order(s)
- mined        → on-chain tx mined
- confirmed    → final settlement

Status değişene kadar exp backoff polling.

ENV:
- STATUS_POLL_MAX_S (default 60)
- STATUS_POLL_MAX_ATTEMPTS (default 12, ~10dk)
- STATUS_POLL_INITIAL_S (default 5)
- STATUS_POLL_FACTOR (default 2.0)

Usage:
    from core.status_poller import poll_order_status
    final = await poll_order_status(client, order_id, expected_terminal=True)
    # final = {"status": "confirmed", "transaction_hash": "0x...", ...}
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("polypaper.core.status_poller")


TERMINAL_STATUSES = {"confirmed", "rejected", "cancelled", "canceled", "failed"}
INTERIM_STATUSES = {"placed", "matched", "mined", "delayed", "live"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


async def fetch_order_status(client, order_id: str) -> Optional[dict]:
    """Fetch single order status via SDK."""
    if client is None or not order_id:
        return None
    loop = asyncio.get_running_loop()
    try:
        # py_clob_client_v2: get_order(order_id)
        if hasattr(client, "get_order"):
            return await loop.run_in_executor(None, lambda: client.get_order(order_id))
        # Fallback: get_order_by_id
        if hasattr(client, "get_order_by_id"):
            return await loop.run_in_executor(None, lambda: client.get_order_by_id(order_id))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"fetch_order_status fail: {e}")
    return None


async def poll_order_status(
    client,
    order_id: str,
    expected_terminal: bool = True,
    max_attempts: Optional[int] = None,
    initial_wait_s: Optional[float] = None,
    max_wait_s: Optional[float] = None,
    factor: Optional[float] = None,
) -> dict:
    """Poll status with exp backoff until terminal or max attempts.

    Args:
        client: py_clob_client_v2 ClobClient
        order_id: Order ID hash
        expected_terminal: Wait until status in TERMINAL_STATUSES
        max_attempts: Override env (default 12 → ~10dk total)

    Returns:
        {
          "final_status": "confirmed"|"failed"|"timeout",
          "attempts": int,
          "elapsed_s": float,
          "history": [{"ts": ..., "status": ...}, ...],
          "raw": <last response>
        }
    """
    max_attempts = max_attempts or _env_int("STATUS_POLL_MAX_ATTEMPTS", 12)
    wait_s = initial_wait_s or _env_float("STATUS_POLL_INITIAL_S", 5.0)
    max_wait = max_wait_s or _env_float("STATUS_POLL_MAX_S", 60.0)
    factor = factor or _env_float("STATUS_POLL_FACTOR", 2.0)

    start_ts = time.time()
    history = []
    last_status = None
    last_response = None

    for attempt in range(1, max_attempts + 1):
        response = await fetch_order_status(client, order_id)
        last_response = response

        if response is None:
            logger.debug(f"poll [{attempt}/{max_attempts}] order {order_id[:12]}... no response")
            history.append({"ts": time.time(), "status": "no_response", "attempt": attempt})
        else:
            status = ""
            if isinstance(response, dict):
                status = (response.get("status") or response.get("state") or "").lower()
            history.append({"ts": time.time(), "status": status, "attempt": attempt})
            last_status = status

            if status in TERMINAL_STATUSES:
                logger.debug(f"poll: terminal '{status}' reached after {attempt} attempts")
                return {
                    "final_status": status,
                    "attempts": attempt,
                    "elapsed_s": time.time() - start_ts,
                    "history": history,
                    "raw": last_response,
                }

            # Interim — continue polling
            logger.debug(f"poll [{attempt}/{max_attempts}] status='{status}' wait {wait_s:.1f}s")

        # Sleep with exp backoff
        await asyncio.sleep(min(wait_s, max_wait))
        wait_s = min(wait_s * factor, max_wait)

    # Max attempts reached
    return {
        "final_status": last_status or "timeout",
        "attempts": max_attempts,
        "elapsed_s": time.time() - start_ts,
        "history": history,
        "raw": last_response,
        "timeout": True,
    }


async def poll_with_callback(
    client,
    order_id: str,
    on_status_change=None,
    **poll_kwargs,
) -> dict:
    """Variant: invoke callback on each status transition.

    Args:
        on_status_change: async fn (old_status, new_status, response) -> None
    """
    result = await poll_order_status(client, order_id, **poll_kwargs)

    if on_status_change and result.get("history"):
        prev = None
        for entry in result["history"]:
            cur = entry.get("status")
            if cur != prev:
                try:
                    await on_status_change(prev, cur, result.get("raw"))
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"status callback fail: {e}")
                prev = cur

    return result
