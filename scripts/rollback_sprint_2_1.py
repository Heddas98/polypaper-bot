"""
Phase 82e Sprint 2.1 Rollback Helper
====================================
Reverts safe_create_task() calls back to asyncio.create_task() across the
11 files modified by deploy_phase82e_sprint_2_1.bat.

Idempotent: running twice is a no-op on the second run.

After rollback the bot behaves exactly as pre-Sprint 2.1. The only thing
you lose is the /diagnose "Background Tasks" section + Telegram notify on
bg task crash. core/bg_task.py remains on disk but is not imported from
anywhere — harmless.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

FILES: List[str] = [
    "core/engine.py",
    "core/ai_brain.py",
    "core/engine_settlement.py",
    "core/keepalive.py",
    "data/market_recorder.py",
    "data/websocket_client.py",
    "data/binance_multistream.py",
    "data/market_scanner.py",
    "data/candle_collector.py",
    "data/chainlink_oracle.py",
    "data/external_feed.py",
    "telegram_bot/bot.py",
]


def _strip_kwargs(args: str) -> str:
    """Remove name=/notify=/reraise=/on_error= kwargs from the call site.

    Leaves positional coroutine arg(s) intact. asyncio.create_task only
    takes the coroutine + (3.8+) optional name kwarg, but for safety
    we drop the name kwarg too — unreferenced.
    """
    # Split on top-level commas (naive but good enough for the calls we
    # emitted: single positional coroutine + simple keyword args).
    parts: List[str] = []
    depth = 0
    buf = ""
    for ch in args:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf.strip())

    # Keep parts that don't start with one of the kwarg keys
    drop_keys = ("name=", "notify=", "reraise=", "on_error=")
    kept = [p for p in parts if not p.startswith(drop_keys)]
    return ", ".join(kept)


def revert_file(path: Path) -> Tuple[bool, List[str]]:
    """Return (changed, messages)."""
    if not path.exists():
        return False, [f"{path}: not found"]

    src = path.read_text(encoding="utf-8")
    orig = src
    messages: List[str] = []

    # 1) safe_create_task(...) → asyncio.create_task(<positional only>)
    #    Use balanced-paren match via custom scan.
    def _replace_calls(text: str) -> str:
        out: List[str] = []
        i = 0
        token = "safe_create_task("
        while True:
            j = text.find(token, i)
            if j == -1:
                out.append(text[i:])
                break
            out.append(text[i:j])
            # Scan balanced parens from end of 'safe_create_task('
            start = j + len(token)
            depth = 1
            k = start
            while k < len(text) and depth > 0:
                c = text[k]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            if depth != 0:
                # Unbalanced — leave original alone
                out.append(text[j:])
                break
            inner = text[start:k]
            cleaned = _strip_kwargs(inner)
            out.append(f"asyncio.create_task({cleaned})")
            i = k + 1  # past the closing paren
        return "".join(out)

    before = src
    src = _replace_calls(src)
    call_changed = src != before
    if call_changed:
        count = before.count("safe_create_task(") - src.count("safe_create_task(")
        messages.append(f"  {count} call(s) reverted")

    # 2) Remove bg_task import lines
    lines = src.splitlines(keepends=True)
    new_lines: List[str] = []
    skip_until_close_paren = False
    removed = 0
    for ln in lines:
        stripped = ln.strip()
        if skip_until_close_paren:
            new_lines_drop = True  # placeholder no-op
            if ")" in stripped:
                skip_until_close_paren = False
            removed += 1
            continue
        if stripped.startswith("from core.bg_task import"):
            # Multi-line import?
            if "(" in ln and ")" not in ln:
                skip_until_close_paren = True
            removed += 1
            continue
        new_lines.append(ln)
    if removed:
        messages.append(f"  {removed} bg_task import line(s) removed")
    src = "".join(new_lines)

    # 3) Remove the notify handler registration block from telegram_bot/bot.py
    if path.name == "bot.py":
        # Remove block between the Sprint 2.1 comment start and the try/except end
        pattern = re.compile(
            r"\n\s*# Phase 82e Sprint 2\.1: register bg_task notify handler.*?"
            r"bg_task notify handler setup failed.*?\"\)\n",
            re.DOTALL,
        )
        new_src, n = pattern.subn("\n", src)
        if n:
            messages.append(f"  notify handler block removed ({n} match)")
            src = new_src

    if src != orig:
        path.write_text(src, encoding="utf-8")
        return True, messages
    return False, ["  (already reverted — no changes)"]


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    print(f"[rollback] root = {root}")
    total_changed = 0
    for rel in FILES:
        p = root / rel
        changed, msgs = revert_file(p)
        header = f"[rollback] {rel:<40s} "
        header += "CHANGED" if changed else "ok"
        print(header)
        for m in msgs:
            print(m)
        if changed:
            total_changed += 1

    # Final syntax check on everything we touched + bg_task.py
    import ast

    print("\n[rollback] syntax check…")
    for rel in FILES + ["core/bg_task.py"]:
        p = root / rel
        if not p.exists():
            continue
        try:
            ast.parse(p.read_text(encoding="utf-8"))
            print(f"  {rel}: OK")
        except SyntaxError as e:
            print(f"  {rel}: SYNTAX ERROR — {e}")
            return 2

    print(f"\n[rollback] done. {total_changed} file(s) reverted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
