"""Backtest LAB Faz 3 — RuleBasedStrategy (2026-05-20).

Heddas direktifi: no-code kural cümlesi ile strateji kurma. Bu testler
3 alanı pin'ler:
  1. _eval_condition / _eval_entry_block — operator, AND/OR, defansif None
  2. RuleBasedStrategy — sinyal üretme, tek-giriş, market context, direction
  3. RuleSet I/O — validate, save/load, list, delete, dosya-güvenli ad
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest.strategies.base import Direction, MarketData, OrderbookSnapshot
from backtest.strategies.rule_based import (
    RuleBasedStrategy,
    RuleSetError,
    _eval_condition,
    _eval_entry_block,
    delete_ruleset,
    list_rulesets,
    load_all_stats,
    load_backtest_stat,
    load_ruleset,
    record_backtest_stat,
    save_ruleset,
    validate_ruleset,
)

# ── Fixtures ────────────────────────────────────────────────


def _snap(**kw) -> OrderbookSnapshot:
    """Tüm field'lar default + override."""
    defaults = dict(
        timestamp_ms=1_000_000,
        up_best_bid=0.50,
        up_best_ask=0.55,
        down_best_bid=0.45,
        down_best_ask=0.50,
        spread=0.05,
        binance_price=100_000.0,
        binance_price_change=0.5,
        up_bid_depth=1000.0,
        up_ask_depth=1500.0,
        down_bid_depth=900.0,
        down_ask_depth=800.0,
        elapsed_seconds=30.0,
        remaining_seconds=270.0,
        elapsed_pct=0.10,
    )
    defaults.update(kw)
    return OrderbookSnapshot(**defaults)


def _market(**kw) -> MarketData:
    defaults = dict(
        market_id="m1",
        coin="BTC",
        market_type="5m",
        hour_utc=22,
        volume=1000.0,
        liquidity=500.0,
        duration_seconds=300,
    )
    defaults.update(kw)
    return MarketData(**defaults)


# ── _eval_condition ─────────────────────────────────────────


def test_cond_basic_gte_true():
    assert _eval_condition(
        {"field": "elapsed_seconds", "op": ">=", "value": 30}, _snap(elapsed_seconds=30), None
    )


def test_cond_basic_gte_false():
    assert not _eval_condition(
        {"field": "elapsed_seconds", "op": ">=", "value": 31}, _snap(elapsed_seconds=30), None
    )


def test_cond_all_operators():
    s = _snap(up_best_ask=0.55)
    for op, val, expected in [
        ("==", 0.55, True),
        ("==", 0.56, False),
        ("!=", 0.55, False),
        ("!=", 0.56, True),
        (">", 0.50, True),
        (">", 0.55, False),
        (">=", 0.55, True),
        ("<", 0.60, True),
        ("<", 0.55, False),
        ("<=", 0.55, True),
        ("in", [0.50, 0.55, 0.60], True),
        ("in", [0.10, 0.20], False),
        ("not_in", [0.10, 0.20], True),
        ("not_in", [0.55], False),
    ]:
        assert _eval_condition(
            {"field": "up_best_ask", "op": op, "value": val}, s, None
        ) is expected, f"{op} {val} bekleniyordu {expected}"


def test_cond_unknown_op_returns_false():
    """Bilinmeyen op → defansif False."""
    assert not _eval_condition(
        {"field": "elapsed_seconds", "op": "::badop::", "value": 30}, _snap(), None
    )


def test_cond_missing_field_returns_false():
    assert not _eval_condition(
        {"field": "nonexistent_field", "op": ">=", "value": 0}, _snap(), None
    )


def test_cond_market_field_lookup():
    """market.hour_utc gibi field market context'ten okunur."""
    s = _snap()
    m = _market(hour_utc=22)
    assert _eval_condition({"field": "hour_utc", "op": "==", "value": 22}, s, m)
    assert not _eval_condition({"field": "hour_utc", "op": "==", "value": 14}, s, m)


def test_cond_type_mismatch_returns_false():
    """Float vs string karşılaştırma → sessiz False, exception YOK."""
    assert not _eval_condition(
        {"field": "up_best_ask", "op": ">", "value": "string"}, _snap(), None
    )


def test_cond_malformed_dict_returns_false():
    assert not _eval_condition("not-a-dict", _snap(), None)
    assert not _eval_condition({}, _snap(), None)  # field eksik
    assert not _eval_condition({"field": ""}, _snap(), None)  # boş field


# ── _eval_entry_block ──────────────────────────────────────


