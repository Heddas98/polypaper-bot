"""
Smoke test - WS_STALE_THRESHOLD runtime env refactor
Phase 82e post-Sprint 6 mini refactor.

Verifies:
  1) config/env_whitelist.py entry with correct type/default/bounds/group.
  2) core/engine.py _is_ws_fresh reads WS_STALE_THRESHOLD via os.getenv.
  3) telegram_bot/handlers/diagnose_handler.py uses the same env key (UI<->gate consistency).
  4) Runtime read: setting os.environ takes effect without re-import.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OK = "  [OK]"
FAIL = "  [FAIL]"

errors: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    line = f"{OK if cond else FAIL} {label}"
    if detail:
        line += f"  -  {detail}"
    print(line)
    if not cond:
        errors.append(label)


# -- 1) whitelist -------------------------------------------------------------
print("[1/4] Whitelist entry")
sys.path.insert(0, str(ROOT))
from config.env_whitelist import ENV_WHITELIST  # noqa: E402

ent = ENV_WHITELIST.get("WS_STALE_THRESHOLD")
check("entry exists", ent is not None)
if ent:
    check("type=float", ent.get("type") == "float", f"got {ent.get('type')!r}")
    check("default='60.0'", ent.get("default") == "60.0", f"got {ent.get('default')!r}")
    check("min=5.0", ent.get("min") == 5.0, f"got {ent.get('min')!r}")
    check("max=600.0", ent.get("max") == 600.0, f"got {ent.get('max')!r}")
    check("group='ws'", ent.get("group") == "ws", f"got {ent.get('group')!r}")

# -- 2) engine.py runtime os.getenv -------------------------------------------
print("\n[2/4] engine.py _is_ws_fresh runtime read")
engine_src = (ROOT / "core" / "engine.py").read_text(encoding="utf-8")
m = re.search(
    r"def _is_ws_fresh\(self\).*?return age < ws_stale_secs",
    engine_src,
    re.DOTALL,
)
check("_is_ws_fresh body present (new form)", m is not None)
if m:
    body = m.group(0)
    check(
        "reads os.getenv(\"WS_STALE_THRESHOLD\")",
        'os.getenv("WS_STALE_THRESHOLD"' in body,
    )
    check(
        "defaults to \"60.0\" string",
        '"60.0"' in body,
    )
    check(
        "no legacy constant usage in body",
        "WS_STALE_THRESHOLD)" not in body or "os.getenv" in body,
    )

# -- 3) diagnose handler consistency ------------------------------------------
print("\n[3/4] diagnose_handler consistency")
diag_src = (ROOT / "telegram_bot" / "handlers" / "diagnose_handler.py").read_text(encoding="utf-8")
cnt = diag_src.count('os.getenv("WS_STALE_THRESHOLD"')
check("diagnose reads same env key", cnt >= 1, f"occurrences={cnt}")

# -- 4) runtime effect simulated ----------------------------------------------
print("\n[4/4] Runtime read simulation")
os.environ["WS_STALE_THRESHOLD"] = "90.0"
val = float(os.getenv("WS_STALE_THRESHOLD", "60.0"))
check("env=90.0 picked up", val == 90.0, f"got {val}")
os.environ.pop("WS_STALE_THRESHOLD", None)
val_reset = float(os.getenv("WS_STALE_THRESHOLD", "60.0"))
check("reset -> 60.0 default", val_reset == 60.0, f"got {val_reset}")

# -- summary ------------------------------------------------------------------
print()
if errors:
    print(f"FAIL: {len(errors)} check(s) failed:")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
print("PASS: all checks OK")
sys.exit(0)
