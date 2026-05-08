"""
PolyPaper Bot — CLOB Heartbeat 5s Coroutine
==============================================
P1.6.1 (P1.6 post-only GTC öncesi ZORUNLU)

Polymarket V2 docs:
- POST /heartbeat 5s'de bir zorunlu (GTC/GTD resting orderlar için)
- 10s + 5s buffer içinde gelmezse TÜM açık order'lar cancel
- İlk request `heartbeat_id=""`, sonraki her request bir önceki response.heartbeat_id

Mevcut FOK-only flow için heartbeat **gereksiz** (P0.2 audit).
Ama P1.6 (post-only GTC maker rebate) eklendiğinde **ZORUNLU**.

Bu modül:
- async loop, 5s interval
- ID rotation (chain heartbeat_id)
- 400 retry (server-provided correct ID)
- Graceful shutdown
- ENV: HEARTBEAT_INTERVAL_S=5, HEARTBEAT_ENABLED (default false until P1.6)

Usage:
    from core.heartbeat import HeartbeatTask
    task = HeartbeatTask(client=clob_client)
    await task.start()
    # ... bot runs ...
    await task.stop()
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("polypaper.core.heartbeat")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name, "true" if default else "false").strip().lower()
    return val in {"1", "true", "yes", "on"}


# Polymarket V2 docs constants
HEARTBEAT_INTERVAL_S_DEFAULT = 5
HEARTBEAT_TIMEOUT_S = 10  # Polymarket dead-man-switch (10s + 5s buffer)


class HeartbeatTask:
    """5s heartbeat keep-alive task for Polymarket CLOB.

    Reasoning:
    - FOK-only flow → heartbeat opsiyonel (resting order yok)
    - GTC/GTD/post-only flow → heartbeat ZORUNLU (10s+5s buffer cancel)

    Attributes:
        client: py_clob_client_v2 ClobClient instance (auth verified)
        _heartbeat_id: Most recent heartbeat_id from server
        _task: asyncio.Task ref
        _running: state flag
        _last_success_ts: Last successful heartbeat timestamp
        _consecutive_fails: Failure counter (for backoff)
    """

    def __init__(self, client, interval_s: Optional[int] = None):
        self.client = client
        self._interval_s = interval_s or _env_int("HEARTBEAT_INTERVAL_S", HEARTBEAT_INTERVAL_S_DEFAULT)
        self._heartbeat_id: str = ""
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_success_ts: float = 0.0
        self._consecutive_fails: int = 0
        self._stop_requested = False

    @property
    def enabled(self) -> bool:
        """ENV-tunable kill switch."""
        return _env_bool("HEARTBEAT_ENABLED", False)

    @property
    def is_alive(self) -> bool:
        """Are we within the 10s+5s dead-man-switch window?"""
        if self._last_success_ts == 0:
            return False
        return (time.time() - self._last_success_ts) < HEARTBEAT_TIMEOUT_S

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "is_alive": self.is_alive,
            "heartbeat_id": self._heartbeat_id[:12] + "..." if len(self._heartbeat_id) > 12 else self._heartbeat_id,
            "last_success_age_s": (time.time() - self._last_success_ts) if self._last_success_ts else None,
            "consecutive_fails": self._consecutive_fails,
            "interval_s": self._interval_s,
            "enabled": self.enabled,
        }

    async def start(self) -> None:
        """Spawn the heartbeat loop task.

        No-op if HEARTBEAT_ENABLED=false (default until P1.6 GTC implementation).
        """
        if not self.enabled:
            logger.info("💓 Heartbeat: DISABLED (HEARTBEAT_ENABLED=false; FOK-only mode)")
            return

        if self._running:
            logger.warning("💓 Heartbeat: already running")
            return

        if self.client is None:
            logger.warning("💓 Heartbeat: client is None, cannot start")
            return

        self._running = True
        self._stop_requested = False
        self._heartbeat_id = ""  # Reset for first request
        self._task = asyncio.create_task(self._loop(), name="clob_heartbeat")
        logger.info(f"💓 Heartbeat: STARTED (interval={self._interval_s}s)")

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._stop_requested = True
        if self._task is not None:
            try:
                self._task.cancel()
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            except Exception as e:  # noqa: BLE001
                logger.debug(f"heartbeat stop: {e}")
        self._running = False
        logger.info("💓 Heartbeat: STOPPED")

    async def _loop(self) -> None:
        """Main heartbeat loop."""
        while not self._stop_requested:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                # Boot orchestrator pattern: log + continue
                logger.warning(f"💓 Heartbeat tick error: {type(e).__name__}: {e}")
                self._consecutive_fails += 1

            # Exponential backoff on consecutive failures (max 30s)
            sleep_s = min(self._interval_s * (2 ** min(self._consecutive_fails, 3)),
                          self._interval_s * 6)
            try:
                await asyncio.sleep(sleep_s)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        """Single heartbeat send + ID rotation."""
        loop = asyncio.get_running_loop()

        # SDK call (sync) — run in executor
        try:
            response = await loop.run_in_executor(
                None,
                lambda: self.client.post_heartbeat(self._heartbeat_id),
            )
        except Exception as e:  # noqa: BLE001
            err_str = str(e)
            # 400 Bad Request → server provides correct heartbeat_id
            if "400" in err_str or "Bad Request" in err_str:
                # Try to extract corrected ID from response
                # SDK might raise PolyApiException with response body
                logger.warning(f"💓 Heartbeat 400, resetting ID: {err_str[:120]}")
                self._heartbeat_id = ""
                self._consecutive_fails += 1
                return
            raise

        # Update ID for next request
        if isinstance(response, dict):
            new_id = response.get("heartbeat_id", "")
            if new_id and isinstance(new_id, str):
                self._heartbeat_id = new_id

        self._last_success_ts = time.time()
        self._consecutive_fails = 0
        # Verbose only if changed
        logger.debug(f"💓 Heartbeat OK (id={self._heartbeat_id[:12]}...)")


# ─────────────────────────────────────────────────────────────────────
# Module-level singleton (engine integration kolaylaştırıcısı)
# ─────────────────────────────────────────────────────────────────────
_default_task: Optional[HeartbeatTask] = None


def get_heartbeat_task(client=None) -> HeartbeatTask:
    """Get or create default singleton."""
    global _default_task
    if _default_task is None:
        if client is None:
            raise RuntimeError("First call requires client argument")
        _default_task = HeartbeatTask(client=client)
    return _default_task


async def start_heartbeat(client) -> HeartbeatTask:
    """Convenience: get singleton, start, return."""
    task = get_heartbeat_task(client)
    await task.start()
    return task


async def stop_heartbeat() -> None:
    global _default_task
    if _default_task is not None:
        await _default_task.stop()