def test_entry_and_all_true():
    entry = {
        "logic": "AND",
        "conditions": [
            {"field": "elapsed_seconds", "op": ">=", "value": 30},
            {"field": "up_best_ask", "op": ">=", "value": 0.55},
        ],
    }
    assert _eval_entry_block(entry, _snap(elapsed_seconds=30, up_best_ask=0.55), None)


def test_entry_and_one_false_fails():
    entry = {
        "logic": "AND",
        "conditions": [
            {"field": "elapsed_seconds", "op": ">=", "value": 30},
            {"field": "up_best_ask", "op": ">=", "value": 0.99},
        ],
    }
    assert not _eval_entry_block(entry, _snap(elapsed_seconds=30, up_best_ask=0.55), None)


def test_entry_or_one_true_passes():
    entry = {
        "logic": "OR",
        "conditions": [
            {"field": "elapsed_seconds", "op": ">=", "value": 300},  # F
            {"field": "up_best_ask", "op": ">=", "value": 0.55},  # T
        ],
    }
    assert _eval_entry_block(entry, _snap(), None)


def test_entry_or_all_false_fails():
    entry = {
        "logic": "OR",
        "conditions": [
            {"field": "elapsed_seconds", "op": ">=", "value": 300},
            {"field": "up_best_ask", "op": ">=", "value": 0.99},
        ],
    }
    assert not _eval_entry_block(entry, _snap(), None)


def test_entry_empty_conditions_never_fires():
    """Boş ruleset = güvenli no-op."""
    assert not _eval_entry_block({"logic": "AND", "conditions": []}, _snap(), None)
    assert not _eval_entry_block({}, _snap(), None)


def test_entry_unknown_logic_defaults_to_and():
    """Yazım hatası → AND'e düşer (predictable)."""
    entry = {
        "logic": "XYZ",
        "conditions": [
            {"field": "elapsed_seconds", "op": ">=", "value": 30},
            {"field": "elapsed_seconds", "op": "<=", "value": 60},
        ],
    }
    assert _eval_entry_block(entry, _snap(elapsed_seconds=30), None)
    assert not _eval_entry_block(entry, _snap(elapsed_seconds=100), None)


# ── RuleBasedStrategy ───────────────────────────────────────


def test_strategy_no_conditions_never_fires():
    s = RuleBasedStrategy()
    s.on_market_open(_market())
    result = s.on_snapshot(_snap())
    assert result is None


def test_strategy_single_condition_fires_once():
    s = RuleBasedStrategy.from_ruleset(
        {
            "name": "test1",
            "direction": "up",
            "confidence": 0.8,
            "entry": {
                "logic": "AND",
                "conditions": [{"field": "elapsed_seconds", "op": ">=", "value": 30}],
            },
        }
    )
    s.on_market_open(_market())
    # İlk eşleşmede ateşler
    r1 = s.on_snapshot(_snap(elapsed_seconds=30))
    assert r1 is not None
    assert r1.direction == Direction.UP
    assert r1.confidence == 0.8
    assert "rule:test1" in r1.reason
    # İkinci snapshot tekrar ateşlemez
    r2 = s.on_snapshot(_snap(elapsed_seconds=60))
    assert r2 is None


def test_strategy_direction_down():
    s = RuleBasedStrategy.from_ruleset(
        {
            "name": "down1",
            "direction": "down",
            "entry": {
                "conditions": [{"field": "elapsed_seconds", "op": ">=", "value": 0}],
            },
        }
    )
    s.on_market_open(_market())
    r = s.on_snapshot(_snap(down_best_ask=0.42))
    assert r is not None
    assert r.direction == Direction.DOWN
    assert r.entry_price == 0.42  # down side ask


def test_strategy_confidence_clamped():
    s = RuleBasedStrategy.from_ruleset(
        {
            "name": "high",
            "confidence": 1.5,  # geçersiz — clamp
            "entry": {"conditions": [{"field": "elapsed_seconds", "op": ">=", "value": 0}]},
        }
    )
    s.on_market_open(_market())
    r = s.on_snapshot(_snap())
    assert r is not None
    assert r.confidence == 1.0


def test_strategy_invalid_confidence_falls_back():
    s = RuleBasedStrategy.from_ruleset(
        {
            "name": "bad",
            "confidence": "not-a-number",
            "entry": {"conditions": [{"field": "elapsed_seconds", "op": ">=", "value": 0}]},
        }
    )
    s.on_market_open(_market())
    r = s.on_snapshot(_snap())
    assert r is not None
    assert r.confidence == 0.7  # default


