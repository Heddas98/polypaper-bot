"""
PolyPaper Bot — Adaptive Becker Weight Tracker (Phase 48)

Per-asset adaptive multiplier on the Becker δ(p) ensemble weights
(BECKER_CALIB_WEIGHT, BECKER_KALSHI_WEIGHT). Mirrors Phase 47a's
MicroWeightTracker but the input signal is the Becker boost / decision
delta, not the microstructure tilt.

Why per-asset?
  Phase 47e showed kalshi's calibration curve is asset-asymmetric
  (favorite-longshot bias kicks in differently for BTC vs SOL/XRP).
  A single global k_w cannot win for all four assets.

Algorithm (mirrors Phase 47a, see core/micro_weight_tracker.py):
  • record_open(order_key, asset, signed_delta)  # δ at order placement,
        same sign as the trade direction (positive = δ AGREED with us).
  • record_close(order_key, asset, pnl_usd)      # at settlement
  • Per-asset deque of (signed_delta, pnl_sign) pairs, max 100.
  • Every UPDATE_EVERY (10) new pairs we compute Pearson-style corr
    PER ASSET (not global), and tune the per-asset multiplier toward
    [MULT_LOW, MULT_HIGH] = [0.50, 1.50] with exp LR = 0.20.
  • Engine looks up `get_multiplier(asset)` when computing the Becker
    boost; multiplier 1.0 = neutral.

Persistence: data_store/becker_weight_state.json
  { "BTC": 1.05, "ETH": 0.92, "SOL": 1.20, "XRP": 1.0,
    "last_update_ts": 1712...}

Safety knobs:
  • Default DISABLED — must be enabled via env var
    ADAPTIVE_BECKER_WEIGHT_ENABLED=true.
  • Per-asset multiplier clamped to [0.50, 1.50] so a misbehaving sample
    can't blow the boost up or zero it.
  • MIN_SAMPLES per asset before tuning kicks in (default 20).
  • All-stdlib (no numpy) so it works on Replit free tier.
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from pathlib import Path
from typing import Optional

logger = logging.getLogger("polypaper.core.becker_weight")

STATE_FILE = Path("data_store") / "becker_weight_state.json"
MAX_HISTORY = 100
UPDATE_EVERY = 10
MIN_SAMPLES = 20
LR = 0.20
MULT_LOW = 0.50
MULT_HIGH = 1.50

ASSETS = ("BTC", "ETH", "SOL", "XRP")


class BeckerWeightTracker:
    """Per-asset adaptive multiplier for the Becker δ(p) boost."""

    def __init__(self, max_history: int = MAX_HISTORY,
                 update_every: int = UPDATE_EVERY,
                 enabled: bool = False,
                 state_file: Path = STATE_FILE):
        self.max_history = max_history
        self.update_every = update_every
        self.enabled = enabled
        self.state_file = state_file
        self._hist: dict[str, deque] = {a: deque(maxlen=max_history) for a in ASSETS}
        self._open_deltas: dict[str, tuple[str, float]] = {}  # order_key → (asset, delta)
        self._mults: dict[str, float] = {a: 1.0 for a in ASSETS}
        self._pairs_since_update: dict[str, int] = {a: 0 for a in ASSETS}
        self._last_update_ts: float = 0.0
        self._load_state()

    # ── persistence ──────────────────────────────────────────────────
    def _load_state(self):
        try:
            if not self.state_file.exists():
                return
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            for a in ASSETS:
                v = data.get(a)
                if v is not None:
                    self._mults[a] = max(MULT_LOW, min(MULT_HIGH, float(v)))
            self._last_update_ts = float(data.get("last_update_ts", 0.0))
            logger.info(
                f"📈 becker_weight: loaded state {self._mults}"
            )
        except Exception as e:
            logger.warning(f"becker_weight: load_state failed: {e}")

    def _save_state(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(self._mults)
            payload["last_update_ts"] = self._last_update_ts
            self.state_file.write_text(json.dumps(payload), encoding="utf-8")
        except Exception as e:
            logger.debug(f"becker_weight: save_state failed: {e}")

    # ── public API ───────────────────────────────────────────────────
    def record_open(self, order_key: str, asset: str, signed_delta: float):
        """Stash the signed Becker δ at order placement."""
        if not self.enabled:
            return
        try:
            self._open_deltas[order_key] = (asset.upper(), float(signed_delta))
        except Exception:
            pass

    def record_close(self, order_key: str, pnl_usd: float):
        """Pair the open delta with the realized PnL sign and update."""
        if not self.enabled:
            return
        rec = self._open_deltas.pop(order_key, None)
        if rec is None:
            return
        asset, delta = rec
        if asset not in self._hist:
            return
        try:
            pnl_sign = 1.0 if pnl_usd > 0 else (-1.0 if pnl_usd < 0 else 0.0)
            self._hist[asset].append((delta, pnl_sign))
            self._pairs_since_update[asset] += 1
            if self._pairs_since_update[asset] >= self.update_every:
                self._recompute(asset)
        except Exception as e:
            logger.debug(f"becker_weight: record_close failed: {e}")

    def get_multiplier(self, asset: str) -> float:
        """Per-asset multiplier on Becker boost weight. Returns 1.0 when off."""
        if not self.enabled:
            return 1.0
        return self._mults.get(asset.upper(), 1.0)

    def get_status(self) -> dict:
        per_asset = {}
        for a, hist in self._hist.items():
            n = len(hist)
            per_asset[a] = {
                "n": n,
                "corr": _pearson_like(hist) if n > 1 else None,
                "mult": self._mults[a],
                "pairs_since_update": self._pairs_since_update[a],
            }
        return {
            "enabled": self.enabled,
            "per_asset": per_asset,
            "last_update_ts": self._last_update_ts,
            "open_orders": len(self._open_deltas),
        }

    # ── core logic ───────────────────────────────────────────────────
    def _recompute(self, asset: str):
        hist = self._hist[asset]
        if len(hist) < MIN_SAMPLES:
            self._pairs_since_update[asset] = 0
            return
        corr = _pearson_like(hist)
        if corr is None:
            self._pairs_since_update[asset] = 0
            return
        target = 1.0 + 0.5 * corr  # corr=+1 → 1.5, corr=0 → 1.0, corr=-1 → 0.5
        target = max(MULT_LOW, min(MULT_HIGH, target))
        prev = self._mults[asset]
        self._mults[asset] = (1 - LR) * prev + LR * target
        self._mults[asset] = max(MULT_LOW, min(MULT_HIGH, self._mults[asset]))
        self._pairs_since_update[asset] = 0
        self._last_update_ts = time.time()
        logger.info(
            f"📈 becker_weight[{asset}]: corr={corr:+.3f} target={target:.3f} "
            f"mult: {prev:.3f}→{self._mults[asset]:.3f} (n={len(hist)})"
        )
        self._save_state()


def _pearson_like(pairs) -> Optional[float]:
    """Pearson correlation between signed deltas and pnl signs.
    Returns None if either series has zero variance."""
    if len(pairs) < 2:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    vx = sum((x - mx) ** 2 for x in xs) / n
    vy = sum((y - my) ** 2 for y in ys) / n
    if vx <= 1e-12 or vy <= 1e-12:
        return None
    return cov / math.sqrt(vx * vy)
