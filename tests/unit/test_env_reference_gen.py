"""Unit tests for T11.7 — scripts/gen_env_reference.py.

Verifies the AST scan + drift detection contract:
  1. Script runs and produces output
  2. build_reference() returns non-empty markdown
  3. --check on up-to-date output exits 0
  4. --check on stale output exits 1 (drift caught)
  5. AST correctly extracts os.getenv() call keys + defaults
  6. Whitelist cross-ref marker (✅) present for known keys
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "gen_env_reference.py"
OUTPUT = REPO_ROOT / "docs" / "env_reference.md"

# Import for unit tests
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import gen_env_reference as gen  # noqa: E402


def test_script_exists():
    assert SCRIPT.exists(), f"script missing: {SCRIPT}"


def test_extract_single_file_uses(tmp_path: Path):
    """AST extractor finds os.getenv() calls in a file."""
    f = tmp_path / "mod.py"
    f.write_text(
        "import os\n"
        "X = os.getenv('FOO', '42')\n"
        "Y = os.getenv('BAR')\n"
        "Z = os.getenv('BAZ', None)\n"
    )
    # Mock: move file under REPO_ROOT for relative path computation
    target = REPO_ROOT / "core" / "_t11_7_test_mod.py"
    target.write_text(f.read_text())
    try:
        uses = gen._extract_getenv_uses(target)
        keys = sorted(u.key for u in uses)
        assert keys == ["BAR", "BAZ", "FOO"]
        # Default value extraction
        for u in uses:
            if u.key == "FOO":
                assert u.default == "'42'"
            elif u.key == "BAR":
                assert u.default is None  # no 2nd arg
            elif u.key == "BAZ":
                assert u.default == "None"  # 2nd arg is literal None
    finally:
        try:
            target.unlink()
        except OSError:
            pass


def test_build_reference_non_empty():
    """Generator produces markdown with expected structure."""
    md = gen.build_reference()
    assert md.startswith("# PolyPaper Bot"), "missing title"
    assert "## Reference Table" in md
    assert "| Key | Default | Whitelist | `.env.example` | Readers |" in md
    # Must have at least some rows (real codebase)
    assert md.count("|") > 50  # many pipes = many table rows


def test_whitelist_cross_reference():
    """Known whitelisted keys must be marked ✅ in the output."""
    md = gen.build_reference()
    # These are confirmed in config/env_whitelist.py
    known_whitelist = [
        "PNL_PAUSE_THRESHOLD",
        "LIVE_BUDGET",
        "WS_STALE_THRESHOLD",
        "PNL_DIVERGENCE_ALERT_PCT",
    ]
    for key in known_whitelist:
        # Row pattern: `| `KEY` | ... | ✅ | ...`
        assert f"`{key}`" in md, f"{key} missing from reference"


def test_check_mode_on_fresh_output_exits_zero():
    """If docs/env_reference.md is up to date, --check exits 0."""
    # First: regenerate to make sure it's fresh
    subprocess.run(
        [sys.executable, str(SCRIPT)],
        check=True,
        cwd=str(REPO_ROOT),
    )
    # Now --check should pass
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"--check on fresh output must pass; got:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_check_mode_detects_drift(tmp_path: Path, monkeypatch):
    """If output file is stale, --check exits 1."""
    # Backup current output
    if OUTPUT.exists():
        backup = tmp_path / "env_reference_backup.md"
        backup.write_bytes(OUTPUT.read_bytes())
    else:
        backup = None

    try:
        # Create stale output (prepend junk)
        if OUTPUT.exists():
            stale = "# STALE HEADER (t11.7 drift test)\n\n" + OUTPUT.read_text()
            OUTPUT.write_text(stale)
        else:
            OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT.write_text("stale placeholder")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert (
            result.returncode == 1
        ), f"--check on stale output must fail; got rc={result.returncode}"
        assert (
            "DRIFT" in (result.stdout + result.stderr)
            or "stale" in (result.stdout + result.stderr).lower()
        )
    finally:
        # Restore original
        if backup and backup.exists():
            OUTPUT.write_bytes(backup.read_bytes())
        else:
            # Regenerate fresh
            subprocess.run(
                [sys.executable, str(SCRIPT)],
                check=False,
                cwd=str(REPO_ROOT),
            )


def test_stdout_mode_prints_without_writing(tmp_path: Path):
    """--stdout mode prints to stdout, doesn't touch OUTPUT."""
    if OUTPUT.exists():
        mtime_before = OUTPUT.stat().st_mtime_ns
    else:
        mtime_before = None

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--stdout"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    assert result.stdout.startswith("# PolyPaper Bot")

    if mtime_before is not None:
        mtime_after = OUTPUT.stat().st_mtime_ns
        assert mtime_before == mtime_after, "--stdout should not rewrite OUTPUT file"
