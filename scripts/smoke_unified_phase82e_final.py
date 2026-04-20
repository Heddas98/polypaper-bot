"""
scripts/smoke_unified_phase82e_final.py
──────────────────────────────────────────────────────────────────────
Phase 82e Sprint 5 (FINAL) — unified smoke for the bundled deploy:

  P0-1  Fusion×29 granular apply
          - db/migrations.py           v15 hyperopt_results_asset_tf
          - backtest/hyperopt.py       HyperOptResult.asset/timeframe,
                                       save_to_db writes new cols
          - backtest/hyperopt_worker.py  --asset / --timeframe CLI
          - telegram_bot/handlers/hyperopt_handler.py
                                       parse --asset/--tf, pending carries
                                       asset/tf, apply matches (type,
                                       asset, tf) and UPDATEs ALL rows
          - core/ai_brain.py           _apply_hyperopt_result reads
                                       asset/tf from row, granular match

  P0-2  Orphan martingale PARAM_SPACES
          - backtest/hyperopt.py       _space_martingale registered in
                                       PARAM_SPACES["martingale"]

  P1-1  Test split overfit gate — sign-aware
          - backtest/hyperopt.py       HyperOptResult.is_overfit() now
                                       handles train_score<0 correctly

  P1-2  engine_signals ENV'e çıkart
          - core/engine_signals.py     WS_STALE_MIN_THRESHOLD,
                                       WHIPSAW_BAND_LO/HI,
                                       PRICE_SANITY_LO/HI

  Bugfix Classic bypasses ALLOWED_ZONES
          - core/engine_signals.py     stype=="classic" bypasses global
                                       ALLOWED_ZONES (opt-out via
                                       CLASSIC_RESPECT_ZONES=true)

Run:    py -3.11 scripts\\smoke_unified_phase82e_final.py
Exit:   0 on success, 1 on any assertion failure.
"""
from __future__ import annotations

import ast
import os
import sys
import inspect
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _ok(tag: str, msg: str = "") -> None:
    print(f"  [ OK  ] {tag}  {msg}".rstrip())


def _fail(tag: str, msg: str) -> int:
    print(f"  [FAIL ] {tag}  {msg}")
    return 1


def _read(rel: str) -> str:
    p = _ROOT / rel
    return p.read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────
#  P0-1 / v15 migration
# ──────────────────────────────────────────────────────────────────
def test_migration_v15() -> int:
    print("[1] db/migrations.py — v15 hyperopt_results_asset_tf")
    src = _read("db/migrations.py")
    if '"version": 15' not in src:
        return _fail("1a", "version 15 kaydı migrations listesinde yok")
    if "hyperopt_results_asset_tf" not in src:
        return _fail("1b", "migration name etiketi yok")
    if "ADD COLUMN asset" not in src or "ADD COLUMN timeframe" not in src:
        return _fail("1c", "ALTER TABLE ADD COLUMN asset/timeframe yok")
    if "idx_hopt_atf" not in src:
        return _fail("1d", "composite index idx_hopt_atf yok")
    _ok("1", "v15 migration doğru şekilde kayıtlı")
    return 0


# ──────────────────────────────────────────────────────────────────
#  P0-1 / HyperOptResult dataclass + save_to_db
# ──────────────────────────────────────────────────────────────────
def test_hyperopt_result_tags() -> int:
    print("[2] HyperOptResult.asset/timeframe + save_to_db INSERT")
    try:
        from backtest.hyperopt import HyperOptResult
    except Exception as e:
        return _fail("2a", f"import error: {e}")

    # Dataclass fields
    f = {x.name for x in HyperOptResult.__dataclass_fields__.values()}
    if "asset" not in f or "timeframe" not in f:
        return _fail("2b", f"dataclass field eksik: {sorted(f)}")

    # Round-trip (in-memory only — no DB write here)
    r = HyperOptResult(
        strategy_name="fusion",
        best_params={"SIGNAL_W_ODDS": 0.15},
        best_score=0.55,
        train_score=0.60,
        test_score=0.54,
        overfit_ratio=0.90,
        asset="BTC",
        timeframe="5m",
    )
    if r.asset != "BTC" or r.timeframe != "5m":
        return _fail("2c", f"asset/tf round-trip bozuk: {r.asset}/{r.timeframe}")

    # save_to_db SQL must reference asset & timeframe in INSERT
    src = _read("backtest/hyperopt.py")
    # locate save_to_db method body
    idx = src.find("def save_to_db")
    if idx < 0:
        return _fail("2d", "save_to_db metodu bulunamadı")
    body = src[idx: idx + 1600]
    if "asset" not in body or "timeframe" not in body:
        return _fail("2e", "save_to_db INSERT asset/timeframe kolonlarını yazmıyor")

    _ok("2", "HyperOptResult.asset/timeframe + save_to_db wired")
    return 0


