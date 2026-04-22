# T10.7 — Batch 2 Exception-to-User Info Leak (Epic 10 post-audit)

**Date:** 2026-04-22
**Epic:** 10 post-audit closure
**Risk classification:** MED (information disclosure via user-visible exception text)
**Scope:** `force_settle_handler.py:206`, `ai_handler.py:354`
**Result:** 🟢 **BOTH FIXED** — exception detail no longer reaches Telegram chat

## Context

T10.2 original audit (`docs/security/T10_2_telegram_input_sanitization.md`)
flagged 2 LOW "exception-to-user info disclosure" sites as Batch 2
forward work — fix before mainnet, nice-to-have, not blocking shadow
live. Epic 10 closure deferred these to post-audit; the Epic 10
post-audit decision was: **close Batch 2 before Epic 11 begins** so no
info-leak surface ships with the mainnet gate flip.

Both sites echoed `esc(str(e))` from a `try/except Exception as e` back
to the user via `update.message.reply_text(...)`. Attack model:

- Unprivileged caller triggers an exception path (e.g. malformed DB
  state, disk-full, driver quirk).
- The exception text leaks internal schema / module paths / library
  versions / fragment of SQL / stack-relevant substrings into the
  Telegram chat, which may be forwarded, screenshot'd, or indexed.
- Not direct RCE, but useful reconnaissance signal for a later attack
  — e.g. identifying which SQL dialect, which DB driver, which
  environment variable is missing.

Shadow-live has `ADMIN_TELEGRAM_ID` gate on state-mutating commands
(T10.2 Batch 1) — but both leaky sites are read-ish paths that do
**not** hit the admin gate, so any Telegram user who DMs the bot
reaches them.

## Fix

### F1 — `telegram_bot/handlers/force_settle_handler.py:201-208`

**Before:**

```python
try:
    rows = await _fetch_open_rows(db)
except Exception as e:
    logger.exception(f"force_settle fetch_open_rows: {e}")
    return await update.message.reply_text(
        f"❌ Açık pozisyonlar sorgulanırken hata: <code>{esc(str(e))[:200]}</code>",
        parse_mode="HTML")
```

**After:**

```python
try:
    rows = await _fetch_open_rows(db)
except Exception as e:
    # Epic 10 T10.7 (2026-04-22): exception detail loglara yazılır,
    # kullanıcıya generic mesaj döner — DB şeması / tablo isimleri /
    # SQL parçaları Telegram'a sızmasın.
    logger.exception(f"force_settle fetch_open_rows: {e}")
    return await update.message.reply_text(
        "❌ Açık pozisyonlar sorgulanamadı. Detay loglarda.",
        parse_mode="HTML")
```

Logger line unchanged — prod ops retain full visibility on
`logger.exception(...)` with traceback. Only the user-facing text loses
the `esc(str(e))[:200]` substring.

### F2 — `telegram_bot/handlers/ai_handler.py:351-354`

**Before:**

```python
except Exception as e:
    logger.error(f"Brain command error: {esc(str(e))}", exc_info=True)
    await update.message.reply_text(
        f"❌ <b>Brain Hatasi</b>\n\nDetay: {esc(str(e)[:100])}", parse_mode="HTML")
```

**After:**

```python
except Exception as e:
    # Epic 10 T10.7 (2026-04-22): exception detay loglara, kullanıcıya
    # generic mesaj — engine.brain_flags / kelly durumu gibi iç durum
    # string'leri Telegram'a sızmasın.
    logger.error(f"Brain command error: {e}", exc_info=True)
    await update.message.reply_text(
        "❌ <b>Brain Hatasi</b>\n\nDetay loglarda.", parse_mode="HTML")
```

Inner `esc()` on the log line was dropped too (logs don't need HTML
escape and `esc()` was accidental — `exc_info=True` is the source of
truth for ops).

## Why **not** a wider sweep

`grep -n 'esc(str(e))' telegram_bot/handlers/*.py` surfaces ~20 more
sites across 12 handler files. These are **NOT** included in T10.7
because:

1. T10.2 Batch 2 scope was pinned to these exact 2 sites. Expanding
   scope silently is exactly the kind of drift Epic 10 post-audit
   was designed to prevent.
2. The remaining sites are either (a) admin-gated commands (less
   reconnaissance value — attacker must already be admin), or
   (b) diagnostic commands where the user is a debugging operator
   and needs to see the error.
3. A categorical sweep deserves its own design pass — "user-facing
   exception render policy" — which is a good Epic 11 T11.x
   candidate.

Flagged in Epic 11 forward work as **T11.x info-leak sweep** (see
closure memo).

## Verification

- `python -m py_compile telegram_bot/handlers/force_settle_handler.py
  telegram_bot/handlers/ai_handler.py` → OK.
- `python -m pytest tests/unit/test_callback_admin_gate.py` → 10 pass
  (admin gate regression unaffected, expected).
- Visual grep: post-fix `force_settle_handler.py:206` and
  `ai_handler.py:354` no longer contain `esc(str(e))`.

## Related

- T10.2 original audit — docs/security/T10_2_telegram_input_sanitization.md
- Epic 10 closure — `.auto-memory/project_epic10_closure.md`
