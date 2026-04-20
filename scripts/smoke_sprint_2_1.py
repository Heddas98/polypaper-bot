"""
Phase 82e Sprint 2.1 Smoke Test
===============================
Verifies safe_create_task end-to-end: exception capture, registry population,
notify handler invocation. Runs in <1 second.

Extracted from deploy_phase82e_sprint_2_1.bat because Windows cmd cannot
parse multiline strings inside `py -3.11 -c "..."` — the deploy bat was
silently failing at step 11 and closing the window before the error could
be read.
"""
from __future__ import annotations

import asyncio
import os
import sys

# Ensure project root is on sys.path regardless of how we're invoked.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    try:
        from core.bg_task import (
            safe_create_task,
            get_registry_snapshot,
            get_recent_errors,
            set_notify_handler,
            clear_registry,
        )
    except Exception as e:
        print(f"  IMPORT FAIL: {type(e).__name__}: {e}")
        return 1

    notified: list[tuple[str, str]] = []

    async def boom():
        raise RuntimeError("sprint2_1_smoke")

    async def fake_notify(name: str, err: str, tb: str) -> None:
        notified.append((name, err))

    async def run():
        clear_registry()
        set_notify_handler(fake_notify)

        t = safe_create_task(boom(), name="test_sprint2_1")
        try:
            await t
        except Exception:
            pass
        # Give the finally block a tick to update registry
        await asyncio.sleep(0.05)

        snap = get_registry_snapshot()
        errs = get_recent_errors()

        assertions = [
            ("registry has task",
             "test_sprint2_1" in snap),
            ("task state == failed",
             snap.get("test_sprint2_1", {}).get("state") == "failed"),
            ("notify handler fired",
             len(notified) >= 1),
            ("recent errors captured",
             any(e.get("name") == "test_sprint2_1" for e in errs)),
        ]

        failures = [name for name, ok in assertions if not ok]
        if failures:
            print(f"  SMOKE FAIL: {failures}")
            print(f"  registry snap: {snap}")
            print(f"  notified: {notified}")
            print(f"  errs: {errs}")
            return 2

        print("  OK (notify fired, registry populated, 4/4 assertions pass)")
        clear_registry()
        return 0

    try:
        return asyncio.run(run())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"  RUN FAIL: {e}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
