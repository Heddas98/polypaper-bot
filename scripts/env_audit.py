"""
ENV Variable Cleanup Audit — P1.5

Scan codebase for `os.getenv("XXX")` usage and compare against `.env.example`.
Identify:
- USED in code, defined in .env.example  → KEEP
- USED in code, NOT in .env.example       → ADD to .env.example (forgotten)
- NOT USED in code, in .env.example       → REMOVE (dead config)
- USED multiple places, inconsistent default → CONSOLIDATE

Usage:
    py -3.11 scripts/env_audit.py [--output evidence/env_audit.md]

Heddas direktifi: 100+ → 25 ENV var. Hedef:
- whitelist edilmemiş ENV'ler tespit
- 5 farklı STRATEGY_X_ENABLED → 1 array ENABLED_STRATEGIES=ema,vwap,...
- Default'lar config/defaults.py'a taşı, .env sadece secret/override
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# os.getenv("VAR_NAME"...) | os.environ["VAR_NAME"] | os.environ.get("VAR_NAME"...)
GETENV_PATTERNS = [
    re.compile(r'os\.getenv\(\s*[\'"]([A-Z][A-Z0-9_]*)[\'"]'),
    re.compile(r'os\.environ\[\s*[\'"]([A-Z][A-Z0-9_]*)[\'"]\s*\]'),
    re.compile(r'os\.environ\.get\(\s*[\'"]([A-Z][A-Z0-9_]*)[\'"]'),
]

ENV_EXAMPLE_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=", re.MULTILINE)


def scan_code_env_usage() -> dict[str, list[str]]:
    """Returns: {VAR_NAME: [file:line, ...]}"""
    usage = defaultdict(list)
    for p in ROOT.rglob("*.py"):
        s = str(p)
        if "_archive" in s or "__pycache__" in s or ".git" in s:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for pat in GETENV_PATTERNS:
                for m in pat.finditer(line):
                    var = m.group(1)
                    rel = str(p.relative_to(ROOT))
                    usage[var].append(f"{rel}:{line_no}")
    return dict(usage)


def scan_env_example() -> set[str]:
    env_path = ROOT / ".env.example"
    if not env_path.exists():
        return set()
    text = env_path.read_text(encoding="utf-8", errors="ignore")
    return set(ENV_EXAMPLE_LINE.findall(text))


def scan_whitelist() -> set[str]:
    """Read config/env_whitelist.py — runtime /env_toggle whitelist."""
    wl_path = ROOT / "config" / "env_whitelist.py"
    if not wl_path.exists():
        return set()
    text = wl_path.read_text(encoding="utf-8", errors="ignore")
    # Pattern: '"VAR_NAME":' or "'VAR_NAME':"
    return set(re.findall(r'[\'"]([A-Z][A-Z0-9_]+)[\'"]\s*:', text))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=None, help="Markdown output file")
    args = ap.parse_args()

    print("📊 ENV Variable Cleanup Audit")
    print(f"   Root: {ROOT}")
    print()

    code_usage = scan_code_env_usage()
    env_example_vars = scan_env_example()
    whitelist_vars = scan_whitelist()

    used_in_code = set(code_usage.keys())
    in_example = env_example_vars
    in_whitelist = whitelist_vars

    # Categories
    keep = used_in_code & in_example
    forgotten = used_in_code - in_example  # in code, not in .env.example
    dead = in_example - used_in_code  # in .env.example, not in code
    whitelist_only = in_whitelist - used_in_code  # whitelist'te ama kullanım yok

    print("📈 Summary:")
    print(f"   ✅ KEEP (code+example):  {len(keep)}")
    print(f"   ➕ FORGOTTEN (add):      {len(forgotten)}")
    print(f"   ❌ DEAD (remove):        {len(dead)}")
    print(f"   ⚠️  WHITELIST_ORPHAN:    {len(whitelist_only)}")
    print(f"   📋 Code total:           {len(used_in_code)}")
    print(f"   📋 .env.example total:   {len(in_example)}")
    print(f"   📋 Whitelist total:      {len(in_whitelist)}")
    print()

    print("➕ FORGOTTEN (used in code but not in .env.example):")
    for v in sorted(forgotten)[:30]:
        sample = code_usage[v][0] if code_usage.get(v) else ""
        print(f"   {v:40s} {sample}")
    if len(forgotten) > 30:
        print(f"   ... ({len(forgotten)-30} more)")
    print()

    print("❌ DEAD (in .env.example but not used in code):")
    for v in sorted(dead)[:30]:
        print(f"   {v}")
    if len(dead) > 30:
        print(f"   ... ({len(dead)-30} more)")
    print()

    # Strategy enabled consolidation candidates
    strat_pattern = re.compile(r"^STRATEGY_(.+?)_ENABLED$")
    strat_candidates = [v for v in used_in_code if strat_pattern.match(v)]
    if strat_candidates:
        print(f"🔄 STRATEGY_X_ENABLED consolidation candidates ({len(strat_candidates)}):")
        for v in sorted(strat_candidates)[:20]:
            print(f"   {v}")
        print("   → Consolidate to: ENABLED_STRATEGIES=...,...,...")
        print()

    # Output MD
    if args.output:
        out_path = Path(args.output)
    else:
        out_dir = ROOT / "evidence"
        out_dir.mkdir(exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"env_audit_{ts}.md"

    with out_path.open("w", encoding="utf-8") as f:
        f.write(f"# ENV Audit — {datetime.now(UTC).isoformat()}\n\n")
        f.write("## Summary\n\n")
        f.write(f"- Code total: **{len(used_in_code)}**\n")
        f.write(f"- .env.example total: **{len(in_example)}**\n")
        f.write(f"- Whitelist total: **{len(in_whitelist)}**\n")
        f.write(f"- KEEP: {len(keep)}\n")
        f.write(f"- FORGOTTEN (add to .env.example): {len(forgotten)}\n")
        f.write(f"- DEAD (remove from .env.example): {len(dead)}\n\n")

        f.write("## ➕ FORGOTTEN — add to .env.example\n\n")
        for v in sorted(forgotten):
            sites = code_usage.get(v, [])
            f.write(f"- `{v}` — used in {len(sites)} site(s): `{sites[0] if sites else 'N/A'}`\n")
        f.write("\n")

        f.write("## ❌ DEAD — remove from .env.example\n\n")
        for v in sorted(dead):
            f.write(f"- `{v}`\n")
        f.write("\n")

        if strat_candidates:
            f.write("## 🔄 STRATEGY_X_ENABLED consolidation\n\n")
            f.write(
                f"Mevcut {len(strat_candidates)} STRATEGY_X_ENABLED → 1 array `ENABLED_STRATEGIES`.\n\n"
            )
            for v in sorted(strat_candidates):
                f.write(f"- `{v}`\n")
            f.write("\n")

        f.write("## ✅ KEEP (code + .env.example)\n\n")
        for v in sorted(keep):
            f.write(f"- `{v}`\n")

    print(f"💾 Detail: {out_path}")


if __name__ == "__main__":
    main()
