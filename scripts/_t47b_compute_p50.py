"""T4.7-B — Compute REST RTT percentiles from rest_timing buffer dump.

Reads JSON dump produced by `/drt save` (Telegram admin) or directly via
`core.observability.rest_timing.get_summary()` introspection. Outputs
recommended REST_LATENCY_MS + REST_LATENCY_JITTER_MS for config/settings.py.

Usage:
    py -3.11 scripts/_t47b_compute_p50.py
    py -3.11 scripts/_t47b_compute_p50.py --dump path/to/sample.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_dump(path: Path | None) -> dict:
    if path:
        return json.loads(path.read_text(encoding="utf-8"))

    # Try in-process introspection (only works on bot host with bot stopped)
    try:
        sys.path.insert(0, str(REPO))
        from core.observability.rest_timing import get_summary
        return get_summary()
    except (ImportError, AttributeError) as e:
        print(f"[t4.7-b] in-process import failed: {e}")
        print("[t4.7-b] Use --dump path/to/json (export via /drt save)")
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", type=Path, default=None,
                        help="Path to /drt save JSON dump.")
    args = parser.parse_args()

    data = load_dump(args.dump)
    samples_by_label = data.get("samples", {}) or data

    overall: list[float] = []
    print(f"{'label':<30} {'n':>6} {'p50':>8} {'p75':>8} {'p90':>8} {'p99':>8}")
    print("-" * 76)
    for label, samples in sorted(samples_by_label.items()):
        if not isinstance(samples, list) or not samples:
            continue
        # samples may be [ms, ...] or [{"ms": .., "ts": ..}]
        if isinstance(samples[0], dict):
            ms = [float(s.get("ms", s.get("rtt_ms", 0))) for s in samples]
        else:
            ms = [float(s) for s in samples]
        if not ms:
            continue
        overall.extend(ms)
        ms_sorted = sorted(ms)
        n = len(ms_sorted)
        p50 = ms_sorted[int(n * 0.50)]
        p75 = ms_sorted[int(n * 0.75)]
        p90 = ms_sorted[int(n * 0.90)]
        p99 = ms_sorted[min(int(n * 0.99), n - 1)]
        print(f"{label:<30} {n:>6} {p50:>8.0f} {p75:>8.0f} {p90:>8.0f} {p99:>8.0f}")

    if not overall:
        print("(no samples — telemetry empty)")
        return 1

    overall.sort()
    n = len(overall)
    p50 = overall[int(n * 0.50)]
    p75 = overall[int(n * 0.75)]
    p90 = overall[int(n * 0.90)]
    iqr = (overall[int(n * 0.75)] - overall[int(n * 0.25)])

    print()
    print("=" * 76)
    print(f"OVERALL  n={n}  p50={p50:.0f}ms  p75={p75:.0f}ms  p90={p90:.0f}ms  iqr={iqr:.0f}ms")
    print()
    print("Recommended config/settings.py update:")
    print(f"  REST_LATENCY_MS         = {int(round(p50))}     # was 200 (heuristic)")
    print(f"  REST_LATENCY_JITTER_MS  = {int(round(iqr/2))}   # was 80 (heuristic, ~iqr/2)")
    print()
    if p50 < 200:
        print(f"INSIGHT: heuristic 200ms over-estimates by "
              f"{(200 - p50) / 200 * 100:.0f}%. Backtest fill currently slower.")
    print()
    print("After applying, re-run:")
    print("  py -3.11 scripts/sweep_fill_heuristic.py --strategy classic --markets 200")
    print("Verdict should approach PASS (delta < 5%).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
