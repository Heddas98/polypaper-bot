#!/usr/bin/env bash
# PolyPaper Bot — full test regression runner (sandbox/WSL).
# Epic 9 T9.10 closing artifact. Parity with run_full_regression.bat.
#
# Usage (repo root):
#   ./run_full_regression.sh              # full sandbox-compatible suite
#   ./run_full_regression.sh unit         # unit only (fast, ~41s)
#   ./run_full_regression.sh integration  # integration smoke only (~1.3s)
#   ./run_full_regression.sh seed 1337    # full suite with pytest-randomly seed

set -u  # unset variable = error (but NOT set -e — we want pytest exit to propagate)

# Stabilize env for deterministic GREEN.
# NOTE: SIGNAL_W_WHALE intentionally NOT set here — test_whale_signal.py
# expects the Phase 79b default (0.00); setting the override to 0.10
# breaks test_signal_fusion_includes_whale_weight. T7.6 runner was
# stale in this regard.
export DATABASE_PATH=":memory:"
export TELEGRAM_BOT_TOKEN="test-token"
export ADMIN_CHAT_ID="0"

echo "============================================================"
echo " PolyPaper Bot — Full Regression (sandbox)"
echo "============================================================"
echo

mode="${1:-full}"

case "$mode" in
    full)
        echo "=== Full suite (unit + integration) ==="
        python -m pytest tests -q
        exit_code=$?
        ;;
    unit)
        echo '=== Unit only (-m "not integration") ==='
        python -m pytest tests -q -m "not integration"
        exit_code=$?
        ;;
    integration)
        echo "=== Integration only (-m integration) ==="
        python -m pytest tests -q -m integration
        exit_code=$?
        ;;
    seed)
        if [ -z "${2:-}" ]; then
            echo "[ERROR] Seed not provided. Usage: ./run_full_regression.sh seed 1337"
            exit 2
        fi
        echo "=== Full suite with pytest-randomly seed=$2 ==="
        python -m pytest tests -q -p randomly --randomly-seed="$2"
        exit_code=$?
        ;;
    *)
        echo "[ERROR] Unknown mode: $mode"
        echo "Valid: full | unit | integration | seed <N>"
        exit 2
        ;;
esac

echo
echo "============================================================"
if [ "$exit_code" -eq 0 ]; then
    echo " ALL TESTS GREEN"
    echo " Baseline: 723 pass + 8 skip + 0 fail (2026-04-22 T9.10 closure)"
else
    echo " pytest exit code: $exit_code"
    echo " Expected pre-existing skips:"
    echo "   - test_phase82b optuna (sandbox deprecated)"
    echo "   - test_phase77 python-telegram-bot"
    echo "   - test_brain_flags_parity kelly_sizing & drift_monitor intentional"
    echo " If new failures: report."
fi
echo "============================================================"

exit "$exit_code"
