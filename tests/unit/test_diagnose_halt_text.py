"""T6.3-B regression — /diagnose halt_text source correctness.

ESKI bug:
  halt_text = rs.get("halt_reason", "No halt") if rs.get("halted") else "Active"
                                                                          ^
                              halted=False -> "Active" (yaniltici, kullanici "halt active" sandi)

YENI:
  halt_text = rs.get("halt_reason", "Halted") if rs.get("halted") else "No halt"

This test pins the corrected mapping so the bug never returns.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DIAGNOSE = REPO_ROOT / "telegram_bot" / "handlers" / "diagnose_handler.py"


def _read_source() -> str:
    return DIAGNOSE.read_text(encoding="utf-8")


def test_no_legacy_active_label_in_halt_text():
    """Old buggy line `... else \"Active\"` must not appear in halt_text."""
    src = _read_source()
    legacy_pattern = re.compile(
        r'halt_text\s*=\s*[^=\n]*else\s*"Active"'
    )
    matches = legacy_pattern.findall(src)
    assert not matches, (
        f"T6.3-B regression: legacy halt_text 'Active' label found in:\n"
        f"{matches}\n"
        f"Use 'No halt' for halted=False branch (T6.3-B fix)."
    )


def test_halt_text_uses_no_halt_when_not_halted():
    """The corrected pattern: halted=False branch should yield 'No halt'."""
    src = _read_source()
    correct_pattern = re.compile(
        r'halt_text\s*=\s*[^=\n]*else\s*"No halt"'
    )
    occurrences = correct_pattern.findall(src)
    assert len(occurrences) >= 2, (
        f"Expected at least 2 occurrences of correct pattern "
        f"`else \"No halt\"` (one per halt_text site); got {len(occurrences)}.\n"
        f"diagnose_handler.py has 2 halt_text formation sites; both must use "
        f"the T6.3-B-correct pattern."
    )


def test_halt_emoji_logic_unchanged():
    """The emoji logic (halted=True -> red, False -> green) is unchanged
    by T6.3-B; pin it to catch accidental flips."""
    src = _read_source()
    emoji_pattern = re.compile(
        r'halt_emoji\s*=\s*"🛑"\s*if\s*rs\.get\("halted",?\s*False\)\s*else\s*"✅"'
    )
    matches = emoji_pattern.findall(src)
    assert len(matches) >= 2, (
        f"halt_emoji logic should be unchanged; expected 2+ occurrences of "
        f'`"🛑" if halted else "✅"` pattern, got {len(matches)}.'
    )


def test_halted_true_branch_uses_halt_reason():
    """When halted=True, text should fall back to halt_reason (with 'Halted'
    as final fallback if reason is missing)."""
    src = _read_source()
    # Match `rs.get("halt_reason", "Halted")` (or "No halt" -- old default)
    # The corrected pattern uses "Halted" as the missing-reason fallback
    pattern = re.compile(
        r'rs\.get\("halt_reason",\s*"Halted"\)\s*if\s*rs\.get\("halted"\)'
    )
    matches = pattern.findall(src)
    assert len(matches) >= 2, (
        f"halted=True branch should use rs.get('halt_reason', 'Halted'); "
        f"got {len(matches)} occurrences. T6.3-B reverses old fallback "
        f"text from 'No halt' to 'Halted' to keep semantics correct."
    )
