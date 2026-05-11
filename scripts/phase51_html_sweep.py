"""
Phase 51 P51-01 — HTML escape sweep codemod.

Walks telegram_bot/handlers/*.py and:
 1. For every file that uses parse_mode HTML but doesn't import `esc`,
    adds `from telegram_bot.templates.safe_html import esc`.
 2. Rewrites risky f-string interpolations like `{e}`, `{slug}`, `{label}`,
    `{strategy.name}` into `{esc(e)}`, `{esc(slug)}`, `{esc(label)}`,
    `{esc(strategy.name)}` — but only when the identifier/attribute matches a
    known-untrusted-string allow-list. Numeric/bool/dict interpolations and
    already-escaped forms are left alone.

Idempotent. Safe to re-run. Reports a diff summary at the end.

Usage:
    py -3.11 scripts/phase51_html_sweep.py            # apply
    py -3.11 scripts/phase51_html_sweep.py --dry      # preview only
"""

from __future__ import annotations

import py_compile
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HANDLERS = ROOT / "telegram_bot" / "handlers"

SUSPICIOUS = {
    # generic text/label fields
    "slug",
    "label",
    "name",
    "title",
    "question",
    "text",
    "reason",
    "desc",
    "description",
    "note",
    "comment",
    "tag",
    "category",
    # exception / error strings
    "error",
    "err",
    "exc",
    "e",
    "msg",
    "message",
    # market / strategy domain
    "market",
    "outcome",
    "condition",
    "side",
    "action",
    "event",
    "symbol",
    "ticker",
    "strategy",
    "strat",
    "status",
    "state",
    "token_id",
    "market_id",
    "cid",
    "correlation_id",
    # user
    "author",
    "user",
    "username",
    "chat",
}

IMPORT_LINE = "from telegram_bot.templates.safe_html import esc\n"

# Match interpolation spans INSIDE an f-string (single or double, incl. f"""...""").
# We'll process each f-string body individually.
FSTRING_RE = re.compile(
    r"""
    (?P<prefix>[fF])
    (?P<quote>\"{3}|'{3}|\"|')
    (?P<body>(?:(?!\s*(?P=quote))[^\\]|\\.)*?)
    (?P=quote)
    """,
    re.VERBOSE | re.DOTALL,
)

# Match one interpolation `{ ... }` inside an f-string body.
# Accepts attribute chains (strategy.label, self.state.slug) and a single
# subscript (row[\"slug\"] or row['slug']). Optional format spec after ':'.
INTERP_RE = re.compile(
    r"""
    \{
      (?P<expr>
        [A-Za-z_][\w]*                    # root identifier
        (?:\.[A-Za-z_][\w]*)*              # .attr chain
        (?:\[(?:'[^']+'|\"[^\"]+\"|[0-9]+)\])?   # optional single subscript
      )
      (?P<fmt>![rsa]|:[^{}]*)?
    \}
    """,
    re.VERBOSE,
)


def leaf_name(expr: str) -> str:
    """Return the identifier we use to decide risk:
    - `e`           -> `e`
    - `strategy.label` -> `label`
    - `row['slug']` -> `slug`
    """
    # subscript with string key
    m = re.search(r"""\[['\"]([A-Za-z_][\w]*)['\"]\]$""", expr)
    if m:
        return m.group(1)
    # attribute chain
    if "." in expr:
        return expr.rsplit(".", 1)[1]
    return expr


def already_escaped(expr: str) -> bool:
    return expr.startswith("esc(") or expr.startswith("esc_code(")


def rewrite_fstring_body(body: str) -> tuple[str, int]:
    """Wrap risky interpolations with esc(...)."""
    changes = 0

    def repl(m: re.Match) -> str:
        nonlocal changes
        expr = m.group("expr")
        fmt = m.group("fmt") or ""
        if already_escaped(expr):
            return m.group(0)
        if leaf_name(expr) not in SUSPICIOUS:
            return m.group(0)
        # If there's a numeric format spec, don't wrap (it's a number)
        if fmt.startswith(":") and re.search(r"[dfbxoeEgGn%]|\.\d+[fFeEgG]", fmt):
            return m.group(0)
        changes += 1
        return "{esc(" + expr + ")" + fmt + "}"

    new_body = INTERP_RE.sub(repl, body)
    return new_body, changes


def transform_source(src: str) -> tuple[str, dict]:
    stats = {"fstrings_scanned": 0, "wraps": 0, "import_added": False}

    # Rewrite f-strings
    def fs_repl(m: re.Match) -> str:
        stats["fstrings_scanned"] += 1
        body = m.group("body")
        new_body, n = rewrite_fstring_body(body)
        if n:
            stats["wraps"] += n
        return m.group("prefix") + m.group("quote") + new_body + m.group("quote")

    new_src = FSTRING_RE.sub(fs_repl, src)

    # Add import if needed
    needs_import = stats["wraps"] > 0 and "safe_html" not in new_src
    if needs_import:
        # Insert after the last top-level `from ... import ...` or `import ...`
        # to keep imports grouped.
        lines = new_src.splitlines(keepends=True)
        insert_idx = 0
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith(("import ", "from ")):
                insert_idx = i + 1
        lines.insert(insert_idx, IMPORT_LINE)
        new_src = "".join(lines)
        stats["import_added"] = True

    return new_src, stats


def main() -> int:
    dry = "--dry" in sys.argv
    files = sorted(HANDLERS.glob("*.py"))
    total_wraps = 0
    total_imports = 0
    touched = []
    failures = []

    for f in files:
        try:
            src = f.read_text(encoding="utf-8")
        except Exception as exc:
            failures.append((f.name, f"read: {exc}"))
            continue
        new_src, stats = transform_source(src)
        if stats["wraps"] == 0 and not stats["import_added"]:
            continue

        # Write atomically, then syntax-check.
        if not dry:
            backup = f.with_suffix(".py.p51bak")
            try:
                backup.write_text(src, encoding="utf-8")
                f.write_text(new_src, encoding="utf-8")
                py_compile.compile(str(f), doraise=True)
                backup.unlink()
            except Exception as exc:
                # Rollback
                if backup.exists():
                    f.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
                    backup.unlink()
                failures.append((f.name, f"syntax: {exc}"))
                continue
        total_wraps += stats["wraps"]
        if stats["import_added"]:
            total_imports += 1
        touched.append((f.name, stats["wraps"], stats["import_added"]))

    mode = "[dry-run]" if dry else "[applied]"
    print(f"{mode} Phase 51 P51-01 HTML sweep")
    print(f"  handlers modified : {len(touched)}")
    print(f"  total wraps added : {total_wraps}")
    print(f"  imports added     : {total_imports}")
    if failures:
        print(f"  FAILURES          : {len(failures)}")
        for name, reason in failures:
            print(f"    - {name}: {reason}")
    for name, n, imp in touched:
        flag = "+imp" if imp else "    "
        print(f"    {flag} {name:40s} wraps={n}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
