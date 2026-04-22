# T10.2 — Telegram Input Sanitization + Authorization Audit

**Date:** 2026-04-22
**Epic:** 10 (Security Pass)
**Risk classification:** HIGH
**Scope:** 28 `telegram_bot/handlers/*.py` + `telegram_bot/bot.py` + `telegram_bot/jobs/*.py`
**Result:** 🟡 **3 CRITICAL authorization gaps + 2 LOW info-leak** — all fixable before mainnet

## Summary

| Severity | Count | Surface |
|---|---:|---|
| 🔴 **CRITICAL** — missing admin/ownership gate on state-mutating callback | 3 | `filters_callback`, `brain_toggle_callback`, `strategies.start/stop/delete_*` |
| 🟢 **LOW** — exception-to-user info disclosure | 2 | `force_settle_handler:206`, `ai_handler:351-353` |

**Attack model.** There is no global admin filter on `Application` (bot.py:801 `start_polling(allowed_updates=ALL_TYPES)`). Per-handler admin checks are enforced by each command individually. Any Telegram user who DMs the bot can send callback_query payloads — if the handler accepts the pattern without rechecking `effective_user.id`, state mutation succeeds regardless of admin identity.

## Safe patterns observed first (baseline is solid)

1. **Admin gate is uniform on privileged `*_command` functions** using
   a consistent `_is_admin()` helper or inline check:
   - `force_settle_handler.py:40-46` `_is_admin()`
   - `env_toggle.py:47-54` inline
   - `lifecycle_handler.py:18-22` `_is_admin()`
   - `filters_handler.py:368-371` inline
   - `hyperopt_handler.py`, `phase77_handler.py`, `roadmap_handler.py` — all gated
2. **HTML escape via `esc()` helper** (from
   `telegram_bot/templates/safe_html.py`) is systematically applied to
   every `reply_text(parse_mode="HTML", …)` with user-supplied text
   (verified in `backtest_v2.py:166`, `194`; `strategies.py:992`;
   `strategy_tester.py:69`; `force_settle_handler.py:206`).
3. **SQL parameterization is correct** across all 22 handler files
   that touch `aiosqlite`. Placeholders `?` with tuple args
   everywhere — zero f-string/%/concat injection vectors found.
4. **Callback data parsing uses static-key lookups** — no dynamic
   eval, no SQL injection through `callback_data`. Format:
   `backtest_v2.py:242` — `coin = data[10:]` validated against
   `AVAILABLE_ASSETS` set.
5. **ConversationHandler text input validated** at the state boundary
   (`strategy_builder.py:435-494` — float/int coercion in try/except,
   invalid input rejected before DB write).

## 🔴 CRITICAL — Missing admin/ownership gate on state-mutating callbacks

### [C1] `filters_callback` — runtime filter mutation without admin check

- **File:** `telegram_bot/handlers/filters_handler.py:378-463`
- **Registration:** `telegram_bot/bot.py:421`
  `CallbackQueryHandler(filters_callback, pattern="^flt:")`
- **Mutations:**
  - L410-412 `preset` — writes 3 whitelisted presets to `os.environ`
    + DB
  - L431-432 `toggle` — flips bool filter → `os.environ` + DB
  - L448-449 `cycle` — advances enum filter → `os.environ` + DB
- **Contrast:** `filters_command` (L365-371) HAS the admin gate:
  ```python
  admin_id = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_CHAT_ID")
  if admin_id and str(update.effective_user.id) != str(admin_id):
      await update.message.reply_text("⛔ Bu komut sadece admin için.")
      return
  ```
  The callback sibling silently omits it.
- **Exploit:** Non-admin Telegram user can flip `PARITY_GATE`,
  `SMART_EXIT`, `CONVICTION_MIN` via raw callback_query — filter state
  governs shadow-live trade gating.
- **Fix:** Paste admin gate at `L382` (after `await query.answer()`,
  before any state read):
  ```python
  admin_id = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_CHAT_ID")
  if admin_id and str(update.effective_user.id) != str(admin_id):
      await query.answer("⛔ Admin only", show_alert=True)
      return
  ```

### [C2] `brain_toggle_callback` — brain flag mutation without admin check

- **File:** `telegram_bot/handlers/ai_handler.py:356-428`
- **Registration:** `telegram_bot/bot.py:374`
  `CallbackQueryHandler(brain_toggle_callback, pattern=f"^{pattern}")`
  for `["brain_toggle", "brain_refresh"]`.
- **Mutations:**
  - L399-402 `kelly_sizing` — writes `engine._kelly_mode` + DB
    `engine.kelly_mode`
  - L404-406 other features — writes `engine.brain_flags[feature]` +
    DB `brain_flags.<feature>`
  - L408-415 side-effect enable/disable on
    `engine.candle_collector` / `engine.market_recorder`
- **Valid feature set** (L384-388): `ai_brain`,
  `thompson_sampling`, `regime_detection`, `autopilot`,
  `kelly_sizing`, `candle_collector`, `market_recorder` — all
  governing live trading decisions.
- **Exploit:** Non-admin Telegram user can disable `ai_brain` (core
  strategy), toggle `autopilot`, or flip Kelly sizing — immediate
  impact on shadow-live and paper engines.
- **Fix:** Insert admin gate at L358 (after `query = ...`):
  ```python
  admin_id = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_CHAT_ID")
  if admin_id and str(update.effective_user.id) != str(admin_id):
      await query.answer("⛔ Admin only", show_alert=True)
      return
  ```

### [C3] Strategy callbacks — no admin AND no ownership check

- **Files:** `telegram_bot/handlers/strategies.py`
  - `start_strategy_callback` L141-149
  - `stop_strategy_callback` L152-160
  - `delete_strategy_callback` L163-217 (2-step confirm)
  - `start_all_callback` L220-237
  - `stop_all_callback` L238-261
