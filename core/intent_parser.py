"""
Phase 51 P51-04 — Intent parser (natural language → bot command).

Takes a free-form user message in Turkish or English and maps it to one
of PolyPaper Bot's real commands plus any arguments. Two layers:

  1. Keyword fallback  → no API cost, instant. Covers the top ~20 queries.
  2. Claude Sonnet     → for anything the keyword layer can't match with
                         high confidence. Re-uses `ANTHROPIC_API_KEY`.

The parser is intentionally self-contained (stdlib + httpx only) so the
rest of the bot can import it without pulling heavy deps.

Return contract
---------------
`parse_intent(text)` returns `IntentResult` with:
  - command:    str  e.g. "/rs"  (always starts with '/')
  - args:       list[str]
  - confidence: float in [0, 1]
  - source:     "keyword" | "claude" | "unknown"
  - reasoning:  short explanation shown to the user
  - original:   the raw input

Callers should generally auto-execute when confidence >= 0.75, otherwise
show the suggestion and ask the user to confirm.

Phase 51 scope note
-------------------
We deliberately map to the *command string* rather than importing and
calling the handler function directly — this keeps the parser decoupled
from telegram_bot/ and lets the /ai handler dispatch via the standard
command router, which benefits from all existing guard-rails
(rate limits, HTML escape, error templates, etc.).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field

logger = logging.getLogger("polypaper.core.intent_parser")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
INTENT_MODEL = os.getenv("INTENT_PARSER_MODEL", "claude-haiku-4-5-20251001")
INTENT_MAX_TOKENS = 400
INTENT_TIMEOUT_S = 12.0


# ---------------------------------------------------------------------------
# Command catalog — the source of truth for what the parser can produce.
# Keep this list in sync with telegram_bot/handlers/* and main.py.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    keywords: tuple[str, ...]
    takes_args: bool = False
    example_args: str = ""


COMMAND_CATALOG: tuple[CommandSpec, ...] = (
    # Phase 52 BUG #4 — primary balance/wallet routes. "bakiye", "para",
    # "balance", "cüzdan" now target /dashboard + /wallets instead of
    # the Kelly sizing command (which used to poach them).
    CommandSpec(
        "/dashboard",
        "Ana panel — bakiye, PnL, aktif stratejiler, risk özeti",
        (
            "dashboard",
            "panel",
            "bakiye",
            "balance",
            "kasam",
            "bakiyem",
            "bakiyemi",
            "paneli",
            "durum özet",
            "özet",
        ),
    ),
    CommandSpec(
        "/wallets",
        "Cüzdanlar — aktif cüzdan, para ekle/çek, bakiyeler",
        ("cüzdan", "cüzdanlar", "wallet", "wallets", "para", "paralarim", "fonlar", "funds"),
    ),
    CommandSpec(
        "/rs",
        "Özet risk durumu (exposure + streak + halt)",
        ("risk", "durum", "status", "expose", "halt", "kill", "acil"),
    ),
    CommandSpec(
        "/risk_hub",
        "Risk Hub — inline tablar (status/limits/canary/kill)",
        ("risk hub", "risk merkezi", "risk panel", "risk yönetimi"),
    ),
    CommandSpec(
        "/strategies",
        "Aktif stratejilerin listesi + WR + PnL",
        ("strateji", "strategies", "stratejilerim", "aktif strateji", "çalışan strateji"),
    ),
    CommandSpec(
        "/stats",
        "Genel PnL özeti + en iyi/kötü trade",
        ("stat", "istatistik", "overall", "genel", "özet", "pnl", "kar"),
    ),
    CommandSpec(
        "/stats_hub",
        "Stats Hub — tablarla tüm istatistik komutları",
        ("stat hub", "stats hub", "istatistik hub"),
    ),
    CommandSpec(
        "/stats_chart",
        "Son N günün PnL bar chart'ı",
        ("grafik", "chart", "graph", "pnl grafiği", "günlük"),
        takes_args=True,
        example_args="30",
    ),
    CommandSpec(
        "/trades",
        "Son trade'lerin listesi (opsiyonel filtre)",
        ("trade", "işlem", "son trade", "recent"),
        takes_args=True,
        example_args="btc 10",
    ),
    CommandSpec(
        "/maker_stats",
        "Maker rebate istatistikleri (24h/7d/lifetime)",
        ("maker", "rebate", "maker istatistik"),
    ),
    CommandSpec(
        # Phase 52 BUG #4 — removed "bakiye"/"balance"/"kasam" synonyms;
        # they now belong to /dashboard + /wallets. Kelly keeps only its
        # narrow sizing vocabulary so "bakiyemi goster" no longer routes
        # here with 0.82 confidence.
        "/kelly",
        "Kelly Criterion bankroll breakdown",
        ("kelly", "bankroll", "stake", "sizing", "position sizing"),
    ),
    CommandSpec(
        "/h",
        "Heartbeat — job'lar + WS + DB + engine durumu",
        ("heartbeat", "kalp", "durum", "health", "sağlık"),
    ),
    CommandSpec(
        "/db_health",
        "DB table-level boyut + satır sayısı",
        ("db", "database", "veritabanı", "boyut", "table size"),
    ),
    CommandSpec(
        "/autopilot",
        "Autopilot mod durumu + ayarları",
        ("autopilot", "otomatik", "auto pilot"),
    ),
    CommandSpec(
        "/alerts",
        "Kayıtlı fiyat alarmları",
        ("alarm", "alarmlar", "alert", "alerts", "uyarı"),
    ),
    CommandSpec(
        "/alert",
        "Yeni fiyat alarmı ekle (asset op price)",
        ("alarm ekle", "alarm kur", "new alert", "yeni uyarı"),
        takes_args=True,
        example_args="BTC > 0.6",
    ),
    CommandSpec(
        "/compare",
        "Strateji karşılaştırma (replay veya backtest)",
        ("karşılaştır", "kıyasla", "compare", "karşı"),
        takes_args=True,
        example_args="hour_edge streak_reversal",
    ),
    CommandSpec(
        "/backtest_v2",
        "Backtest v2 — tek strateji geçmiş simülasyonu",
        ("backtest", "backtest v2", "geriye dönük", "simülasyon", "bt2"),
        takes_args=True,
        example_args="hour_edge BTC 5m",
    ),
    CommandSpec(
        "/promote",
        "Canary strategy promote (canary→live)",
        ("promote", "canary promote", "yayına al"),
    ),
    CommandSpec(
        "/canary",
        "Canary deploy durumu",
        ("canary", "kanarya", "aşamalı deploy"),
    ),
    CommandSpec(
        "/demote",
        "Canary strategy demote (live→canary)",
        ("demote", "düşür", "geri al"),
    ),
    CommandSpec(
        "/filters",
        "Trade filtre paneli (on/off toggle)",
        ("filtre", "filters", "filter", "gate", "güvenlik", "engel", "toggle"),
    ),
    CommandSpec(
        "/positions",
        "Açık pozisyonlar",
        ("pozisyon", "positions", "açık", "open positions"),
    ),
    CommandSpec(
        "/shadow",
        "Shadow live trade özeti",
        ("shadow", "gölge", "shadow trade"),
    ),
    CommandSpec(
        "/brain",
        "AI Brain durumu + son cycle özeti",
        ("brain", "ai brain", "beyin", "ai durum"),
    ),
)


_COMMAND_BY_NAME = {c.name: c for c in COMMAND_CATALOG}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class IntentResult:
    command: str
    args: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "unknown"
    reasoning: str = ""
    original: str = ""

    @property
    def is_high_confidence(self) -> bool:
        # Phase 52 BUG #4 — threshold bumped 0.75 → 0.85 so the parser
        # only short-circuits on strong matches. Borderline keyword hits
        # (like Kelly's prior 0.82 on "bakiyemi goster") now fall through
        # to the Claude layer for disambiguation.
        return self.confidence >= 0.85 and self.command.startswith("/")

    def to_dict(self) -> dict:
        return asdict(self)


UNKNOWN = IntentResult(command="", confidence=0.0, source="unknown", reasoning="eşleşme yok")


# ---------------------------------------------------------------------------
# Keyword layer
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-zçğıöşü0-9]+", re.IGNORECASE)


def _tokenize(text: str) -> set[str]:
    return {m.group(0).lower() for m in _WORD_RE.finditer(text or "")}


def _token_matches(kw: str, tokens: set[str]) -> bool:
    """Token-level match that tolerates Turkish suffixes: 'strateji' also
    matches 'stratejileri', 'stratejiler', 'stratejimi' etc."""
    if kw in tokens:
        return True
    if len(kw) >= 4:
        for t in tokens:
            if t.startswith(kw) or kw.startswith(t[: max(4, len(t) - 2)]):
                return True
    return False


def _score(catalog_entry: CommandSpec, tokens: set[str], raw: str) -> float:
    """Heuristic score: how well the user text matches this command.

    Scoring philosophy: longer, more specific keywords win; multi-word
    phrase matches get a +0.10 bonus so they always beat a same-length
    single-word match.
    """
    if not tokens:
        return 0.0
    raw_lower = raw.lower()
    best = 0.0
    match_count = 0
    for kw in catalog_entry.keywords:
        if " " in kw:
            if kw in raw_lower:
                match_count += 1
                # phrase bonus: base is the length of the whole phrase
                phrase_len = len(kw.replace(" ", ""))
                score = 0.60 + 0.03 * phrase_len + 0.10  # +0.10 phrase bonus
                best = max(best, min(0.90, score))
        else:
            if _token_matches(kw, tokens):
                match_count += 1
                score = 0.52 + 0.05 * len(kw)
                best = max(best, min(0.85, score))
    if match_count == 0:
        return 0.0
    # +0.03 for each additional hit beyond the first
    return min(0.92, best + 0.03 * (match_count - 1))


_ASSET_RE = re.compile(r"\b(btc|eth|sol|xrp|bnb|ada|doge)\b", re.IGNORECASE)
_INT_RE = re.compile(r"\b(\d{1,5})\b")
_OP_RE = re.compile(r"(>=|<=|>|<|==|=)")


def _extract_args(spec: CommandSpec, raw: str) -> list[str]:
    if not spec.takes_args:
        return []
    args: list[str] = []
    # asset
    m = _ASSET_RE.search(raw)
    if m:
        args.append(m.group(1).upper())
    # count / int
    m = _INT_RE.search(raw)
    if m:
        args.append(m.group(1))
    # alert-shape: asset op price
    if spec.name == "/alert":
        mop = _OP_RE.search(raw)
        if mop:
            # find a float
            mp = re.search(r"\b(0?\.\d+|1\.0)\b", raw)
            if mp and args:
                args = [args[0], mop.group(1), mp.group(1)]
    return args


def keyword_match(text: str) -> IntentResult:
    tokens = _tokenize(text)
    best_score = 0.0
    best: CommandSpec | None = None
    for c in COMMAND_CATALOG:
        s = _score(c, tokens, text)
        if s > best_score:
            best_score = s
            best = c
    if best is None or best_score < 0.35:
        return IntentResult(
            command="",
            confidence=0.0,
            source="keyword",
            reasoning="no keyword match",
            original=text,
        )
    args = _extract_args(best, text)
    return IntentResult(
        command=best.name,
        args=args,
        confidence=round(best_score, 2),
        source="keyword",
        reasoning=f"kelime eşleşmesi ({best.description})",
        original=text,
    )


# ---------------------------------------------------------------------------
# Claude layer
# ---------------------------------------------------------------------------

INTENT_SYSTEM = """Sen PolyPaper Bot için bir intent parser'sın.
Kullanıcının Türkçe veya İngilizce mesajını, mevcut komutlardan BİRİNE map'le.

