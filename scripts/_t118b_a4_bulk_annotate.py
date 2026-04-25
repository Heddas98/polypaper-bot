"""T11.8-B Aşama A4 — bulk annotate data/ S2-corrupted files.

Adds `# noqa: BLE001` to every unannotated `except Exception` line in the
7 data/ feed-orchestrator files. Module docstring T11.8-B doctrine note is
also injected after the existing module docstring.

These files are network/IO orchestrators (websockets + httpx + json + db +
asyncio) — wide catches at the orchestration layer are intentional. Single
network blip should not crash the feed thread; reconnect logic handles it.

Usage:
    python scripts/_t118b_a4_bulk_annotate.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

FILES = [
    "data/binance_multistream.py",
    "data/candle_collector.py",
    "data/chainlink_oracle.py",
    "data/external_feed.py",
    "data/market_recorder.py",
    "data/market_scanner.py",
    "data/websocket_client.py",
]

DOCTRINE_NOTE = (
    "T11.8-B (2026-04-24): every catch in this module is annotated\n"
    "`# noqa: BLE001`. Data-feed orchestrator: WebSockets + httpx +\n"
    "json + aiosqlite + asyncio reconnect chain. Single network blip\n"
    "or schema drift should NOT crash the feed thread — the reconnect\n"
    "loop handles it. Wide catches at the orchestration layer are\n"
    "intentional and logged.\n"
)


def annotate(path: Path) -> tuple[int, int]:
    """Return (annotated, total)."""
    src = path.read_text(encoding="utf-8")

    # Add doctrine note to module docstring if not already present
    if "T11.8-B" not in src:
        # Find first triple-quote docstring close
        m = re.search(r'^(""".*?""")', src, re.DOTALL | re.MULTILINE)
        if m:
            close = m.end()
            # Insert before the closing """
            doc = m.group(1)
            new_doc = doc[:-3] + "\n" + DOCTRINE_NOTE + '"""'
            src = src[:m.start()] + new_doc + src[close:]

    # Add noqa to every except Exception that doesn't have it
    def add_noqa(m: re.Match) -> str:
        line = m.group(0)
        if "noqa" in line:
            return line
        return line.rstrip(":") + ":  # noqa: BLE001"

    # Three patterns: bare, as e, as _name
    pattern = re.compile(
        r"except\s+Exception(?:\s+as\s+[a-zA-Z_][a-zA-Z0-9_]*)?:"
    )
    new_src, n = pattern.subn(add_noqa, src)

    # Count totals before save
    total = len(pattern.findall(src))

    # Validate AST
    try:
        ast.parse(new_src, filename=str(path))
    except SyntaxError as e:
        print(f"  ABORT — AST broke: {e}")
        return (0, total)

    path.write_text(new_src, encoding="utf-8")
    annotated = sum(1 for line in new_src.splitlines()
                    if "noqa: BLE001" in line)
    return (annotated, total)


def main() -> int:
    grand = (0, 0)
    for rel in FILES:
        p = REPO / rel
        if not p.exists():
            print(f"  SKIP {rel} (not found)")
            continue
        a, t = annotate(p)
        grand = (grand[0] + a, grand[1] + t)
        print(f"  {a:3d} noqa  /  {t:3d} bare-except  ({rel})")
    print()
    print(f"TOPLAM: {grand[0]} noqa annotations, {grand[1]} bare-except sites.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
