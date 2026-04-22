# Epic 10 T10.4 — `.env` ↔ `.env.example` Sync Audit

**Scope:** Windows prod `.env` (active runtime) vs tracked `.env.example`
(template / placeholder doc).
**Tool:** `sort -u` + `comm` diff + regex `os.getenv("…")` codebase scan.
**Date:** 2026-04-22.
**Risk:** LOW (documentation hygiene; no secret leak).

## Executive summary

| Check | Result |
| --- | --- |
| Secret leak in `.env.example` (placeholder-contract) | ✅ **CLEAN** — 0 real API-key-shaped value |
| `.env` keys undocumented in `.env.example` | ⚠️ **4 found → fixed** |
| `.env.example` keys missing from `.env` (OK) | ℹ️ 72 (doc-only / opt-in features) |
| Code `os.getenv` keys never mentioned in `.env.example` | ℹ️ ~150 (runtime-default-safe, optional overrides) — exact: 123 app-scope / 194 full-tree at T10.10 re-scan; snapshot frozen in F4 below, reproducible via script |
| Net closure | ✅ **.env ⊆ .env.example** after fix |

---

## Findings

### ✅ F1 — No placeholder secret leak (CLEAN)

Scanned `.env.example` for real-value shaped secrets:
- LLM API keys: `sk-ant-*`, `sk-or-v1-*`, `gsk_*`, `AIza*` → **0 match**
- Telegram bot token `\d{9,10}:[A-Za-z0-9_-]{35}` → **0 match**
- GitHub PAT / Slack: `ghp_*`, `xox*` → **0 match**
- Non-empty values for sensitive keys: `TELEGRAM_BOT_TOKEN=`,
  `ADMIN_TELEGRAM_ID=`, `ANTHROPIC_API_KEY=`, `POLYMARKET_API_KEY=`,
  `POLYMARKET_PASSPHRASE=` — all verified **empty placeholders**.

T10.1 baseline holds: `.env.example` placeholder-contract intact.

### ⚠️ F2 — 4 undocumented keys in `.env` (fixed)

Active runtime `.env` ran 4 keys that never appeared in the tracked
`.env.example`:

| Key | Active value | Code ref | Fix |
| --- | --- | --- | --- |
| `SURFACE_2D_ENABLED` | `true` | `calibration/surface_2d.py:47`, `core/engine.py:316`, `core/engine_signals.py:967` | Added with doc, default `true` |
| `SURFACE_2D_WEIGHT` | `0.12` | `calibration/surface_2d.py:48` | Added with doc, default `0.12` |
| `SURFACE_2D_CLAMP` | `0.20` | `calibration/surface_2d.py:49` | Added with doc, default `0.20` |
| `EDGE_ZONE_5065_MIN` | `0.45` | `core/engine_signals.py:1192`, `config/env_whitelist.py:69`, `telegram_bot/handlers/filters_handler.py:109` | Added with doc, default `0.45` |

**Fix:** new "2D Surface Calibration" + "Edge Zone Filter" sections added
to `.env.example` (after BECKER CALIBRATOR block, before MAKER REBATE).
Values are the production defaults observed in active `.env`.

### ℹ️ F3 — 72 keys in `.env.example` but not in `.env`

Doc-only / opt-in features that production `.env` doesn't override —
expected and correct:
- Pattern discovery (`PATTERN_DISCOVERY_*`), cascade detection
  (`CASCADE_*`), lag arb (`LAG_ARB_*`), Markov horizon (`MARKOV_*`),
  whale/sentiment boosts (`WHALE_*`, `TRADE_MEMORY_*`), round-number
  bias (`ROUND_NUM_*`), weekend multipliers (`WEEKEND_*`),
  experiment manager (`EXPERIMENT_*`), Sentry observability (`SENTRY_*`).
- These have sensible code-level defaults via
  `os.getenv("KEY", "default")`. Presence in `.env.example` is
  **documentation**, not requirement.

Ilgili şey: çoğu runtime `/env_toggle` whitelist'inde — admin hot-tune
için görünür.

### ℹ️ F4 — ~150 code `os.getenv` keys absent from `.env.example`

**Reproducible scan pipeline (T10.10 post-audit addition, 2026-04-22).**
F4's original "148" was a snapshot count at T10.4 closure; exact number
drifts as new `os.getenv(...)` call sites land. Verification script
below gives the live number. As of the T10.10 re-scan the scope
matrix was:

```bash
# --- full scope (all tracked .py files) ---
# raw occurrences (every call site, including duplicates)
grep -rhE 'os\.getenv\("[A-Z][A-Z0-9_]*"' --include="*.py" | wc -l
#   → 429

# distinct keys across code
grep -rhEo 'os\.getenv\("[A-Z][A-Z0-9_]*"' --include="*.py" \
  | sort -u | wc -l
#   → 327

# distinct keys declared in .env.example
grep -E "^[A-Z][A-Z0-9_]*=" .env.example | cut -d= -f1 | sort -u | wc -l
#   → 202

# distinct code keys NOT declared in .env.example
comm -23 \
  <(grep -rhEo 'os\.getenv\("[A-Z][A-Z0-9_]*"' --include="*.py" \
    | sed 's/os\.getenv("//' | sed 's/"$//' | sort -u) \
  <(grep -E "^[A-Z][A-Z0-9_]*=" .env.example | cut -d= -f1 | sort -u) \
  | wc -l
#   → 194  (includes _archive/, tests/, scripts/)

# --- app-only scope (core/ + telegram_bot/) ---
grep -rhEo 'os\.getenv\("[A-Z][A-Z0-9_]*"' core/ telegram_bot/ \
  --include="*.py" | sort -u | wc -l
#   → 237

comm -23 \
  <(grep -rhEo 'os\.getenv\("[A-Z][A-Z0-9_]*"' core/ telegram_bot/ \
    --include="*.py" | sed 's/os\.getenv("//' | sed 's/"$//' | sort -u) \
  <(grep -E "^[A-Z][A-Z0-9_]*=" .env.example | cut -d= -f1 | sort -u) \
  | wc -l
#   → 123   (app-level F4 count — runtime-default-safe)
```

