"""
scripts/tail_errors.py — Safe log tail + ERROR scan.

Avoids the delayed-expansion `!` issues that bite inline `py -c`
strings inside Windows .bat with `setlocal enabledelayedexpansion`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    log = _ROOT / "data_store" / "polypaper.log"
    lock = _ROOT / "data_store" / "polypaper.lock"

    # Lockfile
    if lock.exists():
        try:
            pid = lock.read_text(encoding="utf-8", errors="ignore").strip()
            print(f"    Lockfile PID: {pid}")
        except Exception as e:
            print(f"    Lockfile read error: {e}")
    else:
        print("    Lockfile YOK (bot baslamadi!)")

    # Log tail (last 25)
    if not log.exists():
        print("    LOG YOK - bot baslamadi olabilir")
        return 1

    try:
        lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception as e:
        print(f"    Log okuma hatasi: {e}")
        return 1

    print()
    print("    --- son 25 log satiri ---")
    for l in lines[-25:]:
        print(f"     {l}")

    print()
    tail = lines[-200:]  # scan wider for errors
    errs = [l for l in tail if ("ERROR" in l or "CRITICAL" in l)]
    print(f"    ERROR/CRITICAL son 200 satirda: {len(errs)}")
    for l in errs[:10]:
        # mark with plain chars to avoid cmd weirdness
        print(f"     !! {l}")

    if errs:
        return 2   # soft-warn (bat's :fail triggers at errorlevel>=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
