"""T11.7 — Auto-generate docs/env_reference.md from AST scan of os.getenv().

Motivation (T10.10 F4 audit, 2026-04-22):
  Codebase scan showed 327 distinct `os.getenv("KEY", ...)` call sites
  across production code. Only 37 of them are in `/envt` whitelist. The
  rest are either feature flags, platform-supplied vars, secret
  alternatives, or job-scheduler tuning. Tracking drift between code
  and `.env.example` / docs was manual and drifted fast.

This script builds a single source of truth:
  1. Walk all .py files under production dirs (core/data/telegram_bot/db)
  2. Find every `os.getenv(KEY, default)` AST call node
  3. Cross-reference with `config.env_whitelist.ENV_WHITELIST`
  4. Cross-reference with `.env.example` (which keys are documented)
  5. Emit `docs/env_reference.md` — markdown table

Usage:
    py -3.11 scripts/gen_env_reference.py          # write docs/env_reference.md
    py -3.11 scripts/gen_env_reference.py --check  # drift detect (CI)
                                                    exit 1 if stale
    py -3.11 scripts/gen_env_reference.py --stdout # print to stdout

CI drift guard: in --check mode, regenerates in-memory and diffs against
current `docs/env_reference.md`. If different, exits 1 with a hint to
run the generator. This prevents silent drift between code and docs.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set


REPO_ROOT = Path(__file__).resolve().parent.parent
PROD_PREFIXES = ("core/", "data/", "telegram_bot/", "db/", "calibration/",
                 "backtest/", "data_feeds/", "indicators/")
OUTPUT = REPO_ROOT / "docs" / "env_reference.md"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Windows cp1252 default stdout cannot print UTF-8 emoji (e.g. checkmark).
# Reconfigure stdout/stderr to UTF-8 so --stdout mode works on Windows.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in (
    "utf-8", "utf_8", "u8"
):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


@dataclass
class EnvUse:
    key: str
    default: Optional[str]
    file: str
    line: int


def _list_prod_py() -> List[Path]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "*.py"], text=True, cwd=str(REPO_ROOT)
        )
    except subprocess.CalledProcessError:
        return []
    paths: List[Path] = []
    for line in out.splitlines():
        if any(line.replace("\\", "/").lstrip("./").startswith(p)
               for p in PROD_PREFIXES):
            p = REPO_ROOT / line
            if p.exists():
                paths.append(p)
    return paths


def _extract_getenv_uses(path: Path) -> List[EnvUse]:
    """AST walk `os.getenv(...)` calls in a file."""
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return []

    uses: List[EnvUse] = []
    rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match `os.getenv(...)` specifically
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "getenv"
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
        ):
            continue
        if not node.args:
            continue
        # First arg must be string literal
        first = node.args[0]
        key: Optional[str] = None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            key = first.value
        if key is None:
            continue
        # Second arg (default) — may be string constant, None, or expr
        default: Optional[str] = None
        if len(node.args) >= 2:
            d = node.args[1]
            if isinstance(d, ast.Constant):
                if d.value is None:
                    default = "None"
                else:
                    default = repr(d.value)
            else:
                default = "<expr>"
        uses.append(EnvUse(key=key, default=default, file=rel,
                           line=node.lineno))
    return uses


def _load_whitelist_keys() -> Set[str]:
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from config.env_whitelist import ENV_WHITELIST  # type: ignore
        return set(ENV_WHITELIST.keys())
    except ImportError:
        return set()


def _load_env_example_keys() -> Set[str]:
    if not ENV_EXAMPLE.exists():
        return set()
    keys: Set[str] = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k = line.split("=", 1)[0].strip()
            if k and k.replace("_", "").replace("-", "").isalnum():
                keys.add(k)
    return keys


def build_reference() -> str:
    """Build the full markdown reference document."""
    uses_by_key: Dict[str, List[EnvUse]] = {}
    for path in _list_prod_py():
        for u in _extract_getenv_uses(path):
            uses_by_key.setdefault(u.key, []).append(u)

    whitelist = _load_whitelist_keys()
    example = _load_env_example_keys()

    all_keys = sorted(uses_by_key.keys())

    lines: List[str] = []
    lines.append("# PolyPaper Bot — Environment Variable Reference")
    lines.append("")
    lines.append("> **Auto-generated** by `scripts/gen_env_reference.py` "
                 "(T11.7 doctrine).")
    lines.append("> Do not hand-edit. Run the generator after adding any "
                 "new `os.getenv(...)` call in production code.")
    lines.append(">")
    lines.append(f"> **Total keys:** {len(all_keys)} distinct env vars read "
                 f"across production dirs.")
    lines.append(f"> **Whitelist coverage:** "
                 f"{len(set(all_keys) & whitelist)} / {len(whitelist)} "
                 f"whitelisted keys have at least one reader.")
    lines.append(f"> **`.env.example` coverage:** "
                 f"{len(set(all_keys) & example)} / {len(all_keys)} "
                 f"runtime keys are documented in `.env.example`.")
    lines.append("")
    lines.append("## Legend")
    lines.append("")
    lines.append("- **Key:** the environment variable name")
    lines.append("- **Default:** default value when unset "
                 "(`None` if no fallback, `<expr>` if non-literal)")
    lines.append("- **Whitelist:** `/env_toggle` runtime-tuneable? "
                 "(`✅` = in `config/env_whitelist.py`)")
    lines.append("- **`.env.example`:** documented in the template file? "
                 "(`✅` = yes)")
    lines.append("- **Readers:** first 3 call sites (see full scan for more)")
    lines.append("")
    lines.append("## Reference Table")
    lines.append("")
    lines.append("| Key | Default | Whitelist | `.env.example` | Readers |")
    lines.append("|-----|---------|-----------|----------------|---------|")

    for key in all_keys:
        uses = uses_by_key[key]
        # Pick a "canonical" default (most common literal among uses)
        defaults = [u.default for u in uses if u.default is not None]
        if defaults:
            # Use first literal default that isn't <expr>, else first
            lit = next((d for d in defaults if d != "<expr>"), defaults[0])
            default_str = f"`{lit}`"
        else:
            default_str = "`None`"
        wl = "✅" if key in whitelist else ""
        ex = "✅" if key in example else ""
        # Readers: first 3 file:line pairs
        readers = ", ".join(
            f"`{u.file}:{u.line}`" for u in uses[:3]
        )
        if len(uses) > 3:
            readers += f" (+{len(uses) - 3} more)"
        lines.append(f"| `{key}` | {default_str} | {wl} | {ex} | "
                     f"{readers} |")

    lines.append("")
    lines.append("## Drift Detection")
    lines.append("")
    lines.append("Run in CI:")
    lines.append("```bash")
    lines.append("python scripts/gen_env_reference.py --check")
    lines.append("```")
    lines.append("Exits 1 if this document is stale relative to current "
                 "`os.getenv(...)` scan. Fix: re-run without `--check` to "
                 "regenerate + commit.")
    lines.append("")
    lines.append("## Scope")
    lines.append("")
    lines.append("Production dirs scanned: " +
                 ", ".join(f"`{p}`" for p in PROD_PREFIXES))
    lines.append("")
    lines.append("Excluded: `tests/`, `scripts/`, `_archive/`, project root.")
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="T11.7 env_reference AST-gen")
    parser.add_argument("--check", action="store_true",
                        help="drift-detect mode: exit 1 if docs/env_reference.md "
                             "is stale")
    parser.add_argument("--stdout", action="store_true",
                        help="print to stdout instead of writing to file")
    args = parser.parse_args()

    new_content = build_reference()

    if args.stdout:
        sys.stdout.write(new_content)
        return 0

    if args.check:
        if not OUTPUT.exists():
            print(f"[env-ref] {OUTPUT} does not exist. "
                  f"Run without --check to generate.", file=sys.stderr)
            return 1
        current = OUTPUT.read_text(encoding="utf-8")
        if current != new_content:
            print(f"[env-ref] DRIFT: {OUTPUT} is stale.", file=sys.stderr)
            print(f"[env-ref] Fix: python scripts/gen_env_reference.py "
                  f"(no args) + git add {OUTPUT.relative_to(REPO_ROOT)}",
                  file=sys.stderr)
            return 1
        print(f"[env-ref] OK: {OUTPUT.relative_to(REPO_ROOT)} is in sync.")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(new_content, encoding="utf-8")
    print(f"[env-ref] wrote {OUTPUT.relative_to(REPO_ROOT)} "
          f"({len(new_content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
