"""
PolyPaper Bot — Adaptive Micro Weight Tracker (Phase 47a)

Online learner that tunes the Phase 46 microstructure boost weight
based on the realized correlation between (signed micro_boost at order
placement) and (signed PnL outcome at settlement).

Algorithm:
  • At order open, engine calls record_open(asset, micro_boost_signed)
    where micro_boost_signed has the SAME sign as the trade direction
    (positive when boost agreed with our trade direction).
  • At settle, engine calls record_close(asset, pnl_usd).
  • Tracker keeps a deque of (boost, pnl_sign) pairs per asset, capped
    at MAX_HISTORY (default 100).
  • Every UPDATE_EVERY new pairs we recompute Pearson-style correlation
    between boost magnitudes and pnl signs and tune the global weight
    multiplier toward [0.5, 1.5] · base_weight using exponential update.

Safety knobs:
  • Tracker is OFF by default — must be enabled via
    ADAPTIVE_MICRO_WEIGHT_ENABLED setting.
  • Multiplier is clamped to [0.50, 1.50] so a misbehaving sample can't
    blow the boost up or zero it out completely.
  • Per-asset and global views — for now we only consume the global
    multiplier; per-asset is exposed for /micro and future Phase 48
    micro-per-asset gating.

Persistence:
  • Best-effort JSON snapshot under data_store/micro_weight_state.json
    so the multiplier survives bot restarts. Loaded lazily; corrupt or
    missing file → start fresh at multiplier 1.0.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from pathlib import Path

logger = logging.getLogger("polypaper.core.micro_weight")

STATE_FILE = Path("data_store") / "micro_weight_state.json"
MAX_HISTORY = 100
UPDATE_EVERY = 10
MIN_SAMPLES = 20  # don't tune until we have at least this many pairs
LR = 0.20  # exponential update rate per recompute
MULT_LOW = 0.50
MULT_HIGH = 1.50

ASSETS = ("BTC", "ETH", "SOL", "XRP")


class MicroWeightTracker:
    """Per-asset rolling history of (boost, pnl_sign) → global multiplier."""

    def __init__(
        self,
        max_history: int = MAX_HISTORY,
        update_every: int = UPDATE_EVERY,
        enabled: bool = False,
        state_file: Path = STATE_FILE,
    ):
        self.max_history = max_history
        self.update_every = update_every
        self.enabled = enabled
        self.state_file = state_file
        self._hist: dict[str, deque] = {a: deque(maxlen=max_history) for a in ASSETS}
        self._open_boosts: dict[str, float] = {}  # order_key → boost
        self._pairs_since_update = 0
        self._global_mult = 1.0
        self._last_update_ts: float = 0.0
        self._load_state()

    # ── persistence ───────────────────────────────────────────────
    def _load_state(self):
        try:
            if not self.state_file.exists():
                return
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            mult = float(data.get("global_mult", 1.0))
            self._global_mult = max(MULT_LOW, min(MULT_HIGH, mult))
            self._last_update_ts = float(data.get("last_update_ts", 0.0))
            logger.info(f"📈 micro_weight: loaded state mult={self._global_mult:.3f}")
        except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError) as e:
            # T1.4 Faz 3: read_text (OSError: perm/missing/locked),
            # json.loads (JSONDecodeError on corrupt state),
            # float() coerce (ValueError/TypeError on bad values),
            # data.get chain (AttributeError if JSON root isn't a dict).
            logger.warning(f"micro_weight: load_state failed: {e}")

    def _save_state(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps(
                    {
                        "global_mult": self._global_mult,
                        "last_update_ts": self._last_update_ts,
                    }
                ),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError) as e:
            # T1.4 Faz 3: mkdir + write_text (OSError: disk/perm),
            # json.dumps (TypeError defensive for future field additions;
            # current payload is float/float64 only).
            logger.debug(f"micro_weight: save_state failed: {e}")

    # ── public API ────────────────────────────────────────────────
    def record_open(self, order_key: str, asset: str, signed_boost: float):
        """Stash the boost magnitude at order placement."""
        if not self.enabled:
            return
        try:
            self._open_boosts[order_key] = float(signed_boost)
        except (ValueError, TypeError):
            # T1.4 Faz 3: float() coerce only. Silent swallow intentional
            # — record_open is best-effort stash.
            pass

    def record_close(self, order_key: str, asset: str, pnl_usd: float):
        """Pair the open boost with the realized PnL sign and update."""
        if not self.enabled:
            return
        boost = self._open_boosts.pop(order_key, None)
        if boost is None:
            return
        a = asset.upper()
        if a not in self._hist:
            return
        try:
            pnl_sign = 1.0 if pnl_usd > 0 else (-1.0 if pnl_usd < 0 else 0.0)
            self._hist[a].append((boost, pnl_sign))
            self._pairs_since_update += 1
            if self._pairs_since_update >= self.update_every:
                self._recompute()
        except (ValueError, TypeError, ArithmeticError, KeyError, AttributeError) as e:
            # T1.4 Faz 3: pnl_usd comparison (TypeError on non-numeric),
            # deque append, then _recompute which calls _pearson_like
            # (ArithmeticError defensive for edge divisions) +
            # _save_state (OSError absorbed in its own catch).
            logger.debug(f"micro_weight: record_close failed: {e}")

    def get_multiplier(self) -> float:
        """Multiplier applied to MICRO_BOOST_WEIGHT in engine.

        Returns 1.0 when disabled or insufficient samples — caller is
        free to use this unconditionally.
        """
        if not self.enabled:
            return 1.0
        return self._global_mult

    def get_status(self) -> dict:
        """Snapshot for /micro and /diag commands."""
        per_asset: dict[str, dict[str, int | float | None]] = {}
        for a, hist in self._hist.items():
            n = len(hist)
            if n == 0:
                per_asset[a] = {"n": 0, "corr": None}
                continue
            per_asset[a] = {"n": n, "corr": _pearson_like(hist)}
        return {
            "enabled": self.enabled,
            "global_mult": self._global_mult,
            "pairs_since_update": self._pairs_since_update,
            "last_update_ts": self._last_update_ts,
            "per_asset": per_asset,
            "open_orders": len(self._open_boosts),
        }

    # ── core logic ────────────────────────────────────────────────
    def _recompute(self):
        """Aggregate all assets, compute correlation, nudge multiplier."""
        all_pairs = []
        for hist in self._hist.values():
            all_pairs.extend(hist)
        if len(all_pairs) < MIN_SAMPLES:
            self._pairs_since_update = 0
            return
        corr = _pearson_like(all_pairs)
        if corr is None:
            self._pairs_since_update = 0
            return
        # corr ∈ [-1, 1] → target multiplier in [MULT_LOW, MULT_HIGH] with
        # linear map. corr=+1 → 1.5, corr=0 → 1.0, corr=-1 → 0.5
        target = 1.0 + 0.5 * corr
        target = max(MULT_LOW, min(MULT_HIGH, target))
        prev = self._global_mult
        self._global_mult = (1 - LR) * prev + LR * target
        self._global_mult = max(MULT_LOW, min(MULT_HIGH, self._global_mult))
        self._pairs_since_update = 0
        self._last_update_ts = time.time()
        logger.info(
            f"📈 micro_weight: corr={corr:+.3f} target={target:.3f} "
            f"mult: {prev:.3f}→{self._global_mult:.3f} "
            f"(samples={len(all_pairs)})"
        )
        self._save_state()


# T7.6 B2: extracted to core.stats_utils — see module for canonical impl.
# Preserving module-local binding so call sites ``_pearson_like(all_pairs)``
# continue to work without touching every caller.
from core.stats_utils import pearson_like as _pearson_like  # noqa: E402
