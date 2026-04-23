"""Unit tests for T11.8 — scripts/bare_except_check.py.

Directly imports `_check_file` for sandbox-safe test isolation (tmp_path
writes avoid core/ permission issues in WSL mount). Verifies:
  1. Clean file with narrow tuples passes (0 violations)
  2. `except:` naked caught
  3. `except Exception:` caught
  4. `except Exception as e:` caught
  5. `except BaseException:` caught
  6. `# noqa: BLE001` annotation suppresses (ruff standard)
  7. `# noqa: BLE-OK` annotation suppresses (T11.8 new)
  8. `# noqa: BLE-OK reason=...` annotation suppresses
  9. Silent `pass` detected (added to violation message)
 10. `_is_prod_path` correctly classifies dirs
 11. CLI entry: --all on clean core/ returns 0
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "bare_except_check.py"

# Import the module directly for unit tests
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import bare_except_check as guard  # noqa: E402


def test_script_file_exists():
    assert SCRIPT.exists(), f"guard script missing: {SCRIPT}"


def test_clean_file_zero_violations(tmp_path: Path):
    f = tmp_path / "clean.py"
    f.write_text(
        "def foo():\n"
        "    try:\n"
        "        pass\n"
        "    except (ValueError, TypeError) as e:\n"
        "        raise\n"
    )
    assert guard._check_file(f) == []


def test_naked_except_caught(tmp_path: Path):
    f = tmp_path / "naked.py"
    f.write_text(
        "def foo():\n"
        "    try:\n"
        "        pass\n"
        "    except:\n"
        "        pass\n"
    )
    viols = guard._check_file(f)
    assert len(viols) == 1
    assert "naked" in viols[0][2].lower()


def test_except_exception_caught(tmp_path: Path):
    f = tmp_path / "exc.py"
    f.write_text(
        "def foo():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        raise\n"
    )
    viols = guard._check_file(f)
    assert len(viols) == 1
    assert "no tuple narrowing" in viols[0][2]


def test_except_exception_as_caught(tmp_path: Path):
    f = tmp_path / "exc_as.py"
    f.write_text(
        "def foo():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as e:\n"
        "        raise\n"
    )
    viols = guard._check_file(f)
    assert len(viols) == 1


def test_except_baseexception_caught(tmp_path: Path):
    f = tmp_path / "base.py"
    f.write_text(
        "def foo():\n"
        "    try:\n"
        "        pass\n"
        "    except BaseException:\n"
        "        raise\n"
    )
    viols = guard._check_file(f)
    assert len(viols) == 1
    assert "BaseException" in viols[0][2]


def test_noqa_ble001_suppresses(tmp_path: Path):
    """ruff standard `# noqa: BLE001` must suppress (T7.6 existing pattern)."""
    f = tmp_path / "noqa_ble001.py"
    f.write_text(
        "def foo():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as e:  # noqa: BLE001\n"
        "        raise\n"
    )
    assert guard._check_file(f) == []


def test_noqa_ble_ok_suppresses(tmp_path: Path):
    """T11.8 `# noqa: BLE-OK` must suppress."""
    f = tmp_path / "noqa_ble_ok.py"
    f.write_text(
        "def foo():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as e:  # noqa: BLE-OK\n"
        "        raise\n"
    )
    assert guard._check_file(f) == []


def test_noqa_ble_ok_reason_suppresses(tmp_path: Path):
    """T11.8 `# noqa: BLE-OK reason=...` must suppress."""
    f = tmp_path / "noqa_reason.py"
    f.write_text(
        "def foo():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as e:  # noqa: BLE-OK reason=bg_task contract\n"
        "        raise\n"
    )
    assert guard._check_file(f) == []


def test_silent_pass_detected(tmp_path: Path):
    """`except Exception: pass` anti-pattern flagged with extra marker."""
    f = tmp_path / "silent.py"
    f.write_text(
        "def foo():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"
        "        pass\n"
    )
    viols = guard._check_file(f)
    assert len(viols) == 1
    assert "silent pass" in viols[0][2]


def test_is_prod_path_classification():
    assert guard._is_prod_path("core/engine.py")
    assert not guard._is_prod_path("tests/unit/test_x.py")
    assert not guard._is_prod_path("scripts/foo.py")
    assert not guard._is_prod_path("_archive/old.py")
    # Advisory dirs not in strict PROD_PREFIXES
    assert not guard._is_prod_path("data/websocket.py")
    assert not guard._is_prod_path("telegram_bot/bot.py")


def test_cli_all_on_clean_core_zero_exit():
    """CLI `--all` on production core/ (strict zone) must exit 0 after
    T7.6 + T8.1 cleanup. Regression guard — if any core/ file regresses,
    this test fails before the pre-commit hook blocks the commit."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--all"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"core/ strict zone must be clean (T7.6 doctrine); got FAIL:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # Note: advisory (data/telegram_bot/db) violations are EXPECTED and
    # do not fail — they are T11.8-B forward work (reported to stdout).


def test_comments_not_flagged(tmp_path: Path):
    """Regex patterns inside `#`-commented lines should NOT be flagged."""
    f = tmp_path / "comment.py"
    f.write_text(
        "def foo():\n"
        "    # Example of what NOT to do: except Exception:\n"
        "    try:\n"
        "        pass\n"
        "    except (ValueError,):\n"
        "        raise\n"
    )
    assert guard._check_file(f) == []