# ──────────────────────────────────────────────────────────────────
#  P1-1 / is_overfit sign-aware
# ──────────────────────────────────────────────────────────────────
def test_is_overfit_sign_aware() -> int:
    print("[3] HyperOptResult.is_overfit() sign-aware davranış")
    try:
        from backtest.hyperopt import HyperOptResult
    except Exception as e:
        return _fail("3a", f"import error: {e}")

    # Force threshold to 0.70 for deterministic checks
    prev = os.environ.get("HYPEROPT_OVERFIT_THRESHOLD")
    os.environ["HYPEROPT_OVERFIT_THRESHOLD"] = "0.70"
    try:
        # ── positive train, healthy test → NOT overfit (ratio 0.95 >= 0.70)
        pos_ok = HyperOptResult(strategy_name="x", best_params={}, best_score=1.0,
                                train_score=1.0, test_score=0.95, overfit_ratio=0.95)
        if pos_ok.is_overfit():
            return _fail("3b", "pos/healthy false-positive overfit")

        # ── positive train, low test → overfit (ratio 0.30 < 0.70)
        pos_bad = HyperOptResult(strategy_name="x", best_params={}, best_score=1.0,
                                 train_score=1.0, test_score=0.30, overfit_ratio=0.30)
        if not pos_bad.is_overfit():
            return _fail("3c", "pos/bad false-negative overfit")

        # ── zero train → overfit (conservative)
        zero = HyperOptResult(strategy_name="x", best_params={}, best_score=0.0,
                              train_score=0.0, test_score=0.0, overfit_ratio=0.0)
        if not zero.is_overfit():
            return _fail("3d", "zero train should be overfit (conservative)")

        # ── negative train but test roughly the same → NOT overfit (ratio=1.0, 1.0 < 1.30 thr)
        neg_ok = HyperOptResult(strategy_name="x", best_params={}, best_score=-0.5,
                                train_score=-0.5, test_score=-0.5, overfit_ratio=1.0)
        if neg_ok.is_overfit():
            return _fail("3e", "neg/matched test false-positive overfit")

        # ── negative train and test much worse → overfit (ratio=2.0 > 1.30 thr=2-0.70)
        neg_bad = HyperOptResult(strategy_name="x", best_params={}, best_score=-0.5,
                                 train_score=-0.5, test_score=-1.0, overfit_ratio=2.0)
        if not neg_bad.is_overfit():
            return _fail("3f", "neg/much-worse-test false-negative overfit")
    finally:
        if prev is None:
            os.environ.pop("HYPEROPT_OVERFIT_THRESHOLD", None)
        else:
            os.environ["HYPEROPT_OVERFIT_THRESHOLD"] = prev

    _ok("3", "is_overfit() 5/5 sign-aware kontrolü geçti")
    return 0


# ──────────────────────────────────────────────────────────────────
#  P0-2 / martingale PARAM_SPACES
# ──────────────────────────────────────────────────────────────────
def test_martingale_param_space() -> int:
    print("[4] PARAM_SPACES[\"martingale\"] kayıtlı mı?")
    try:
        from backtest.hyperopt import PARAM_SPACES, _space_martingale
    except Exception as e:
        return _fail("4a", f"import error: {e}")

    if "martingale" not in PARAM_SPACES:
        return _fail("4b", f"PARAM_SPACES anahtarları: {sorted(PARAM_SPACES)}")

    if PARAM_SPACES["martingale"] is not _space_martingale:
        return _fail("4c", "registry referansı _space_martingale değil")

    sig_params = list(inspect.signature(_space_martingale).parameters)
    if sig_params != ["trial"]:
        return _fail("4d", f"_space_martingale imzası beklenmiyor: {sig_params}")

    _ok("4", "martingale PARAM_SPACES ile Optuna tunable")
    return 0