def test_strategy_market_context_field():
    s = RuleBasedStrategy.from_ruleset(
        {
            "name": "hour22",
            "direction": "up",
            "entry": {
                "conditions": [{"field": "hour_utc", "op": "==", "value": 22}],
            },
        }
    )
    s.on_market_open(_market(hour_utc=22))
    assert s.on_snapshot(_snap()) is not None

    s2 = RuleBasedStrategy.from_ruleset(
        {
            "name": "hour22",
            "direction": "up",
            "entry": {"conditions": [{"field": "hour_utc", "op": "==", "value": 22}]},
        }
    )
    s2.on_market_open(_market(hour_utc=15))
    assert s2.on_snapshot(_snap()) is None


def test_strategy_complex_and_combination():
    """Kullanıcı senaryosu: 30-50sn arası AL + up_ask 0.55-0.70 arası."""
    s = RuleBasedStrategy.from_ruleset(
        {
            "name": "complex",
            "entry": {
                "logic": "AND",
                "conditions": [
                    {"field": "elapsed_seconds", "op": ">=", "value": 30},
                    {"field": "elapsed_seconds", "op": "<=", "value": 50},
                    {"field": "up_best_ask", "op": ">=", "value": 0.55},
                    {"field": "up_best_ask", "op": "<=", "value": 0.70},
                ],
            },
        }
    )
    s.on_market_open(_market())
    assert s.on_snapshot(_snap(elapsed_seconds=40, up_best_ask=0.60)) is not None

    s2 = RuleBasedStrategy()
    s2.params = s.params  # aynı ruleset
    s2.on_market_open(_market())
    assert s2.on_snapshot(_snap(elapsed_seconds=51, up_best_ask=0.60)) is None  # saniye dışı


def test_strategy_registry_finds_rule_based():
    from backtest.strategies.base import StrategyRegistryV2

    klass = StrategyRegistryV2.get("rule_based")
    assert klass is RuleBasedStrategy


def test_strategy_from_ruleset_handles_non_dict():
    s = RuleBasedStrategy.from_ruleset("not-a-dict")  # type: ignore
    # Default'lar korunmuş, hiç ateşlemiyor
    s.on_market_open(_market())
    assert s.on_snapshot(_snap()) is None


# ── RuleSet I/O ─────────────────────────────────────────────


def _valid_ruleset(name: str = "t1") -> dict:
    return {
        "name": name,
        "version": "1.0",
        "description": "test",
        "direction": "up",
        "confidence": 0.7,
        "entry": {
            "logic": "AND",
            "conditions": [{"field": "elapsed_seconds", "op": ">=", "value": 30}],
        },
    }


def test_validate_accepts_valid():
    out = validate_ruleset(_valid_ruleset())
    assert out["name"] == "t1"
    assert out["direction"] == "up"
    assert len(out["entry"]["conditions"]) == 1


def test_validate_rejects_non_dict():
    with pytest.raises(RuleSetError):
        validate_ruleset("string")
    with pytest.raises(RuleSetError):
        validate_ruleset(None)


def test_validate_rejects_bad_name():
    bad_names = ["", "../escape", "with space", "a" * 65, "ç-special"]
    for bad in bad_names:
        rs = _valid_ruleset()
        rs["name"] = bad
        with pytest.raises(RuleSetError):
            validate_ruleset(rs)


def test_validate_rejects_bad_direction():
    rs = _valid_ruleset()
    rs["direction"] = "sideways"
    with pytest.raises(RuleSetError):
        validate_ruleset(rs)


def test_validate_rejects_bad_op():
    rs = _valid_ruleset()
    rs["entry"]["conditions"] = [{"field": "x", "op": "BAD", "value": 0}]
    with pytest.raises(RuleSetError):
        validate_ruleset(rs)


def test_validate_rejects_bad_logic():
    rs = _valid_ruleset()
    rs["entry"]["logic"] = "XOR"
    with pytest.raises(RuleSetError):
        validate_ruleset(rs)


def test_save_load_roundtrip(tmp_path: Path):
    rs = _valid_ruleset("save_test")
    saved = save_ruleset(rs, dir_path=tmp_path)
    assert saved.exists()
    assert saved.name == "save_test.json"
    loaded = load_ruleset(saved)
    assert loaded["name"] == "save_test"
    assert loaded["entry"]["conditions"][0]["field"] == "elapsed_seconds"


