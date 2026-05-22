"""
PolyPaper Bot - Database Migration Framework (v1)
==================================================
Lightweight, framework-free migration runner for async SQLite.
Tracks applied migrations in schema_version table, runs unapplied
migrations in order, idempotent + transactional.
"""

import logging
from datetime import datetime

import aiosqlite

logger = logging.getLogger("polypaper.db.migrations")


# ── MIGRATION DEFINITIONS ──
MIGRATIONS = [
    {
        "version": 1,
        "name": "strategies_strategy_type",
        "sql": "ALTER TABLE strategies ADD COLUMN strategy_type TEXT DEFAULT 'fusion'",
    },
    {
        "version": 2,
        "name": "executions_realized_slippage",
        "sql": "ALTER TABLE executions ADD COLUMN realized_slippage REAL DEFAULT 0.0",
    },
    {
        "version": 3,
        "name": "is_maker_columns",
        "sql": [
            "ALTER TABLE executions ADD COLUMN is_maker INTEGER DEFAULT 0",
            "ALTER TABLE live_trades ADD COLUMN is_maker INTEGER DEFAULT 0",
        ],
    },
    {
        "version": 4,
        "name": "strategies_deploy_stage",
        "sql": "ALTER TABLE strategies ADD COLUMN deploy_stage TEXT DEFAULT 'canary'",
    },
    {
        "version": 5,
        "name": "strategies_status_normalization",
        "sql": [
            "UPDATE strategies SET status='active' WHERE status IN ('running','on','enabled','live','run')",
            "UPDATE strategies SET status='stopped' WHERE status IN ('off','disabled','halt','halted')",
            "UPDATE strategies SET status='paused' WHERE status='pause'",
        ],
    },
    {
        "version": 6,
        "name": "executions_disposition_tracking",
        "sql": "ALTER TABLE executions ADD COLUMN max_unrealized_price REAL DEFAULT NULL",
    },
    {
        "version": 7,
        "name": "executions_ev_tracking",
        "sql": [
            "ALTER TABLE executions ADD COLUMN expected_ev REAL DEFAULT 0.0",
            "ALTER TABLE executions ADD COLUMN win_probability REAL DEFAULT 0.5",
        ],
    },
    {
        "version": 8,
        "name": "phase79_missing_columns",
        "sql": [
            "ALTER TABLE executions ADD COLUMN signal_score REAL DEFAULT NULL",
            "ALTER TABLE executions ADD COLUMN conviction REAL DEFAULT NULL",
            "CREATE TABLE IF NOT EXISTS whale_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT, token_id TEXT, direction TEXT, side TEXT, price REAL, size REAL, notional_usd REAL, ts_ms INTEGER, ts_iso TEXT)",
        ],
    },
    {
        "version": 9,
        "name": "sprint0_activate_strategies",
        "sql": [
            # S0-03: Activate best-performing strategies (Contrarian + Momentum + Fusion)
            "UPDATE strategies SET status='active' WHERE label='BTC Contrarian Dip' AND status='stopped'",
            "UPDATE strategies SET status='active' WHERE label='SOL Contrarian Dip' AND status='stopped'",
            "UPDATE strategies SET status='active' WHERE label='ETH Contrarian Dip' AND status='stopped'",
            "UPDATE strategies SET status='active' WHERE label='BTC Momentum Trend' AND status='stopped'",
            "UPDATE strategies SET status='active' WHERE label='Sweet Spot Fusion' AND status='stopped'",
        ],
    },
    {
        "version": 10,
        "name": "sprint1_default_tp_sl",
        "sql": [
            # S1-04: Add default SL (20%) where missing — protect against unlimited losses
            "UPDATE strategies SET stop_loss_percent = 0.20 WHERE stop_loss_percent IS NULL AND stop_loss_odds IS NULL",
            # S1-04: Add default TP (10%) where missing — take profit faster in 5m markets
            "UPDATE strategies SET take_profit_percent = 0.10 WHERE take_profit_percent IS NULL AND take_profit_odds IS NULL",
        ],
    },
    # HOTFIX: v10 set SL=20% but 5m markets trigger SL in 2-16 seconds — too tight
    {
        "version": 12,
        "name": "hotfix_sl_loosen_30pct",
        "sql": [
            "UPDATE strategies SET stop_loss_percent = 0.30 WHERE stop_loss_percent = 0.20",
        ],
    },
    {
        "version": 11,
        "name": "sprint2_enriched_metrics",
        "sql": [
            # S2-03: Trade close enrichment columns
            "ALTER TABLE executions ADD COLUMN duration_sec INTEGER DEFAULT NULL",
            "ALTER TABLE executions ADD COLUMN max_favorable_move REAL DEFAULT NULL",
            "ALTER TABLE executions ADD COLUMN max_adverse_move REAL DEFAULT NULL",
            "ALTER TABLE executions ADD COLUMN regime_at_entry TEXT DEFAULT NULL",
            # S2-04: Daily snapshots table
            """CREATE TABLE IF NOT EXISTS daily_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                pnl REAL DEFAULT 0.0,
                wr REAL DEFAULT 0.0,
                avg_signal_score REAL DEFAULT NULL,
                active_strategies INTEGER DEFAULT 0,
                skip_breakdown TEXT DEFAULT NULL,
                top_strategy TEXT DEFAULT NULL,
                worst_strategy TEXT DEFAULT NULL,
                balance REAL DEFAULT 0.0,
                fees REAL DEFAULT 0.0,
                created_at TEXT NOT NULL
            )""",
        ],
    },
    {
        "version": 13,
        "name": "strategy_changelog",
        "sql": [
            """CREATE TABLE IF NOT EXISTS strategy_changelog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                strategy_label TEXT,
                action TEXT NOT NULL,
                source TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                reason TEXT,
                wr_at_time REAL,
                pnl_at_time REAL,
                trades_at_time INTEGER,
                created_at TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_changelog_strat ON strategy_changelog(strategy_id)",
            "CREATE INDEX IF NOT EXISTS idx_changelog_ts ON strategy_changelog(created_at)",
        ],
    },
    {
        "version": 14,
        "name": "hyperopt_results",
        "sql": [
            """CREATE TABLE IF NOT EXISTS hyperopt_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                strategy_id TEXT,
                best_params TEXT,
                best_score REAL DEFAULT 0.0,
                metric TEXT DEFAULT 'win_rate',
                train_score REAL DEFAULT 0.0,
                test_score REAL DEFAULT 0.0,
                overfit_ratio REAL DEFAULT 0.0,
                is_overfit INTEGER DEFAULT 0,
                applied INTEGER DEFAULT 0,
                source TEXT DEFAULT 'telegram',
                n_trials INTEGER DEFAULT 0,
                duration_s REAL DEFAULT 0.0,
                created_at TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_hopt_strat ON hyperopt_results(strategy_name)",
            "CREATE INDEX IF NOT EXISTS idx_hopt_ts ON hyperopt_results(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_hopt_applied ON hyperopt_results(applied)",
        ],
    },
    {
        # ── Phase 82e Sprint 5 (FINAL): Fusion×29 granular apply ──
        # hyperopt_results needs (asset, timeframe) so the apply callback can
        # route best_params to the correct per-asset/per-tf strategy slice.
        # Without these columns, apply matched by strategy_type only and
        # updated rows[0] — a Fusion hyperopt would land on only one of the
        # 29 fusion instances. Now apply can match (strategy_type, asset,
        # timeframe) and UPDATE ALL. Nullable + empty default → backward
        # compatible with pre-migration rows.
        "version": 15,
        "name": "hyperopt_results_asset_tf",
        "sql": [
            "ALTER TABLE hyperopt_results ADD COLUMN asset TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE hyperopt_results ADD COLUMN timeframe TEXT NOT NULL DEFAULT ''",
            "CREATE INDEX IF NOT EXISTS idx_hopt_atf "
            "ON hyperopt_results(strategy_name, asset, timeframe)",
        ],
    },
    # ── 2026-04-28 Heddas direktifi: Hyperopt tam silme ────────────────
    # Phase 82d hyperopt_results table'ı drop edildi. Hyperopt subsistemi
    # tamamen kaldırıldı (5 backtest dosyası, AI Brain hyperopt fonksiyonları,
    # bot.py register, .env HYPEROPT_*). Geçmiş hyperopt sonuçları read-only
    # arşiv olarak DB'de kalmasına gerek yok — DROP IF EXISTS güvenli.
    {
        "version": 16,
        "name": "drop_hyperopt_results",
        "sql": [
            "DROP INDEX IF EXISTS idx_hopt_strat",
            "DROP INDEX IF EXISTS idx_hopt_ts",
            "DROP INDEX IF EXISTS idx_hopt_applied",
            "DROP INDEX IF EXISTS idx_hopt_atf",
            "DROP TABLE IF EXISTS hyperopt_results",
        ],
    },
    # ── 2026-04-29 Polymarket Portfolio Cache (Aşama 1) ──────────────────
    # data/polymarket_portfolio.py PortfolioSnapshot'u JSON blob olarak
    # cache'ler. Telegram /portfolio komutu cache'ten okur (anlık), 60s
    # refresh job tarafından güncellenir. Tek satır row pattern (id=1).
    {
        "version": 17,
        "name": "polymarket_portfolio_cache",
        "sql": [
            "CREATE TABLE IF NOT EXISTS polymarket_portfolio_cache ("
            "id INTEGER PRIMARY KEY,"
            "user_address TEXT NOT NULL,"
            "snapshot_json TEXT NOT NULL,"
            "fetched_at TEXT NOT NULL,"
            "fetch_latency_ms INTEGER DEFAULT 0,"
            "error_count INTEGER DEFAULT 0)",
            "CREATE INDEX IF NOT EXISTS idx_pm_portfolio_fetched "
            "ON polymarket_portfolio_cache(fetched_at)",
        ],
    },
    # ════════════════════════════════════════════════════════════════════
    # P0-08-E2 (2026-05-08): Event-driven multi-TF data layer.
    #
    # Polymarket WSS market channel event payload'larıyla 1:1 hizalı schema:
    #   - book event       → ob_snapshots (full L2, 60s recovery anchor)
    #   - price_change     → ob_deltas (delta-driven, fill simulation kaynağı)
    #   - last_trade_price → public_trades (taker tape + fee_rate_bps)
    #   - external feed    → external_prices (Binance/Chainlink reference)
    #   - candles_ext      → 5m base only (15m/1h/24h runtime aggregation)
    #   - candles_poly     → per-market, TF-aware
    #
    # Field naming Polymarket convention (asset_id, condition_id, fee_rate_bps).
    # Reference: docs.polymarket.com/market-data/websocket/market-channel
    # ════════════════════════════════════════════════════════════════════
    {
        "version": 18,
        "name": "p0_08_e2_event_driven_data_layer",
        "sql": [
            "CREATE TABLE IF NOT EXISTS ob_deltas ("
            "ts_ms INTEGER NOT NULL,"
            "asset_id TEXT NOT NULL,"
            "condition_id TEXT,"
            "side TEXT NOT NULL,"
            "price REAL NOT NULL,"
            "size REAL NOT NULL,"
            "hash TEXT,"
            "best_bid REAL,"
            "best_ask REAL,"
            "PRIMARY KEY (ts_ms, asset_id, side, price))",
            "CREATE INDEX IF NOT EXISTS idx_ob_deltas_asset_ts " "ON ob_deltas(asset_id, ts_ms)",
            "CREATE INDEX IF NOT EXISTS idx_ob_deltas_condition_ts "
            "ON ob_deltas(condition_id, ts_ms)",
            "CREATE TABLE IF NOT EXISTS public_trades ("
            "ts_ms INTEGER NOT NULL,"
            "asset_id TEXT NOT NULL,"
            "condition_id TEXT,"
            "taker_side TEXT NOT NULL,"
            "price REAL NOT NULL,"
            "size REAL NOT NULL,"
            "fee_rate_bps REAL,"
            "PRIMARY KEY (ts_ms, asset_id))",
            "CREATE INDEX IF NOT EXISTS idx_public_trades_asset_ts "
            "ON public_trades(asset_id, ts_ms)",
            "CREATE INDEX IF NOT EXISTS idx_public_trades_condition_ts "
            "ON public_trades(condition_id, ts_ms)",
            "CREATE TABLE IF NOT EXISTS ob_snapshots ("
            "ts_ms INTEGER NOT NULL,"
            "asset_id TEXT NOT NULL,"
            "condition_id TEXT,"
            "asset TEXT,"
            "timeframe TEXT,"
            "slug TEXT,"
            "best_bid REAL,"
            "best_ask REAL,"
            "mid_price REAL,"
            "spread REAL,"
            "bids_json TEXT,"
            "asks_json TEXT,"
            "hash TEXT,"
            "PRIMARY KEY (ts_ms, asset_id))",
            "CREATE INDEX IF NOT EXISTS idx_ob_snapshots_asset_ts "
            "ON ob_snapshots(asset_id, ts_ms)",
            "CREATE INDEX IF NOT EXISTS idx_ob_snapshots_atf "
            "ON ob_snapshots(asset, timeframe, ts_ms)",
            "CREATE TABLE IF NOT EXISTS external_prices ("
            "ts_ms INTEGER NOT NULL,"
            "symbol TEXT NOT NULL,"
            "source TEXT NOT NULL,"
            "price REAL NOT NULL,"
            "PRIMARY KEY (ts_ms, symbol, source))",
            "CREATE INDEX IF NOT EXISTS idx_external_prices_symbol_ts "
            "ON external_prices(symbol, ts_ms)",
            "CREATE INDEX IF NOT EXISTS idx_external_prices_source_ts "
            "ON external_prices(source, ts_ms)",
            "CREATE TABLE IF NOT EXISTS candles_ext ("
            "symbol TEXT NOT NULL,"
            "interval TEXT NOT NULL DEFAULT '5m',"
            "open_ts INTEGER NOT NULL,"
            "open REAL,"
            "high REAL,"
            "low REAL,"
            "close REAL,"
            "volume REAL,"
            "PRIMARY KEY (symbol, interval, open_ts))",
            "CREATE TABLE IF NOT EXISTS candles_poly ("
            "asset_id TEXT NOT NULL,"
            "slug TEXT,"
            "asset TEXT,"
            "timeframe TEXT NOT NULL,"
            "open_ts INTEGER NOT NULL,"
            "open REAL,"
            "high REAL,"
            "low REAL,"
            "close REAL,"
            "volume REAL,"
            "PRIMARY KEY (asset_id, timeframe, open_ts))",
            "CREATE INDEX IF NOT EXISTS idx_candles_poly_tf_ts "
            "ON candles_poly(timeframe, open_ts)",
            "CREATE INDEX IF NOT EXISTS idx_candles_poly_asset_tf "
            "ON candles_poly(asset, timeframe, open_ts)",
        ],
    },
    # ════════════════════════════════════════════════════════════════════
    # P0-08-E1 hotfix (2026-05-09): polymarket_portfolio_cache tablosu
    # E1 cleanup'ında DROP edildi ama schema_version v17 zaten "applied"
    # işaretli olduğu için v17 IF NOT EXISTS migration'ı tekrar koşmadı.
    # Bu hotfix tabloyu yeniden create eder; idempotent.
    # ════════════════════════════════════════════════════════════════════
    {
        "version": 19,
        "name": "p0_08_e1_hotfix_portfolio_cache",
        "sql": [
            "CREATE TABLE IF NOT EXISTS polymarket_portfolio_cache ("
            "id INTEGER PRIMARY KEY,"
            "user_address TEXT NOT NULL,"
            "snapshot_json TEXT NOT NULL,"
            "fetched_at TEXT NOT NULL,"
            "fetch_latency_ms INTEGER DEFAULT 0,"
            "error_count INTEGER DEFAULT 0)",
            "CREATE INDEX IF NOT EXISTS idx_pm_portfolio_fetched "
            "ON polymarket_portfolio_cache(fetched_at)",
        ],
    },
    # ════════════════════════════════════════════════════════════════════
    # P0-08-E1 hotfix #2 (2026-05-09): executions tablosu eksik column'lar
    # E1 cleanup'ında executions DROP edildi, bot startup'ta minimal
    # _create_tables tablosu yarattı (sadece base column'lar). v3-v15'teki
    # ALTER TABLE ADD COLUMN migration'ları schema_version'da ZATEN "applied"
    # işaretli olduğu için tekrar koşmadı. Live trade engine_fills.create_execution()
    # `signal_score` column'a INSERT yapmaya çalışınca patlıyordu.
    #
    # Çözüm: executions DROP + CREATE full schema (v1-v15 ALTER'larının union'u).
    # Tablo zaten boş (live trade kayıt edilemediği için), data kaybı yok.
    # ════════════════════════════════════════════════════════════════════
    {
        "version": 20,
        "name": "p0_08_e1_hotfix_executions_full_schema",
        "sql": [
            "DROP TABLE IF EXISTS executions",
            "CREATE TABLE executions ("
            "id TEXT PRIMARY KEY,"
            "user_id TEXT NOT NULL REFERENCES users(id),"
            "wallet_id TEXT NOT NULL REFERENCES wallets(id),"
            "strategy_id TEXT REFERENCES strategies(id),"
            "event_slug TEXT NOT NULL,"
            "market_token_id TEXT,"
            "direction TEXT NOT NULL,"
            "trade_amount REAL NOT NULL,"
            "fee_amount REAL DEFAULT 0.0,"
            "odds_threshold REAL,"
            "execution_price REAL,"
            "status TEXT NOT NULL DEFAULT 'pending',"
            "stop_loss_percent REAL,"
            "stop_loss_odds REAL,"
            "take_profit_percent REAL,"
            "take_profit_odds REAL,"
            "pnl REAL DEFAULT 0.0,"
            "payout REAL DEFAULT 0.0,"
            "result TEXT,"
            "closed_at TEXT,"
            "error_message TEXT,"
            "created_at TEXT NOT NULL,"
            "updated_at TEXT NOT NULL,"
            # v3-v15 ALTER columns:
            "realized_slippage REAL DEFAULT 0.0,"
            "is_maker INTEGER DEFAULT 0,"
            "max_unrealized_price REAL DEFAULT NULL,"
            "expected_ev REAL DEFAULT 0.0,"
            "win_probability REAL DEFAULT 0.5,"
            "signal_score REAL DEFAULT NULL,"
            "conviction REAL DEFAULT NULL,"
            "duration_sec INTEGER DEFAULT NULL,"
            "max_favorable_move REAL DEFAULT NULL,"
            "max_adverse_move REAL DEFAULT NULL,"
            "regime_at_entry TEXT DEFAULT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status)",
            "CREATE INDEX IF NOT EXISTS idx_executions_user ON executions(user_id)",
        ],
    },
    # ════════════════════════════════════════════════════════════════════
    # P0-07 (2026-05-09): reference_price_audit
    # Polymarket binary Up/Down market'lerin official resolution price'ı
    # ile bot'un local Binance/Chainlink reference feed'i arasındaki
    # sapmayı settle anında snapshot eder. >5 bps sistematik bias →
    # edge tahmini geçersizlik alarmı. Acceptance kriteri:
    # 7 günlük markdown rapor + 10 worst deviation örneği.
    #
    # Tablo schema'sı:
    #   - settle_ts_ms INTEGER NOT NULL — settle moment epoch ms (boundary)
    #   - condition_id TEXT NOT NULL    — Polymarket market id
    #   - asset_id TEXT                 — ERC1155 token id (UP outcome)
    #   - slug TEXT                     — event slug (debug/UI)
    #   - asset TEXT                    — BTC/ETH/SOL/XRP
    #   - timeframe TEXT                — 5m/15m/1h/24h
    #   - official_resolution_price REAL — Polymarket'in resolved boundary price
    #   - bot_binance_rest_price REAL   — external_prices source='binance' nearest
    #   - bot_binance_ws_price REAL     — external_prices source='binance_spot_ws'
    #   - bot_chainlink_price REAL      — external_prices source='chainlink'
    #   - dev_binance_bps REAL          — basis-point sapma (WS tercih, REST fallback)
    #   - dev_chainlink_bps REAL        — basis-point sapma
    #   - settle_outcome TEXT           — UP / DOWN / INVALID
    #   - data_quality TEXT             — ok / missing_external / missing_resolution
    #   - created_at TEXT NOT NULL
    #
    # PRIMARY KEY (condition_id, settle_ts_ms) — aynı market aynı boundary
    # için birden fazla audit yazılmaz; backfill + live re-run idempotent.
    # ════════════════════════════════════════════════════════════════════
    {
        "version": 21,
        "name": "p0_07_reference_price_audit",
        "sql": [
            "CREATE TABLE IF NOT EXISTS reference_price_audit ("
            "settle_ts_ms INTEGER NOT NULL,"
            "condition_id TEXT NOT NULL,"
            "asset_id TEXT,"
            "slug TEXT,"
            "asset TEXT,"
            "timeframe TEXT,"
            "official_resolution_price REAL,"
            "bot_binance_rest_price REAL,"
            "bot_binance_ws_price REAL,"
            "bot_chainlink_price REAL,"
            "dev_binance_bps REAL,"
            "dev_chainlink_bps REAL,"
            "settle_outcome TEXT,"
            "data_quality TEXT NOT NULL DEFAULT 'ok',"
            "created_at TEXT NOT NULL,"
            "PRIMARY KEY (condition_id, settle_ts_ms))",
            # Index for "show me last 7 days" reports (most common query)
            "CREATE INDEX IF NOT EXISTS idx_ref_audit_ts " "ON reference_price_audit(settle_ts_ms)",
            # Index for "per-asset/tf statistics" reports
            "CREATE INDEX IF NOT EXISTS idx_ref_audit_asset_tf "
            "ON reference_price_audit(asset, timeframe, settle_ts_ms)",
            # Index for "find pending re-fetches" (data_quality != 'ok')
            "CREATE INDEX IF NOT EXISTS idx_ref_audit_quality "
            "ON reference_price_audit(data_quality, settle_ts_ms)",
            # Index for "worst deviations leaderboard"
            "CREATE INDEX IF NOT EXISTS idx_ref_audit_dev_binance "
            "ON reference_price_audit(dev_binance_bps)",
        ],
    },
    # ════════════════════════════════════════════════════════════════════
    # v22 (2026-05-22, Heddas: fan 3. kez) — ob_deltas KULLANILMAYAN 2 ikincil
    # index'i düşür. ob_deltas write-only (hiçbir sorgu okumuyor; backtest +
    # gerçekçi fill ob_snapshots kullanıyor). (asset_id,ts_ms) + (condition_id,
    # ts_ms) index'leri rastgele B-tree insert → milyonlarca satırda cache'e
    # sığmaz → her yazımda disk → aiosqlite %88 / CPU %107 / fan. Düşürünce
    # yazımlar zaman-sıralı PK'ya (sona-ekleme) iner = ucuz. Kayıt SÜRER (veri
    # korunur). 14g retention (db_retention_job) disk'i kapar. İleride ultra-
    # gerçekçi fill-sim (maker kuyruk pozisyonu) yaparsak index geri eklenir.
    # ════════════════════════════════════════════════════════════════════
    {
        "version": 22,
        "name": "ob_deltas_drop_unused_indexes",
        "sql": [
            "DROP INDEX IF EXISTS idx_ob_deltas_asset_ts",
            "DROP INDEX IF EXISTS idx_ob_deltas_condition_ts",
        ],
    },
]


