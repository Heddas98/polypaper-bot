"""Epic 10 T10.2 regression test — admin gate on state-mutating callbacks.

Pins the requirement that every callback handler that mutates engine /
filter / strategy state must check the caller's identity against
ADMIN_TELEGRAM_ID / ADMIN_CHAT_ID before doing any side effect.

AST-based test (no python-telegram-bot import) so it also runs in the
sandbox where telegram lib is not installed.

Covers:
- filters_handler.filters_callback (C1)
- ai_handler.brain_toggle_callback (C2)
- strategies.start_strategy_callback / stop_strategy_callback /
  delete_strategy_callback / start_all_callback /
  stop_all_callback (C3)
- hyperopt_handler.hyperopt_apply_callback (C4 — Epic 10 T10.6
  post-audit: T10.2 kapsam kaçağı, strategies + hyperopt_results
  tablosunda UPDATE yapıyor — admin gate zorunlu)
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _source(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _find_func(src: str, name: str):
    """Return the AsyncFunctionDef or FunctionDef named `name`."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")


# ─── C1: filters_callback ───────────────────────────────────────────

def test_filters_callback_has_admin_gate():
    """The callback reads ADMIN_TELEGRAM_ID / ADMIN_CHAT_ID and rejects
    non-admin callers before any state mutation."""
    src = _source("telegram_bot/handlers/filters_handler.py")
    func = _find_func(src, "filters_callback")
    func_src = ast.unparse(func)

    assert "ADMIN_TELEGRAM_ID" in func_src, (
        "filters_callback must check ADMIN_TELEGRAM_ID — Epic 10 T10.2 C1")
    assert "effective_user" in func_src, (
        "filters_callback must read update.effective_user for gate")
    # Gate must appear before any os.environ mutation.
    gate_idx = func_src.find("ADMIN_TELEGRAM_ID")
    env_set_idx = func_src.find('os.environ["')
    # env_set_idx can be -1 if the mutation uses a different idiom;
    # find any environ[ assign:
    if env_set_idx == -1:
        env_set_idx = func_src.find("os.environ[")
    if env_set_idx != -1:
        assert gate_idx < env_set_idx, (
            "filters_callback admin gate must run BEFORE os.environ "
            "mutation")


# ─── C2: brain_toggle_callback ──────────────────────────────────────

def test_brain_toggle_callback_has_admin_gate():
    """brain_toggle_callback must reject non-admin callers before
    mutating engine.brain_flags / engine._kelly_mode."""
    src = _source("telegram_bot/handlers/ai_handler.py")
    func = _find_func(src, "brain_toggle_callback")
    func_src = ast.unparse(func)

    assert "ADMIN_TELEGRAM_ID" in func_src, (
        "brain_toggle_callback must check ADMIN_TELEGRAM_ID — T10.2 C2")
    gate_idx = func_src.find("ADMIN_TELEGRAM_ID")
    # Mutation points: engine.brain_flags or engine._kelly_mode
    mut_idx = min(
        (i for i in (
            func_src.find("brain_flags["),
            func_src.find("_kelly_mode"),
        ) if i != -1),
        default=-1,
    )
    if mut_idx != -1:
        assert gate_idx < mut_idx, (
            "brain_toggle_callback admin gate must run BEFORE brain_flags "
            "or _kelly_mode mutation")


# ─── C3: strategy callbacks ─────────────────────────────────────────

STRATEGY_CALLBACKS = [
    "start_strategy_callback",
    "stop_strategy_callback",
    "delete_strategy_callback",
    "start_all_callback",
    "stop_all_callback",
]


@pytest.mark.parametrize("fn_name", STRATEGY_CALLBACKS)
def test_strategy_callback_has_admin_gate(fn_name):
    """Each of the 5 state-mutating strategy callbacks must call the
    module-level _is_admin_call() helper before any DB mutation."""
    src = _source("telegram_bot/handlers/strategies.py")
    func = _find_func(src, fn_name)
    func_src = ast.unparse(func)

    assert "_is_admin_call" in func_src, (
        f"{fn_name} must call _is_admin_call() — Epic 10 T10.2 C3")
    assert "_deny_callback" in func_src, (
        f"{fn_name} must use _deny_callback() on refusal")

    # Gate must appear before any update_strategy_status / delete_strategy.
    gate_idx = func_src.find("_is_admin_call")
    mut_points = [
        func_src.find("update_strategy_status"),
        func_src.find("delete_strategy("),
    ]
    mut_idx = min((i for i in mut_points if i != -1), default=-1)
    if mut_idx != -1:
        assert gate_idx < mut_idx, (
            f"{fn_name}: admin gate must run BEFORE DB mutation")


# ─── C4: hyperopt_apply_callback (Epic 10 T10.6 post-audit) ────────

def test_hyperopt_apply_callback_has_admin_gate():
    """hyperopt_apply_callback must reject non-admin callers before
    mutating strategies / hyperopt_results tables.

    T10.2 original audit kaçırmıştı (only 7 callbacks taranmıştı, 85
    var). Post-audit'te yakalandı ve T10.6 ile fix'lendi.
    """
    src = _source("telegram_bot/handlers/hyperopt_handler.py")
    func = _find_func(src, "hyperopt_apply_callback")
    func_src = ast.unparse(func)

    assert "_is_admin_call" in func_src, (
        "hyperopt_apply_callback must call _is_admin_call() — "
        "Epic 10 T10.6 post-audit C4")
    assert "_deny_callback" in func_src, (
        "hyperopt_apply_callback must use _deny_callback() on refusal")

    # Gate must appear before any DB UPDATE. The body literally contains
    # both "UPDATE hyperopt_results" and "UPDATE strategies" SQL.
    gate_idx = func_src.find("_is_admin_call")
    mut_points = [
        func_src.find("UPDATE hyperopt_results"),
        func_src.find("UPDATE strategies"),
        func_src.find("registry.set_config"),
    ]
    mut_idx = min((i for i in mut_points if i != -1), default=-1)
    if mut_idx != -1:
        assert gate_idx < mut_idx, (
            "hyperopt_apply_callback: admin gate must run BEFORE DB "
            "mutation (UPDATE hyperopt_results / UPDATE strategies / "
            "registry.set_config)")


def test_hyperopt_apply_callback_imports_helpers():
    """Sanity: module imports _is_admin_call + _deny_callback from
    strategies.py — single-source-of-truth ilkesi."""
    src = _source("telegram_bot/handlers/hyperopt_handler.py")
    # Look for the exact import line.
    assert "from telegram_bot.handlers.strategies import _is_admin_call" in src, (
        "hyperopt_handler must import _is_admin_call from strategies")
    assert "_deny_callback" in src, (
        "hyperopt_handler must import _deny_callback from strategies")


def test_is_admin_call_helper_fallback_dev_mode():
    """When ADMIN_TELEGRAM_ID / ADMIN_CHAT_ID are both unset (dev mode)
    _is_admin_call returns True — preserves existing dev behavior."""
    src = _source("telegram_bot/handlers/strategies.py")
    func = _find_func(src, "_is_admin_call")
    func_src = ast.unparse(func)
    # Expectation: no-admin-configured branch returns True.
    assert "return True" in func_src, (
        "_is_admin_call must allow calls when no admin configured "
        "(dev mode) — otherwise every test that doesn't set the env "
        "would break")
