"""Unit tests for core/kill_switch.py — 3-channel emergency stop.

Coverage gap baseline (2026-04-22): `kill_switch.py` 0% / 37 stmts.
Phase 7 feature: file-based + in-memory + Telegram channels.

File-based kill is checked EVERY engine cycle — an external cron can
stop trading even if asyncio is stuck. This test pins the contract.

Scope:
  1. File-based trip (highest priority)
  2. Memory-based trip
  3. Activate / deactivate roundtrip
  4. File auto-cleanup on deactivate
  5. Status dict shape
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core import kill_switch as ks_mod
from core.kill_switch import KillSwitch


@pytest.fixture
def kill_file_tmp(tmp_path, monkeypatch):
    """Redirect KILL_FILE to a tmp_path so tests can't collide with real file."""
    tmp_file = str(tmp_path / "polypaper.stop")
    monkeypatch.setattr(ks_mod, "KILL_FILE", tmp_file)
    yield tmp_file
    # tmp_path auto-cleanup handles removal


class TestIsKilledChannels:
    def test_fresh_instance_not_killed(self, kill_file_tmp):
        ks = KillSwitch()
        assert ks.is_killed() is False

    def test_file_presence_trips_kill(self, kill_file_tmp):
        """Channel 1: file-based kill — external cron can stop us."""
        Path(kill_file_tmp).touch()
        ks = KillSwitch()
        assert ks.is_killed() is True
        # Also mirrors into memory flag so reason/killed_at are populated
        assert ks._memory_kill is True
        assert kill_file_tmp in ks._kill_reason

    def test_memory_kill_persists_without_file(self, kill_file_tmp):
        """Channel 2: activate() in-memory → is_killed True even w/o file."""
        ks = KillSwitch()
        ks.activate(reason="test")
        assert not os.path.exists(kill_file_tmp)
        assert ks.is_killed() is True


class TestActivateDeactivate:
    def test_activate_sets_all_fields(self, kill_file_tmp):
        ks = KillSwitch()
        ks.activate(reason="RiskLimit breached")
        assert ks._memory_kill is True
        assert ks._kill_reason == "RiskLimit breached"
        assert ks._killed_at != ""  # ISO timestamp populated

    def test_deactivate_clears_all_fields(self, kill_file_tmp):
        ks = KillSwitch()
        ks.activate(reason="test")
        ks.deactivate()
        assert ks._memory_kill is False
        assert ks._kill_reason == ""
        assert ks._killed_at == ""

    def test_deactivate_removes_kill_file(self, kill_file_tmp):
        """External kill file must be cleaned up on /resume — otherwise the
        file trips again on next is_killed() check."""
        Path(kill_file_tmp).touch()
        ks = KillSwitch()
        ks.is_killed()  # mirror file→memory
        ks.deactivate()
        assert not os.path.exists(kill_file_tmp)

    def test_deactivate_missing_file_no_crash(self, kill_file_tmp):
        """If file already gone, deactivate must not raise."""
        ks = KillSwitch()
        ks.activate("test")
        # No file exists — deactivate should still succeed
        ks.deactivate()
        assert ks._memory_kill is False


class TestGetStatus:
    def test_status_keys_present(self, kill_file_tmp):
        """UI contract — /kill_status handler reads these fields."""
        ks = KillSwitch()
        s = ks.get_status()
        expected = {"killed", "reason", "killed_at", "file_exists", "file_path", "memory_flag"}
        assert expected <= set(s.keys())

    def test_status_reflects_file_absence(self, kill_file_tmp):
        ks = KillSwitch()
        s = ks.get_status()
        assert s["killed"] is False
        assert s["file_exists"] is False
        assert s["memory_flag"] is False

    def test_status_reflects_activated_state(self, kill_file_tmp):
        ks = KillSwitch()
        ks.activate(reason="DAILY_LOSS_TRIP")
        s = ks.get_status()
        assert s["killed"] is True
        assert s["reason"] == "DAILY_LOSS_TRIP"
        assert s["killed_at"] != ""
        assert s["memory_flag"] is True


class TestIdempotency:
    def test_double_activate_safe(self, kill_file_tmp):
        """Calling activate twice shouldn't break — last reason wins."""
        ks = KillSwitch()
        ks.activate(reason="first")
        first_time = ks._killed_at
        ks.activate(reason="second")
        assert ks._kill_reason == "second"
        # Timestamp updated to latest call
        assert ks._killed_at >= first_time

    def test_deactivate_before_activate_safe(self, kill_file_tmp):
        """Deactivate on fresh instance must not raise."""
        ks = KillSwitch()
        ks.deactivate()  # no-op but shouldn't crash
        assert ks._memory_kill is False
