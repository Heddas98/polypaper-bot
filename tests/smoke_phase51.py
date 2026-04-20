"""
Phase 51 smoke suite — locks in the HTML escape sweep and other P51 invariants.

Run standalone: `py -3.11 tests/smoke_phase51.py`
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HANDLERS = REPO / "telegram_bot" / "handlers"
ESC_IMPORT_ATTR = "esc"
ESC_IMPORT_MOD = "telegram_bot.templates.safe_html"

_failures: list[str] = []


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _fail(msg: str) -> None:
    print(f"  ❌ {msg}")
    _failures.append(msg)


def _file_imports_esc(src: str) -> bool:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == ESC_IMPORT_MOD:
                for alias in node.names:
                    if alias.name == ESC_IMPORT_ATTR:
                        return True
    return False


def _file_uses_html(src: str) -> bool:
    return ('parse_mode="HTML"' in src or "parse_mode='HTML'" in src
            or "ParseMode.HTML" in src)


def check_html_escape_sweep() -> None:
    """P51-01: every HTML handler must import esc()."""
    print("▶ P51-01 HTML escape sweep coverage")
    total = 0
    covered = 0
    missing: list[str] = []
    for f in sorted(HANDLERS.glob("*.py")):
        if f.name == "__init__.py":
            continue
        src = f.read_text(encoding="utf-8")
        if not _file_uses_html(src):
            continue
        total += 1
        if _file_imports_esc(src):
            covered += 1
        else:
            missing.append(f.name)
    if total == 0:
        _fail("no HTML handlers found — repo layout changed?")
        return
    if covered == total:
        _ok(f"{covered}/{total} HTML handlers import esc")
    else:
        _fail(f"{covered}/{total} HTML handlers import esc; missing: {missing}")


def check_safe_html_module() -> None:
    """Ensure safe_html module still exposes esc and esc_code."""
    print("▶ safe_html module contract")
    mod = REPO / "telegram_bot" / "templates" / "safe_html.py"
    if not mod.exists():
        _fail("telegram_bot/templates/safe_html.py missing")
        return
    src = mod.read_text(encoding="utf-8")
    if "def esc(" in src:
        _ok("esc() defined")
    else:
        _fail("esc() missing")
    if "def esc_code(" in src:
        _ok("esc_code() defined")
    else:
        _fail("esc_code() missing")
    if "html.escape" in src:
        _ok("uses stdlib html.escape")
    else:
        _fail("should use stdlib html.escape")


def check_handler_syntax() -> None:
    """Every handler file must parse cleanly."""
    print("▶ Handler syntax")
    bad: list[str] = []
    for f in sorted(HANDLERS.glob("*.py")):
        try:
            ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError as e:
            bad.append(f"{f.name}:{e.lineno}")
    if bad:
        _fail(f"syntax errors in {len(bad)} files: {bad[:5]}")
    else:
        count = len(list(HANDLERS.glob("*.py")))
        _ok(f"{count} handler files parse cleanly")


def check_intent_parser() -> None:
    """P51-04/05: keyword-layer catalog must route the canonical examples."""
    print("▶ P51-04/05 intent parser routing")
    sys.path.insert(0, str(REPO))
    try:
        from core.intent_parser import parse_intent_sync, COMMAND_CATALOG
    except Exception as e:
        _fail(f"intent_parser import: {e}")
        return
    cases = [
        ("risk durumum ne", "/rs"),
        ("stratejilerim", "/strategies"),
        ("bakiye ne kadar", "/dashboard"),
        ("son 10 btc trade", "/trades"),
        ("pnl grafiği 30 gün", "/stats_chart"),
        ("alarm kur btc > 0.6", "/alert"),
        ("karşılaştır hour_edge streak_reversal", "/compare"),
        ("becker replay hour_edge BTC", "/becker_replay"),
        ("shadow ne durumda", "/shadow"),
        ("canary durumu", "/canary"),
        ("brain ne yapıyor", "/brain"),
        ("backtest yap hour_edge", "/backtest_v2"),
    ]
    fail: list[str] = []
    for text, expected in cases:
        try:
            r = parse_intent_sync(text)
        except Exception as e:
            fail.append(f"{text!r}: raised {e}")
            continue
        if r.command != expected:
            fail.append(f"{text!r} → {r.command or 'None'} (expected {expected})")
    if fail:
        _fail(f"{len(fail)}/{len(cases)} intent cases failed: {fail[:3]}")
    else:
        _ok(f"{len(cases)}/{len(cases)} intent routes match")
    if len(COMMAND_CATALOG) < 20:
        _fail(f"COMMAND_CATALOG only has {len(COMMAND_CATALOG)} entries")
    else:
        _ok(f"COMMAND_CATALOG: {len(COMMAND_CATALOG)} commands")


def check_fill_model() -> None:
    """P51-06: MAKER / MAKER_HYBRID modes must exist and dispatch."""
    print("▶ P51-06 backtest maker fill path")
    sys.path.insert(0, str(REPO))
    try:
        from backtest.simulation.fill_model import FillMode, FillSimulator
        from backtest.strategies.base import Direction
    except Exception as e:
        _fail(f"fill_model import: {e}")
        return
    modes = {m.value for m in FillMode}
    for needed in ("maker", "maker_hybrid"):
        if needed not in modes:
            _fail(f"FillMode missing {needed}")
            return
    _ok("FillMode has maker + maker_hybrid")
    from types import SimpleNamespace
    snap = SimpleNamespace(
        up_best_bid=0.48, up_best_ask=0.50,
        up_bid_depth=1000, up_ask_depth=1000,
        down_best_bid=0.48, down_best_ask=0.50,
        down_bid_depth=1000, down_ask_depth=1000,
        ts=1700000000.0,
    )
    sim = FillSimulator(mode=FillMode.MAKER_HYBRID, maker_queue_probability=0.0)
    r = sim.simulate_fill(Direction.UP, 100.0, snap)
    if r.filled and not r.is_maker:
        _ok("MAKER_HYBRID falls back to taker when queue prob=0")
    else:
        _fail(f"MAKER_HYBRID fallback broken: filled={r.filled} is_maker={r.is_maker}")
    sim2 = FillSimulator(mode=FillMode.MAKER, maker_queue_probability=1.0)
    r2 = sim2.simulate_fill(Direction.UP, 100.0, snap)
    if r2.filled and r2.is_maker and r2.fill_price == 0.48:
        _ok("MAKER posts at best_bid and fills at prob=1")
    else:
        _fail(f"MAKER certain fill broken: filled={r2.filled} price={r2.fill_price}")


def check_callback_proxy() -> None:
    """BUG-FIX: CallbackUpdateProxy must expose callback_query.message as
    .message while delegating other attributes to the underlying update.
    Regression guard for stats_hub/risk_hub 'Route failed' errors."""
    print("▶ BUG-FIX callback proxy")
    sys.path.insert(0, str(REPO))
    try:
        from telegram_bot.templates.callback_proxy import CallbackUpdateProxy
    except Exception as e:
        _fail(f"import: {e}")
        return
    class _Msg:
        def __init__(self, tag): self.tag = tag
        def reply_text(self, *a, **k): return f"replied-from-{self.tag}"
    class _Query:
        def __init__(self, m): self.message = m
    class _Update:
        def __init__(self, msg, cq):
            self.message = msg
            self.callback_query = cq
            self.effective_user = "fake-user"
    origin = _Msg("origin")
    update = _Update(None, _Query(origin))
    proxy = CallbackUpdateProxy.from_update(update)
    if proxy is update:
        _fail("proxy returned real update for callback-style input")
        return
    if proxy.message is not origin:
        _fail(f"proxy.message wrong: {proxy.message}")
        return
    if proxy.message.reply_text() != "replied-from-origin":
        _fail("proxy.message.reply_text() broken")
        return
    if proxy.effective_user != "fake-user":
        _fail("delegation broken for effective_user")
        return
    _ok("proxy exposes origin message + delegates effective_user")
    # Non-callback update should pass through untouched
    plain_msg = _Msg("plain")
    plain = _Update(plain_msg, None)
    passthrough = CallbackUpdateProxy.from_update(plain)
    if passthrough is not plain:
        _fail("plain update should pass through untouched")
        return
    _ok("plain updates pass through untouched")


def main() -> int:
    print("=" * 60)
    print("Phase 51 Smoke Suite")
    print("=" * 60)
    check_safe_html_module()
    check_html_escape_sweep()
    check_handler_syntax()
    check_intent_parser()
    check_fill_model()
    check_callback_proxy()
    print()
    if _failures:
        print(f"❌ {len(_failures)} Phase 51 check(s) failed.")
        return 1
    print("✅ All Phase 51 smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
