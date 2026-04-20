# Secrets Rotation Runbook (Phase 48, updated Phase 49)

Last reviewed: 2026-04-09 (Phase 49 CRITICAL finding added)

## ⚠️ Phase 49 A-01: stale Polymarket triplet detected

The `POLYMARKET_API_KEY` / `API_SECRET` / `PASSPHRASE` currently in `.env`
**does not correspond** to `POLYGON_PRIVATE_KEY` / `POLYGON_WALLET`.
Running `client.create_or_derive_api_creds()` against the current private key
yields a different `api_key` than what is stored in `.env`, meaning the
stored triplet was either left over from an earlier wallet or was derived
incorrectly.

**Impact**: `live_trader.py` up to Phase 49 used the stored triplet verbatim,
so any live `post_order` call would have hit `401 invalid api key` on the
first attempt. Phase 49 patches `live_trader.py` to derive-and-cache L2
creds from the private key at startup, with verification, before enabling.
See `core/live_trader.py::_derive_and_verify_sync`.

**Action items**:
1. Run the rotation procedure below (section "Polymarket CLOB creds") so the
   stored triplet matches the wallet on disk. Not strictly required for the
   Phase 49 fix (derive path supersedes stored), but keeps the fallback
   branch correct for operators reading `.env` manually.
2. Confirm `POLYGON_WALLET` matches the address that actually holds the
   $1.49 USDC balance. If not, decide whether to fund the correct wallet
   or switch `.env` to the funded wallet's private key.
3. Never enable `LIVE_ENABLED=true` until `/live_status` reports
   `auth_verified=true`.

## Inventory

| Secret | Blast radius if leaked | Rotation cost | Target cadence |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Attacker can impersonate the bot, read all admin DMs | Low (BotFather `/revoke`) | Quarterly |
| `ANTHROPIC_API_KEY` | Uncapped spend, rate-limit exhaustion | Low (console revoke + issue) | Quarterly |
| `GROK_API_KEY` | Same as above, lower impact (fallback) | Low | Quarterly |
| `POLYMARKET_API_KEY` / `API_SECRET` / `PASSPHRASE` | Read/write on the tied wallet via CLOB API | Medium (regenerate via CLOB Python client) | **Monthly** while LIVE_ENABLED=true |
| `POLYGON_PRIVATE_KEY` | **Total wallet drain.** Single highest-value secret. | High (new wallet, move USDC, update CLOB ApiCreds) | **Immediately on any suspicion** |
| `POLYGON_WALLET` | Address is public on-chain, not a secret | — | — |
| `POLYBACKTEST_API_KEY` | Rate-limit exhaustion on backtest API | Low | Quarterly |
| `ADMIN_TELEGRAM_ID` | Not a secret (numeric ID), but remove from public repos | — | — |

## Rotation procedure

### Telegram bot token
1. Message `@BotFather` → `/revoke` → pick `@PolyPaperBot`.
2. Copy the new token.
3. Edit `.env`: set `TELEGRAM_BOT_TOKEN=<new>`.
4. Double-click `claude_restart.bat`.
5. Verify `/health` works from admin chat within 30s.

### Anthropic / Grok / PolyBackTest API keys
1. In provider console: revoke old key, issue new.
2. Update `.env`, restart bot.
3. Watch next AI Brain cycle for `429` or `401` in `bot_restart.log`.

### Polymarket CLOB creds (api_key / secret / passphrase)
1. Set `LIVE_ENABLED=false` in `.env` first. Restart.
2. In a Python REPL:
   ```python
   from py_clob_client.client import ClobClient
   c = ClobClient("https://clob.polymarket.com", key=<PK>, chain_id=137)
   c.set_api_creds(c.create_or_derive_api_creds())
   print(c.get_api_creds())
   ```
3. Copy `api_key`, `api_secret`, `api_passphrase` into `.env`.
4. `LIVE_ENABLED=true`, restart, verify a `/live_status` dry check.

### Polygon private key (EMERGENCY)
1. **Do not restart the bot yet** — finish these steps first.
2. In MetaMask or ethers.js: create a fresh EOA, note address + PK.
3. Transfer USDC (and any MATIC for gas) from the old wallet to the new wallet.
4. Follow the CLOB creds rotation procedure above **on the new wallet**.
5. Update `.env`: `POLYGON_WALLET` and `POLYGON_PRIVATE_KEY`.
6. Restart; verify balance in `/live_status`.

## Hygiene checklist

- [ ] `.env` is in `.gitignore` (verified 2026-04-09).
- [ ] `.env.example` contains every key the code reads, with blank values.
- [ ] `.env` file perms are `600` / `700` (owner-only).
- [ ] No secret ever pasted into Telegram chat, README, screenshots, or log files.
- [ ] `grep -r "sk-ant\|0x[a-f0-9]\{64\}" .` returns zero hits on code (only in `.env`).
- [ ] Replit Secrets tab is in sync with local `.env` (if bot also runs on Replit).
- [ ] After any rotation, old logs containing the *old* secret are deleted or redacted.

## Compromise response

If you suspect a secret is leaked (accidental commit, screenshot, shared screen):

1. **Rotate immediately** — don't wait to confirm. Faster than reversing damage.
2. For `POLYGON_PRIVATE_KEY`: transfer all funds in the same minute.
3. Check bot logs for unexpected activity since the suspected leak window.
4. File a short incident note in `docs/INCIDENTS/YYYY-MM-DD_<topic>.md` with timeline and actions taken.

## Automation gap (tracked in Phase 48 gap analysis)

Rotation is manual today. Candidates for future work:
- HashiCorp Vault or 1Password Secrets Automation for fetch-at-startup.
- Sentry-style deploy-time secret scanner on commit hook.
- Prometheus alert for `401/403` spikes suggesting a revoked key in the wild.