# ──────────────────────────────────────────────────────────────────
#  P0-1 / worker CLI --asset --timeframe
# ──────────────────────────────────────────────────────────────────
def test_worker_cli_asset_tf() -> int:
    print("[5] hyperopt_worker CLI --asset / --timeframe parse eder")
    src = _read("backtest/hyperopt_worker.py")
    if '"--asset"' not in src:
        return _fail("5a", "worker argparse'e --asset eklenmemiş")
    if '"--timeframe"' not in src:
        return _fail("5b", "worker argparse'e --timeframe eklenmemiş")

    # Ensure CLI values flow through to HyperOptConfig asset_filter/timeframe_filter
    if "asset_filter=" not in src or "timeframe_filter=" not in src:
        return _fail("5c", "asset_filter/timeframe_filter HyperOptConfig'e iletilmiyor")

    _ok("5", "worker CLI & config propagation tamam")
    return 0


# ──────────────────────────────────────────────────────────────────
#  P0-1 / handler --asset --tf parse + pending carries
# ──────────────────────────────────────────────────────────────────
def test_handler_parse_and_apply() -> int:
    print("[6] hyperopt_handler --asset/--tf parse + apply granular match")
    try:
        from telegram_bot.handlers.hyperopt_handler import _parse_hyperopt_args
    except Exception as e:
        return _fail("6a", f"import error: {e}")

    out = _parse_hyperopt_args(
        ["fusion", "50", "--asset", "btc", "--tf", "5m", "--last", "30"])
    if out.get("strategy") != "fusion" or out.get("n_trials") != 50:
        return _fail("6b", f"pozisyonel parse bozuk: {out}")
    if out.get("asset") != "BTC":
        return _fail("6c", f"asset upper-strip eksik: {out.get('asset')!r}")
    if out.get("timeframe") != "5m":
        return _fail("6d", f"tf alias (--tf) parse eksik: {out.get('timeframe')!r}")
    if out.get("last_n") != 30:
        return _fail("6e", f"--last hala çalışmalı: {out}")

    # --timeframe (long form) should also work
    out2 = _parse_hyperopt_args(["fusion", "50", "--timeframe", "15m"])
    if out2.get("timeframe") != "15m":
        return _fail("6f", f"--timeframe long-form parse eksik: {out2}")

    # Apply callback SQL shape (AST substring checks — no DB roundtrip)
    src = _read("telegram_bot/handlers/hyperopt_handler.py")
    if "strategy_type = ? AND asset = ? AND timeframe = ?" not in src:
        return _fail("6g", "apply callback granular WHERE clause yok")
    # Must iterate rows and UPDATE all — spot-check variable pattern
    if "labels_applied" not in src:
        return _fail("6h", "labels_applied list (UPDATE ALL proof) yok")

    _ok("6", "handler parse + apply SQL granular match")
    return 0


# ──────────────────────────────────────────────────────────────────
#  P0-1 / ai_brain granular apply
# ──────────────────────────────────────────────────────────────────
def test_ai_brain_granular_apply() -> int:
    print("[7] ai_brain._apply_hyperopt_result asset/tf ile granular")
    src = _read("core/ai_brain.py")

    # SELECT must fetch asset/timeframe from hyperopt_results
    if "asset, timeframe" not in src:
        return _fail("7a", "SELECT asset, timeframe eklenmemiş")
    # WHERE clause must match strategy_type + asset + timeframe
    if "strategy_type = ? AND asset = ? AND timeframe = ?" not in src:
        return _fail("7b", "granular WHERE clause yok (ai_brain)")
    # UPDATE loop must iterate strat_rows (not rows[0])
    if "for sid_row in strat_rows" not in src:
        return _fail("7c", "strat_rows üzerinde döngü yok (still rows[0]?)")

    _ok("7", "ai_brain apply path UPDATEs ALL matched strategies")
    return 0


