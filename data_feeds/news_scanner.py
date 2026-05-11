"""
News RSS Scanner — Phase 76
Monitors crypto RSS feeds for market-moving news.
Generates quick sentiment signals without X/Twitter dependency.

Pipeline:
1. Poll RSS feeds every POLL_INTERVAL seconds
2. Parse entries, extract titles + descriptions
3. Score each entry with keyword-based sentiment
4. Aggregate scores per asset (BTC, ETH, SOL, XRP)
5. Emit signal boost for engine fusion

ENV:
  NEWS_SCANNER_ENABLED=true
  NEWS_POLL_INTERVAL=60            # seconds between polls
  NEWS_SIGNAL_WEIGHT=0.08          # weight in signal fusion
  NEWS_MIN_SCORE=0.3               # min absolute score to emit signal
  NEWS_LOOKBACK_MINUTES=30         # how far back to consider
  NEWS_MAX_ENTRIES=50              # max entries to process per poll
"""

import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── ENV ──────────────────────────────────────────────────
NEWS_SCANNER_ENABLED = os.getenv("NEWS_SCANNER_ENABLED", "true").lower() == "true"
NEWS_POLL_INTERVAL = int(os.getenv("NEWS_POLL_INTERVAL", "60"))
NEWS_SIGNAL_WEIGHT = float(os.getenv("NEWS_SIGNAL_WEIGHT", "0.08"))
NEWS_MIN_SCORE = float(os.getenv("NEWS_MIN_SCORE", "0.3"))
NEWS_LOOKBACK_MINUTES = int(os.getenv("NEWS_LOOKBACK_MINUTES", "30"))
NEWS_MAX_ENTRIES = int(os.getenv("NEWS_MAX_ENTRIES", "50"))

# ── RSS Feeds (free, no auth required) ──
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
    "https://cryptonews.com/news/feed/",
    "https://decrypt.co/feed",
    "https://www.theblock.co/rss.xml",
]

# ── Keyword sentiment dictionaries ──
BULLISH_KEYWORDS = {
    # Strong bullish
    "surge": 0.8,
    "soar": 0.8,
    "rally": 0.7,
    "breakout": 0.7,
    "all-time high": 0.9,
    "ath": 0.9,
    "moon": 0.6,
    "pump": 0.5,
    "bull": 0.6,
    "bullish": 0.7,
    "adoption": 0.5,
    "etf approved": 0.9,
    "etf approval": 0.9,
    "institutional": 0.5,
    "accumulation": 0.5,
    "buy": 0.4,
    "upgrade": 0.5,
    "partnership": 0.4,
    "halving": 0.5,
    "supply shock": 0.6,
    "inflow": 0.5,
    "record high": 0.8,
    "support": 0.3,
    "recovery": 0.4,
    # "breakout": 0.6 — duplicate (already declared above at 0.7); ruff F601 fix.
    "green": 0.3,
    "profit": 0.4,
}

BEARISH_KEYWORDS = {
    # Strong bearish
    "crash": -0.8,
    "plunge": -0.8,
    "dump": -0.7,
    "collapse": -0.9,
    "bear": -0.6,
    "bearish": -0.7,
    "sell-off": -0.7,
    "selloff": -0.7,
    "hack": -0.8,
    "exploit": -0.7,
    "rug pull": -0.9,
    "rugpull": -0.9,
    "sec lawsuit": -0.7,
    "regulation": -0.4,
    "ban": -0.8,
    "shutdown": -0.7,
    "fraud": -0.8,
    "ponzi": -0.9,
    "outflow": -0.5,
    "resistance": -0.3,
    "decline": -0.5,
    "loss": -0.4,
    "liquidation": -0.6,
    "fud": -0.4,
    "bankrupt": -0.9,
    "insolvent": -0.9,
    "default": -0.7,
}

# ── Asset keyword mapping ──
ASSET_KEYWORDS = {
    "BTC": ["bitcoin", "btc", "satoshi", "lightning network"],
    "ETH": ["ethereum", "eth", "vitalik", "layer 2", "l2", "eip"],
    "SOL": ["solana", "sol"],
    "XRP": ["ripple", "xrp", "sec vs ripple"],
    "CRYPTO": ["crypto", "cryptocurrency", "defi", "web3", "blockchain"],
}


@dataclass
class NewsEntry:
    """Single parsed RSS entry."""

    title: str = ""
    description: str = ""
    link: str = ""
    published: float = 0.0  # Unix timestamp
    source: str = ""
    sentiment_score: float = 0.0  # -1.0 to 1.0
    assets: list[str] = field(default_factory=list)


@dataclass
class NewsSentiment:
    """Aggregated sentiment for an asset."""

    asset: str = ""
    score: float = 0.0  # -1.0 to 1.0
    direction: Optional[str] = None  # "up", "down", None
    entry_count: int = 0
    top_headline: str = ""
    confidence: float = 0.0


