"""T7.6 Aşama A Windows smoke — 16 dosya import + CorrelationFilter canlılık.

Kullanım (Windows):
    py -3.11 scripts\smoke_t76_asama_a_imports.py

Başarı kriteri:
    - 16/16 modül hata atmadan import ediliyor
    - core.observability package export'u (CorrelationFilter) canlı
    - py_compile'dan öte, gerçek import (aiohttp/httpx/telegram stack'i dahil)
"""
from __future__ import annotations

import sys
import traceback

MODULES = [
    "core.observability",
    "core.kelly",
    "core.strategy_selector",
    "core.signals.whale_flow",
    "core.engine_support",
    "core.ev_tracker",
    "core.signal_fusion",
    "core.experiment_runner",
    "core.intent_parser",
    "core.decision_explainer",
    "core.keepalive",
    "core.bg_task",
    "core.becker_weight_tracker",
    "core.micro_weight_tracker",
    "core.changelog",
    "core.becker_rolling_recal",
]


def main() -> int:
    ok = 0
    fail: list[tuple[str, str]] = []
    for mod in MODULES:
        try:
            __import__(mod)
            ok += 1
            print(f"  [OK] {mod}")
        except Exception as e:  # noqa: BLE001 - smoke test needs full umbrella
            fail.append((mod, f"{type(e).__name__}: {e}"))
            print(f"  [FAIL] {mod} -- {type(e).__name__}: {e}")

    print()
    print(f"Sonuc: {ok}/{len(MODULES)} import OK")

    # CorrelationFilter canlılık kontrolü (Phase 48 shadow-fix doğrulaması)
    try:
        from core.observability import CorrelationFilter  # noqa: F401
        print("  [OK] core.observability.CorrelationFilter export canli")
    except ImportError as e:
        fail.append(("core.observability.CorrelationFilter", str(e)))
        print(f"  [FAIL] CorrelationFilter export yok -- {e}")

    if fail:
        print()
        print("=== Basarisiz import'lar ===")
        for mod, err in fail:
            print(f"  {mod}: {err}")
        return 1
    print()
    print("T7.6 Asama A Windows smoke PASSED (16/16 + CorrelationFilter)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