# ──────────────────────────────────────────────────────────────────
#  P1-2 / engine_signals ENV thresholds
# ──────────────────────────────────────────────────────────────────
def test_engine_signals_env_thresholds() -> int:
    print("[8] engine_signals ENV-tunable thresholds")
    src = _read("core/engine_signals.py")
    for needle, tag in [
        ('WS_STALE_MIN_THRESHOLD', "8a WS_STALE_MIN_THRESHOLD"),
        ('WHIPSAW_BAND_LO',        "8b WHIPSAW_BAND_LO"),
        ('WHIPSAW_BAND_HI',        "8c WHIPSAW_BAND_HI"),
        ('PRICE_SANITY_LO',        "8d PRICE_SANITY_LO"),
        ('PRICE_SANITY_HI',        "8e PRICE_SANITY_HI"),
    ]:
        if needle not in src:
            return _fail(tag, f"{needle} env override eksik")

    _ok("8", "5 env override (WS/whipsaw/price) yerinde")
    return 0


# ──────────────────────────────────────────────────────────────────
#  Classic ZONE bypass
# ──────────────────────────────────────────────────────────────────
def test_classic_zone_bypass() -> int:
    print("[9] Classic stype, ALLOWED_ZONES bypass + opt-out hatchi")
    src = _read("core/engine_signals.py")
    if "_classic_bypass_zones" not in src:
        return _fail("9a", "_classic_bypass_zones değişkeni yok")
    if "CLASSIC_RESPECT_ZONES" not in src:
        return _fail("9b", "CLASSIC_RESPECT_ZONES opt-out env yok")
    # Must be guarded in ZONE_BLOCKED branch
    idx = src.find("ZONE_BLOCKED")
    if idx < 0:
        return _fail("9c", "ZONE_BLOCKED skip branch yok")
    window = src[max(0, idx - 400): idx + 200]
    if "_classic_bypass_zones" not in window:
        return _fail("9d", "zone guard içinde bypass flag kullanılmıyor")

    _ok("9", "classic stype ALLOWED_ZONES bypass doğru konumda")
    return 0


# ──────────────────────────────────────────────────────────────────
#  Syntax check for all modified files (catch stray edits early)
# ──────────────────────────────────────────────────────────────────
def test_syntax_all() -> int:
    print("[10] Tüm değişen dosyalar için ast.parse")
    files = [
        "db/migrations.py",
        "backtest/hyperopt.py",
        "backtest/hyperopt_worker.py",
        "telegram_bot/handlers/hyperopt_handler.py",
        "core/ai_brain.py",
        "core/engine_signals.py",
    ]
    for f in files:
        try:
            ast.parse(_read(f))
        except SyntaxError as e:
            return _fail("10", f"{f}: {e}")
    _ok("10", f"{len(files)}/{len(files)} dosya syntax temiz")
    return 0


def main() -> int:
    print("═════════════════════════════════════════════════════════════")
    print("  Phase 82e Sprint 5 FINAL — Unified Smoke")
    print("═════════════════════════════════════════════════════════════")

    checks = [
        test_migration_v15,
        test_hyperopt_result_tags,
        test_is_overfit_sign_aware,
        test_martingale_param_space,
        test_worker_cli_asset_tf,
        test_handler_parse_and_apply,
        test_ai_brain_granular_apply,
        test_engine_signals_env_thresholds,
        test_classic_zone_bypass,
        test_syntax_all,
    ]
    fails = 0
    for fn in checks:
        try:
            rc = fn()
        except Exception as e:
            print(f"  [FAIL ] {fn.__name__} raised: {e}")
            rc = 1
        fails += (1 if rc else 0)

    print()
    print("═════════════════════════════════════════════════════════════")
    if fails == 0:
        print(f"  ALL {len(checks)} CHECKS PASSED ✅")
        print("═════════════════════════════════════════════════════════════")
        return 0
    print(f"  {fails}/{len(checks)} CHECK FAILED ❌")
    print("═════════════════════════════════════════════════════════════")
    return 1


if __name__ == "__main__":
    sys.exit(main())
