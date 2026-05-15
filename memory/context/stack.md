# Tech Stack & Process

## Runtime

- **Python 3.11** (Windows 10/11 lokal, `py -3.11`)
- **.venv** virtualenv
- **Telegram bot** entrypoint: `py -3.11 -m telegram_bot.bot`
- Linux/Docker = P1-05 roadmap

## Polymarket SDK Stack (V2)

| Bileşen | Sürüm |
|---------|-------|
| `py-clob-client-v2` | 1.0.0 (pinned) |
| `py-builder-relayer-client` | 0.0.1 (pin P0-06 forward work) |
| WSS endpoint | `wss://ws-subscriptions-clob.polymarket.com/ws/market` |
| Subscribe flag | `custom_feature_enabled: true` |
| WSS events | `book`, `price_change`, `tick_size_change`, `last_trade_price`, `new_market`, `market_resolved` |
| pUSD allowance | 3-contract (pUSD → CTF + CTF Exchange + Neg Risk) gasless via Relayer |

## AI Stack

| LLM | Rol | Provider |
|-----|-----|----------|
| Claude Sonnet 4.6 | Critic (risk + downside) | Anthropic API |
| Groq Llama 70B | Optimist (bullish bias) | Groq API |
| **Budget** | $15 hard cap | 429 cooldown guard |

Default `AI_ADVISOR_ENABLED=false` → in-process AI Brain. Opt-in için `AI_ADVISOR_ENABLED=true` + `scripts\start_ai_advisor.bat` → FastAPI HTTP'a delege.

## Storage

- **SQLite + WAL** (`db/migrations/` versioned schema, current ~v21)
- PostgreSQL = P1-08 forward work (deep dive doc'u var)
- DB backups: `data_store/backups/` + `manifest.json` + SHA256 (P0-05)
- Restore CLI: `scripts/restore_from_backup.py --list/--verify-all/--latest/--restore`

## Test Stack

- pytest + pytest-cov
- 3-seed deterministic replay (42 / 1337 / 9001)
- `tests/unit/` + `tests/integration/` (integration marker filtreli)
- Coverage `.coveragerc` source = core + data + telegram_bot + backtest
- `fail_under = 43` lock (ratchet ladder)

## Linting / Type

- `ruff check` 0 violation (`--unsafe-fixes` clean)
- `mypy --strict` 0 hata (55 source file, services/ dahil)
- Bare-except policy: 0 strict zone (`core/`), 0 advisory zone (data/telegram_bot/db) — hepsi narrow ya da `# noqa: BLE001` documented
- `.githooks/pre-commit` + CI integration

## CI/CD

- `.github/workflows/ci.yml`
- pytest + `--cov=core --cov-fail-under=21` step
- pytest-cov install + coverage artifact upload
- `bare_except_check.py` + `gen_env_reference.py --check` drift guard

## Observability

- **Sentry**: env-gated custom transactions on `engine.cycle`, `ai_brain.advise`, `live_trader.execute_buy`
  - Zero cost when `SENTRY_DSN` unset
  - `core/observability/sentry_tx.py`
- **Reality Gap Job**: paper × 0.66 vs live PnL drift (calibrated from 199 trades × 200 markets sweep, T4.7-C)
- **Shadow Report Job**: 30-min shadow vs paper comparison
- **PnL Divergence Job**: daily paper/live PnL alert
- **REST Timing**: `/drt` admin command + `core/observability/rest_timing.time_call()` wrap (T4.9)

## 6 Live Guards

| # | Guard | Threshold |
|---|-------|-----------|
| G1 | Kill Switch | File-channel sentinel (96ms detect) |
| G2 | Live Budget | `LIVE_BUDGET=$1.49` (config) hard cap |
| G3 | Daily Loss | `LIVE_MAX_DAILY_LOSS=$1.00` |
| G4 | PnL Divergence | Alert ≥ X% paper↔live drift |
| G5 | Rolling WR Kill | Rolling window WR floor |
| G6 | WS Stale | `WS_STALE_SEC=60` min 5 |

37 whitelisted `/envt` parameters runtime-hot-tunable.

## Workflow Convention

- Roadmap (`02_POLYPAPER_YOL_HARITASI.md`) → batch çıkar
- Progress log (`03_POLYPAPER_PROGRESS_LOG.md`) entry yaz
- Commit message: `feat/fix/docs/chore/test/deps(scope): <Turkish summary>`
- Büyük closure → `data_store/.auto-memory/project_*.md` landmark
- Cleanup backlog → `TASKS.md` checkboxları (`[ ]`/`[~]`/`[x]`/`[!]`/`[-]`)

## Branch + Git

- Branch: `main` only (solo)
- GitHub remote: `Heddas98/polypaper-bot`
- Working tree typically dirty (yol haritası + progress log + core/* WIP)
- Commit cadence: günde 5-15 (batch'lere göre)

## Cowork / Agent

- Agent klasörü: `C:\Users\heddas\Desktop\Heddas\Dersnotu2\Polyscout31`
- Linux mount: `/sessions/<sid>/mnt/Polyscout31`
- Outputs scratchpad: `/sessions/<sid>/mnt/outputs` (kullanıcı görmez)
- Heddas Windows-side bat helpers: `scripts/*.bat` (smoke, calibrate, restore)
