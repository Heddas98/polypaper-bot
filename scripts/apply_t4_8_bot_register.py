"""T4.8 bot.py register patch -- idempotent.

Applies two edits to telegram_bot/bot.py:
  1. Import of dump_rest_timing_command (after live_guards_handler import)
  2. Command registration (after live_guards command pair)

Idempotent: running twice is a no-op. Detects existing edits by string
match before inserting.

Usage:
    python scripts/apply_t4_8_bot_register.py
    # exit 0: patched (or already patched)
    # exit 1: unable to locate anchor (file shape changed)
"""
from __future__ import annotations

import sys
from pathlib import Path


BOT_PY = Path(__file__).resolve().parent.parent / "telegram_bot" / "bot.py"

IMPORT_ANCHOR = (
    "from telegram_bot.handlers.live_guards_handler import (  "
    "# Epic 11 T11.2 [D]: 6-guard live snapshot\n"
    "    live_guards_command)"
)
IMPORT_INSERT = (
    "\nfrom telegram_bot.handlers.rest_timing_handler import (  "
    "# Epic 4 T4.8: REST RTT telemetry summary\n"
    "    dump_rest_timing_command)"
)

REGISTER_ANCHOR = (
    "            # Epic 11 T11.2 [D]: 6-guard live snapshot (admin)\n"
    '            ("live_guards", live_guards_command),\n'
    '            ("lg", live_guards_command),'
)
REGISTER_INSERT = (
    "\n            # Epic 4 T4.8: REST RTT telemetry summary (admin)\n"
    '            ("dump_rest_timing", dump_rest_timing_command),\n'
    '            ("drt", dump_rest_timing_command),'
)


def main() -> int:
    if not BOT_PY.exists():
        print(f"ERROR: {BOT_PY} not found", file=sys.stderr)
        return 1

    src = BOT_PY.read_text(encoding="utf-8")

    # Idempotency checks
    import_done = "from telegram_bot.handlers.rest_timing_handler" in src
    register_done = '"dump_rest_timing"' in src and "dump_rest_timing_command" in src

    if import_done and register_done:
        print("[t4.8-patch] already applied -- no changes.")
        return 0

    # Apply import (if needed)
    if not import_done:
        if IMPORT_ANCHOR not in src:
            print(
                "ERROR: import anchor not found -- bot.py shape changed",
                file=sys.stderr,
            )
            print(f"Expected anchor:\n{IMPORT_ANCHOR}", file=sys.stderr)
            return 1
        src = src.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + IMPORT_INSERT, 1)
        print("[t4.8-patch] import inserted after live_guards_handler")

    # Apply registration (if needed)
    if not register_done:
        if REGISTER_ANCHOR not in src:
            print(
                "ERROR: register anchor not found -- bot.py shape changed",
                file=sys.stderr,
            )
            print(f"Expected anchor:\n{REGISTER_ANCHOR}", file=sys.stderr)
            return 1
        src = src.replace(
            REGISTER_ANCHOR, REGISTER_ANCHOR + REGISTER_INSERT, 1
        )
        print("[t4.8-patch] CommandHandler pair inserted after live_guards")

    # Syntax check before writing
    import ast
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"ERROR: patched bot.py has SyntaxError: {e}", file=sys.stderr)
        return 1

    BOT_PY.write_text(src, encoding="utf-8")
    print(f"[t4.8-patch] OK: {BOT_PY} patched. AST parse clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