def test_save_validates_before_writing(tmp_path: Path):
    """Geçersiz ruleset save edilmemeli."""
    bad = _valid_ruleset()
    bad["direction"] = "invalid"
    with pytest.raises(RuleSetError):
        save_ruleset(bad, dir_path=tmp_path)
    assert list(tmp_path.glob("*.json")) == []  # hiçbir dosya yazılmadı


def test_save_overwrites(tmp_path: Path):
    rs = _valid_ruleset("overwrite_me")
    save_ruleset(rs, dir_path=tmp_path)
    rs["description"] = "updated"
    p = save_ruleset(rs, dir_path=tmp_path)
    loaded = load_ruleset(p)
    assert loaded["description"] == "updated"


def test_list_rulesets_empty_dir(tmp_path: Path):
    assert list_rulesets(tmp_path) == []


def test_list_rulesets_returns_sorted(tmp_path: Path):
    for nm in ["b_test", "a_test", "c_test"]:
        save_ruleset(_valid_ruleset(nm), dir_path=tmp_path)
    out = list_rulesets(tmp_path)
    names = [r["name"] for r in out]
    assert names == ["a_test", "b_test", "c_test"]


def test_list_rulesets_skips_corrupted(tmp_path: Path):
    save_ruleset(_valid_ruleset("good"), dir_path=tmp_path)
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")
    (tmp_path / "invalid.json").write_text(
        json.dumps({"name": "bad", "direction": "sideways", "entry": {"conditions": []}}),
        encoding="utf-8",
    )
    out = list_rulesets(tmp_path)
    assert len(out) == 1
    assert out[0]["name"] == "good"


def test_delete_ruleset_existing(tmp_path: Path):
    save_ruleset(_valid_ruleset("delme"), dir_path=tmp_path)
    assert delete_ruleset("delme", dir_path=tmp_path) is True
    assert not (tmp_path / "delme.json").exists()


def test_delete_ruleset_missing(tmp_path: Path):
    assert delete_ruleset("nope", dir_path=tmp_path) is False


def test_delete_ruleset_invalid_name(tmp_path: Path):
    """../escape gibi adlar reddedilmeli — path traversal koruması."""
    assert delete_ruleset("../escape", dir_path=tmp_path) is False


# ── Adım 3 — backtest istatistikleri ────────────────────────


def test_backtest_stat_roundtrip(tmp_path: Path):
    """record → load: runs sayacı artar, son sonuç saklanır."""
    assert load_backtest_stat("s1", dir_path=tmp_path) is None  # henüz yok
    record_backtest_stat("s1", "BTC 5m", "son 200", -2.5, 42.9, 7, dir_path=tmp_path)
    rec = load_backtest_stat("s1", dir_path=tmp_path)
    assert rec is not None
    assert rec["runs"] == 1
    assert rec["last_market"] == "BTC 5m"
    assert rec["last_scope"] == "son 200"
    assert rec["last_pnl"] == -2.5
    assert rec["last_win_rate"] == 42.9
    assert rec["last_n_trades"] == 7
    assert "last_ts" in rec
    # ikinci run → runs=2, son değer güncellenir
    record_backtest_stat("s1", "BTC 1h", "tümü", 5.0, 70.0, 3, dir_path=tmp_path)
    rec = load_backtest_stat("s1", dir_path=tmp_path)
    assert rec["runs"] == 2
    assert rec["last_market"] == "BTC 1h"
    assert rec["last_pnl"] == 5.0


def test_backtest_stat_invalid_name_noop(tmp_path: Path):
    """Path-traversal isim → kayıt yapılmaz, dosya oluşmaz."""
    record_backtest_stat("../escape", "BTC 5m", "son 50", 1.0, 50.0, 2, dir_path=tmp_path)
    assert load_backtest_stat("../escape", dir_path=tmp_path) is None
    assert not (tmp_path / "_stats.json").exists()


def test_load_all_stats_corrupt_returns_empty(tmp_path: Path):
    (tmp_path / "_stats.json").write_text("{ not json", encoding="utf-8")
    assert load_all_stats(dir_path=tmp_path) == {}


def test_list_rulesets_skips_stats_file(tmp_path: Path):
    """_stats.json ruleset olarak yüklenmemeli (warning bile üretmemeli)."""
    save_ruleset(_valid_ruleset("real_rs"), dir_path=tmp_path)
    record_backtest_stat("real_rs", "BTC 5m", "son 200", 1.0, 60.0, 5, dir_path=tmp_path)
    assert (tmp_path / "_stats.json").exists()
    out = list_rulesets(tmp_path)
    assert [r["name"] for r in out] == ["real_rs"]  # _stats.json atlandı


