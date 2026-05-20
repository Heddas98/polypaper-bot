"""Polymarket sabitleri drift-check (Faz 6, 2026-05-20).

Heddas direktifi: backtest sabitlerinin Polymarket docs ile uyumlu olduğunu
periyodik olarak doğrula. Bu script `core/fees_v2.py` + tail zone + RTDS
referanslarını terminal'e basar; `pytest tests/unit/test_polymarket_constants_drift.py`
ile birlikte koşulduğunda code-vs-docs uyumu doğrulanır.

KULLANIM:
    py -3.11 scripts/check_polymarket_drift.py            # summary print
    py -3.11 scripts/check_polymarket_drift.py --strict   # exit 1 if any test fails

PERIYODIK KOSULMASI (önerilen):
    - haftada 1 manuel
    - Polymarket'in fee/order types değiştirdiğine dair Twitter/Discord
      sinyali geldiğinde acil

DOCS MCP DOĞRULAMASI (son):
    2026-05-20 — bu commit (`.claude/worktrees/sweet-archimedes-5a2ce9`).
    Tüm pin'lenmiş sabitler bu tarihte docs MCP ile karşılaştırıldı:
      * /trading/fees (CATEGORY_FEES, fee curve)
      * /concepts/prices-orderbook (tick + limit order doctrine)
      * /trading/orders/overview (GTC/GTD/FOK/FAK)
      * /market-data/websocket/rtds (Chainlink 5m/15m crypto)

SONRAKİ DOĞRULAMA: 2026-06-20 (1 ay sonrası önerilir).
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

# Windows console cp1252 default — Türkçe karakter ve emoji'leri kaldırır.
# UTF-8'e zorla, çıktı her platformda aynı görünür.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)

# Path setup — repo root'a göre import
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.fees_v2 import (  # noqa: E402
    CATEGORY_FEES,
    DEFAULT_CATEGORY,
    TAIL_HIGH,
    TAIL_LOW,
    polymarket_taker_fee_v2,
)


def _print_header(title: str) -> None:
    print(f"\n=== {title} ===")


def _check_categories() -> None:
    _print_header("CATEGORY_FEES (core/fees_v2.py)")
    print(f"  Varsayilan kategori: {DEFAULT_CATEGORY}")
    print(f"  Tail zones: TAIL_LOW={TAIL_LOW} | TAIL_HIGH={TAIL_HIGH}")
    print()
    print(f"  {'Category':<12} {'taker_rate':>10} {'exp':>5} {'maker_rebate':>14}")
    print(f"  {'-' * 50}")
    for cat in sorted(CATEGORY_FEES.keys()):
        p = CATEGORY_FEES[cat]
        print(
            f"  {cat:<12} {p['taker_rate']:>10.4f} {p['taker_exp']:>5} "
            f"{p['maker_rebate_pct']:>14.2%}"
        )


def _check_fee_curve() -> None:
    """Docs Crypto fee table (price | trade value | taker fee) yeniden hesapla."""
    _print_header("Crypto fee curve — docs table cross-check")
    print("  Docs: docs.polymarket.com/trading/fees#crypto")
    print()
    print(f"  {'Price':>6} {'Amount $':>10} {'Code Fee $':>12} {'Docs Fee $':>12} {'Drift':>8}")
    print(f"  {'-' * 55}")
    docs_table = [
        (0.01, 1.0, 0.07),
        (0.05, 5.0, 0.33),
        (0.10, 10.0, 0.63),
        (0.20, 20.0, 1.12),
        (0.30, 30.0, 1.47),
        (0.50, 50.0, 1.75),
        (0.70, 70.0, 1.47),
        (0.90, 90.0, 0.63),
        (0.99, 99.0, 0.07),
    ]
    drift_count = 0
    for price, amt, docs_fee in docs_table:
        code_fee = polymarket_taker_fee_v2(price, amt, "crypto")
        drift = abs(code_fee - docs_fee)
        flag = "OK" if drift < 0.01 else "DRIFT"
        if flag == "DRIFT":
            drift_count += 1
        print(
            f"  {price:>6.2f} {amt:>10.2f} {code_fee:>12.4f} "
            f"{docs_fee:>12.4f} {flag:>8}"
        )
    print()
    if drift_count:
        print(f"  [DRIFT] {drift_count} satir uyumsuz - docs MCP ile yeniden dogrula.")
    else:
        print("  [OK] Tum satirlar uyumlu (drift < 0.01).")


def _check_order_types_reference() -> None:
    _print_header("Order types — referans (docs/trading/orders)")
    print("  GTC — Good-Til-Cancelled  (limit, rests on book)")
    print("  GTD — Good-Til-Date       (limit, time-limited)")
    print("  FOK — Fill-Or-Kill        (market, all-or-nothing)")
    print("  FAK — Fill-And-Kill       (market, partial OK)")
    print()
    print("  NOT: V2 SDK serbest geçiş; bot side'da hard-validation yok.")
    print("  GTC limit sim Faz 5 (replay_engine.entry_limit_price).")


def _check_resolution_source() -> None:
    _print_header("Resolution source — 5m/15m crypto markets")
    print("  Docs: market-data/websocket/rtds#chainlink-source")
    print("  Doctrine: 5m + 15m BTC/ETH up/down → Chainlink BTC/USD data stream")
    print("            (Binance spot DEĞIL).")
    rtds_path = REPO_ROOT / "data" / "polymarket_rtds.py"
    if rtds_path.exists():
        print(f"  [OK] {rtds_path.name} mevcut - RTDS baglantisi kurulu.")
    else:
        print(f"  [MISSING] {rtds_path.name} BULUNAMADI - RTDS baglantisi kayip!")


def _run_pin_tests(strict: bool) -> int:
    """test_polymarket_constants_drift.py'yi çalıştır, exit code döndür."""
    _print_header("Pinning tests (test_polymarket_constants_drift.py)")
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(REPO_ROOT / "tests" / "unit" / "test_polymarket_constants_drift.py"),
        "-q",
    ]
    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    if result.returncode != 0:
        print("  [FAIL] Pin testleri kirildi - bir sabit docs ile uyumsuz.")
        if strict:
            return 1
    return 0


def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    print("=" * 60)
    print("  Polymarket Constants Drift Check")
    print(f"  Repo: {REPO_ROOT}")
    print("=" * 60)
    _check_categories()
    _check_fee_curve()
    _check_order_types_reference()
    _check_resolution_source()
    exit_code = _run_pin_tests(strict)
    print()
    print("=" * 60)
    print(
        "  Son docs MCP doğrulaması: 2026-05-20.\n"
        "  Önerilen sonraki doğrulama: 2026-06-20."
    )
    print("=" * 60)
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
