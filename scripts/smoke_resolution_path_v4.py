"""
Sprint 5 HOTFIX v4 — Static smoke test for resolution path fix.

Verifies the 4 code changes landed in:
  1. data/polymarket_client.py   — check_market_resolved rewrite + get_resolution_price
  2. core/engine_monitor.py      — get_resolution_price wired + TF-aware force_after
  3. config/settings.py          — UMA_FORCE_SETTLE_SHORT_SEC field present
  4. telegram_bot/bot.py         — /force_settle (+ /fs) registered
  5. telegram_bot/handlers/force_settle_handler.py — handler module imports clean

This is a pure source-text scan — no bot / DB / network required.

Exit codes:
  0  → all markers found, package intact
  >0 → first failing marker count (helps pinpoint which file regressed)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Windows cp1252 console can't encode emoji — force UTF-8 on stdout/stderr.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

OK = "[OK]"
FAIL = "[FAIL]"

ROOT = Path(__file__).resolve().parent.parent


def read(rel_path: str) -> str:
    p = ROOT / rel_path
    if not p.exists():
        print(f"  {FAIL} MISSING FILE: {rel_path}")
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def check(label: str, src: str, markers: list[str]) -> int:
    """Return number of missing markers (0 = all good)."""
    miss = [m for m in markers if m not in src]
    if miss:
        print(f"  {FAIL} {label}")
        for m in miss:
            print(f"      missing: {m!r}")
        return len(miss)
    print(f"  {OK} {label}")
    return 0


def main() -> int:
    print("Sprint 5 HOTFIX v4 -- Resolution Path smoke")
    print("=" * 60)

    failures = 0

    # 1) polymarket_client.py — check_market_resolved + get_resolution_price
    src = read("data/polymarket_client.py")
    failures += check(
        "polymarket_client: check_market_resolved rewrite",
        src,
        [
            "outcomePrices",
            "check_market_resolved",
            "get_resolution_price",
            # New resolver must NOT rely on tokens[i].winner field
        ],
    )
    # Only flag real code usage (function-style), not doc mentions of the old bug
    if "tok.get(\"winner\")" in src or "tokens[i].winner ==" in src:
        print(f"  {FAIL} stale tokens[i].winner CODE path still present in polymarket_client")
        failures += 1

    # 2) engine_monitor.py — both code changes
    src = read("core/engine_monitor.py")
    failures += check(
        "engine_monitor: get_resolution_price wired",
        src,
        [
            "get_resolution_price(token_id)",
            "Sprint 5 HOTFIX v4",
            "UMA_FORCE_SETTLE_SHORT_SEC",
        ],
    )
    # Confirm the old buggy call is gone from the oracle-fallback branch.
    # (get_live_price still legitimately used for live TP/SL pricing.)
    bad_ctx = "CLOB price-based oracle fallback"
    if bad_ctx in src:
        start = src.index(bad_ctx)
        window = src[start:start + 1200]
        if "get_live_price(token_id, \"BUY\")" in window:
            print(f"  {FAIL} engine_monitor: old get_live_price still in oracle fallback")
            failures += 1

    # 3) settings.py — UMA_FORCE_SETTLE_SHORT_SEC
    src = read("config/settings.py")
    failures += check(
        "settings: UMA_FORCE_SETTLE_SHORT_SEC field",
        src,
        [
            "UMA_FORCE_SETTLE_SHORT_SEC",
            "\"900\"",  # default
        ],
    )

    # 4) bot.py registration
    src = read("telegram_bot/bot.py")
    failures += check(
        "bot.py: /force_settle + /fs registered",
        src,
        [
            "from telegram_bot.handlers.force_settle_handler",
            "force_settle_command",
            '("force_settle", force_settle_command)',
            '("fs", force_settle_command)',
        ],
    )

    # 5) handler module loads cleanly (syntax + top-level imports)
    src = read("telegram_bot/handlers/force_settle_handler.py")
    failures += check(
        "force_settle_handler: skeleton",
        src,
        [
            "async def force_settle_command",
            "_is_admin",
            "_resolve_oracle",
            "check_market_resolved",
            "get_resolution_price",
            "all_stuck",
        ],
    )

    # Attempt real import to catch any syntax / import errors
    sys.path.insert(0, str(ROOT))
    try:
        import importlib
        importlib.import_module("telegram_bot.handlers.force_settle_handler")
        print(f"  {OK} force_settle_handler imports cleanly")
    except Exception as e:
        print(f"  {FAIL} force_settle_handler import failed: {e}")
        failures += 1

    print("=" * 60)
    if failures == 0:
        print(f"{OK} ALL GREEN -- Sprint 5 HOTFIX v4 package intact")
        return 0
    print(f"{FAIL} {failures} marker(s) missing -- fix before deploy")
    return failures


if __name__ == "__main__":
    sys.exit(main())