async def _ensure_schema_version_table(conn):
    """Create schema_version table if it doesn't exist."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version INTEGER NOT NULL UNIQUE,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
    """)
    await conn.commit()


async def _get_current_version(conn) -> int:
    """Get the current schema version. Returns 0 if no migrations applied."""
    try:
        async with conn.execute("SELECT MAX(version) FROM schema_version") as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else 0
    except aiosqlite.Error:
        # T11.8-B (2026-04-24): narrow from bare Exception. SELECT MAX surfaces
        # aiosqlite.OperationalError when schema_version table missing (first
        # boot before _ensure_schema_version_table). 0 = "no migrations yet".
        return 0


async def _apply_migration(conn, migration: dict) -> bool:
    """Apply a single migration in a transaction. Returns True on success."""
    version = migration["version"]
    name = migration["name"]
    sql_statements = migration["sql"]

    # Normalize to list
    if isinstance(sql_statements, str):
        sql_statements = [sql_statements]

    try:
        # Check if already applied
        async with conn.execute(
            "SELECT 1 FROM schema_version WHERE version=?", (version,)
        ) as cursor:
            if await cursor.fetchone():
                logger.debug(f"Migration v{version} ({name}) already applied, skipping")
                return True

        # Apply each SQL statement
        for sql in sql_statements:
            await conn.execute(sql)

        # Record in schema_version
        now_iso = datetime.now().isoformat()
        await conn.execute(
            "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
            (version, name, now_iso),
        )

        # Commit transaction
        await conn.commit()
        logger.info(f"Migration v{version} ({name}) applied successfully")
        return True
    except aiosqlite.Error as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. Migration SQL
        # (ALTER/CREATE/UPDATE) raises aiosqlite.Error subclasses only.
        # Unknown exception types would indicate a Python bug — let those
        # propagate (caller catches at run_migrations level).
        logger.error(f"Migration v{version} ({name}) failed: " f"{type(e).__name__}: {e}")
        try:
            await conn.rollback()
        except aiosqlite.Error:
            # T11.8-B (2026-04-24): narrow from bare Exception. rollback()
            # raises aiosqlite.Error if no transaction active. Silent swallow
            # correct — we already logged the original migration failure.
            pass
        return False


