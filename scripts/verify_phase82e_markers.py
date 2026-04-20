"""
scripts/verify_phase82e_markers.py — Phase 82e Sprint 5 FINAL.

Checks that the expected code markers are present in each of the 6
modified files. If any marker is missing, a prior edit was lost (git
pull conflict, unstaged rollback, etc.) and the bot should NOT restart.

Exit 0 if all markers present, 1 otherwise.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

MARKERS: dict[str, list[str]] = {
    "db/migrations.py": [
        '"version": 15',
        "idx_hopt_atf",
        "ADD COLUMN asset",
        "ADD COLUMN timeframe",
    ],
    "backtest/hyperopt.py": [
        "def _space_martingale",
        '"martingale": _space_martingale',
        "def is_overfit",
        "asset: str = \"\"",
        "timeframe: str = \"\"",
    ],
    "backtest/hyperopt_worker.py": [
        '"--asset"',
        '"--timeframe"',
        "asset_filter=",
        "timeframe_filter=",
    ],
    "telegram_bot/handlers/hyperopt_handler.py": [
        "strategy_type = ? AND asset = ? AND timeframe = ?",
        "labels_applied",
        "pending_asset",
        "pending_tf",
    ],
    "core/ai_brain.py": [
        "strategy_type = ? AND asset = ? AND timeframe = ?",
        "for sid_row in strat_rows",
        "r_asset",
        "r_tf",
    ],
    "core/engine_signals.py": [
        "WS_STALE_MIN_THRESHOLD",
        "WHIPSAW_BAND_LO",
        "WHIPSAW_BAND_HI",
        "PRICE_SANITY_LO",
        "PRICE_SANITY_HI",
        "_classic_bypass_zones",
        "CLASSIC_RESPECT_ZONES",
    ],
}


def main() -> int:
    missing: list[tuple[str, str]] = []
    total = 0
    for rel, markers in MARKERS.items():
        p = _ROOT / rel
        try:
            src = p.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  [FAIL ] {rel}: okunamadi ({e})")
            return 1
        for m in markers:
            total += 1
            if m not in src:
                missing.append((rel, m))

    if missing:
        print(f"  [FAIL ] {len(missing)}/{total} marker kayip:")
        for rel, m in missing:
            print(f"          {rel}  :  {m}")
        return 1

    print(f"  [ OK  ] {total}/{total} marker hazir (6 dosya)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
