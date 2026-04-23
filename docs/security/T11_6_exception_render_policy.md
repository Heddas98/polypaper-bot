# T11.6 — User-Facing Exception Render Policy

**Status:** ✅ Closed 2026-04-24 (sandbox)
**Artifact:** `telegram_bot/handlers/_exc_render.py` (helper) + `tests/unit/test_exc_render_policy.py` (9 tests)
**Scope:** All handlers under `telegram_bot/handlers/*.py` that reply to users

## Problem

Before T11.6 (T10.7 partial fix closed 2 sites, left ~13), handlers routinely used:

```python
except Exception as e:
    await update.message.reply_text(
        f"❌ ...: {esc(str(e))}", parse_mode="HTML"
    )
```

This pattern **leaks internal state** to Telegram users (even with HTML escape):

- SQL fragments: `no such table: X`, `UNIQUE constraint failed: table.col`
- File paths: `/home/heddas/.../data_store/polypaper.db`
- Stack context: `'NoneType' object has no attribute 'connection'`
- Schema/internal typo hints: `KeyError: 'strategy_label'`

Info leak is real even when users are "just" admins — debug accidentally
left on, screen-share, forwarded screenshots etc.

## Policy (default behaviour)

**User-facing exception messages must NOT contain `str(exception)`** unless
the operator opts in via `DEBUG_SHOW_EXC=true` at runtime.

The helper `render_user_exception(exc, prefix=None)` enforces this:

```python
from telegram_bot.handlers._exc_render import render_user_exception

try:
    do_risky()
except Exception as e:  # noqa: BLE001
    logger.exception("context label for server-side diagnosis")
    await update.message.reply_text(
        render_user_exception(e, "❌ EV stats hatası"),
        parse_mode="HTML",
    )
```

### Behaviour matrix

| `DEBUG_SHOW_EXC` | User sees |
|---|---|
| unset / `false` (default prod) | `❌ EV stats hatası — beklenmeyen hata (type=<code>ValueError</code>)` |
| `true` (admin diagnostic) | `❌ EV stats hatası — <code>ValueError: <escaped str(e)[:200]></code>` |

### Runtime tune

`DEBUG_SHOW_EXC` is NOT in the `/envt` whitelist by design (forward-work
decision: policy leans toward disabling even admin-level leak in prod).
Operators flip it via `.env` edit + bot restart if truly needed for
live debugging. Normal workflow relies on `logger.exception()` on the
server side (full traceback + request context).

## Scope (what changed in T11.6)

11 sites refactored to use the helper:

| File | Sites | Context |
|------|-------|---------|
| `telegram_bot/handlers/roadmap_handler.py` | 4 | `/ev_stats`, `/metrics`, `/surface`, `/latency` |
| `telegram_bot/handlers/backtest_v2.py` | 5 | `/becker_replay`, `/becker_deep`, `/becker_zones` |
| `telegram_bot/handlers/archive_info_handler.py` | 1 | `/archive_info` (ImportError branch) |
| `telegram_bot/handlers/strategy_report.py` | 1 | `/strategy_report` (DB branch) |

## Exemptions

**2 sites exempt from the generic policy** (documented with inline comment):

| File | Site | Rationale |
|------|------|-----------|
| `telegram_bot/handlers/env_toggle.py` L188 | `_apply_set()` .env write fail | Admin-only command + operator must distinguish permission vs disk full vs locked file to resolve. Truncated to `[:120]`. |
| `telegram_bot/handlers/env_toggle.py` L209 | `_apply_reset()` .env write fail | Same rationale as `_apply_set`. |

Exemption rule: **admin-only + file-I/O error + operator MUST know the
concrete OS error class to resolve**. Still truncated to limit leak surface.

Future exemption additions require:
1. Inline `# T11.6 policy exemption: <reason>` comment at the site
2. Update this policy doc's Exemptions table
3. Truncation to 120-200 chars
4. Admin-only context (admin gate must be enforced upstream)

## Enforcement (forward work)

T11.8 `scripts/bare_except_check.py` does NOT currently scan for
`esc(str(e))` pattern. Adding a similar grep check as a T11.8-C
follow-up would lock this policy at pre-commit time.

Suggested regex: `reply_text\(.*esc\(str\(e[0-9]*\)\).*\)`
Exception: lines with `# T11.6 policy exemption:` comment.

## Tests

`tests/unit/test_exc_render_policy.py` — 9 tests:

1. Default mode hides `str(e)` content (sensitive path leak regression)
2. Default mode no-prefix → generic warning line
3. Debug mode shows `str(e)` truncated to 200 chars
4. Truncation cap enforced
5. HTML escape on type name
6. HTML escape on debug body (script/quote injection block)
7. Runtime env re-read (T6.1 parity — `/envt DEBUG_SHOW_EXC` flips behaviour)
8. Prefix preserved at start
9. Empty prefix falls back to generic warning

All 9 PASS (2026-04-24 sandbox).

## References

- T10.7 (partial fix, 2 sites closed 2026-04-22): `docs/security/T10_7_*.md`
- T6.1 runtime re-read doctrine: `project_t61_pnl_pause_runtime.md`
- T1.4 narrow exception pattern: Epic 1 T1.4 Faz 1
- T7.6 noqa BLE001 escape pattern: Epic 7 T7.6 Aşama A closure