async def run_migrations(conn) -> bool:
    """
    Run all unapplied migrations in order.

    - Ensures schema_version table exists
    - Gets current version
    - Applies each migration >= current_version + 1
    - Each migration runs in a transaction
    - Idempotent: safe to re-run

    Returns True if all migrations succeeded, False if any failed.
    """
    try:
        # Ensure schema_version table exists
        await _ensure_schema_version_table(conn)

        # Get current version
        current_version = await _get_current_version(conn)
        logger.info(f"Current schema version: {current_version}")

        # Get migrations to apply (version > current_version)
        to_apply = [m for m in MIGRATIONS if m["version"] > current_version]

        if not to_apply:
            logger.info("Schema is up to date, no migrations to apply")
            return True

        logger.info(f"Applying {len(to_apply)} migration(s)")

        # Apply each migration in order
        all_success = True
        for migration in to_apply:
            success = await _apply_migration(conn, migration)
            if not success:
                all_success = False
                # Continue to attempt remaining migrations

        if all_success:
            final_version = await _get_current_version(conn)
            logger.info(f"Migrations complete. Schema version now: {final_version}")
        else:
            logger.warning("Some migrations failed. Check logs above.")

        return all_success
    except aiosqlite.Error as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. Top-level
        # runner only handles aiosqlite errors not caught by inner per-
        # migration loop (e.g. schema_version table creation failure).
        # Returns False so bot boot can decide to abort.
        logger.error(f"Migration runner error: {type(e).__name__}: {e}")
        return False