Mevcut komutlar (yalnızca bunlar):
{commands}

ÇIKTI KURALLARI:
1. SADECE geçerli JSON döndür, başka hiçbir şey yazma.
2. Format:
   {{"command": "/xxx", "args": ["arg1","arg2"], "confidence": 0.0-1.0, "reasoning": "kısa türkçe"}}
3. Eğer hiçbir komuta güvenle map'leyemiyorsan: {{"command": "", "args": [], "confidence": 0.0, "reasoning": "..." }}
4. Confidence >= 0.75 sadece kesin eşleşmede.
5. Komut adı listede olmalı, uydurmasyon YOK.
6. args: kullanıcı asset/sayı/işleç belirttiyse çıkar, yoksa [].
"""


def _catalog_text() -> str:
    lines = []
    for c in COMMAND_CATALOG:
        extra = f" (args: {c.example_args})" if c.takes_args and c.example_args else ""
        lines.append(f"  {c.name} — {c.description}{extra}")
    return "\n".join(lines)


async def _call_claude(text: str) -> dict | None:
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import httpx
    except ImportError:
        logger.debug("httpx not available for intent parser")
        return None
    system = INTENT_SYSTEM.format(commands=_catalog_text())
    payload = {
        "model": INTENT_MODEL,
        "max_tokens": INTENT_MAX_TOKENS,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": text}],
    }
    try:
        async with httpx.AsyncClient(timeout=INTENT_TIMEOUT_S) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            d = r.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, KeyError) as e:
        # T1.4 Faz 3: async with httpx.AsyncClient POST + r.json() inside
        # one try. httpx is bound as a local from L360's import try that
        # already handles ImportError separately, so reaching this except
        # means the client was constructed successfully. Realistic failure
        # modes:
        #   - httpx.HTTPError: umbrella for Connect/Timeout/RequestError/
        #     HTTPStatusError from the Anthropic API call
        #   - json.JSONDecodeError: r.json() on malformed response body
        #   - ValueError: response body coercion edge cases
        #   - KeyError: defensive for unexpected dict shape during parsing
        logger.warning(f"intent parser Claude call failed: {e}")
        return None
    content = d.get("content") or []
    if not content:
        return None
    raw = content[0].get("text", "").strip()
    # strip markdown fences if any
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # last-resort: pull first {...} block
        m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not m:
            return None  # type: ignore[unreachable]
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def _coerce_result(raw: dict, original: str) -> IntentResult:
    cmd = (raw.get("command") or "").strip()
    if cmd and not cmd.startswith("/"):
        cmd = "/" + cmd
    if cmd and cmd not in _COMMAND_BY_NAME:
        # unknown command → treat as no-match
        return IntentResult(
            command="",
            args=[],
            confidence=0.0,
            source="claude",
            reasoning=f"Claude returned unknown command: {cmd}",
            original=original,
        )
    args_raw = raw.get("args") or []
    if isinstance(args_raw, str):
        args_raw = args_raw.split()
    args = [str(a) for a in args_raw][:8]
    try:
        conf = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    return IntentResult(
        command=cmd,
        args=args,
        confidence=round(conf, 2),
        source="claude",
        reasoning=str(raw.get("reasoning", ""))[:200],
        original=original,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def parse_intent(text: str, *, use_claude: bool = True) -> IntentResult:
    """Map free-form NL input to a bot command."""
    text = (text or "").strip()
    if not text:
        return IntentResult(
            command="", confidence=0.0, source="unknown", reasoning="empty input", original=text
        )

    # Already a command? Pass through.
    if text.startswith("/"):
        parts = text.split()
        cmd = parts[0]
        if cmd in _COMMAND_BY_NAME:
            return IntentResult(
                command=cmd,
                args=parts[1:],
                confidence=1.0,
                source="passthrough",
                reasoning="zaten komut",
                original=text,
            )

    kw = keyword_match(text)
    if kw.is_high_confidence:
        return kw

    if use_claude and ANTHROPIC_API_KEY:
        raw = await _call_claude(text)
        if raw:
            cl = _coerce_result(raw, text)
            # Prefer Claude if it's more confident than keyword layer
            if cl.confidence >= kw.confidence:
                return cl

    # Fall back to whatever the keyword layer produced (may be low-conf).
    if kw.command:
        return kw
    return IntentResult(
        command="", confidence=0.0, source="unknown", reasoning="eşleşme bulunamadı", original=text
    )


def parse_intent_sync(text: str, *, use_claude: bool = False) -> IntentResult:
    """Synchronous convenience wrapper — keyword-only by default."""
    if not use_claude:
        text = (text or "").strip()
        if not text:
            return IntentResult(command="", source="unknown", original=text)
        if text.startswith("/"):
            parts = text.split()
            if parts[0] in _COMMAND_BY_NAME:
                return IntentResult(
                    command=parts[0],
                    args=parts[1:],
                    confidence=1.0,
                    source="passthrough",
                    original=text,
                )
        return keyword_match(text)
    return asyncio.run(parse_intent(text, use_claude=True))


def list_commands() -> list[dict]:
    """Introspection helper — returns the catalog as plain dicts."""
    return [asdict(c) for c in COMMAND_CATALOG]