- **Registration:** `telegram_bot/bot.py:456-458` +
  `349-350`
- **Mutations:**
  ```python
  # start_strategy_callback L143-145
  sid = q.data.replace("start_strat_", "")
  await db.update_strategy_status(sid, StrategyStatus.ACTIVE)
  ```
  `sid` is taken verbatim from `callback_data` — no check that
  `effective_user.id` owns `sid`.
- **Exploit:**
  1. Non-admin user DMs bot → can craft `start_strat_<any_uuid>`,
     `stop_strat_<any_uuid>`, `delete_strat_confirm_<any_uuid>`
     callback payloads if they can guess/enumerate strategy IDs.
  2. Even the **admin's** callback is vulnerable: if the admin
     shared a screenshot, or `delete_strat_confirm_<sid>` leaked to
     any party, that party can delete the admin's strategies.
  3. `start_all_callback` / `stop_all_callback` affect the
     caller's own strategies (via `user_by_telegram_id`), so the
     risk is lower but still lets a non-admin user manipulate their
     own session against the admin's intent.
- **Fix options:**
  - **Option A (preferred — admin-only bot):** Add admin gate to
    all 5 callbacks identical to C1/C2 fix.
  - **Option B (multi-tenant future):** Verify ownership via
    `await db.get_strategy(sid)` → `strategy.user_id ==
    effective_user_id_mapped`. Add once bot supports >1 user.
  - **Current single-admin deployment → go with Option A.**

## 🟢 LOW — Exception message leaks to user

### [L1] `force_settle_handler.py:206` — exception str echoed in HTML

```python
logger.exception(f"fetch_open_rows error: {e}")
return await update.message.reply_text(
    f"❌ Açık pozisyonlar sorgulanırken hata: <code>{esc(str(e))[:200]}</code>",
    parse_mode="HTML")
```

**Issue:** Full exception may include SQL fragment, file path, or
aiosqlite internal details. `esc()` prevents HTML/XSS, but info
disclosure remains (max 200 chars). Mainnet acceptance: minor.

**Fix:** Keep server log, replace user-facing message with generic:
```python
logger.exception(f"fetch_open_rows error: {e}")
return await update.message.reply_text(
    "❌ Açık pozisyonlar sorgulanamadı — admin log kontrolü gerekli.",
    parse_mode="HTML")
```

### [L2] `ai_handler.py:351-353` — brain_command exception disclosed

Same pattern as L1, ai-brain context. Apply same fix.

## Things explicitly scanned and clean

- **ReDoS:** `grep -rE "re\.compile|re\.search|re\.match"
  telegram_bot/` → 0 catastrophic-backtracking patterns. All
  patterns are literal prefix matches or simple char classes.
- **SQL parameterization:** `grep -rE 'execute\(f"|execute\("%|
  execute\(.*\+'` across handlers → 0 hits. All 100+ `execute()`
  calls use `?` placeholders.
- **`shell=True`:** 0 matches in the handler/jobs tree.
- **`eval` / `exec`:** 0 matches in the handler/jobs tree.
- **`os.system`:** 0 matches.
- **JSON parse of user text without try:** `json.loads(update.message.text)`
  pattern scan → 0 unprotected uses.

## Forward work — fix order + forward-test plan

**Batch 1 (CRITICAL, 3 callbacks, ~15 min + tests):**

1. `filters_callback` — admin gate (C1)
2. `brain_toggle_callback` — admin gate (C2)
3. 5 strategy callbacks — admin gate (C3)
4. Each fix paired with a regression test under `tests/unit/`:
   - `test_callback_admin_gate.py` — parametrized over the 7
     callbacks. Non-admin caller → `query.answer("⛔ …")` + no DB
     write. Admin caller → normal flow.

**Batch 2 (LOW, 2 error paths):**

5. `force_settle_handler:206` — generic user message
6. `ai_handler:351-353` — generic user message

**Batch 3 (defense in depth, optional for mainnet):**

7. Consider adding a global `filters.User(allowed_admin_ids)` or
   `TypeHandler(Update, _admin_precheck)` at Application-level so
   per-handler gates become defense-in-depth rather than the only
   line of defense. Deferred to Epic 11.
8. `detect-secrets` pre-commit hook also flags Telegram tokens in
   exception messages (tied to T11.4).

## Verification commands

```bash
# Callback admin gate coverage
git grep -n "async def .*callback" telegram_bot/handlers/ | while read line; do
  f=$(echo $line | cut -d: -f1)
  fn=$(echo $line | grep -oP 'async def \K[a-z_]+')
  # crude: check next 10 lines for ADMIN check
  grep -A 10 "async def $fn" "$f" | head -10 | grep -q "ADMIN" && echo "OK  $f :: $fn" || echo "⚠️  $f :: $fn"
done

# SQL param sanity (should return 0)
grep -rE 'execute\s*\(\s*f"' telegram_bot/ | grep -v "\.bak"
grep -rE 'execute\s*\(\s*".*"\s*\+' telegram_bot/ | grep -v "\.bak"

# ReDoS-risk regex (should return 0)
grep -rE 'compile\(r?"[^"]*\(\.\*\)\+|\(\\w\+\\s\?\)\+|\(\.\+\)\+' telegram_bot/
```

## Conclusion

Baseline is **solid**: HTML escape + SQL param + admin-gated
commands + static callback parsing. The gap is **callback
authorization** — 3 callback families lack the admin check their
command siblings enforce.

Pre-mainnet: Batch 1 is **required**. Batch 2 is nice-to-have. Batch
3 is Epic 11.