async def grandfather_deploy_stage(conn) -> None:
    """
    Phase 47f.10 P3#15 — one-shot grandfather logic.
    Promote strategies that ALREADY have trade history.
    Prevents the migration from silently halving sizing on the live 47f.7 baseline.
    Guarded by bot_settings flag so demoted strategies don't get auto-repromoted.

    This is run AFTER migrations, as a separate idempotent operation.
    """
    try:
        # Check if already done
        async with conn.execute(
            "SELECT value FROM bot_settings WHERE key='phase47f10_grandfather'"
        ) as cursor:
            row = await cursor.fetchone()

        if row:
            logger.debug("Phase 47f.10 grandfather already completed, skipping")
            return

        # Promote strategies with trade history
        await conn.execute(
            """UPDATE strategies SET deploy_stage='promoted'
               WHERE id IN (SELECT DISTINCT strategy_id FROM executions
                            WHERE result IS NOT NULL)"""
        )

        # Mark as done
        now_iso = datetime.now().isoformat()
        await conn.execute(
            "INSERT INTO bot_settings(key, value, updated_at) VALUES (?, ?, ?)",
            ("phase47f10_grandfather", "done", now_iso),
        )
        await conn.commit()
        logger.info("Phase 47f.10 grandfather: promoted strategies with trade history")
    except aiosqlite.Error as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. SELECT/UPDATE/
        # INSERT all aiosqlite.Error. Idempotent operation — silent skip on
        # any DB error is correct (next boot retries).
        logger.warning(f"Phase 47f.10 grandfather skipped: " f"{type(e).__name__}: {e}")
