"""
PolyPaper Bot — Polymarket V2 Error Code Mapping
====================================================
P2.2 (Phase D Bulgu 11)

Polymarket V2 docs `/trading/orders/create#error-messages`:
15+ error code → user-friendly message + auto-resolution suggestion.

Public API:
    from core.error_handler.polymarket_errors import classify_error
    info = classify_error("INVALID_ORDER_MIN_TICK_SIZE", price=0.555)
    # info.tr_message, info.en_message, info.suggestion, info.severity, info.auto_fix
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

SEVERITY_INFO = "info"
SEVERITY_WARN = "warn"
SEVERITY_ERROR = "error"
SEVERITY_CRITICAL = "critical"


@dataclass
class ErrorInfo:
    code: str
    severity: str
    en_message: str
    tr_message: str
    suggestion: str
    auto_fix: Optional[str] = None  # e.g., "snap_to_tick", "increase_balance"
    user_action_required: bool = True


# Polymarket V2 docs error catalog
ERROR_CATALOG: dict[str, ErrorInfo] = {
    "INVALID_ORDER_MIN_TICK_SIZE": ErrorInfo(
        code="INVALID_ORDER_MIN_TICK_SIZE",
        severity=SEVERITY_WARN,
        en_message="Price doesn't conform to the market's tick size",
        tr_message="Fiyat marketin tick adımına uymuyor",
        suggestion="Fiyatı tick boyutuna (0.01, 0.001, 0.0001) yuvarla ve tekrar dene",
        auto_fix="snap_to_tick",
        user_action_required=False,
    ),
    "INVALID_ORDER_MIN_SIZE": ErrorInfo(
        code="INVALID_ORDER_MIN_SIZE",
        severity=SEVERITY_WARN,
        en_message="Order size is below the minimum threshold",
        tr_message="Order büyüklüğü minimum altında ($5 min)",
        suggestion="Order tutarını $5'a (Polymarket V2 minimum) yükselt",
        auto_fix="increase_to_min",
        user_action_required=False,
    ),
    "INVALID_ORDER_DUPLICATED": ErrorInfo(
        code="INVALID_ORDER_DUPLICATED",
        severity=SEVERITY_INFO,
        en_message="Identical order has already been placed",
        tr_message="Aynı order zaten yerleştirilmiş",
        suggestion="Bu order zaten kuyrukta — duplicate çağrı bekle veya cancel önceki",
        auto_fix=None,
        user_action_required=False,
    ),
    "INVALID_ORDER_NOT_ENOUGH_BALANCE": ErrorInfo(
        code="INVALID_ORDER_NOT_ENOUGH_BALANCE",
        severity=SEVERITY_ERROR,
        en_message="Funder doesn't have sufficient balance or allowance",
        tr_message="Funder cüzdanda yetersiz pUSD bakiye veya allowance",
        suggestion="Polymarket'e pUSD deposit yap VEYA /allowance_check ile approve durumunu kontrol et",
        auto_fix=None,
        user_action_required=True,
    ),
    "INVALID_ORDER_EXPIRATION": ErrorInfo(
        code="INVALID_ORDER_EXPIRATION",
        severity=SEVERITY_WARN,
        en_message="Expiration timestamp is in the past",
        tr_message="Order expiration tarihi geçmişte",
        suggestion="GTD expiration'ı min `now + 60 + N` formatında ayarla",
        auto_fix="adjust_expiration",
        user_action_required=False,
    ),
    "INVALID_ORDER_ERROR": ErrorInfo(
        code="INVALID_ORDER_ERROR",
        severity=SEVERITY_ERROR,
        en_message="System error while inserting order",
        tr_message="Sistem hatası — order eklenemedi",
        suggestion="Polymarket sunucu sorunu — 30s sonra tekrar dene",
        auto_fix="retry_with_backoff",
        user_action_required=False,
    ),
    "INVALID_POST_ONLY_ORDER_TYPE": ErrorInfo(
        code="INVALID_POST_ONLY_ORDER_TYPE",
        severity=SEVERITY_WARN,
        en_message="Post-only flag used with a market order type (FOK/FAK)",
        tr_message="Post-only flag market order ile uyumsuz (sadece GTC/GTD)",
        suggestion="Post-only sadece GTC veya GTD ile kullanılır — order_type'ı düzelt",
        auto_fix="remove_post_only",
        user_action_required=False,
    ),
    "INVALID_POST_ONLY_ORDER": ErrorInfo(
        code="INVALID_POST_ONLY_ORDER",
        severity=SEVERITY_INFO,
        en_message="Post-only order would cross the book",
        tr_message="Post-only order kitabı geçecekti (taker olurdu, reject)",
        suggestion="Spread çok dar — limit fiyatı best bid/ask'ten daha pasif yap",
        auto_fix="adjust_price_to_passive",
        user_action_required=False,
    ),
    "EXECUTION_ERROR": ErrorInfo(
        code="EXECUTION_ERROR",
        severity=SEVERITY_ERROR,
        en_message="System error while executing trade",
        tr_message="Trade execution sistem hatası",
        suggestion="Polymarket matching engine sorunu — 30s bekle, tekrar dene",
        auto_fix="retry_with_backoff",
        user_action_required=False,
    ),
    "ORDER_DELAYED": ErrorInfo(
        code="ORDER_DELAYED",
        severity=SEVERITY_INFO,
        en_message="Order placement delayed due to market conditions",
        tr_message="Market koşulları nedeniyle order gecikmeli",
        suggestion="Order kabul edildi ama matching geciktirildi — status polling yap",
        auto_fix="poll_status",
        user_action_required=False,
    ),
    "DELAYING_ORDER_ERROR": ErrorInfo(
        code="DELAYING_ORDER_ERROR",
        severity=SEVERITY_WARN,
        en_message="System error while delaying order",
        tr_message="Order delay sistem hatası",
        suggestion="Order yerleşmedi — yeniden gönder (idempotency anahtarı ile)",
        auto_fix="retry_with_idempotency",
        user_action_required=False,
    ),
    "FOK_ORDER_NOT_FILLED_ERROR": ErrorInfo(
        code="FOK_ORDER_NOT_FILLED_ERROR",
        severity=SEVERITY_INFO,
        en_message="FOK order couldn't be fully filled",
        tr_message="FOK (Fill-or-Kill) tam dolmadı, iptal edildi",
        suggestion="FAK kullan (partial fill OK) veya price'ı daha aggressive yap",
        auto_fix="switch_to_fak",
        user_action_required=False,
    ),
    "MARKET_NOT_READY": ErrorInfo(
        code="MARKET_NOT_READY",
        severity=SEVERITY_WARN,
        en_message="Market is not yet accepting orders",
        tr_message="Market henüz order kabul etmiyor (henüz başlamamış)",
        suggestion="Market start time'ını bekle (gameStartTime kontrol)",
        auto_fix=None,
        user_action_required=False,
    ),
    # Auth-level
    "INVALID_API_KEY": ErrorInfo(
        code="INVALID_API_KEY",
        severity=SEVERITY_CRITICAL,
        en_message="Unauthorized/Invalid api key",
        tr_message="Geçersiz API key (creds expired veya yanlış)",
        suggestion="Bot restart → derive fallback otomatik yenisini alır. Manuel: .env'den POLYMARKET_API_KEY/SECRET/PASSPHRASE sil → derive zorla",
        auto_fix="rederive_creds",
        user_action_required=True,
    ),
    "INVALID_SIGNATURE": ErrorInfo(
        code="INVALID_SIGNATURE",
        severity=SEVERITY_CRITICAL,
        en_message="Order signature verification failed",
        tr_message="Order imza doğrulama hatası (EIP-712 v2 mismatch)",
        suggestion="V2 SDK kullanıldığını doğrula (`requirements.txt`: py-clob-client-v2). signature_type=2 (GNOSIS_SAFE) ve funder=proxy adresi kontrol",
        auto_fix=None,
        user_action_required=True,
    ),
    # HTTP-level
    "HTTP_425_TOO_EARLY": ErrorInfo(
        code="HTTP_425_TOO_EARLY",
        severity=SEVERITY_INFO,
        en_message="Matching engine restarting (HTTP 425)",
        tr_message="Matching engine yeniden başlıyor — kısa süre içinde tekrar açık olacak",
        suggestion="5-10 saniye bekle, sonra retry — Polymarket restart window",
        auto_fix="retry_with_backoff",
        user_action_required=False,
    ),
    "HTTP_429_RATE_LIMITED": ErrorInfo(
        code="HTTP_429_RATE_LIMITED",
        severity=SEVERITY_WARN,
        en_message="Rate limit exceeded",
        tr_message="Rate limit aşıldı (3500/10s POST /order, 1500/10s GET /book)",
        suggestion="Exponential backoff (5→10→30→60s) + concurrent request azalt",
        auto_fix="retry_with_backoff",
        user_action_required=False,
    ),
    "HTTP_403_CLOUDFLARE": ErrorInfo(
        code="HTTP_403_CLOUDFLARE",
        severity=SEVERITY_WARN,
        en_message="Blocked by Cloudflare bot detection",
        tr_message="Cloudflare bot detect → IP geçici olarak block",
        suggestion="1h cooldown + cached creds reuse (live_trader.SHARED_CREDS_CACHE)",
        auto_fix="use_shared_cache",
        user_action_required=False,
    ),
}


def classify_error(code_or_message: str, **context) -> ErrorInfo:
    """Map error code or raw exception message to ErrorInfo.

    Args:
        code_or_message: "INVALID_ORDER_NOT_ENOUGH_BALANCE" or raw exception text
        **context: e.g., price, amount_usd (used in suggestion formatting)

    Returns: ErrorInfo (UNKNOWN if unmatched)
    """
    text = (code_or_message or "").upper()

    # Exact code match
    for code, info in ERROR_CATALOG.items():
        if code in text:
            return info

    # HTTP status fuzzy match
    if "401" in text or "UNAUTHORIZED" in text:
        return ERROR_CATALOG["INVALID_API_KEY"]
    if "403" in text and ("CLOUDFLARE" in text or "BLOCKED" in text):
        return ERROR_CATALOG["HTTP_403_CLOUDFLARE"]
    if "425" in text or "TOO EARLY" in text:
        return ERROR_CATALOG["HTTP_425_TOO_EARLY"]
    if "429" in text or "RATE LIMIT" in text:
        return ERROR_CATALOG["HTTP_429_RATE_LIMITED"]

    # Unknown
    return ErrorInfo(
        code="UNKNOWN",
        severity=SEVERITY_WARN,
        en_message=f"Unrecognized error: {code_or_message[:100]}",
        tr_message=f"Tanımlanmayan hata: {code_or_message[:100]}",
        suggestion="Logu kontrol et + Polymarket docs'a bak",
        auto_fix=None,
        user_action_required=True,
    )


def format_for_telegram(info: ErrorInfo, raw_msg: str = "") -> str:
    """HTML format for Telegram error notification."""
    severity_emoji = {
        SEVERITY_INFO: "ℹ️",
        SEVERITY_WARN: "⚠️",
        SEVERITY_ERROR: "🔴",
        SEVERITY_CRITICAL: "🚨",
    }.get(info.severity, "❓")

    lines = [
        f"{severity_emoji} <b>{info.code}</b>",
        f"  {info.tr_message}",
        "",
        f"💡 <b>Çözüm:</b> {info.suggestion}",
    ]
    if info.auto_fix:
        lines.append(f"🔧 Auto-fix: <code>{info.auto_fix}</code>")
    if raw_msg and len(raw_msg) > 0:
        lines.append("")
        lines.append(f"<i>Raw:</i> <code>{raw_msg[:120]}</code>")
    return "\n".join(lines)
