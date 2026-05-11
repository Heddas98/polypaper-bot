"""T11.6-B -- User-facing exception leak guard (regression prevention).

Bans `reply_text(..., esc(str(e))...)` and similar patterns in handler
files. Forces use of `render_user_exception(e, prefix)` helper from
`telegram_bot/handlers/_exc_render.py` (T11.6 policy).

Allowed:
  - `logger.error/warning/exception(... esc(str(e)) ...)` (server-side log only)
  - Lines with `# noqa: T11.6-OK reason=...` annotation (admin-diagnostic exemption)

Banned:
  - `reply_text(... esc(str(e)) ...)` -- this leaked SQL fragments, paths,
    schema details to user. Use render_user_exception() instead.
  - `send_message(... esc(str(e)) ...)` -- same.

Usage (pre-commit hook):
    python scripts/check_exc_leak.py <file1.py> <file2.py> ...
    -> exit 0: clean (or noqa-justified)
    -> exit 1: leak found

Usage (CI audit):
    python scripts/check_exc_leak.py --all
    -> scans all telegram_bot/handlers/*.py

Doctrine source: docs/security/T11_6_exception_render_policy.md
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_PREFIXES = ("telegram_bot/",)

# Pattern: send_message OR reply_text containing esc(str(eXX)) on the
# same line. Multi-line patterns (split across lines) are caught by
# trailing-comma scan in same logical statement (single-line matcher
# gets ~95% of practical cases).
LEAK_PATTERN = re.compile(
    r"(reply_text|send_message)\s*\([^)]*esc\(str\(e\w*\)",
    re.DOTALL,
)

# Multi-line variant: reply_text OR send_message starts a multi-line call,
# inside which `esc(str(e))` (or slice variant esc(str(e)[:N])) appears
# before the closing `)`. \w* handles e, e2, e_inner, etc.
MULTI_LINE_LEAK = re.compile(
    r"(reply_text|send_message)\s*\(\s*\n[^)]*?esc\(str\(e\w*\)",
    re.DOTALL,
)

# Escape hatch: line containing the pattern + same-line noqa annotation
NOQA_PATTERN = re.compile(r"#\s*noqa\s*:\s*T11\.6-OK", re.IGNORECASE)


def _is_in_scope(path: str) -> bool:
    """True if path is under telegram_bot/."""
    norm = path.replace("\\", "/").lstrip("./")
    return any(norm.startswith(p) for p in SCAN_PREFIXES)


def _check_file(path: Path) -> List[tuple]:
    """Return list of (lineno, snippet) for leak sites."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        sys.stderr.write(f"[exc-leak-check] skip {path}: {e}\n")
        return []

    violations: List[tuple] = []
    lines = text.splitlines()

    # Single-line pass
    for i, line in enumerate(lines, start=1):
        if NOQA_PATTERN.search(line):
            continue
        if LEAK_PATTERN.search(line):
            violations.append((i, line.rstrip()))

    # Multi-line scan: find reply_text/send_message starts, scan forward
    # up to next ) at same indent, look for esc(str(e
    for i, line in enumerate(lines, start=1):
        m = re.search(r"(reply_text|send_message)\s*\(\s*$", line)
        if not m:
            continue
        # Multi-line call detected — scan forward for esc(str(e)) before close
        depth = line.count("(") - line.count(")")
        for j in range(i, min(i + 20, len(lines))):
            nxt = lines[j]
            depth += nxt.count("(") - nxt.count(")")
            if "esc(str(e" in nxt:
                # Check noqa on the call-start line OR the leak line
                if NOQA_PATTERN.search(line) or NOQA_PATTERN.search(nxt):
                    break
                violations.append((j + 1, f"  {line.rstrip()}\n  ... {nxt.rstrip()}"))
                break
            if depth <= 0:
                break

    # Dedupe (multi-line + single-line may overlap)
    seen: set = set()
    out = []
    for lineno, snippet in violations:
        if lineno in seen:
            continue
        seen.add(lineno)
        out.append((lineno, snippet))
    return out


def _list_handler_py() -> List[Path]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "telegram_bot/handlers/*.py"],
            text=True,
            cwd=str(REPO_ROOT),
        )
    except subprocess.CalledProcessError:
        return []
    return [REPO_ROOT / p for p in out.splitlines() if p.endswith(".py")]


def main() -> int:
    parser = argparse.ArgumentParser(description="T11.6-B exc leak guard")
    parser.add_argument("--all", action="store_true", help="scan all handler .py (CI mode)")
    parser.add_argument("files", nargs="*", help="specific files (pre-commit mode)")
    args = parser.parse_args()

    if args.all:
        targets = _list_handler_py()
    else:
        targets = [Path(f) for f in args.files if _is_in_scope(f)]

    if not targets:
        return 0

    total = 0
    for path in targets:
        viols = _check_file(path)
        if not viols:
            continue
        for lineno, snippet in viols:
            print(f"{path}:{lineno}: T11.6 leak (reply_text/send_message + esc(str(e)))")
            print(f"    {snippet.strip()[:120]}")
            total += 1

    if total:
        print()
        print(f"[exc-leak-check] FAIL: {total} user-facing exception leak(s)")
        print("[exc-leak-check] Fix: replace with `render_user_exception(e, prefix)`")
        print("    from telegram_bot.handlers._exc_render import render_user_exception")
        print("    Or annotate site with `# noqa: T11.6-OK reason=<admin-diagnostic>`")
        print("    See docs/security/T11_6_exception_render_policy.md")
        return 1

    print(f"[exc-leak-check] OK: {len(targets)} handler(s) scanned, 0 leak.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
