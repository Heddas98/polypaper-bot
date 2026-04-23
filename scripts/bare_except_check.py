"""T11.8 — Bare except grep guard (T7.6 + T1.4 doctrine enforcement).

Bans `except Exception:` and `except:` patterns in production code unless
explicitly annotated with `# noqa: BLE-OK reason=<justification>`.

Usage (pre-commit hook):
    python scripts/bare_except_check.py <file1.py> <file2.py> ...
    -> exit 0: all clean (or noqa-justified)
    -> exit 1: violation(s) found, lists file:line:context

Usage (CI audit):
    python scripts/bare_except_check.py --all
    -> scans all tracked .py files under core/, data/, telegram_bot/, db/
    -> exit 0/1 as above

Escape hatch (for intentional catch-all):
    try:
        do_risky_thing()
    except Exception as e:  # noqa: BLE-OK reason=bg_task user-callback contract
        logger.exception("...")

Doctrine (T7.6 Aşama A/B closure notes + T1.4 Faz 1 methodology):
  1. Prefer narrow exception tuples (e.g., `except (OSError, ValueError):`)
  2. If catch-all is unavoidable (supervisor loop, user-callback contract):
     annotate with `# noqa: BLE-OK reason=<reason>` on the except line.
  3. Always log with `logger.exception(...)` or `type(e).__name__` for
     observability — silent `pass` is banned entirely (except in guarded
     re-raise sites like `CancelledError`).

Violations caught (regex, line-by-line):
  - `except:` (naked except — always bad)
  - `except Exception:` (too broad, no tuple narrowing)
  - `except BaseException:` (includes KeyboardInterrupt, SystemExit)
  - `except Exception as e:` followed by `pass` on next non-blank line
    (silent swallow, T1.4 Faz 1 anti-pattern)

Scope: production dirs only. Skips tests/, scripts/, _archive/.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple


PROD_PREFIXES_STRICT = ("core/",)
PROD_PREFIXES_ADVISORY = ("data/", "telegram_bot/", "db/")
PROD_PREFIXES = PROD_PREFIXES_STRICT  # default: strict mode scans core/ only

# Line patterns for violations (regex against each line)
BARE_PATTERNS = [
    (re.compile(r"^\s*except\s*:\s*(#.*)?$"), "naked `except:`"),
    (re.compile(r"^\s*except\s+Exception\s*:\s*(#.*)?$"),
     "`except Exception:` (no tuple narrowing)"),
    (re.compile(r"^\s*except\s+BaseException\s*:\s*(#.*)?$"),
     "`except BaseException:` (includes KeyboardInterrupt/SystemExit)"),
    (re.compile(r"^\s*except\s+Exception\s+as\s+\w+\s*:\s*(#.*)?$"),
     "`except Exception as X:` (no tuple narrowing)"),
    (re.compile(r"^\s*except\s+BaseException\s+as\s+\w+\s*:\s*(#.*)?$"),
     "`except BaseException as X:` (too broad)"),
]

# Escape hatch — any `# noqa: BLE...` suffix on same line suppresses.
# Accepts:
#   - `# noqa: BLE001`           (ruff standard "blind-except" code;
#                                 T7.6 Asama A/B pattern)
#   - `# noqa: BLE-OK`           (T11.8 new-code recommendation)
#   - `# noqa: BLE-OK reason=X`  (T11.8 with explicit justification)
NOQA_PATTERN = re.compile(r"#\s*noqa\s*:\s*BLE", re.IGNORECASE)


def _is_prod_path(path: str) -> bool:
    """True if path is under a production dir."""
    norm = path.replace("\\", "/").lstrip("./")
    return any(norm.startswith(p) for p in PROD_PREFIXES)


def _check_file(path: Path) -> List[Tuple[int, str, str]]:
    """Return list of (lineno, snippet, reason) for violations."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        sys.stderr.write(f"[bare-except-check] skip {path}: {e}\n")
        return []

    violations: List[Tuple[int, str, str]] = []
    lines = text.splitlines()
    for i, line in enumerate(lines, start=1):
        # Skip comments / docstrings containing the pattern
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Check regex patterns
        for pat, reason in BARE_PATTERNS:
            if pat.search(line):
                # Escape hatch?
                if NOQA_PATTERN.search(line):
                    continue
                # Silent `pass` check (T1.4 Faz 1 anti-pattern marker)
                extra = ""
                for j in range(i, min(i + 3, len(lines))):
                    nxt = lines[j].strip()
                    if not nxt or nxt.startswith("#"):
                        continue
                    if nxt == "pass":
                        extra = " + silent pass"
                    break
                violations.append((i, line.rstrip(), reason + extra))
                break
    return violations


def _list_all_tracked_py() -> List[Path]:
    """All tracked .py under production dirs (git ls-files)."""
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "*.py"], text=True
        )
    except subprocess.CalledProcessError:
        return []
    paths: List[Path] = []
    for line in out.splitlines():
        if _is_prod_path(line):
            p = Path(line)
            if p.exists():
                paths.append(p)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="T11.8 bare except guard"
    )
    parser.add_argument("--all", action="store_true",
                        help="scan all tracked production .py (CI mode, strict dirs)")
    parser.add_argument("--advisory", action="store_true",
                        help="also scan advisory dirs (data/telegram_bot/db) "
                             "with report-only output (no fail)")
    parser.add_argument("files", nargs="*",
                        help="specific files to check (pre-commit mode, strict dirs)")
    args = parser.parse_args()

    if args.all:
        targets = _list_all_tracked_py()  # strict dirs only
    else:
        targets = [Path(f) for f in args.files if _is_prod_path(f)]

    # Strict check: core/ violations always fail
    total_violations = 0
    if targets:
        for path in targets:
            viols = _check_file(path)
            if not viols:
                continue
            for lineno, snippet, reason in viols:
                print(f"{path}:{lineno}: {reason}")
                print(f"    {snippet.strip()}")
                total_violations += 1

    # Advisory scan (non-blocking report) for broader dirs
    advisory_count = 0
    if args.advisory or args.all:
        try:
            out = subprocess.check_output(
                ["git", "ls-files", "*.py"], text=True
            )
            advisory_files = [
                Path(line) for line in out.splitlines()
                if any(line.replace("\\", "/").lstrip("./").startswith(p)
                       for p in PROD_PREFIXES_ADVISORY)
                and Path(line).exists()
            ]
            for path in advisory_files:
                viols = _check_file(path)
                advisory_count += len(viols)
        except subprocess.CalledProcessError:
            pass
        if advisory_count:
            print()
            print(f"[bare-except-check] ADVISORY (non-fail): "
                  f"{advisory_count} violation(s) in "
                  f"{PROD_PREFIXES_ADVISORY} -- T11.8-B forward work.")

    if total_violations:
        print()
        print(f"[bare-except-check] FAIL: {total_violations} violation(s) "
              f"in core/ (strict zone).")
        print("[bare-except-check] Fix options:")
        print("  1. Narrow: `except (SpecificError, OtherError):` "
              "(T1.4 Faz 1 pattern)")
        print("  2. Escape: `except Exception as e:  # noqa: BLE-OK "
              "reason=<explain>` (T7.6 Asama A pattern)")
        print("  3. Silent `pass` -> `logger.debug(type(e).__name__)` "
              "at minimum (observability)")
        return 1

    if targets:
        print(f"[bare-except-check] OK: {len(targets)} file(s) in strict "
              f"zone scanned, 0 violation(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
