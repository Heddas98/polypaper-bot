"""Single source of truth for bot version string.

Phase 52 BUG #1 — eliminate hardcoded "v9.2" drift across UI surfaces.
Update this file per release; every header will pick it up automatically.
"""

BOT_VERSION = "v9.7.9"
# T0.5 sync 2026-04-20 — codename = CHANGELOG top entry. Update per phase;
# every UI surface (/help, startup log, Sentry release) follows automatically.
BOT_CODENAME = "Phase 82e Sprint 6 — /env_toggle hot-tune"
