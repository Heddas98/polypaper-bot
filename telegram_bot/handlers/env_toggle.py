"""
Phase 82e Sprint 6 — /env_toggle Telegram handler.

Admin-only hot-tuning of runtime ENV knobs. Writes to:
    1. os.environ[key]  (immediate effect, next getenv() call sees new value)
    2. .env file        (patches or appends; persists across bot restarts)
    3. logs/env_toggle_audit.log  (timestamp, admin, key, old->new)

Commands:
    /env_toggle                  → grouped list with current values
    /env_toggle <KEY>            → single-key detail (type, default, desc, curr)
    /env_toggle <KEY> <VALUE>    → set + persist + audit
    /env_toggle reset <KEY>      → restore default (removes .env line)
Alias: /envt

Whitelisted keys only. Validation + range checks happen in
config.env_whitelist.coerce_value. Unknown/invalid keys return a clear
error without touching process state.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from config.env_whitelist import (
    ENV_WHITELIST, coerce_value, list_groups)
from config.settings import Settings
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.env_toggle")

# Located relative to project root (one level above telegram_bot/).
_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _ROOT / ".env"
_AUDIT_PATH = _ROOT / "logs" / "env_toggle_audit.log"


# ──────────────────────────────────────────────────────────────────────
#  Admin check — mirrors force_settle_handler pattern.
# ──────────────────────────────────────────────────────────────────────
def _is_admin(context, telegram_id: int) -> bool:
    settings: Settings = context.bot_data.get("settings")
    if not settings:
        logger.warning(
            "env_toggle _is_admin: settings missing, denying "
            f"{telegram_id}")
        return False
    return settings.is_admin(telegram_id)


# ──────────────────────────────────────────────────────────────────────
#  .env patching — safe rewrite (line-match or append).
# ──────────────────────────────────────────────────────────────────────
_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _read_env_file() -> list[str]:
    if not _ENV_PATH.exists():
        return []
    try:
        return _ENV_PATH.read_text(
            encoding="utf-8", errors="replace").splitlines()
    except Exception as e:
        logger.exception(f"env_toggle read .env: {e}")
        return []


def _write_env_file(lines: list[str]) -> None:
    _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _patch_env_file(key: str, value: str | None) -> None:
    """Set or remove a key in .env. value=None removes the line."""
    lines = _read_env_file()
    found = False
    out: list[str] = []
    for ln in lines:
        m = _LINE_RE.match(ln)
        if m and m.group(1) == key:
            found = True
            if value is not None:
                out.append(f"{key}={value}")
            # value=None → drop the line
        else:
            out.append(ln)
    if not found and value is not None:
        if out and out[-1].strip():
            out.append("")  # separator before append
        out.append(f"# Added by /env_toggle "
                   f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
        out.append(f"{key}={value}")
    _write_env_file(out)


# ──────────────────────────────────────────────────────────────────────
#  Audit log.
# ──────────────────────────────────────────────────────────────────────
def _audit(admin_id: int, action: str, key: str, old: str, new: str) -> None:
    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"{ts}\tadmin={admin_id}\t{action}\t{key}\told={old}\tnew={new}\n"
        with _AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.exception(f"env_toggle audit write: {e}")


# ──────────────────────────────────────────────────────────────────────
#  Formatting.
# ──────────────────────────────────────────────────────────────────────
def _cur(key: str) -> str:
    """Live value — env if set, else default."""
    v = os.environ.get(key)
    if v is None:
        return str(ENV_WHITELIST[key]["default"])
    return v


def _format_list() -> str:
    lines = ["⚙️ <b>/env_toggle</b>  <i>(hot-tunable)</i>"]
    for group in list_groups():
        lines.append(f"\n<b>── {esc(group)} ──</b>")
        for key, meta in ENV_WHITELIST.items():
            if meta.get("group", "other") != group:
                continue
            cur = _cur(key)
            default = str(meta["default"])
            mark = "·" if cur == default else "◆"
            lines.append(
                f"{mark} <code>{esc(key)}</code> = "
                f"<b>{esc(cur)}</b>  <i>(def {esc(default)})</i>")
    lines.append(
        "\nKullanim:\n"
        "• <code>/env_toggle KEY</code> — detay\n"
        "• <code>/env_toggle KEY VALUE</code> — degistir\n"
        "• <code>/env_toggle reset KEY</code> — default'a don\n"
        "<i>◆ = default'tan sapmis</i>")
    return "\n".join(lines)


def _format_detail(key: str) -> str:
    meta = ENV_WHITELIST[key]
    lines = [
        f"⚙️ <b>{esc(key)}</b>",
        f"group   : <code>{esc(meta.get('group', 'other'))}</code>",
        f"type    : <code>{esc(meta['type'])}</code>",
        f"default : <code>{esc(str(meta['default']))}</code>",
        f"current : <code>{esc(_cur(key))}</code>",
    ]
    if "min" in meta:
        lines.append(f"min     : <code>{meta['min']}</code>")
    if "max" in meta:
        lines.append(f"max     : <code>{meta['max']}</code>")
    if meta.get("choices"):
        lines.append(
            f"choices : <code>{esc(', '.join(meta['choices']))}</code>")
    lines.append(f"\n<i>{esc(meta.get('desc', ''))}</i>")
    lines.append(f"\nDegistir: <code>/env_toggle {esc(key)} YENI_DEGER</code>")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
#  Core actions.
# ──────────────────────────────────────────────────────────────────────
def _apply_set(key: str, raw_value: str,
               admin_id: int) -> tuple[bool, str]:
    ok, coerced, err = coerce_value(key, raw_value)
    if not ok:
        return False, f"❌ {esc(err)}"
    old = _cur(key)
    os.environ[key] = coerced
    try:
        _patch_env_file(key, coerced)
    except Exception as e:
        logger.exception(f"env_toggle patch .env failed: {e}")
        # Keep the os.environ change — still effective this session.
        _audit(admin_id, "SET_OS_ONLY", key, old, coerced)
        return True, (
            f"⚠️ <b>{esc(key)}</b>: {esc(old)} → <b>{esc(coerced)}</b>\n"
            f"<i>os.environ guncellendi ama .env yazilamadi: "
            f"{esc(str(e))[:120]}</i>")
    _audit(admin_id, "SET", key, old, coerced)
    logger.info(f"env_toggle SET {key}: {old} -> {coerced} by {admin_id}")
    return True, (
        f"✅ <b>{esc(key)}</b>: {esc(old)} → <b>{esc(coerced)}</b>\n"
        f"<i>os.environ + .env guncellendi</i>")


def _apply_reset(key: str, admin_id: int) -> tuple[bool, str]:
    default = str(ENV_WHITELIST[key]["default"])
    old = _cur(key)
    os.environ[key] = default  # ensure runtime code sees default now
    try:
        _patch_env_file(key, None)  # drop from .env
    except Exception as e:
        logger.exception(f"env_toggle reset patch failed: {e}")
        _audit(admin_id, "RESET_OS_ONLY", key, old, default)
        return True, (
            f"⚠️ <b>{esc(key)}</b> default'a dondu: "
            f"{esc(old)} → <b>{esc(default)}</b>\n"
            f"<i>.env temizlenemedi: {esc(str(e))[:120]}</i>")
    _audit(admin_id, "RESET", key, old, default)
    logger.info(f"env_toggle RESET {key}: {old} -> {default} by {admin_id}")
    return True, (
        f"♻️ <b>{esc(key)}</b> default'a dondu: "
        f"{esc(old)} → <b>{esc(default)}</b>")


# ──────────────────────────────────────────────────────────────────────
#  Entry point.
# ──────────────────────────────────────────────────────────────────────
async def env_toggle_command(update: Update,
                             context: ContextTypes.DEFAULT_TYPE):
    """/env_toggle — hot-tune runtime env knobs (admin only)."""
    user = update.effective_user
    if not user or not _is_admin(context, user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    args = context.args or []

    # No args → list all.
    if not args:
        return await update.message.reply_text(
            _format_list(), parse_mode="HTML")

    first = args[0].strip()

    # reset flow: /env_toggle reset KEY
    if first.lower() == "reset":
        if len(args) < 2:
            return await update.message.reply_text(
                "Kullanim: <code>/env_toggle reset KEY</code>",
                parse_mode="HTML")
        key = args[1].strip().upper()
        if key not in ENV_WHITELIST:
            return await update.message.reply_text(
                f"❌ Bilinmeyen key: <code>{esc(key)}</code>\n"
                "Liste: <code>/env_toggle</code>",
                parse_mode="HTML")
        _, msg = _apply_reset(key, user.id)
        return await update.message.reply_text(msg, parse_mode="HTML")

    key = first.upper()
    if key not in ENV_WHITELIST:
        return await update.message.reply_text(
            f"❌ Bilinmeyen key: <code>{esc(first)}</code>\n"
            "Liste: <code>/env_toggle</code>",
            parse_mode="HTML")

    # Single-arg → detail
    if len(args) == 1:
        return await update.message.reply_text(
            _format_detail(key), parse_mode="HTML")

    # KEY VALUE → set
    raw_value = " ".join(args[1:]).strip()
    _, msg = _apply_set(key, raw_value, user.id)
    return await update.message.reply_text(msg, parse_mode="HTML")
