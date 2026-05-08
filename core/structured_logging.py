"""
PolyPaper Bot — Structured Logging + Secret Scrubbing
========================================================
P1.7 (5AI Yol Haritası §5.2 Deepseek "trust signal")

JSON structured log + secret scrubbing custom formatter.
T10.8 13 secret regex baseline ✅ + bu modül runtime scrub.

ENV:
- STRUCTURED_LOG_ENABLED (default false — backward compat)
- STRUCTURED_LOG_FILE (default data_store/structured.jsonl)
- LOG_SECRET_SCRUB (default true)

Usage:
    from core.structured_logging import setup_structured_logging
    setup_structured_logging()
    # Existing loggers continue to work + JSON file write
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional


# 13 secret regex (T10.8 baseline) + ek
SECRET_PATTERNS = [
    # Private keys (Ethereum 64+ hex char with optional 0x prefix)
    (re.compile(r"\b0x[a-fA-F0-9]{40,}"), "[REDACTED_PRIVATE_KEY]"),
    # API keys / passwords (env-style)
    (re.compile(r"(?i)(api[_-]?key|apikey)\s*[:=]\s*['\"]?[\w-]{16,}", re.MULTILINE), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)(api[_-]?secret|secret)\s*[:=]\s*['\"]?[\w/+=]{16,}", re.MULTILINE), "[REDACTED_API_SECRET]"),
    (re.compile(r"(?i)(api[_-]?passphrase|passphrase|password)\s*[:=]\s*['\"]?[\w-]{8,}", re.MULTILINE), "[REDACTED_PASSPHRASE]"),
    (re.compile(r"(?i)(bot[_-]?token|telegram[_-]?token)\s*[:=]\s*['\"]?[\d:]{20,}", re.MULTILINE), "[REDACTED_BOT_TOKEN]"),
    # Telegram bot token format: 123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
    (re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,40}\b"), "[REDACTED_TELEGRAM_TOKEN]"),
    # JWT tokens
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[REDACTED_JWT]"),
    # AWS access keys
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    # Generic high-entropy strings near keywords
    (re.compile(r"(?i)(creds?|credential|auth)\s*[:=]\s*['\"]?[\w/+=]{20,}", re.MULTILINE), "[REDACTED_CREDS]"),
    # Polymarket-specific stored creds
    (re.compile(r"\bPOLYMARKET_API_KEY\s*[=:]\s*['\"]?[\w-]+", re.MULTILINE), "[REDACTED_POLYMARKET_API_KEY]"),
    (re.compile(r"\bPOLYMARKET_API_SECRET\s*[=:]\s*['\"]?[\w/+=]+", re.MULTILINE), "[REDACTED_POLYMARKET_SECRET]"),
    (re.compile(r"\bPOLYMARKET_PASSPHRASE\s*[=:]\s*['\"]?[\w-]+", re.MULTILINE), "[REDACTED_POLYMARKET_PASSPHRASE]"),
    (re.compile(r"\bPOLYGON_PRIVATE_KEY\s*[=:]\s*['\"]?[\w]+", re.MULTILINE), "[REDACTED_POLYGON_PK]"),
]


def scrub_secrets(text: str) -> str:
    """Apply all secret patterns. Order matters (specific → generic)."""
    if not isinstance(text, str):
        return text
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SecretScrubFilter(logging.Filter):
    """Logging filter that scrubs secrets in record.msg + args."""

    def __init__(self, enabled: bool = True):
        super().__init__()
        self.enabled = enabled

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.enabled:
            return True
        try:
            # Scrub the formatted message
            if isinstance(record.msg, str):
                record.msg = scrub_secrets(record.msg)
            # Scrub args — preserve numeric/non-string types to avoid breaking
            # %d / %f format specifiers downstream. Only str args are scrubbed.
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: (scrub_secrets(v) if isinstance(v, str) else v)
                        for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        scrub_secrets(a) if isinstance(a, str) else a
                        for a in record.args
                    )
        except Exception:  # noqa: BLE001 — defensive: never break logging
            pass
        return True


class JsonFormatter(logging.Formatter):
    """JSON line formatter — Splunk/ELK/Loki uyumlu."""

    def format(self, record: logging.LogRecord) -> str:
        # Defensive: record.msg % record.args may TypeError on mismatched %s/%d
        # (e.g. SecretScrubFilter coerces args to str, but msg has %d).
        try:
            msg_str = record.getMessage() if record.args else str(record.msg)
        except (TypeError, ValueError):
            # Fallback: raw msg + args repr
            msg_str = f"{record.msg!s} {record.args!r}" if record.args else str(record.msg)
        log_obj = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": msg_str,
            "module": record.module,
            "lineno": record.lineno,
        }
        if record.exc_info:
            log_obj["exc"] = self.formatException(record.exc_info)
        # Scrub before JSON encode (defensive)
        if os.getenv("LOG_SECRET_SCRUB", "true").strip().lower() in {"1", "true", "yes"}:
            log_obj["msg"] = scrub_secrets(log_obj["msg"])
        return json.dumps(log_obj, ensure_ascii=False, default=str)


def setup_structured_logging(
    log_file: Optional[str] = None,
    enable_scrub: bool = True,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> Optional[RotatingFileHandler]:
    """Wire JSON file handler + secret scrub filter to root logger.

    Idempotent: safe to call multiple times.

    Returns: RotatingFileHandler veya None (disabled).
    """
    enabled = os.getenv("STRUCTURED_LOG_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
    if not enabled:
        return None

    log_path = log_file or os.getenv("STRUCTURED_LOG_FILE", "data_store/structured.jsonl")
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    # JSON rotating file handler
    handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(logging.INFO)
    handler.setFormatter(JsonFormatter())

    # Add scrub filter to handler
    if enable_scrub:
        scrub = SecretScrubFilter(enabled=True)
        handler.addFilter(scrub)

    # Attach to root logger (idempotent — check existing handlers)
    root = logging.getLogger()
    existing_paths = [
        getattr(h, "baseFilename", "")
        for h in root.handlers
        if isinstance(h, RotatingFileHandler)
    ]
    if any(log_path in p for p in existing_paths):
        return None  # already attached

    root.addHandler(handler)
    root.info(f"📝 Structured logging: {log_path} (rotate {max_bytes//1024}KB × {backup_count})")

    return handler