# ── Faz 5b — RuleSet limit alanları ─────────────────────────


def test_validate_accepts_optional_limit_fields():
    rs = _valid_ruleset()
    rs["entry_limit_price"] = 0.45
    rs["entry_limit_expire_seconds"] = 60
    out = validate_ruleset(rs)
    assert out["entry_limit_price"] == 0.45
    assert out["entry_limit_expire_seconds"] == 60


def test_validate_rejects_limit_price_out_of_range():
    rs = _valid_ruleset()
    rs["entry_limit_price"] = 1.5  # >= 1.0
    with pytest.raises(RuleSetError):
        validate_ruleset(rs)
    rs["entry_limit_price"] = 0.0  # not > 0
    with pytest.raises(RuleSetError):
        validate_ruleset(rs)


def test_validate_rejects_negative_expire():
    rs = _valid_ruleset()
    rs["entry_limit_price"] = 0.50
    rs["entry_limit_expire_seconds"] = -10
    with pytest.raises(RuleSetError):
        validate_ruleset(rs)


def test_validate_rejects_non_numeric_limit():
    rs = _valid_ruleset()
    rs["entry_limit_price"] = "high"
    with pytest.raises(RuleSetError):
        validate_ruleset(rs)


def test_validate_no_limit_fields_default_ok():
    """Limit alanları opsiyonel — yoksa eski davranış."""
    rs = _valid_ruleset()  # limit yok
    out = validate_ruleset(rs)
    assert "entry_limit_price" not in out
    assert "entry_limit_expire_seconds" not in out


def test_rulebased_strategy_propagates_limit_to_signal_metadata():
    """RuleSet'te limit varsa Signal.metadata'ya geçirilmeli."""
    s = RuleBasedStrategy.from_ruleset(
        {
            "name": "limit_test",
            "direction": "up",
            "entry": {
                "conditions": [{"field": "elapsed_seconds", "op": ">=", "value": 0}]
            },
            "entry_limit_price": 0.45,
            "entry_limit_expire_seconds": 60,
        }
    )
    s.on_market_open(_market())
    sig = s.on_snapshot(_snap())
    assert sig is not None
    assert sig.metadata.get("entry_limit_price") == 0.45
    assert sig.metadata.get("entry_limit_expire_seconds") == 60


def test_rulebased_strategy_omits_metadata_when_no_limit():
    """Limit yoksa metadata'da bu alanlar olmamalı."""
    s = RuleBasedStrategy.from_ruleset(
        {
            "name": "no_limit",
            "direction": "up",
            "entry": {
                "conditions": [{"field": "elapsed_seconds", "op": ">=", "value": 0}]
            },
        }
    )
    s.on_market_open(_market())
    sig = s.on_snapshot(_snap())
    assert sig is not None
    assert "entry_limit_price" not in (sig.metadata or {})
    assert "entry_limit_expire_seconds" not in (sig.metadata or {})


def test_rulebased_strategy_ignores_invalid_limit_silently():
    """Invalid limit (out of range, non-numeric) sessizce skip — strateji yine çalışır."""
    s = RuleBasedStrategy.from_ruleset(
        {
            "name": "bad_limit",
            "direction": "up",
            "entry": {
                "conditions": [{"field": "elapsed_seconds", "op": ">=", "value": 0}]
            },
            "entry_limit_price": "not-numeric",
            "entry_limit_expire_seconds": "also-bad",
        }
    )
    s.on_market_open(_market())
    sig = s.on_snapshot(_snap())
    assert sig is not None
    # Sessiz skip
    assert "entry_limit_price" not in (sig.metadata or {})
    assert "entry_limit_expire_seconds" not in (sig.metadata or {})


def test_validate_limit_price_at_boundary():
    """Sınır: 0.0 değil, 1.0 değil — strictly 0 < lp < 1."""
    rs = _valid_ruleset()
    rs["entry_limit_price"] = 0.001  # OK
    out = validate_ruleset(rs)
    assert out["entry_limit_price"] == 0.001
    rs["entry_limit_price"] = 0.999  # OK
    out = validate_ruleset(rs)
    assert out["entry_limit_price"] == 0.999
    rs["entry_limit_price"] = 1.0  # FAIL (not strictly < 1)
    with pytest.raises(RuleSetError):
        validate_ruleset(rs)