**Interpretation.** The F4 bucket is "keys the code reads but
`.env.example` doesn't declare". The original 148 was an app-level
snapshot (core + telegram_bot); today the same scope is 123 (the
count shrunk after T10.4 F2 fix added 4 keys and a few `os.getenv`
keys were archived alongside the Epic 7/T7.6 cleanup). Full-tree
scope is 194, inflated by test fixtures and archived scripts that
legitimately reference keys nobody configures through `.env`.

None of this is a security blocker — every F4 key uses the
`os.getenv("KEY", <default>)` pattern with a sensible default, so a
missing `.env` entry falls back to the code default. The Epic 11
`docs/env_reference.md` auto-generator will make this reproducible
and drift-proof.

**Categorized (original T10.4 buckets, still valid):**

1. **Platform-supplied** (no user action): `PORT`, `REPLIT_DOMAINS`,
   `REPLIT_DEV_DOMAIN` — Replit ortamı otomatik set ediyor.
2. **Secret alternatives**: `ADMIN_CHAT_ID` (alias of
   `ADMIN_TELEGRAM_ID`), `OPENROUTER_API_KEY` (alt LLM provider) —
   worth mentioning in `.env.example` comments for visibility, but
   not critical.
3. **Feature flags with safe defaults** (majority): `LIVE_BUDGET`,
   `LIVE_MAX_DAILY_LOSS`, `ROLLING_WR_KILL`, `WS_STALE_SEC`,
   `CLASSIC_RESPECT_FEE_TAIL`, `FUSION_BLOCKED_ZONES`, `WARMUP_MAX_WAIT`,
   etc. — runtime-tunable, `os.getenv(K, default)` pattern, prod `.env`
   rely on defaults.
4. **Jobs / scheduler tuning**: `DB_ARCHIVE_INTERVAL_SEC`,
   `HEARTBEAT_INTERVAL_SEC`, `SHADOW_COMPARE_INTERVAL_SEC`,
   `PATTERN_DISCOVERY_INTERVAL_SEC`, etc. — ops knobs, defaults OK.

**Karar:** Bu ~150 key kitle olarak `.env.example`'a eklemek
**kapsam genişletmesi** olur (T10.4 LOW-risk sınırını aşar). Bunun
yerine:
- F2'deki 4 tane **active-in-prod-without-doc** key hemen fix.
- F4 tamamen "sensible default + opt-in" kategorisinde — Epic 11'de
  formal `docs/env_reference.md` AST-walk generator'u çıkarılabilir
  (backlog) ve bu tablo yeniden üretilebilir hale gelir.

---

## Fix artifact

**Patch (`.env.example`):**
```diff
 ADAPTIVE_BECKER_WEIGHT_ENABLED=false
 ADAPTIVE_MICRO_WEIGHT_ENABLED=false

+# ═════════════════ 2D SURFACE CALIBRATION (Phase 82e Sprint X) ═════════════════
+# 2D probability surface calibration — complements 1D Becker with
+# price × time_to_expiry (or similar 2-axis) empirical surface.
+# See calibration/surface_2d.py.
+SURFACE_2D_ENABLED=true                 # Master toggle (default ON)
+SURFACE_2D_WEIGHT=0.12                  # Weight in signal boost (≈1D)
+SURFACE_2D_CLAMP=0.20                   # Max absolute boost (abs cap)
+
+# ═════════════════ EDGE ZONE FILTER (50-65c zone) ═════════════════
+# Minimum confidence floor for trades in 50-65c zone (flat/chop region).
+# Raise to 0.60+ for stricter signal requirement in mid-zone.
+EDGE_ZONE_5065_MIN=0.45
+
 # ═════════════════ MAKER REBATE (Phase 47f.9) ═════════════════
```

**Post-fix diff:** `comm -23 env_keys example_keys` → **0 lines
(empty)**. `.env` ⊆ `.env.example`.

---

## Verification

```bash
# Re-extract keys from both files
grep -E '^[A-Z][A-Z0-9_]*=' .env         | sed 's/=.*//' | sort -u > env_keys.txt
grep -E '^[A-Z][A-Z0-9_]*=' .env.example | sed 's/=.*//' | sort -u > example_keys.txt

# Assert .env ⊆ .env.example (undocumented-in-prod should be EMPTY)
comm -23 env_keys.txt example_keys.txt
# Expected: no output
```

---

## Forward work (Epic 11)

- **docs/env_reference.md**: per-key doc page — cross-index of all 200+
  `os.getenv` consumers. Auto-generated via AST walk. Kullanıcıya her
  key için (a) default, (b) reader module, (c) runtime-whitelist status
  göster.
- **T11.x** `.env.example` linter: pre-commit hook ki her yeni
  `os.getenv("XXX"` için `.env.example`'da yorumlu bir satır olsun
  (default-doc contract).
- **T11.x** `.env` leak guard: ensure `.env.example` never contains
  realistic-looking secret values (regex match + CI gate) —
  T10.1 static scan'i pre-commit olarak formalize et.
