"""
PolyPaper Bot — Polymarket slug + market TF/asset inference helpers (P0-08-D).

Polymarket'ın resmi docs'una göre TF (timeframe) çıkarımı için **birincil yol**
market dict'inin `tags` ve `series` field'larıdır; slug regex sadece **fallback**.
Slug pattern Polymarket'ın internal naming convention'ı; docs'ta direkt
belgelenmiyor (örn. wti-up-or-down-on-april-7-2026 sadece RTDS sayfasında geçer).

Kanıtlanmış pattern'ler (canlı gamma-api 2026-05-08):
  - 5m / 15m → `{asset}-updown-{tf}-{epoch}` (eski format, slug-prefix discovery)
  - 1h → `{asset}-up-or-down-{month}-{day}-{year}-{hour}{ampm}-et` (series_id=10114 BTC)
  - 24h → `{asset}-up-or-down-on-{month}-{day}-{year}` (series_id=41 BTC)

Tag-based inference (sağlam, docs uyumlu):
  - tags içinde slug='daily' veya 'daily-close' → 24h
  - tags içinde slug='weekly' → 168h
  - tags içinde slug='hourly' → 1h
  - series.slug 'hourly' içeriyorsa → 1h
  - series.slug 'daily' içeriyorsa → 24h

Reference: memory/reference_polymarket_updown_discovery.md
"""

from __future__ import annotations

import re

# ── Asset detection ────────────────────────────────────────────────────
_ASSET_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"^btc[-_]"), "BTC"),
    (re.compile(r"^bitcoin[-_]"), "BTC"),
    (re.compile(r"^eth[-_]"), "ETH"),
    (re.compile(r"^ethereum[-_]"), "ETH"),
    (re.compile(r"^sol[-_]"), "SOL"),
    (re.compile(r"^solana[-_]"), "SOL"),
    (re.compile(r"^xrp[-_]"), "XRP"),
    (re.compile(r"^ripple[-_]"), "XRP"),
)


def infer_asset_from_slug(slug: str | None) -> str:
    """BTC / ETH / SOL / XRP — bilinmiyorsa "?" döner."""
    if not slug:
        return "?"
    s = slug.lower()
    for pat, code in _ASSET_PATTERNS:
        if pat.match(s):
            return code
    return "?"


# ── TF detection (slug-only fallback) ──────────────────────────────────
# 5m/15m: epoch suffix (btc-updown-5m-1778268000)
_RE_LEGACY_TF = re.compile(r"^(?:btc|eth|sol|xrp)-updown-(5m|15m)-\d+")

# 1h hourly: bitcoin-up-or-down-may-8-2026-12pm-et
_RE_HOURLY = re.compile(
    r"^(?:btc|bitcoin|eth|ethereum|sol|solana|xrp|ripple)"
    r"-up-or-down-[a-z]+-\d+-\d{4}-\d+(?:am|pm)-et"
)

# 24h daily: bitcoin-up-or-down-on-may-9-2026  veya
#            bitcoin-up-or-down-on-march-17 (year suffix opsiyonel — eski format)
_RE_DAILY = re.compile(
    r"^(?:btc|bitcoin|eth|ethereum|sol|solana|xrp|ripple)" r"-up-or-down-on-[a-z]+-\d+(?:-\d{4})?$"
)


def infer_tf_from_slug(slug: str | None) -> str:
    """Slug elindeyse fallback parsing. Bilinmiyorsa "?" döner.

    Birincil yol `infer_tf_from_market(market_dict)` — market dict mevcutsa
    tags/series'ten okumak slug regex'inden daha sağlamdır.
    """
    if not slug:
        return "?"
    s = slug.lower()

    # 5m / 15m — eski epoch-suffix slug
    m = _RE_LEGACY_TF.match(s)
    if m:
        return m.group(1)

    # 24h — daily on-date pattern (önce kontrol; daha spesifik)
    if _RE_DAILY.match(s):  # type: ignore[unreachable]
        return "24h"  # type: ignore[unreachable]

    # 1h — hourly with am/pm-et suffix
    if _RE_HOURLY.match(s):
        return "1h"

    return "?"


# ── TF detection (market dict — birincil yol) ──────────────────────────
def _tags_to_set(tags) -> set[str]:
    """Market.tags veya Event.tags listesini slug set'ine çevir."""
    out: set[str] = set()
    if not tags:
        return out
    if isinstance(tags, dict):
        s = tags.get("slug")
        if s:
            out.add(s.lower())
        return out
    try:
        for t in tags:
            if isinstance(t, dict):
                s = t.get("slug")
                if s:
                    out.add(s.lower())
            elif isinstance(t, str):
                out.add(t.lower())
    except TypeError:
        pass
    return out


def _series_slugs(series) -> set[str]:
    """Market.series veya Event.series listesini slug set'ine çevir."""
    out: set[str] = set()
    if not series:
        return out
    if isinstance(series, dict):
        s = series.get("slug")
        if s:
            out.add(s.lower())
        return out
    try:
        for s in series:
            if isinstance(s, dict):
                slug = s.get("slug")
                if slug:
                    out.add(slug.lower())
    except TypeError:
        pass
    return out


def infer_tf_from_market(market: dict | None) -> str:
    """Birincil TF inference — market dict'in tags + series'ten okur,
    sonra slug regex'e fallback yapar.

    Polymarket docs convention:
      - tag.slug 'daily-close' veya 'daily' → 24h
      - tag.slug 'weekly' → 168h
      - tag.slug 'hourly' → 1h
      - series.slug '...-hourly' içeriyorsa → 1h
      - series.slug '...-daily' içeriyorsa → 24h
      - 5m/15m için tag yok → slug fallback
    """
    if not market or not isinstance(market, dict):
        return "?"

    tags = _tags_to_set(market.get("tags"))
    if "daily-close" in tags or "daily" in tags:
        return "24h"
    if "hourly" in tags:
        return "1h"
    if "weekly" in tags:
        return "168h"

    series = _series_slugs(market.get("series"))
    for s in series:
        if "hourly" in s:
            return "1h"
        if "daily" in s:
            return "24h"
        if "weekly" in s:
            return "168h"

    # Fallback: slug regex (tags yoksa, eski 5m/15m mostly)
    return infer_tf_from_slug(market.get("slug"))
