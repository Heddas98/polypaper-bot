"""
PolyPaper Bot — Per-Trade Order Validator
============================================
P0.10 (5AI Yol Haritası §5.1)

Telegram-initiated order'ları validate eder:
- MAX_ORDER_USD hard cap (mainnet ilk hafta = $10)
- MIN_PRICE / MAX_PRICE hard cap (0.05 - 0.95)
- Polymarket V2 docs minimum order $5 enforcement
- Tick size compliance (0.01, 0.001, 0.0001)
- Sanity check: side, token_id format

Heddas direktifi:
> "Telegram komutu `/buy 100 0.99` parmak kayması = otomatik reject."

ENV (T6.1 hot-tune):
- ORDER_MAX_USD            (default 10.0; mainnet ilk hafta)
- ORDER_MIN_USD            (default 5.0; Polymarket V2 min)
- ORDER_MIN_PRICE          (default 0.05)
- ORDER_MAX_PRICE          (default 0.95)
- ORDER_VALIDATOR_ENABLED  (default true)

Usage:
    from telegram_bot.handlers.order_validator import validate_order, ValidationResult

    result = validate_order(
        side="BUY",
        amount_usd=10,
        price=0.55,
        token_id="0x..."
    )
    if not result.ok:
        await update.effective_message.reply_html(result.error_html)
        return
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("polypaper.handlers.order_validator")


# Polymarket V2 docs constants
POLYMARKET_MIN_ORDER_USD = 5.0
TICK_SIZES = {"0.1", "0.01", "0.001", "0.0001"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name, "true" if default else "false").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _max_usd() -> float:
    return _env_float("ORDER_MAX_USD", 10.0)


def _min_usd() -> float:
    # Polymarket V2 absolute floor $5
    return max(_env_float("ORDER_MIN_USD", 5.0), POLYMARKET_MIN_ORDER_USD)


def _min_price() -> float:
    return _env_float("ORDER_MIN_PRICE", 0.05)


def _max_price() -> float:
    return _env_float("ORDER_MAX_PRICE", 0.95)


def _enabled() -> bool:
    return _env_bool("ORDER_VALIDATOR_ENABLED", True)


@dataclass
class ValidationResult:
    """Validation outcome."""
    ok: bool
    error_html: str = ""
    warnings: list[str] = None  # type: ignore
    sanitized: dict = None  # type: ignore

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []
        if self.sanitized is None:
            self.sanitized = {}


def _is_valid_side(side: str) -> bool:
    return side.upper() in {"BUY", "SELL"}


def _is_valid_token_id(token_id: str) -> bool:
    """Polymarket token_id is a large decimal integer string."""
    if not token_id:
        return False
    # Strip 0x prefix if present (some APIs use hex, some decimal)
    s = token_id.lstrip("0x").lstrip("0X")
    return bool(re.match(r"^[0-9a-fA-F]+$", s)) and len(s) >= 16


def _check_tick_size(price: float, tick_size: Optional[str]) -> tuple[bool, str]:
    """Verify price conforms to market's tick size.

    If tick_size unknown, assume 0.01 (most common for crypto Up/Down).
    """
    ts = tick_size or "0.01"
    if ts not in TICK_SIZES:
        return False, f"Geçersiz tick_size: {ts}"
    try:
        ts_f = float(ts)
        # Round to tick precision and compare
        rounded = round(price / ts_f) * ts_f
        if abs(price - rounded) > 1e-9:
            return False, f"Fiyat tick uyumlu değil ({ts}): {price}"
    except (ValueError, TypeError):
        return False, "Tick size hesabı başarısız"
    return True, ""


def validate_order(
    side: str,
    amount_usd: float,
    price: float,
    token_id: str,
    tick_size: Optional[str] = None,
    skip_validator: bool = False,
) -> ValidationResult:
    """Hard-cap validation for Telegram-initiated orders.

    Args:
        side: "BUY" or "SELL"
        amount_usd: Notional dollar amount
        price: Limit price (0..1)
        token_id: Polymarket condition token ID
        tick_size: Optional, default "0.01"
        skip_validator: Override (admin emergency); ENV bypass'a alternatif

    Returns: ValidationResult
    """
    if not _enabled() or skip_validator:
        return ValidationResult(
            ok=True,
            warnings=["Validator disabled (ORDER_VALIDATOR_ENABLED=false or skip_validator=True)"],
            sanitized={
                "side": side.upper() if side else "",
                "amount_usd": float(amount_usd) if amount_usd else 0,
                "price": float(price) if price else 0,
                "token_id": token_id,
            },
        )

    errors = []

    # 1. Side
    if not _is_valid_side(side):
        errors.append(f"❌ Geçersiz side: <code>{side}</code> (BUY|SELL bekleniyor)")

    # 2. Token ID
    if not _is_valid_token_id(token_id):
        errors.append(f"❌ Geçersiz token_id: <code>{token_id[:16] if token_id else ''}...</code>")

    # 3. Amount range
    try:
        amt = float(amount_usd)
    except (ValueError, TypeError):
        errors.append(f"❌ Geçersiz amount: <code>{amount_usd}</code>")
        amt = 0

    min_usd = _min_usd()
    max_usd = _max_usd()
    if amt < min_usd:
        errors.append(
            f"❌ Tutar çok küçük: ${amt:.2f} < min ${min_usd:.2f} "
            f"(Polymarket V2 minimum)"
        )
    if amt > max_usd:
        errors.append(
            f"❌ Tutar çok büyük: ${amt:.2f} > max ${max_usd:.2f} "
            f"(<code>ORDER_MAX_USD</code> hard cap — parmak kayması koruması)"
        )

    # 4. Price range
    try:
        prc = float(price)
    except (ValueError, TypeError):
        errors.append(f"❌ Geçersiz price: <code>{price}</code>")
        prc = 0

    min_p = _min_price()
    max_p = _max_price()
    if prc < min_p:
        errors.append(
            f"❌ Fiyat çok düşük: {prc:.4f} < min {min_p:.4f} "
            f"(düşük olasılık edge zayıf)"
        )
    if prc > max_p:
        errors.append(
            f"❌ Fiyat çok yüksek: {prc:.4f} > max {max_p:.4f} "
            f"(yüksek olasılık asymmetric risk)"
        )

    # 5. Tick size
    if prc > 0:
        ok_tick, tick_err = _check_tick_size(prc, tick_size)
        if not ok_tick:
            errors.append(f"❌ {tick_err}")

    # Build result
    if errors:
        body = "<b>🛑 Order Validation FAILED</b>\n\n" + "\n".join(errors)
        body += "\n\n<i>Limit'leri ayarlamak: <code>/envt ORDER_MAX_USD 50</code> "
        body += "(admin only, /env_toggle whitelist gerek).</i>"
        return ValidationResult(ok=False, error_html=body)

    return ValidationResult(
        ok=True,
        sanitized={
            "side": side.upper(),
            "amount_usd": amt,
            "price": prc,
            "token_id": token_id,
            "tick_size": tick_size or "0.01",
        },
    )


def parse_buy_command_args(args: list[str]) -> tuple[Optional[float], Optional[float], list[str]]:
    """Parse `/buy <amount> <price>` Telegram command args.

    Returns: (amount_usd, price, errors)
    """
    errors = []
    amount = None
    price = None

    if len(args) < 2:
        errors.append("Kullanım: <code>/buy &lt;amount_usd&gt; &lt;price&gt;</code>")
        return None, None, errors

    try:
        amount = float(args[0])
    except (ValueError, TypeError):
        errors.append(f"Geçersiz amount: <code>{args[0]}</code>")

    try:
        price = float(args[1])
    except (ValueError, TypeError):
        errors.append(f"Geçersiz price: <code>{args[1]}</code>")

    return amount, price, errors


def render_caps_html() -> str:
    """For /h or /caps command — display current limits."""
    lines = [
        "<b>🛡️ Per-Trade Hard Caps</b>",
        "",
        f"💵 Amount: <code>${_min_usd():.2f}</code> - <code>${_max_usd():.2f}</code>",
        f"📊 Price:  <code>{_min_price():.4f}</code> - <code>{_max_price():.4f}</code>",
        f"🔄 Validator: <code>{'ENABLED' if _enabled() else 'DISABLED'}</code>",
        "",
        "<i>Tick size: 0.01 (default crypto Up/Down)</i>",
        "<i>Polymarket V2 minimum: $5 (cannot go below)</i>",
    ]
    return "\n".join(lines)
