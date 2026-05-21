"""PolyPaper Bot - Backtest LAB Faz 3 — RuleBasedStrategy (2026-05-20).

Heddas direktifi: "no-code strateji kurma menüsü" — kullanıcı kural cümleleri
ile strateji oluşturabilsin (Python kodu yazmadan). Bu modül JSON kural
setlerini alıp Signal üreten generic stratejiyi sağlar.

Faz 2'deki ReplayConfig knob'ları (entry_second_min/max, hour_filter, ...)
LAB-GENELI config — TÜM stratejilere uygulanır. Bu modüldeki RuleBasedStrategy
STRATEJI-DUZEYINDE kural — her strateji kendi koşullarına sahip.

İkisi birbirini tamamlar:
  - Faz 2: "Tüm backtest sadece 22:00-23:00 marketlerinde" (config)
  - Faz 3: "Sinyal sadece up_best_ask ≥ 0.55 + binance_pos_delta > 0 iken" (rule)

RuleSet JSON şeması:
    {
      "name": "my_strategy",           # benzersiz, dosya adı için
      "version": "1.0",
      "description": "açıklama metni",
      "direction": "up",               # "up" | "down"
      "confidence": 0.7,               # 0..1
      "entry": {
        "logic": "AND",                # "AND" | "OR"
        "conditions": [
          {"field": "elapsed_seconds", "op": ">=", "value": 30},
          {"field": "elapsed_seconds", "op": "<=", "value": 50},
          {"field": "up_best_ask",     "op": ">=", "value": 0.55}
        ]
      }
    }

Available fields (OrderbookSnapshot + MarketData):
  Snapshot — elapsed_seconds, elapsed_pct, remaining_seconds,
             up_best_bid, up_best_ask, down_best_bid, down_best_ask,
             spread, up_bid_depth, up_ask_depth, down_bid_depth,
             down_ask_depth, binance_price, binance_price_change,
             taker_buy_volume, taker_sell_volume
  Market   — coin, market_type, hour_utc, volume, liquidity

Operators: ==, !=, <, <=, >, >=, in, not_in
"""

from __future__ import annotations

import json
import logging
import operator
import re
import time
from pathlib import Path
from typing import Any, Optional

from backtest.strategies.base import (
    BaseBacktestStrategy,
    Direction,
    MarketData,
    OrderbookSnapshot,
    Signal,
    StrategyRegistryV2,
)

logger = logging.getLogger("polypaper.backtest.rule_based")


# ── Operators ───────────────────────────────────────────────


def _op_in(a, b) -> bool:
    try:
        return a in b
    except TypeError:
        return False


def _op_not_in(a, b) -> bool:
    try:
        return a not in b
    except TypeError:
        return False


_OPS: dict[str, Any] = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "in": _op_in,
    "not_in": _op_not_in,
}


# ── Field resolution ────────────────────────────────────────


def _get_field(name: str, snap: OrderbookSnapshot, market: Optional[MarketData]) -> Any:
    """Field lookup — önce snapshot, sonra market, yoksa None.

    `None` döndüğünde caller koşulu False sayar (defansif — yanlış field adı
    sessiz bir "her zaman ateşle" davranışına yol açmamalı).
    """
    if hasattr(snap, name):
        return getattr(snap, name)
    if market is not None and hasattr(market, name):
        return getattr(market, name)
    return None


# ── Condition evaluation ───────────────────────────────────


def _eval_condition(
    cond: dict, snap: OrderbookSnapshot, market: Optional[MarketData]
) -> bool:
    """Tek bir koşulu değerlendir. Hata durumlarında defansif False."""
    if not isinstance(cond, dict):
        return False
    field = cond.get("field", "")
    op_name = cond.get("op", "==")
    target = cond.get("value")

    if not field or op_name not in _OPS:
        return False

    actual = _get_field(field, snap, market)
    if actual is None:
        return False

    op_fn = _OPS[op_name]
    try:
        return bool(op_fn(actual, target))
    except (TypeError, ValueError):
        # Tip uyuşmazlığı (örn float ile string) → sessiz False
        return False


