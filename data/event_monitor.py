"""
Phase 59: Event Calendar Monitor
Reads data/event_calendar.json and provides pre-event volatility warnings.

Usage:
    monitor = EventMonitor()
    alert = monitor.get_active_event()
    if alert:
        # We're in a pre-event window — adjust strategy parameters
        # e.g., tighten thresholds, reduce position sizes
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("polypaper.data.event_monitor")

# Path to event calendar JSON
_CALENDAR_PATH = Path(__file__).parent / "event_calendar.json"


class EventAlert:
    """Represents an active event window."""
    __slots__ = ("name", "event_time", "impact", "hours_until",
                 "pre_hours", "event_type")

    def __init__(self, name: str, event_time: datetime, impact: str,
                 hours_until: float, pre_hours: float, event_type: str):
        self.name = name
        self.event_time = event_time
        self.impact = impact
        self.hours_until = hours_until
        self.pre_hours = pre_hours
        self.event_type = event_type

    @property
    def severity(self) -> float:
        """0.0-1.0 severity based on impact and proximity."""
        impact_mult = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(self.impact, 0.3)
        # Closer to event = higher severity
        proximity = max(0.0, 1.0 - (self.hours_until / self.pre_hours))
        return round(impact_mult * proximity, 3)

    def __repr__(self):
        return (f"EventAlert({self.name}, {self.hours_until:.1f}h away, "
                f"impact={self.impact}, severity={self.severity})")


class EventMonitor:
    """Monitors upcoming macro events and signals pre-event windows."""

    def __init__(self, calendar_path: Optional[str] = None):
        self._path = Path(calendar_path) if calendar_path else _CALENDAR_PATH
        self._events: list[dict] = []
        self._last_load = 0.0
        self._reload_interval = 300  # reload every 5 min
        self._enabled = os.getenv("EVENT_CALENDAR_ENABLED", "true").lower() == "true"

    def _load(self):
        """Load or reload the calendar JSON."""
        import time
        now = time.time()
        if now - self._last_load < self._reload_interval and self._events:
            return
        try:
            if self._path.exists():
                with open(self._path) as f:
                    data = json.load(f)
                self._events = data.get("upcoming", [])
                self._last_load = now
                logger.debug(f"Event calendar: loaded {len(self._events)} events")
            else:
                logger.debug(f"Event calendar not found: {self._path}")
                self._events = []
        except Exception as e:
            logger.warning(f"Event calendar load error: {e}")
            self._events = []

    def get_active_event(self) -> Optional[EventAlert]:
        """Check if we're in a pre-event volatility window.

        Returns the most impactful active EventAlert, or None.
        """
        if not self._enabled:
            return None

        self._load()
        if not self._events:
            return None

        now = datetime.now(timezone.utc)
        best: Optional[EventAlert] = None

        for ev in self._events:
            try:
                dt_str = ev.get("datetime", "")
                if not dt_str:
                    continue
                event_time = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                pre_hours = float(ev.get("pre_hours", 2))

                # Skip past events
                if event_time < now:
                    continue

                hours_until = (event_time - now).total_seconds() / 3600.0

                # Check if we're in the pre-event window
                if hours_until <= pre_hours:
                    alert = EventAlert(
                        name=ev.get("name", "Unknown"),
                        event_time=event_time,
                        impact=ev.get("impact", "medium"),
                        hours_until=hours_until,
                        pre_hours=pre_hours,
                        event_type=ev.get("type", "unknown"),
                    )
                    # Keep highest severity
                    if best is None or alert.severity > best.severity:
                        best = alert
            except Exception:
                continue

        return best

    def get_upcoming(self, hours: float = 24) -> list[dict]:
        """List events in next N hours (for /events command)."""
        self._load()
        now = datetime.now(timezone.utc)
        result = []
        for ev in self._events:
            try:
                dt_str = ev.get("datetime", "")
                if not dt_str:
                    continue
                event_time = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                if event_time < now:
                    continue
                hours_until = (event_time - now).total_seconds() / 3600.0
                if hours_until <= hours:
                    result.append({
                        "name": ev.get("name"),
                        "time": event_time.strftime("%Y-%m-%d %H:%M UTC"),
                        "hours_until": round(hours_until, 1),
                        "impact": ev.get("impact"),
                        "type": ev.get("type"),
                    })
            except Exception:
                continue
        return sorted(result, key=lambda x: x["hours_until"])
