"""PolyPaper Bot — shared statistics helpers.

Tiny numerical utilities that were duplicated across three modules prior to
T7.6 B2 (2026-04-22). Keep this module dependency-light — no numpy, no
pandas, no engine imports. The downstream callers are hot-ish recalibration
paths (becker weight tracker, micro weight tracker, weekly becker recal)
that run every few minutes, so stdlib-only keeps import overhead minimal.

Adding new helpers?
-------------------
1. Must be pure (no side effects, no I/O).
2. Must use stdlib only (``math``, ``statistics``, built-ins).
3. Must have a docstring that names the domain (what pairs mean) and the
   exact return-None contract.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Optional


def pearson_like(
    pairs: Sequence[tuple[float, float]] | Iterable[tuple[float, float]],
) -> Optional[float]:
    """Pearson correlation for a sequence of ``(x, y)`` pairs.

    Used across the bot to correlate one signal axis (odds delta, signal
    boost magnitude, etc.) against realised trade outcome (PnL sign or PnL
    magnitude).

    Args:
        pairs: Iterable of two-element tuples ``(x, y)``.

    Returns:
        Correlation coefficient in ``[-1, 1]``, or ``None`` if the sample
        size is below 2 or if either axis has essentially zero variance
        (``<= 1e-12``). A ``None`` return is the caller's cue that the
        estimate is unreliable — do NOT coerce to 0.0, because that would
        misrepresent "no data" as "uncorrelated".

    Implementation notes:
        - Accepts generic iterables (we internally materialise to lists so
          we can walk the input twice).
        - Uses ``math.sqrt`` (not ``** 0.5``) — results are mathematically
          identical; this form is slightly faster in CPython and avoids
          a ``float() `` cast for the exponent path.
    """
    xs_list: list[float] = []
    ys_list: list[float] = []
    for x, y in pairs:
        xs_list.append(float(x))
        ys_list.append(float(y))
    n = len(xs_list)
    if n < 2:
        return None
    mx = sum(xs_list) / n
    my = sum(ys_list) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs_list, ys_list, strict=False)) / n
    vx = sum((x - mx) ** 2 for x in xs_list) / n
    vy = sum((y - my) ** 2 for y in ys_list) / n
    if vx <= 1e-12 or vy <= 1e-12:
        return None
    return cov / math.sqrt(vx * vy)
