"""
scripts/watch_classic_fire.py — Canli classic strategy fire watcher.

Kullanici v3 + v4 deploy sonrasi classic strategy'nin gercek trade atip
atmadigini dogrulamak icin polypaper.log'u tail eder ve yalnizca classic
ile ilgili olan satirlari ekrana basar.

Yakalanan pattern'ler:
  * "[classic]" — classic plugin eval (not-at-trigger VEYA fire)
  * "classic UP @ ..." / "classic DOWN @ ..." — FIRE event (should_trade=true)
  * "NEAR_CLOSE"  / "UNSELLABLE" / "ZONE_BLOCKED" — downstream gate blocks
  * "BUY"        — engine actual order sent (indicates trade attempt)
  * "🚀"         — FILL event

Exit: Ctrl-C.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Windows cp1252 console fix
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data_store" / "polypaper.log"

# Patterns to highlight (colorized via ANSI if tty)
INTEREST = [
    "[classic]",
    "classic UP @",
    "classic DOWN @",
    "NEAR_CLOSE",
    "UNSELLABLE",
    "ZONE_BLOCKED",
    "FEE_TAIL",
    "🚀",        # order filled
    "✅ BUY",    # order placed
    "FIRE",
]

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def highlight(line: str) -> str:
    if "classic UP @" in line or "classic DOWN @" in line or "🚀" in line:
        return f"{GREEN}{line}{RESET}"
    if "NEAR_CLOSE" in line or "UNSELLABLE" in line or "ZONE_BLOCKED" in line:
        return f"{RED}{line}{RESET}"
    if "[classic]" in line:
        return f"{YELLOW}{line}{RESET}"
    return line


def tail(path: Path):
    """Follow file like `tail -f`, yield new lines."""
    with path.open("r", encoding="utf-8", errors="replace") as f:
        # Seek to end
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue
            yield line.rstrip("\n")


def main() -> int:
    if not LOG.exists():
        print(f"[FAIL] log not found: {LOG}")
        return 1

    print(f"[watch] Tailing {LOG}")
    print(f"[watch] Looking for: {', '.join(INTEREST)}")
    print(f"[watch] Ctrl-C to exit.")
    print("=" * 78)

    try:
        for line in tail(LOG):
            if any(pat in line for pat in INTEREST):
                print(highlight(line), flush=True)
    except KeyboardInterrupt:
        print("\n[watch] bye.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