@dataclass
class NewsSnapshot:
    """Full news state."""

    sentiments: dict[str, NewsSentiment] = field(default_factory=dict)
    total_entries: int = 0
    last_poll: float = 0.0
    feeds_ok: int = 0
    feeds_failed: int = 0


class NewsScanner:
    """
    RSS-based crypto news scanner.
    Polls free RSS feeds, scores headlines with keyword sentiment,
    and emits per-asset trading signals.
    """

    def __init__(self):
        self._entries: list[NewsEntry] = []
        self._sentiments: dict[str, NewsSentiment] = {}
        self._last_poll: float = 0.0
        self._feeds_ok: int = 0
        self._feeds_failed: int = 0
        self._http_session = None
        logger.info(
            f"[NEWS] init feeds={len(RSS_FEEDS)} poll={NEWS_POLL_INTERVAL}s "
            f"weight={NEWS_SIGNAL_WEIGHT}"
        )

    async def _get_session(self):
        """Lazy init aiohttp session."""
        if self._http_session is None:
            try:
                import aiohttp

                self._http_session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"User-Agent": "PolyPaperBot/1.0 RSS Reader"},
                )
            except ImportError:
                logger.error("[NEWS] aiohttp not installed")
                return None
        return self._http_session

    async def poll(self):
        """Poll all RSS feeds and update sentiment."""
        if not NEWS_SCANNER_ENABLED:
            return

        now = time.time()
        if now - self._last_poll < NEWS_POLL_INTERVAL:
            return

        self._last_poll = now
        session = await self._get_session()
        if not session:
            return

        new_entries = []
        self._feeds_ok = 0
        self._feeds_failed = 0

        for feed_url in RSS_FEEDS:
            try:
                async with session.get(feed_url) as resp:
                    if resp.status != 200:
                        self._feeds_failed += 1
                        continue

                    text = await resp.text()
                    entries = self._parse_rss(text, feed_url)
                    new_entries.extend(entries)
                    self._feeds_ok += 1

            except TimeoutError:
                self._feeds_failed += 1
                logger.debug(f"[NEWS] timeout: {feed_url}")
            except Exception as e:
                self._feeds_failed += 1
                logger.debug(f"[NEWS] feed error {feed_url}: {e}")

        # Filter by lookback window
        cutoff = now - (NEWS_LOOKBACK_MINUTES * 60)
        # Keep entries even without timestamp (assume recent)
        self._entries = [e for e in new_entries if e.published >= cutoff or e.published == 0]
        self._entries = self._entries[:NEWS_MAX_ENTRIES]

        # Score and aggregate
        for entry in self._entries:
            entry.sentiment_score = self._score_text(entry.title + " " + entry.description)
            entry.assets = self._detect_assets(entry.title + " " + entry.description)

        self._aggregate_sentiments()

        logger.info(
            f"[NEWS] polled {self._feeds_ok}/{len(RSS_FEEDS)} feeds, "
            f"{len(self._entries)} entries, "
            f"assets: {list(self._sentiments.keys())}"
        )

    def _parse_rss(self, xml_text: str, source_url: str) -> list[NewsEntry]:
        """Parse RSS XML into NewsEntry list."""
        entries = []
        try:
            root = ET.fromstring(xml_text)

            # Standard RSS 2.0
            items = root.findall(".//item")
            # Atom format
            if not items:
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                items = root.findall(".//atom:entry", ns)

            for item in items[:20]:  # Max 20 per feed
                title = ""
                desc = ""
                link = ""
                pub_date = 0.0

                # Try different tag names
                for tag in ["title"]:
                    el = item.find(tag)
                    if el is not None and el.text:
                        title = el.text.strip()

                for tag in ["description", "summary", "content"]:
                    el = item.find(tag)
                    if el is not None and el.text:
                        desc = el.text.strip()[:500]
                        # Strip HTML tags
                        desc = re.sub(r"<[^>]+>", "", desc)
                        break

                for tag in ["link", "guid"]:
                    el = item.find(tag)
                    if el is not None and el.text:
                        link = el.text.strip()
                        break

                # Parse date (best effort)
                for tag in ["pubDate", "published", "updated"]:
                    el = item.find(tag)
                    if el is not None and el.text:
                        try:
                            from email.utils import parsedate_to_datetime

                            dt = parsedate_to_datetime(el.text)
                            pub_date = dt.timestamp()
                        except Exception:
                            pub_date = 0.0
                        break

                if title:
                    entries.append(
                        NewsEntry(
                            title=title,
                            description=desc,
                            link=link,
                            published=pub_date,
                            source=source_url,
                        )
                    )
        except ET.ParseError:
            logger.debug(f"[NEWS] XML parse error for {source_url}")
        except Exception as e:
            logger.debug(f"[NEWS] parse error: {e}")

        return entries

    def _score_text(self, text: str) -> float:
        """
        Score text sentiment using keyword matching.
        Returns: float in [-1.0, 1.0]
        """
        text_lower = text.lower()
        score = 0.0
        matches = 0

        for keyword, weight in BULLISH_KEYWORDS.items():
            if keyword in text_lower:
                score += weight
                matches += 1

        for keyword, weight in BEARISH_KEYWORDS.items():
            if keyword in text_lower:
                score += weight  # weight is already negative
                matches += 1

        if matches == 0:
            return 0.0

        # Normalize by number of matches (prevent extreme scores)
        normalized = score / max(matches, 1)
        return max(-1.0, min(1.0, normalized))

    def _detect_assets(self, text: str) -> list[str]:
        """Detect which assets are mentioned in text."""
        text_lower = text.lower()
        found = []

        for asset, keywords in ASSET_KEYWORDS.items():
            for kw in keywords:
                if kw in text_lower:
                    found.append(asset)
                    break

        # If no specific asset, tag as CRYPTO (general market)
        if not found:
            for kw in ASSET_KEYWORDS["CRYPTO"]:
                if kw in text_lower:
                    found.append("CRYPTO")
                    break

        return found

    def _aggregate_sentiments(self):
        """Aggregate per-entry scores into per-asset sentiments."""
        asset_scores: dict[str, list[float]] = {}
        asset_headlines: dict[str, str] = {}

        for entry in self._entries:
            for asset in entry.assets:
                if asset not in asset_scores:
                    asset_scores[asset] = []
                    asset_headlines[asset] = entry.title
                asset_scores[asset].append(entry.sentiment_score)

        self._sentiments = {}
        for asset, scores in asset_scores.items():
            if not scores:
                continue

            avg_score = sum(scores) / len(scores)

            direction = None
            confidence = 0.0
            if abs(avg_score) >= NEWS_MIN_SCORE:
                direction = "up" if avg_score > 0 else "down"
                confidence = min(abs(avg_score), 1.0)

            self._sentiments[asset] = NewsSentiment(
                asset=asset,
                score=round(avg_score, 4),
                direction=direction,
                entry_count=len(scores),
                top_headline=asset_headlines.get(asset, ""),
                confidence=round(confidence, 4),
            )

    async def get_signal(self, asset: str) -> float:
        """
        Get sentiment signal for an asset.
        Returns: float in [-WEIGHT, +WEIGHT] for signal fusion.
        """
        if not NEWS_SCANNER_ENABLED:
            return 0.0

        await self.poll()

        sentiment = self._sentiments.get(asset)
        if not sentiment or sentiment.direction is None:
            # Try general CRYPTO sentiment
            sentiment = self._sentiments.get("CRYPTO")
            if not sentiment or sentiment.direction is None:
                return 0.0

        return round(sentiment.score * NEWS_SIGNAL_WEIGHT, 4)

    async def get_sentiment(self, asset: str) -> Optional[NewsSentiment]:
        """Get full sentiment data for an asset."""
        await self.poll()
        return self._sentiments.get(asset) or self._sentiments.get("CRYPTO")

    def get_snapshot(self) -> NewsSnapshot:
        """Get full news state."""
        return NewsSnapshot(
            sentiments=dict(self._sentiments),
            total_entries=len(self._entries),
            last_poll=self._last_poll,
            feeds_ok=self._feeds_ok,
            feeds_failed=self._feeds_failed,
        )

    def format_telegram(self) -> str:
        """Format news state for Telegram HTML."""
        snap = self.get_snapshot()

        lines = [
            "<b>📰 Haber Taramasi</b>",
            f"  Feed: {snap.feeds_ok}/{len(RSS_FEEDS)} basarili",
            f"  Haber: {snap.total_entries} entry",
            "",
        ]

        if not snap.sentiments:
            lines.append("  <i>Henuz sentiment verisi yok</i>")
            return "\n".join(lines)

        for asset, sent in sorted(
            snap.sentiments.items(), key=lambda x: abs(x[1].score), reverse=True
        ):
            if asset == "CRYPTO":
                continue
            icon = "🟢" if sent.direction == "up" else ("🔴" if sent.direction == "down" else "⚪")
            headline = sent.top_headline[:40] if sent.top_headline else ""
            lines.append(
                f"  {icon} <b>{asset}</b>: {sent.score:+.2f} "
                f"({sent.entry_count} haber) {sent.direction or 'notral'}"
            )
            if headline:
                lines.append(f"      <i>{headline}</i>")

        # General crypto sentiment
        crypto = snap.sentiments.get("CRYPTO")
        if crypto:
            icon = (
                "🟢" if crypto.direction == "up" else ("🔴" if crypto.direction == "down" else "⚪")
            )
            lines.append(
                f"\n  {icon} <b>GENEL</b>: {crypto.score:+.2f} ({crypto.entry_count} haber)"
            )

        return "\n".join(lines)

    async def close(self):
        """Clean up HTTP session."""
        if self._http_session:
            await self._http_session.close()
            self._http_session = None


# ── Module-level singleton ──
_scanner: Optional[NewsScanner] = None


def get_news_scanner() -> NewsScanner:
    global _scanner
    if _scanner is None:
        _scanner = NewsScanner()
    return _scanner
