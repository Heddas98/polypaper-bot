"""
PolyPaper Bot - Kill Switch (Phase 7)
3-channel emergency stop inspired by probablyprofit:
1. File-based: touch /tmp/polypaper.stop (works when event loop hangs)
2. In-memory: set via code or Telegram command
3. Telegram: /kill and /resume commands

The file-based switch is checked EVERY engine cycle. Even if asyncio
is stuck, an external cron or monitoring script can stop trading.
"""
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger("polypaper.core.killswitch")

# data_store klasöründe — hem Windows hem sandbox'tan erişilebilir
KILL_FILE = os.path.join("data_store", "polypaper.stop")


class KillSwitch:
    """Multi-channel emergency stop."""

    def __init__(self):
        self._memory_kill = False
        self._kill_reason = ""
        self._killed_at: str = ""

    def is_killed(self) -> bool:
        """Check ALL channels. Called every engine cycle."""
        # Channel 1: File-based (highest priority, survives crashes)
        if os.path.exists(KILL_FILE):
            if not self._memory_kill:
                logger.warning(f"🛑 KILL SWITCH: File detected ({KILL_FILE})")
                self._memory_kill = True
                self._kill_reason = f"Kill file: {KILL_FILE}"
                self._killed_at = datetime.now(timezone.utc).isoformat()
            return True

        # Channel 2: In-memory
        if self._memory_kill:
            return True

        return False

    def activate(self, reason: str = "Manual"):
        """Activate kill switch via code or Telegram."""
        self._memory_kill = True
        self._kill_reason = reason
        self._killed_at = datetime.now(timezone.utc).isoformat()
        logger.warning(f"🛑 KILL SWITCH ACTIVATED: {reason}")

    def deactivate(self):
        """Resume trading. Removes file if exists."""
        self._memory_kill = False
        self._kill_reason = ""
        self._killed_at = ""
        # Remove kill file if it exists
        if os.path.exists(KILL_FILE):
            try:
                os.remove(KILL_FILE)
                logger.info(f"  Removed {KILL_FILE}")
            except OSError:
                pass
        logger.info("✅ KILL SWITCH DEACTIVATED: Trading resumed")

    def get_status(self) -> dict:
        file_exists = os.path.exists(KILL_FILE)
        return {
            "killed": self.is_killed(),
            "reason": self._kill_reason,
            "killed_at": self._killed_at,
            "file_exists": file_exists,
            "file_path": KILL_FILE,
            "memory_flag": self._memory_kill,
        }
