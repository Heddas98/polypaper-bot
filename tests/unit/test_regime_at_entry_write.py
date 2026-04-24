"""T4.10 — regime_at_entry write path regression.

Verifies the 3-piece change:
  1. db/models.py: Execution dataclass has regime_at_entry field
  2. db/database.py: create_execution() SQL includes regime_at_entry column + bind
  3. core/engine_fills.py: Execution(...) call passes regime snapshot

Strategy: source-grep + AST inspect. No live engine boot needed.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS = REPO_ROOT / "db" / "models.py"
DATABASE = REPO_ROOT / "db" / "database.py"
ENGINE_FILLS = REPO_ROOT / "core" / "engine_fills.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_execution_dataclass_has_regime_at_entry():
    src = _read(MODELS)
    # Match `regime_at_entry: Optional[str] = None` (pydantic field style)
    pattern = re.compile(
        r"regime_at_entry\s*:\s*Optional\[str\]\s*=\s*None"
    )
    assert pattern.search(src), (
        "Execution dataclass must have `regime_at_entry: Optional[str] = None` "
        "field. T4.10 fix in db/models.py."
    )


def test_execution_dataclass_field_is_in_class_body():
    """AST verify: regime_at_entry is a field of Execution class (not just
    a stray module-level identifier)."""
    src = _read(MODELS)
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Execution":
            for item in node.body:
                # AnnAssign: `regime_at_entry: Optional[str] = None`
                if (isinstance(item, ast.AnnAssign)
                        and isinstance(item.target, ast.Name)
                        and item.target.id == "regime_at_entry"):
                    found = True
                    break
    assert found, (
        "Execution.regime_at_entry must be an annotated dataclass field"
    )


def test_create_execution_sql_includes_regime_at_entry():
    src = _read(DATABASE)
    # SQL fragment check — column name in INSERT
    sql_pattern = re.compile(
        r"INSERT INTO executions[^)]*regime_at_entry",
        re.DOTALL,
    )
    assert sql_pattern.search(src), (
        "db/database.py create_execution() INSERT must list regime_at_entry "
        "column. T4.10 fix."
    )


def test_create_execution_binds_regime_at_entry():
    src = _read(DATABASE)
    # Binding: execution.regime_at_entry must appear in the .execute() tuple
    bind_pattern = re.compile(r"execution\.regime_at_entry")
    assert bind_pattern.search(src), (
        "db/database.py create_execution() must bind execution.regime_at_entry "
        "in the parameter tuple."
    )


def test_create_execution_value_count_matches_columns():
    """SQL must have matching VALUES (?,?,...) count = column count.
    With T4.10 it's 25 cols + 25 ? marks (was 24 + 24)."""
    src = _read(DATABASE)
    # Find the create_execution function body
    m = re.search(
        r"async def create_execution.*?await self\.conn\.commit\(\)",
        src, re.DOTALL,
    )
    assert m, "create_execution function not found"
    body = m.group(0)
    # Count ? marks in the VALUES (...)
    values_match = re.search(r"VALUES\s*\(([\?,\s]+)\)", body)
    assert values_match, "VALUES (?,?,...) clause not found"
    placeholders = values_match.group(1).count("?")
    assert placeholders == 25, (
        f"Expected 25 ? placeholders (24 original + 1 regime_at_entry); "
        f"got {placeholders}. SQL/tuple drift!"
    )


def test_engine_fills_passes_regime_at_entry():
    src = _read(ENGINE_FILLS)
    # Look for `regime_at_entry=` in an Execution(...) call near self.regime usage
    pattern = re.compile(r"regime_at_entry\s*=\s*_regime_at_entry")
    assert pattern.search(src), (
        "core/engine_fills.py must set regime_at_entry=<snapshot> when "
        "constructing Execution(). T4.10 fix."
    )


def test_engine_fills_snapshots_self_regime():
    """The snapshot must come from self.regime.regime (RegimeClassifier)."""
    src = _read(ENGINE_FILLS)
    pattern = re.compile(
        r"_regime_obj\s*=\s*getattr\(self,\s*[\"']regime[\"']"
    )
    assert pattern.search(src), (
        "T4.10: snapshot must use getattr(self, 'regime', None) defensive "
        "pattern (mixin-safe, regime may be unset in test harness)."
    )


def test_engine_fills_snapshot_handles_missing_regime():
    """Falls back to None when regime attr or .regime field is missing.
    No crash, no AttributeError surfaced."""
    src = _read(ENGINE_FILLS)
    # try/except wrapper around the snapshot
    pattern = re.compile(
        r"_regime_at_entry\s*=\s*None.*?try:.*?_regime_at_entry.*?except.*?_regime_at_entry\s*=\s*None",
        re.DOTALL,
    )
    assert pattern.search(src), (
        "T4.10: snapshot must be wrapped in try/except with None fallback. "
        "Avoids crashing if RegimeClassifier shape drifts."
    )
