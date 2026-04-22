# T10.1 — Log + Git History Secret Leak Scan

**Date:** 2026-04-22
**Epic:** 10 (Security Pass)
**Risk classification:** HIGH
**Result:** ✅ **CLEAN** — no secret leak detected

## Scope

Pre-mainnet security pass. Target surfaces:

1. **Tracked files in HEAD** — all `.py`, `.md`, `.yml`, `.json`, `.txt`
   under version control.
2. **Git history** — `git log -p --all` across all branches and tags.
3. **Ephemeral filesystem** — `logs/`, `reports/`, `backups/` (gitignored
   but may contain runtime data).
4. **`.env` vs `.env.example` hygiene** — template vs real values.

## Patterns scanned

| Pattern | Provider | Example shape |
|---|---|---|
| `sk-ant-[A-Za-z0-9_-]{20,}` | Anthropic | `sk-ant-api03-...` |
| `sk-or-v1-[A-Za-z0-9_-]{20,}` | OpenRouter | `sk-or-v1-abc123...` |
| `gsk_[A-Za-z0-9]{20,}` | Groq | `gsk_1234...` |
| `AIza[A-Za-z0-9_-]{30,}` | Google / Gemini | `AIzaSy...` |
| `\d{9,10}:[A-Za-z0-9_-]{35}` | Telegram bot | `123456789:ABC...` |
| `0x[a-fA-F0-9]{64}` | Ethereum priv key | `0xabc...` (64 hex) |
| `.env`, `*.key`, `credentials` | sensitive filenames | (directly tracked?) |
| Explicit `api_key=<long>` / `secret=<long>` lines | generic | any provider |

## Findings

### Tracked files (HEAD)
All 6 LLM/crypto patterns: **0 matches**. Checked:
- `core/`, `telegram_bot/`, `scripts/`, `tests/`, `data/`, `docs/`,
  `config/`, repo root `.py`/`.md`/`.yml`/`.txt`/`.json`.

### Git history (all branches, all commits)
- `git log -p --all` across 6 secret patterns: **0 matches**
- Filename filter (`.env`, `*.key`, `credentials.json`): **0 hits** —
  no sensitive file was EVER added, even briefly.

### Ephemeral filesystem
- `logs/watchdog.log` — **clean** (single runtime log; no tokens).
- `reports/becker_deep_analysis.html` — **clean**.
- `backups/polypaper_pre_phase82b_2026 0Fr.db` — SQLite binary; pattern
  scan across text pattern: no match.

### `.env` vs `.env.example` hygiene
- `.env` is gitignored (verified via `git check-ignore .env`).
- `.env` was NEVER committed (`git ls-files | grep '^.env$'` returns
  empty; history diff-filter=A shows no `.env` addition).
- `.env.example` IS tracked (via `!.env.example` allowlist in
  `.gitignore` line 6), contains only placeholder values:
  - `TELEGRAM_BOT_TOKEN=` (empty)
  - `ANTHROPIC_API_KEY=` (empty)
  - `POLYGON_PRIVATE_KEY=` (empty, annotated "64-hex, NEVER share")
  - `POLYMARKET_API_KEY=` / `POLYMARKET_API_SECRET=` /
    `POLYMARKET_PASSPHRASE=` (all empty)
  - No suspiciously long non-comment values detected.

### `.gitignore` coverage
Sensitive-surface patterns covered:
- `.env` ✓
- `.env.*` ✓ (with `!.env.example` allowlist)
- `*.key` ✓
- `secrets/` ✓
- `*.db` ✓
- `backups/` ✓
- `logs/` ✓
- `reports/` ✓

## Conclusion

**No secret leak detected.** Mainnet readiness check: PASS.

No rotation or `git filter-repo` cleanup required. `.env.example` is
a clean template with placeholder-only values. All ephemeral runtime
directories are gitignored, and the working-tree copies of those
directories contain no API keys or private keys.

## Doctrine / preventive measures already in place

1. **`.env.*` glob + `!.env.example` allowlist** — new env vars
   automatically inherit the ignore; only the explicit allowlist
   exception leaks through.
2. **Dedicated `secrets/` directory** for any future credential files.
3. **No `*.db` / `*.key` ever tracked** — paper-trading DB lives in
   gitignored `backups/` only.

## Recommended forward steps (Epic 11 T11.4 CI gate)

- Add [`detect-secrets`](https://github.com/Yelp/detect-secrets) or
  [`gitleaks`](https://github.com/gitleaks/gitleaks) pre-commit hook.
  Baseline secrets file committed; any NEW detection fails the hook.
- Add the same scan to CI as a separate job — PR fails on regex match.
- This is already tracked in TASKS.md T11.4 "Coverage CI gate +
  pre-commit hook" as a sibling to the coverage ratchet.

## Verification commands (for Windows re-run)

```bash
# Tracked-files scan
git grep -E "sk-ant-|sk-or-v1-|gsk_[A-Za-z0-9]{20}|AIza[A-Za-z0-9_-]{30}"
git grep -E "\b[0-9]{9,10}:[A-Za-z0-9_-]{35}\b"
git grep -E "0x[a-fA-F0-9]{64}"

# Git history scan
git log --all -p | grep -E "sk-ant-|sk-or-v1-|gsk_"
git log --all --diff-filter=A --name-only | grep -E "^\.env$|\.key$"

# Ephemeral filesystem
grep -rE "sk-ant-|sk-or-v1-|gsk_" logs/ reports/ backups/ 2>/dev/null
```

All should return empty (as of 2026-04-22 T10.1 closure).