def _eval_entry_block(
    entry: dict, snap: OrderbookSnapshot, market: Optional[MarketData]
) -> bool:
    """Entry bloğu — `logic` (AND/OR) + `conditions` list."""
    if not isinstance(entry, dict):
        return False
    conditions = entry.get("conditions")
    if not conditions or not isinstance(conditions, list):
        return False  # Boş ruleset HİÇBİR ZAMAN ateşlemez (güvenli)

    logic = str(entry.get("logic", "AND")).upper()
    if logic == "OR":
        return any(_eval_condition(c, snap, market) for c in conditions)
    # Default AND (bilinmeyen değerler de AND'e düşer — predictable)
    return all(_eval_condition(c, snap, market) for c in conditions)


# ── RuleBasedStrategy ───────────────────────────────────────


@StrategyRegistryV2.register
class RuleBasedStrategy(BaseBacktestStrategy):
    """JSON kural cümlesinden Signal üreten generic strateji.

    Backtest'te kullanım:
        ruleset = load_ruleset(Path("data_store/bt_strategies/my.json"))
        cfg = ReplayConfig(strategy_name="rule_based", strategy_params=ruleset)

    `_run_market` sinyal yakalayınca Faz 2 filtreleri (entry_second,
    entry_yes_price, vs.) hâlâ devrede — birbirini tamamlar.

    Faz 4'te Telegram Kurucu UI bu sınıfı `from_ruleset()` ile sarar
    ve user-friendly inline button akışıyla kural ekler/siler.
    """

    name = "rule_based"
    version = "1.0"
    description = "JSON kural cümlesi ile sinyal üreten generic strateji"

    # Default ruleset — hiçbir koşul → hiç ateşlemez (güvenli)
    DEFAULT_PARAMS = {
        "name": "rule_based",
        "version": "1.0",
        "description": "",
        "direction": "up",
        "confidence": 0.7,
        "entry": {"logic": "AND", "conditions": []},
    }

    def __init__(self):
        self.params = dict(self.DEFAULT_PARAMS)
        self.params["entry"] = dict(self.DEFAULT_PARAMS["entry"])
        self._market: Optional[MarketData] = None
        self._snapshots_seen = 0
        self._signal_emitted = False

    @classmethod
    def from_ruleset(cls, ruleset: dict) -> RuleBasedStrategy:
        """Bir RuleSet dict'inden instance üret — params'a deep-merge."""
        s = cls()
        if not isinstance(ruleset, dict):
            return s
        # Top-level alanları override et
        for k, v in ruleset.items():
            if k == "entry" and isinstance(v, dict):
                s.params["entry"] = dict(v)
            else:
                s.params[k] = v
        return s

    def on_market_open(self, market: MarketData) -> None:
        self._market = market
        self._snapshots_seen = 0
        self._signal_emitted = False

    def on_snapshot(self, snapshot: OrderbookSnapshot) -> Optional[Signal]:
        self._snapshots_seen += 1
        if self._signal_emitted:
            return None

        entry = self.params.get("entry") or {}
        if not _eval_entry_block(entry, snapshot, self._market):
            return None

        self._signal_emitted = True
        direction = str(self.params.get("direction", "up")).lower()
        d = Direction.UP if direction == "up" else Direction.DOWN

        # entry_price referansı — buy edilecek tarafın ASK fiyatı
        if d == Direction.UP:
            entry_price = snapshot.up_best_ask or 0.5
        else:
            entry_price = snapshot.down_best_ask or 0.5

        try:
            confidence = float(self.params.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        confidence = max(0.0, min(1.0, confidence))

        # Faz 5b (2026-05-20): RuleSet'te limit alanları varsa Signal.metadata'ya
        # geçir — `_run_market` bunu okuyup ReplayConfig default'undan ÖNCE
        # uygular (per-strateji limit, multi-strategy compare için kritik).
        meta = {
            "ruleset_name": self.params.get("name", "unnamed"),
            "ruleset_version": self.params.get("version", ""),
        }
        if "entry_limit_price" in self.params:
            try:
                lp = float(self.params["entry_limit_price"])
                if 0.0 < lp < 1.0:
                    meta["entry_limit_price"] = lp
            except (TypeError, ValueError):
                pass
        if "entry_limit_expire_seconds" in self.params:
            try:
                es = int(self.params["entry_limit_expire_seconds"])
                if es > 0:
                    meta["entry_limit_expire_seconds"] = es
            except (TypeError, ValueError):
                pass

        return Signal(
            direction=d,
            confidence=confidence,
            entry_price=entry_price,
            reason=f"rule:{self.params.get('name', 'unnamed')}",
            metadata=meta,
        )


# ── RuleSet I/O — JSON load/save/list ───────────────────────

# Faz 4 UI bunlardan beslenecek — şimdiden hazır.

_DEFAULT_DIR = Path("data_store/bt_strategies")
_NAME_RX = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


class RuleSetError(ValueError):
    """RuleSet doğrulama/IO hatası."""


def validate_ruleset(ruleset: Any) -> dict:
    """Tipini ve zorunlu alanları doğrula. Hatalı ise RuleSetError fırlatır.

    Doğrulanmış dict'i döndürür (normalize edilmiş kopyası — caller'ın
    orijinal dict'i mutate edilmez).
    """
    if not isinstance(ruleset, dict):
        raise RuleSetError("ruleset bir JSON object olmalı")

    name = ruleset.get("name")
    if not isinstance(name, str) or not _NAME_RX.match(name):
        raise RuleSetError(
            "ruleset.name 1-64 karakter [A-Za-z0-9_-] olmalı (dosya-güvenli)"
        )

    direction = str(ruleset.get("direction", "up")).lower()
    if direction not in ("up", "down"):
        raise RuleSetError("ruleset.direction 'up' veya 'down' olmalı")

    entry = ruleset.get("entry") or {}
    if not isinstance(entry, dict):
        raise RuleSetError("ruleset.entry dict olmalı")
    conditions = entry.get("conditions") or []
    if not isinstance(conditions, list):
        raise RuleSetError("ruleset.entry.conditions list olmalı")
    for i, cond in enumerate(conditions):
        if not isinstance(cond, dict):
            raise RuleSetError(f"entry.conditions[{i}] dict olmalı")
        if "field" not in cond or not isinstance(cond["field"], str):
            raise RuleSetError(f"entry.conditions[{i}].field zorunlu (string)")
        op_name = cond.get("op", "==")
        if op_name not in _OPS:
            raise RuleSetError(
                f"entry.conditions[{i}].op bilinmeyen: '{op_name}' "
                f"(geçerli: {sorted(_OPS.keys())})"
            )

    logic = str(entry.get("logic", "AND")).upper()
    if logic not in ("AND", "OR"):
        raise RuleSetError("ruleset.entry.logic 'AND' veya 'OR' olmalı")

    # Faz 5b: opsiyonel limit alanları — validate ama "yok" hâli serbest
    out: dict = {
        "name": name,
        "version": str(ruleset.get("version", "1.0")),
        "description": str(ruleset.get("description", "")),
        "direction": direction,
        "confidence": float(ruleset.get("confidence", 0.7)),
        "entry": {
            "logic": logic,
            "conditions": [dict(c) for c in conditions],
        },
    }
    if "entry_limit_price" in ruleset:
        try:
            lp = float(ruleset["entry_limit_price"])
        except (TypeError, ValueError) as e:
            raise RuleSetError(
                f"ruleset.entry_limit_price sayısal olmalı (geldi: "
                f"{ruleset['entry_limit_price']!r})"
            ) from e
        if not (0.0 < lp < 1.0):
            raise RuleSetError(
                f"ruleset.entry_limit_price 0..1 aralığında olmalı (geldi: {lp})"
            )
        out["entry_limit_price"] = lp
    if "entry_limit_expire_seconds" in ruleset:
        try:
            es = int(ruleset["entry_limit_expire_seconds"])
        except (TypeError, ValueError) as e:
            raise RuleSetError(
                f"ruleset.entry_limit_expire_seconds tamsayı olmalı (geldi: "
                f"{ruleset['entry_limit_expire_seconds']!r})"
            ) from e
        if es < 0:
            raise RuleSetError(
                f"ruleset.entry_limit_expire_seconds negatif olamaz (geldi: {es})"
            )
        out["entry_limit_expire_seconds"] = es
    return out


def load_ruleset(path: Path) -> dict:
    """JSON dosyasından ruleset oku + doğrula."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_ruleset(raw)


def save_ruleset(ruleset: dict, dir_path: Optional[Path] = None) -> Path:
    """RuleSet'i `{dir}/{name}.json` olarak yaz. Mevcut dosya OVERWRITE."""
    validated = validate_ruleset(ruleset)
    d = Path(dir_path) if dir_path else _DEFAULT_DIR
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{validated['name']}.json"
    target.write_text(
        json.dumps(validated, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return target


def list_rulesets(dir_path: Optional[Path] = None) -> list[dict]:
    """Bir dizindeki tüm ruleset'leri yükle. Bozuk dosyaları sessizce atla."""
    d = Path(dir_path) if dir_path else _DEFAULT_DIR
    if not d.exists():
        return []
    results: list[dict] = []
    for fp in sorted(d.glob("*.json")):
        if fp.name.startswith("_"):
            continue  # _stats.json gibi meta dosyaları ruleset sanma
        try:
            results.append(load_ruleset(fp))
        except (json.JSONDecodeError, RuleSetError, OSError) as e:
            logger.warning("list_rulesets: %s atlandı — %s", fp.name, e)
            continue
    return results


def delete_ruleset(name: str, dir_path: Optional[Path] = None) -> bool:
    """Adıyla ruleset dosyasını sil. Yoksa False, silindiyse True."""
    if not _NAME_RX.match(name):
        return False
    d = Path(dir_path) if dir_path else _DEFAULT_DIR
    target = d / f"{name}.json"
    if not target.exists():
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False


# ─── Backtest istatistikleri (Adım 3: "kaç backtest, son PnL") ───
# data_store/bt_strategies/_stats.json — {name: {runs, last_*}} sözlüğü.
# Ayrı meta dosya (ruleset şemasını kirletmez; list_rulesets `_` prefix'i atlar).

_STATS_NAME = "_stats.json"


def _stats_path(dir_path: Optional[Path] = None) -> Path:
    d = Path(dir_path) if dir_path else _DEFAULT_DIR
    return d / _STATS_NAME


def load_all_stats(dir_path: Optional[Path] = None) -> dict:
    """Tüm strateji backtest istatistiklerini oku (name → stat dict).

    Bozuk/eksik dosya → boş dict (asla patlamaz).
    """
    p = _stats_path(dir_path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("load_all_stats bozuk dosya: %s", e)
        return {}


def load_backtest_stat(name: str, dir_path: Optional[Path] = None) -> Optional[dict]:
    """Tek strateji için son backtest istatistiği (yoksa None)."""
    if not _NAME_RX.match(name or ""):
        return None
    rec = load_all_stats(dir_path).get(name)
    return rec if isinstance(rec, dict) else None


def record_backtest_stat(
    name: str,
    market: str,
    scope: str,
    pnl: float,
    win_rate: float,
    n_trades: int,
    dir_path: Optional[Path] = None,
) -> None:
    """Bir backtest run'ını kaydet — runs sayacı + son sonuç özeti.

    Path-güvenli (name regex-validate). Yazma/okuma hatası sessizce yutulur
    (backtest sonucu gösterimini asla bloklamaz).
    """
    if not _NAME_RX.match(name or ""):
        return
    stats = load_all_stats(dir_path)
    prev = stats.get(name)
    runs = (int(prev.get("runs", 0)) + 1) if isinstance(prev, dict) else 1
    stats[name] = {
        "runs": runs,
        "last_market": str(market)[:32],
        "last_scope": str(scope)[:32],
        "last_pnl": round(float(pnl), 2),
        "last_win_rate": round(float(win_rate), 1),
        "last_n_trades": int(n_trades),
        "last_ts": int(time.time()),
    }
    p = _stats_path(dir_path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as e:
        logger.warning("record_backtest_stat yazılamadı: %s", e)
