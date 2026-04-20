"""
Smoke test for Phase 82e Sprint 6 — /env_toggle.

Runs in process (no bot startup). Exercises:
  1. config.env_whitelist imports + coerce_value truth table
  2. handler module imports
  3. _patch_env_file round-trip on a temp .env
  4. _apply_set / _apply_reset mutate os.environ correctly
  5. bot.py has the import + cmds entry
  6. Unknown-key rejection
  7. Non-admin path (mocked Settings returning False)

ASCII-only stdout (Windows cp1252 safe). Exit 0 on success.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def ok(msg: str) -> None:
    print(f"OK  : {msg}")


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


# ── 1: whitelist import + coerce_value truth table ───────────────────
from config.env_whitelist import (  # noqa: E402
    ENV_WHITELIST, coerce_value, list_groups)

assert "CLASSIC_TAKER_LIMIT_CEIL" in ENV_WHITELIST
assert "TAKER_STUCK_TIMEOUT_SEC" in ENV_WHITELIST
ok(f"whitelist loaded, {len(ENV_WHITELIST)} keys across "
   f"{len(list_groups())} groups")

cases = [
    # (key,                          raw,     expect_ok, expected_coerced)
    ("CLASSIC_BYPASS_ALL_GATES",     "true",  True,      "true"),
    ("CLASSIC_BYPASS_ALL_GATES",     "FALSE", True,      "false"),
    ("CLASSIC_BYPASS_ALL_GATES",     "on",    True,      "true"),
    ("CLASSIC_BYPASS_ALL_GATES",     "maybe", False,     None),
    ("CLASSIC_TAKER_LIMIT_CEIL",     "0.95",  True,      "0.95"),
    ("CLASSIC_TAKER_LIMIT_CEIL",     "1.5",   False,     None),   # above max
    ("CLASSIC_TAKER_LIMIT_CEIL",     "-0.1",  False,     None),   # below min
    ("CLASSIC_TAKER_LIMIT_CEIL",     "abc",   False,     None),
    ("TAKER_STUCK_TIMEOUT_SEC",      "60",    True,      "60"),
    ("OPTIMISM_TAX_TICKS",           "3",     True,      "3"),
    ("OPTIMISM_TAX_TICKS",           "99",    False,     None),
    ("PNL_PAUSE_THRESHOLD",          "-5",    True,      "-5"),
    ("PNL_PAUSE_THRESHOLD",          "5",     False,     None),   # above max=0
    ("NOT_IN_WHITELIST",             "x",     False,     None),
]
for key, raw, exp_ok, exp_val in cases:
    got_ok, got_val, err = coerce_value(key, raw)
    if got_ok != exp_ok:
        fail(f"coerce {key} {raw!r}: ok={got_ok} expected={exp_ok} err={err}")
    if exp_ok and got_val != exp_val:
        fail(f"coerce {key} {raw!r}: val={got_val} expected={exp_val}")
    ok(f"coerce {key}={raw!r} -> ok={got_ok} val={got_val}")

# ── 2: handler module imports ────────────────────────────────────────
import importlib  # noqa: E402
mod = importlib.import_module("telegram_bot.handlers.env_toggle")
for fn in ("env_toggle_command", "_is_admin", "_apply_set",
           "_apply_reset", "_patch_env_file", "_audit", "_format_list",
           "_format_detail"):
    if not hasattr(mod, fn):
        fail(f"handler missing symbol: {fn}")
ok("handler module imports + exposes expected symbols")

# ── 3: _patch_env_file round-trip on temp .env ───────────────────────
with tempfile.TemporaryDirectory() as td:
    tmp_env = Path(td) / ".env"
    tmp_env.write_text(
        "# header\n"
        "FOO=1\n"
        "CLASSIC_TAKER_LIMIT_CEIL=0.88\n"
        "BAR=x\n",
        encoding="utf-8")
    # Redirect the handler to our temp .env for this block.
    mod._ENV_PATH = tmp_env
    # update an existing key
    mod._patch_env_file("CLASSIC_TAKER_LIMIT_CEIL", "0.95")
    txt = tmp_env.read_text(encoding="utf-8")
    if "CLASSIC_TAKER_LIMIT_CEIL=0.95" not in txt:
        fail(f"patch update failed; file={txt!r}")
    if "CLASSIC_TAKER_LIMIT_CEIL=0.88" in txt:
        fail("old value still present after patch")
    ok("_patch_env_file: updates existing key")
    # append a new key
    mod._patch_env_file("TAKER_STUCK_TIMEOUT_SEC", "90")
    txt = tmp_env.read_text(encoding="utf-8")
    if "TAKER_STUCK_TIMEOUT_SEC=90" not in txt:
        fail("patch append failed")
    ok("_patch_env_file: appends new key")
    # remove a key (reset)
    mod._patch_env_file("TAKER_STUCK_TIMEOUT_SEC", None)
    txt = tmp_env.read_text(encoding="utf-8")
    if "TAKER_STUCK_TIMEOUT_SEC=" in txt:
        fail("patch remove failed")
    ok("_patch_env_file: removes key on value=None")

# ── 4: _apply_set / _apply_reset mutate os.environ ───────────────────
# Use a temp .env again to avoid touching project state.
with tempfile.TemporaryDirectory() as td:
    tmp_env = Path(td) / ".env"
    tmp_env.write_text("", encoding="utf-8")
    mod._ENV_PATH = tmp_env
    mod._AUDIT_PATH = Path(td) / "audit.log"

    # Guard: snapshot current os.environ for CLASSIC_TAKER_LIMIT_CEIL
    key = "CLASSIC_TAKER_LIMIT_CEIL"
    before = os.environ.get(key)

    okf, msg = mod._apply_set(key, "0.90", admin_id=12345)
    if not okf:
        fail(f"_apply_set returned not-ok: {msg}")
    if os.environ.get(key) != "0.9":
        fail(f"os.environ not updated: got {os.environ.get(key)!r}")
    ok("_apply_set updates os.environ")

    # audit log written
    audit = (Path(td) / "audit.log").read_text(encoding="utf-8")
    if "admin=12345" not in audit or "SET" not in audit:
        fail(f"audit log missing entry; got={audit!r}")
    ok("_apply_set writes audit line")

    # reset
    okf, msg = mod._apply_reset(key, admin_id=12345)
    if not okf:
        fail(f"_apply_reset returned not-ok: {msg}")
    if os.environ.get(key) != str(ENV_WHITELIST[key]["default"]):
        fail(f"reset did not restore default: got {os.environ.get(key)!r}")
    ok("_apply_reset restores default in os.environ")

    # restore environment
    if before is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = before

# ── 5: bot.py wiring ─────────────────────────────────────────────────
bot_src = (ROOT / "telegram_bot" / "bot.py").read_text(
    encoding="utf-8", errors="replace")
if "env_toggle_command" not in bot_src:
    fail("bot.py missing env_toggle_command import/register")
if '"env_toggle"' not in bot_src:
    fail('bot.py missing ("env_toggle", env_toggle_command) entry')
if '"envt"' not in bot_src:
    fail('bot.py missing /envt alias')
ok("bot.py wires /env_toggle + /envt alias")

# ── 6: unknown key rejection ─────────────────────────────────────────
ok6, val6, err6 = coerce_value("WEIRD_FAKE_KEY", "x")
if ok6:
    fail("unknown key should be rejected")
ok(f"unknown key rejected: {err6}")

# ── 7: non-admin rejection path (simulated) ─────────────────────────
class _FakeSettings:
    def is_admin(self, _id):
        return False


class _FakeCtx:
    bot_data = {"settings": _FakeSettings()}


ctx = _FakeCtx()
if mod._is_admin(ctx, 999):
    fail("_is_admin should reject non-admin user")
ok("_is_admin rejects non-admin")

# ── 8: admin accept path ─────────────────────────────────────────────
class _AdminSettings:
    def is_admin(self, _id):
        return True


class _AdminCtx:
    bot_data = {"settings": _AdminSettings()}


if not mod._is_admin(_AdminCtx(), 111):
    fail("_is_admin should accept admin user")
ok("_is_admin accepts admin")

print("\nALL OK")
sys.exit(0)
