# PolyPaper Bot — Environment Variable Reference

> **Auto-generated** by `scripts/gen_env_reference.py` (T11.7 doctrine).
> Do not hand-edit. Run the generator after adding any new `os.getenv(...)` call in production code.
>
> **Total keys:** 267 distinct env vars read across production dirs.
> **Whitelist coverage:** 40 / 40 whitelisted keys have at least one reader.
> **`.env.example` coverage:** 125 / 267 runtime keys are documented in `.env.example`.

## Legend

- **Key:** the environment variable name
- **Default:** default value when unset (`None` if no fallback, `<expr>` if non-literal)
- **Whitelist:** `/env_toggle` runtime-tuneable? (`✅` = in `config/env_whitelist.py`)
- **`.env.example`:** documented in the template file? (`✅` = yes)
- **Readers:** first 3 call sites (see full scan for more)

## Reference Table

| Key | Default | Whitelist | `.env.example` | Readers |
|-----|---------|-----------|----------------|---------|
| `ADAPTIVE_DEAD_THRESHOLD` | `'0.85'` |  |  | `core/auto_optimizer.py:720` |
| `ADAPTIVE_MAKER_ENABLED` | `'true'` | ✅ | ✅ | `core/engine_signals.py:1493` |
| `ADAPTIVE_MAKER_IMPROVE_TICKS` | `'1'` | ✅ |  | `core/engine_signals.py:1526` |
| `ADAPTIVE_MAKER_MAX_SIGNAL` | `'0.45'` | ✅ |  | `core/engine_signals.py:1519` |
| `ADAPTIVE_MAKER_MIN_MINS` | `'2.0'` | ✅ |  | `core/engine_signals.py:1518` |
| `ADAPTIVE_MAX_THRESHOLD` | `'0.85'` |  |  | `core/ai_brain.py:745`, `core/auto_optimizer.py:719` |
| `ADAPTIVE_PNL_ENABLED` | `'true'` |  | ✅ | `core/auto_optimizer.py:109` |
| `ADAPTIVE_PNL_FLOOR` | `'-10.0'` |  | ✅ | `core/auto_optimizer.py:112` |
| `ADAPTIVE_PNL_STEP` | `'0.5'` |  | ✅ | `core/auto_optimizer.py:110` |
| `ADAPTIVE_PNL_TRADES_PER_STEP` | `'20'` |  | ✅ | `core/auto_optimizer.py:111` |
| `ADMIN_CHAT_ID` | `None` |  |  | `core/engine.py:612`, `core/engine.py:870`, `core/engine_settlement.py:196` (+7 more) |
| `ADMIN_TELEGRAM_ID` | `'0'` |  | ✅ | `core/auto_optimizer.py:581`, `core/engine.py:612`, `core/engine.py:870` (+15 more) |
| `AI_AUTO_CONFIDENCE` | `'0.70'` |  | ✅ | `core/ai_brain.py:415`, `core/ai_brain.py:1856` |
| `AI_BRAIN_CYCLE` | `'3600'` |  | ✅ | `core/ai_brain.py:109` |
| `AI_BRAIN_FALLBACK_CHAIN` | `'groq,claude'` |  | ✅ | `core/ai_brain.py:239` |
| `AI_MIN_TRADES` | `'15'` |  | ✅ | `core/ai_brain.py:111` |
| `AI_STRATEGY_SUGGEST_FIRST` | `'1800'` |  |  | `telegram_bot/bot.py:792` |
| `AI_STRATEGY_SUGGEST_INTERVAL` | `'14400'` |  |  | `core/strategy_suggester.py:24`, `telegram_bot/bot.py:791` |
| `AI_TWO_AGENT_MODE` | `'true'` |  | ✅ | `core/ai_brain.py:378` |
| `ALERT_DAILY_PNL_PCT` | `'0.4'` |  |  | `core/risk_manager.py:406` |
| `ALERT_LOSS_STREAK` | `'5'` |  |  | `core/risk_manager.py:389` |
| `ALLOWED_ZONES` | `'(tumu)'` |  | ✅ | `core/ai_brain.py:744`, `core/engine_signals.py:55` |
| `ANTHROPIC_API_KEY` | `''` |  | ✅ | `core/ai_brain.py:32`, `core/intent_parser.py:47` |
| `ARCHIVE_DIR` | `<expr>` |  |  | `backtest/archive_reader.py:115` |
| `AUTO_PROMOTE_ENABLED` | `'1'` |  |  | `telegram_bot/bot.py:755` |
| `AUTO_PROMOTE_FIRST_SEC` | `'1200'` |  |  | `telegram_bot/bot.py:757` |
| `AUTO_PROMOTE_INTERVAL_SEC` | `'86400'` |  |  | `telegram_bot/bot.py:756` |
| `AUTO_RESUME_MIN_PNL` | `'0.0'` |  | ✅ | `core/auto_optimizer.py:424` |
| `AUTO_RESUME_MIN_WR` | `'50.0'` |  | ✅ | `core/auto_optimizer.py:425` |
| `AUTO_RESUME_ON_STARTUP` | `'false'` |  | ✅ | `core/auto_optimizer.py:421` |
| `BALANCE_PREFLIGHT` | `'true'` |  |  | `core/live_trader.py:505` |
| `BAYESIAN_SIGNAL_ACCURACY` | `'0.60'` |  | ✅ | `core/signal_fusion.py:547` |
| `BAYESIAN_UPDATER_ENABLED` | `'false'` |  | ✅ | `core/ai_brain.py:738`, `core/signal_fusion.py:546` |
| `BECKER_DECISION_MODE` | `'boost'` |  |  | `telegram_bot/jobs/shadow_report_job.py:255` |
| `BG_TASK_HISTORY_SIZE` | `'50'` |  |  | `core/bg_task.py:57` |
| `BG_TASK_NOTIFY_COOLDOWN_SEC` | `'300'` |  |  | `core/bg_task.py:98` |
| `BG_TASK_NOTIFY_ENABLED` | `'1'` |  |  | `core/bg_task.py:61` |
| `BONDING_CONFIDENCE_BASE` | `'0.80'` |  | ✅ | `backtest/strategies/bonding_yield.py:30`, `core/strategy_plugins.py:730` |
| `BONDING_MAX_HOURS_LEFT` | `'48'` |  | ✅ | `backtest/strategies/bonding_yield.py:29`, `core/strategy_plugins.py:729` |
| `BONDING_MAX_PRICE` | `'0.99'` |  | ✅ | `backtest/strategies/bonding_yield.py:26`, `core/strategy_plugins.py:727` |
| `BONDING_MIN_PRICE` | `'0.90'` |  | ✅ | `backtest/strategies/bonding_yield.py:25`, `core/strategy_plugins.py:726` |
| `BONDING_MIN_YIELD` | `'0.01'` |  | ✅ | `backtest/strategies/bonding_yield.py:27`, `core/strategy_plugins.py:728` |
| `BONDING_TIME_WEIGHT` | `'true'` |  | ✅ | `backtest/strategies/bonding_yield.py:28` |
| `BRIER_ALARM_ENABLED` | `'true'` | ✅ |  | `core/engine_signals.py:1119` |
| `BRIER_GAP_MAX` | `'0.30'` |  | ✅ | `core/engine_signals.py:65` |
| `CALENDAR_MULT_ENABLED` | `'false'` |  | ✅ | `core/ai_brain.py:740`, `core/signal_fusion.py:533` |
| `CANARY_SIZE_MULT` | `'1.0'` |  | ✅ | `core/engine_signals.py:1397` |
| `CHANGELOG_DEFAULT_LIMIT` | `'20'` |  |  | `telegram_bot/handlers/changelog_handler.py:33` |
| `CHANGELOG_MAX_LIMIT` | `'100'` |  |  | `telegram_bot/handlers/changelog_handler.py:34` |
| `CLASSIC_BYPASS_ALL_GATES` | `'true'` | ✅ | ✅ | `core/engine_signals.py:140` |
| `CLASSIC_NOTIFY_ALL_STYPES` | `'false'` |  |  | `core/engine_settlement.py:178`, `core/engine_settlement.py:270` |
| `CLASSIC_NOTIFY_RESOLUTION` | `'true'` | ✅ |  | `core/engine_settlement.py:77`, `core/engine_settlement.py:162` |
| `CLASSIC_RESPECT_FEE_TAIL` | `'false'` | ✅ |  | `core/engine_signals.py:1596` |
| `CLASSIC_RESPECT_TOKEN_CAP` | `'false'` | ✅ |  | `core/engine_signals.py:1651` |
| `CLASSIC_RESPECT_UNSELLABLE` | `'false'` | ✅ | ✅ | `core/engine_signals.py:1142` |
| `CLASSIC_RESPECT_ZONES` | `'false'` | ✅ | ✅ | `core/engine_signals.py:813` |
| `CLASSIC_TAKER_LIMIT_CEIL` | `'0.99'` | ✅ |  | `core/engine_signals.py:1563` |
| `CLOB_SIGNATURE_TYPE` | `'2'` |  | ✅ | `core/live_trader.py:217`, `core/live_trader.py:447`, `data/polymarket_actions.py:57` (+1 more) |
| `CLOB_TIMEOUT` | `'5.0'` |  | ✅ | `data/polymarket_client.py:21` |
| `CONFLUENCE_K` | `'3'` |  | ✅ | `core/ai_brain.py:741`, `core/signal_fusion.py:551` |
| `CONFLUENCE_MODE` | `'true'` |  | ✅ | `core/signal_fusion.py:550` |
| `CONFLUENCE_PENALTY` | `'0.3'` |  | ✅ | `core/ai_brain.py:742`, `core/signal_fusion.py:553` |
| `CONFLUENCE_SIGNAL_THRESHOLD` | `'0.05'` |  |  | `core/signal_fusion.py:552` |
| `CONVICTION_ENABLED` | `'true'` |  |  | `core/engine_signals.py:1295` |
| `CONVICTION_MIN` | `'0.3'` | ✅ | ✅ | `core/engine_signals.py:1310` |
| `DB_ARCHIVE_ENABLED` | `'1'` |  |  | `telegram_bot/bot.py:772` |
| `DB_ARCHIVE_FIRST_SEC` | `'600'` |  |  | `telegram_bot/bot.py:774` |
| `DB_ARCHIVE_INTERVAL_SEC` | `'86400'` |  |  | `telegram_bot/bot.py:773` |
| `DB_RETENTION_FIRST_SEC` | `'900'` |  | ✅ | `telegram_bot/bot.py:712` |
| `DB_RETENTION_INTERVAL_SEC` | `'86400'` |  | ✅ | `telegram_bot/bot.py:711` |
| `DB_RETENTION_MODE` | `'report'` |  | ✅ | `telegram_bot/jobs/db_retention_job.py:245` |
| `DB_RETENTION_NOTIFY` | `'1'` |  | ✅ | `telegram_bot/jobs/db_retention_job.py:353` |
| `DB_RETENTION_VACUUM_ENABLED` | `'1'` |  | ✅ | `telegram_bot/jobs/db_retention_job.py:317` |
| `DB_SNAPSHOT_FIRST_SEC` | `'300'` |  |  | `telegram_bot/bot.py:692` |
| `DB_SNAPSHOT_INTERVAL_SEC` | `'86400'` |  |  | `telegram_bot/bot.py:691` |
| `DEBUG_SHOW_EXC` | `'false'` |  |  | `telegram_bot/handlers/_exc_render.py:56` |
| `DECISION_EXPLAINER_ENABLED` | `'true'` |  | ✅ | `core/decision_explainer.py:30`, `core/engine.py:252` |
| `DECISION_NOTIFY_DETAIL` | `'medium'` |  | ✅ | `core/decision_explainer.py:31` |
| `DISPOSITION_TRACKING` | `'true'` |  | ✅ | `core/engine_monitor.py:46` |
| `EDGE_ZONE_5065_MIN` | `'0.45'` | ✅ | ✅ | `core/engine_signals.py:1081` |
| `ENABLE_DAILY_DB_SNAPSHOT` | `'true'` |  |  | `telegram_bot/jobs/maintenance_jobs.py:45` |
| `ENABLE_WAL_CHECKPOINT` | `'true'` |  |  | `telegram_bot/jobs/maintenance_jobs.py:186` |
| `ENGINE_STALL_ENABLED` | `'1'` |  |  | `core/engine.py:586` |
| `ENGINE_STALL_TIMEOUT` | `'90'` |  |  | `core/engine.py:589` |
| `EVENT_CALENDAR_ENABLED` | `'true'` |  |  | `data/event_monitor.py:60` |
| `EV_FEE_OVERRIDE` | `'0.0'` |  |  | `calibration/ev_threshold.py:41` |
| `EV_MINIMUM` | `'0.005'` |  | ✅ | `calibration/ev_threshold.py:38` |
| `EV_SIZE_PENALTY` | `'0.50'` |  | ✅ | `calibration/ev_threshold.py:40` |
| `EV_STRICT_MODE` | `'false'` |  | ✅ | `calibration/ev_threshold.py:39` |
| `EV_THRESHOLD_ENABLED` | `'true'` |  | ✅ | `calibration/ev_threshold.py:37`, `core/engine_signals.py:1343` |
| `EXPERIMENT_ENABLED` | `'true'` |  | ✅ | `core/engine.py:263`, `core/experiment_runner.py:28` |
| `EXPERIMENT_MAX_PARAMS` | `'5'` |  | ✅ | `core/experiment_runner.py:29` |
| `FEE_GATE_ENABLED` | `'true'` | ✅ |  | `core/engine_signals.py:1105` |
| `FILL_IMPACT_MIN_FLOOR` | `'0.001'` |  |  | `backtest/simulation/fill_model.py:504` |
| `FILL_IMPACT_SCALE` | `'0.025'` |  |  | `backtest/simulation/fill_model.py:501` |
| `FILL_LATENCY_DRIFT_BPS_PER_MS` | `'0.04'` |  |  | `backtest/simulation/fill_model.py:238` |
| `FILL_SPREAD_COST` | `'0.023'` |  |  | `backtest/simulation/fill_model.py:73` |
| `FORCE_EXIT_SECONDS` | `'0'` |  | ✅ | `core/engine_monitor.py:32` |
| `FUSION_BLOCKED_ZONES` | `'30-40'` |  |  | `core/engine_signals.py:62` |
| `GROK_API_KEY` | `''` |  | ✅ | `core/ai_brain.py:33` |
| `HEARTBEAT_INTERVAL_SEC` | `'600'` |  |  | `telegram_bot/bot.py:686` |
| `INTENT_PARSER_MODEL` | `'claude-haiku-4-5-20251001'` |  |  | `core/intent_parser.py:48` |
| `KELLY_BANKROLL_MAX` | `'10000.0'` |  | ✅ | `core/kelly.py:65` |
| `KELLY_BANKROLL_MIN` | `'10.0'` |  | ✅ | `core/kelly.py:64` |
| `KELLY_DECAY_ENABLED` | `'true'` |  | ✅ | `core/kelly.py:39` |
| `KELLY_DECAY_RANGING` | `'0.167'` |  | ✅ | `core/kelly.py:42` |
| `KELLY_DECAY_TRENDING` | `'0.25'` |  | ✅ | `core/kelly.py:41` |
| `KELLY_DECAY_VOLATILE` | `'0.125'` |  | ✅ | `core/kelly.py:43` |
| `KELLY_EXPLORATION_MIN_WR` | `'0.50'` |  |  | `core/kelly.py:117` |
| `LIFECYCLE_EVALUATION_MAX` | `'50'` |  |  | `core/strategy_lifecycle.py:38` |
| `LIFECYCLE_EXPLORATION_MAX` | `'20'` |  |  | `core/strategy_lifecycle.py:37` |
| `LIFECYCLE_MIN_OVERRIDE` | `'false'` |  |  | `core/engine_signals.py:737` |
| `LIQUIDITY_MIN_DEPTH_PCT` | `'0.50'` |  |  | `core/risk_manager.py:522` |
| `LIVE_BUDGET` | `'1.49'` | ✅ |  | `core/live_trader.py:83` |
| `LIVE_ENABLED` | `'false'` |  | ✅ | `core/live_trader.py:162`, `telegram_bot/handlers/live_guards_handler.py:77` |
| `LIVE_MAX_DAILY_LOSS` | `'1.00'` | ✅ |  | `core/live_trader.py:53` |
| `LIVE_MAX_TRADE` | `'1.00'` | ✅ |  | `core/live_trader.py:45` |
| `LIVE_MIN_ODDS` | `'0.75'` | ✅ |  | `core/live_trader.py:69` |
| `LIVE_MIN_SIGNAL` | `'0.75'` | ✅ |  | `core/live_trader.py:61` |
| `LLM_RATELIMIT_BACKOFF_SEC` | `'60'` | ✅ |  | `core/ai_brain.py:58` |
| `LLM_RATELIMIT_MIN_COST` | `'0.001'` | ✅ |  | `core/ai_brain.py:66` |
| `MAKER_REBATE_ENABLED` | `'true'` |  | ✅ | `core/engine_settlement.py:103` |
| `MAKER_WIDE_SPREAD` | `'0.03'` |  | ✅ | `core/engine_support.py:36` |
| `MAX_429_RETRIES` | `'3'` |  | ✅ | `data/polymarket_client.py:23` |
| `MAX_CALENDAR_MULT` | `'2.5'` |  | ✅ | `core/signal_fusion.py:534` |
| `MAX_CROSS_ASSET_EXPOSURE` | `'0'` |  |  | `core/risk_manager.py:331` |
| `MAX_DAILY_LOSS` | `'500'` |  | ✅ | `core/engine.py:117`, `core/engine.py:118`, `telegram_bot/handlers/diagnose_handler.py:168` (+1 more) |
| `MAX_DAILY_TRADES` | `None` |  | ✅ | `core/engine.py:121`, `core/engine.py:122` |
| `MAX_LOSS_STREAK` | `'5'` |  | ✅ | `core/engine.py:119`, `core/engine.py:120`, `telegram_bot/handlers/diagnose_handler.py:169` (+1 more) |
| `MAX_OB_LEVELS` | `'50'` |  | ✅ | `data/market_recorder.py:47` |
| `MAX_OPEN_POSITIONS` | `'10'` |  | ✅ | `core/engine.py:129`, `core/engine.py:130`, `telegram_bot/handlers/diagnose_handler.py:166` (+1 more) |
| `MAX_POSITION_SIZE` | `None` |  | ✅ | `core/engine.py:131`, `core/engine.py:132` |
| `MAX_STOPS_PER_CYCLE` | `'2'` |  |  | `core/ai_brain.py:1116` |
| `MAX_TOTAL_EXPOSURE` | `'5000'` |  | ✅ | `core/engine.py:123`, `core/engine.py:124`, `telegram_bot/handlers/diagnose_handler.py:167` (+1 more) |
| `MAX_WS_TOKENS` | `'200'` |  |  | `data/websocket_client.py:119` |
| `MCI_ANTISYM_WEIGHT` | `'0.35'` |  |  | `calibration/coherence.py:52` |
| `MCI_COVERAGE_WEIGHT` | `'0.25'` |  |  | `calibration/coherence.py:53` |
| `MCI_ENABLED` | `'true'` |  | ✅ | `calibration/coherence.py:49`, `core/signal_fusion.py:384` |
| `MCI_ERROR_WEIGHT` | `'0.25'` |  |  | `calibration/coherence.py:54` |
| `MCI_MINIMUM` | `'0.40'` |  | ✅ | `calibration/coherence.py:50` |
| `MCI_SIZE_PENALTY` | `'0.60'` |  |  | `calibration/coherence.py:51` |
| `MCI_VOLUME_WEIGHT` | `'0.15'` |  |  | `calibration/coherence.py:55` |
| `MIN_BALANCE_FLOOR` | `None` |  | ✅ | `core/engine.py:133`, `core/engine.py:134` |
| `MIN_COMPOSITE` | `'0.30'` | ✅ | ✅ | `core/ai_brain.py:731`, `core/engine_signals.py:741`, `core/signal_fusion.py:116` |
| `MIN_EDGE_OVER_FEE` | `'2.0'` | ✅ |  | `core/engine_signals.py:1107` |
| `MIN_ORDER_SHARES` | `'1.0'` |  | ✅ | `core/engine.py:1192`, `core/engine_signals.py:52`, `telegram_bot/handlers/diagnose_handler.py:171` (+1 more) |
| `MIN_ORDER_USD` | `'2.0'` |  |  | `telegram_bot/handlers/diagnose_handler.py:170`, `telegram_bot/handlers/diagnose_handler.py:344` |
| `MIN_TRADES_BEFORE_PAUSE` | `'20'` |  |  | `core/auto_optimizer.py:33` |
| `NEWS_LOOKBACK_MINUTES` | `'30'` |  |  | `data_feeds/news_scanner.py:39` |
| `NEWS_MAX_ENTRIES` | `'50'` |  |  | `data_feeds/news_scanner.py:40` |
| `NEWS_MIN_SCORE` | `'0.3'` |  |  | `data_feeds/news_scanner.py:38` |
| `NEWS_POLL_INTERVAL` | `'60'` |  |  | `data_feeds/news_scanner.py:36` |
| `NEWS_SCANNER_ENABLED` | `'true'` |  |  | `data_feeds/news_scanner.py:35` |
| `NEWS_SIGNAL_WEIGHT` | `'0.08'` |  |  | `data_feeds/news_scanner.py:37` |
| `OB_CACHE_TTL` | `'2.0'` |  |  | `core/engine.py:164` |
| `OB_SIGNAL_DECAY` | `'0.85'` |  | ✅ | `core/signal_fusion.py:401` |
| `OB_SIGNAL_LEVELS` | `'20'` |  | ✅ | `core/signal_fusion.py:400` |
| `OPENROUTER_API_KEY` | `''` |  |  | `core/ai_brain.py:211` |
| `OPTIMISM_TAX_ENABLED` | `'true'` | ✅ | ✅ | `core/engine_signals.py:1541` |
| `OPTIMISM_TAX_TICKS` | `'1'` | ✅ | ✅ | `core/engine_signals.py:1542` |
| `PATTERN_DISCOVERY_ENABLED` | `'1'` |  |  | `telegram_bot/bot.py:763` |
| `PATTERN_DISCOVERY_FIRST_SEC` | `'3600'` |  |  | `telegram_bot/bot.py:766` |
| `PATTERN_DISCOVERY_INTERVAL_SEC` | `'604800'` |  |  | `telegram_bot/bot.py:765` |
| `PENNY_ZONE_ENABLED` | `'true'` |  | ✅ | `core/strategy_plugins.py:632` |
| `PENNY_ZONE_MAX_CONFIDENCE` | `'0.70'` |  |  | `core/strategy_plugins.py:636` |
| `PENNY_ZONE_MAX_PRICE` | `'0.05'` |  |  | `core/strategy_plugins.py:633` |
| `PENNY_ZONE_MIN_HIGH_PRICE` | `'0.95'` |  |  | `core/strategy_plugins.py:634` |
| `PENNY_ZONE_MIN_SPREAD` | `'0.01'` |  |  | `core/strategy_plugins.py:635` |
| `PNL_DIVERGENCE_ALERT_PCT` | `'5.0'` | ✅ |  | `telegram_bot/handlers/live_guards_handler.py:191`, `telegram_bot/jobs/pnl_divergence_job.py:58` |
| `PNL_DIVERGENCE_ENABLED` | `'true'` | ✅ |  | `telegram_bot/bot.py:736`, `telegram_bot/handlers/live_guards_handler.py:189`, `telegram_bot/jobs/pnl_divergence_job.py:48` |
| `PNL_DIVERGENCE_FIRST_SEC` | `'3600'` |  |  | `telegram_bot/bot.py:738` |
| `PNL_DIVERGENCE_INTERVAL_SEC` | `'86400'` |  |  | `telegram_bot/bot.py:737` |
| `PNL_DIVERGENCE_MIN_TRADES` | `'5'` | ✅ |  | `telegram_bot/handlers/live_guards_handler.py:192`, `telegram_bot/jobs/pnl_divergence_job.py:59` |
| `PNL_DIVERGENCE_WINDOW_H` | `'24'` | ✅ |  | `telegram_bot/handlers/live_guards_handler.py:190`, `telegram_bot/jobs/pnl_divergence_job.py:57` |
| `PNL_PAUSE_THRESHOLD` | `'-8.0'` | ✅ |  | `core/auto_optimizer.py:45` |
| `POLYBACKTEST_API_KEY` | `''` |  | ✅ | `backtest/data_sources/polybacktest.py:49` |
| `POLYGON_PRIVATE_KEY` | `''` |  | ✅ | `core/live_trader.py:160`, `core/live_trader.py:439`, `data/polymarket_actions.py:230` (+2 more) |
| `POLYGON_WALLET` | `''` |  | ✅ | `core/live_trader.py:696`, `core/live_trader.py:161`, `core/live_trader.py:440` (+5 more) |
| `POLYMARKET_API_KEY` | `''` |  | ✅ | `core/live_trader.py:243` |
| `POLYMARKET_API_SECRET` | `''` |  | ✅ | `core/live_trader.py:244` |
| `POLYMARKET_BUILDER_CODE` | `''` |  | ✅ | `core/live_trader.py:552` |
| `POLYMARKET_CLOB_HOST` | `'https://clob.polymarket.com'` |  | ✅ | `data/polymarket_actions.py:42`, `data/polymarket_portfolio.py:44` |
| `POLYMARKET_DATA_API` | `'https://data-api.polymarket.com'` |  | ✅ | `data/polymarket_portfolio.py:43` |
| `POLYMARKET_PASSPHRASE` | `''` |  | ✅ | `core/live_trader.py:245` |
| `POLYPAPER_DB` | `<expr>` |  |  | `backtest/archive_reader.py:112` |
| `PORT` | `8080` |  |  | `core/keepalive.py:22` |
| `PORTFOLIO_ALERT_COOLDOWN_SEC` | `'1800'` |  | ✅ | `telegram_bot/jobs/polymarket_portfolio_job.py:108` |
| `PORTFOLIO_FAIL_ALERT_THRESHOLD` | `'5'` |  | ✅ | `telegram_bot/jobs/polymarket_portfolio_job.py:63` |
| `PORTFOLIO_HTTP_TIMEOUT` | `'10.0'` |  | ✅ | `data/polymarket_portfolio.py:45` |
| `PORTFOLIO_REFRESH_ENABLED` | `'true'` |  | ✅ | `telegram_bot/bot.py:725`, `telegram_bot/jobs/polymarket_portfolio_job.py:39` |
| `PORTFOLIO_REFRESH_FIRST_SEC` | `'30'` |  | ✅ | `telegram_bot/bot.py:727` |
| `PORTFOLIO_REFRESH_SEC` | `'60'` |  | ✅ | `telegram_bot/bot.py:726` |
| `PRICE_ALERT_ENABLED` | `'1'` |  |  | `telegram_bot/bot.py:747` |
| `PRICE_ALERT_INTERVAL_SEC` | `'30'` |  |  | `telegram_bot/bot.py:748` |
| `PRICE_SANITY_HI` | `'0.99'` |  |  | `core/engine_signals.py:800` |
| `PRICE_SANITY_LO` | `'0.02'` |  |  | `core/engine_signals.py:799` |
| `PROTECTED_STRATEGY_TYPES` | `'classic'` |  |  | `core/auto_optimizer.py:84`, `core/strategy_lifecycle.py:45`, `telegram_bot/handlers/live_guards_handler.py:214` |
| `REMAINING_EDGE_MIN` | `'0.05'` |  | ✅ | `core/engine_monitor.py:41` |
| `REPLIT_DEV_DOMAIN` | `''` |  |  | `core/keepalive.py:47`, `core/keepalive.py:202` |
| `REPLIT_DOMAINS` | `''` |  |  | `core/keepalive.py:46` |
| `REST_TIMING_BUFFER_SIZE` | `'10000'` |  |  | `core/observability/rest_timing.py:76` |
| `REST_TIMING_TELEMETRY` | `'false'` | ✅ |  | `core/observability/rest_timing.py:74` |
| `ROLLING_WR_KILL` | `'40.0'` | ✅ |  | `core/auto_optimizer.py:67` |
| `ROLLING_WR_WINDOW` | `'20'` | ✅ |  | `core/auto_optimizer.py:59` |
| `ROUND_NUMBER_ENABLED` | `'true'` |  | ✅ | `core/signal_fusion.py:538` |
| `ROUND_NUM_ALPHA` | `'0.024'` |  | ✅ | `core/signal_fusion.py:539` |
| `ROUND_NUM_BETA` | `'0.03'` |  | ✅ | `core/signal_fusion.py:540` |
| `SCAN_INTERVAL_S` | `'5'` |  |  | `data/market_scanner.py:32` |
| `SHADOW_COMPARE_FIRST_SEC` | `'1800'` |  |  | `telegram_bot/bot.py:719` |
| `SHADOW_COMPARE_INTERVAL_SEC` | `'3600'` |  |  | `telegram_bot/bot.py:718` |
| `SHADOW_COMPARE_MIN_TRADES` | `'10'` |  | ✅ | `telegram_bot/jobs/shadow_vs_paper_job.py:94` |
| `SHADOW_COMPARE_PNL_ALERT` | `'5.0'` |  | ✅ | `telegram_bot/jobs/shadow_vs_paper_job.py:92` |
| `SHADOW_COMPARE_WINDOW_H` | `'24'` |  | ✅ | `telegram_bot/jobs/shadow_vs_paper_job.py:91` |
| `SHADOW_COMPARE_WR_ALERT` | `'15.0'` |  | ✅ | `telegram_bot/jobs/shadow_vs_paper_job.py:93` |
| `SHADOW_REPORT_FIRST_SEC` | `'60'` |  |  | `telegram_bot/bot.py:680` |
| `SHADOW_REPORT_INTERVAL_SEC` | `'1800'` |  |  | `telegram_bot/bot.py:679` |
| `SHADOW_WATCHED_TYPES` | `None` |  |  | `telegram_bot/jobs/shadow_report_job.py:73` |
| `SIGNAL_DRIFT_WINDOW` | `'100'` |  |  | `core/regime.py:136` |
| `SIGNAL_W_EMA` | `'0.25'` |  |  | `core/ai_brain.py:733`, `core/signal_fusion.py:59` |
| `SIGNAL_W_MOMENTUM` | `'0.30'` |  |  | `core/ai_brain.py:734`, `core/signal_fusion.py:60` |
| `SIGNAL_W_ODDS` | `'0.05'` |  |  | `core/ai_brain.py:732`, `core/signal_fusion.py:58` |
| `SIGNAL_W_ORDERBOOK` | `'0.20'` |  |  | `core/ai_brain.py:736`, `core/signal_fusion.py:63` |
| `SIGNAL_W_TIME` | `'0.10'` |  |  | `core/ai_brain.py:735`, `core/signal_fusion.py:62` |
| `SIGNAL_W_VOLATILITY` | `'0.00'` |  |  | `core/signal_fusion.py:61` |
| `SIGNAL_W_WHALE` | `'0.00'` |  |  | `core/signal_fusion.py:64` |
| `SLIPPAGE_GATE_ENABLED` | `'true'` | ✅ | ✅ | `core/engine_signals.py:1070` |
| `SMART_EXIT_ENABLED` | `'true'` |  | ✅ | `core/ai_brain.py:743`, `core/engine_monitor.py:38` |
| `SMART_EXIT_GRACE_SEC` | `'60'` |  | ✅ | `core/engine_monitor.py:48` |
| `STOP_LOSS_DELTA` | `'0.12'` |  | ✅ | `core/engine_monitor.py:44` |
| `STRATEGY_SUGGESTER_ENABLED` | `'true'` |  |  | `telegram_bot/bot.py:780` |
| `STRATS_ZERO_WARN_MINUTES` | `'10'` |  |  | `core/engine.py:857` |
| `STREAK_COOLDOWN_HOURS` | `'6'` |  |  | `core/risk_manager.py:262`, `core/risk_manager.py:644` |
| `SURFACE_2D_ANTISYM_THRESHOLD` | `'0.03'` |  |  | `calibration/surface_2d.py:51` |
| `SURFACE_2D_CLAMP` | `'0.20'` |  | ✅ | `calibration/surface_2d.py:49` |
| `SURFACE_2D_ENABLED` | `'true'` |  | ✅ | `calibration/surface_2d.py:47`, `core/engine.py:274`, `core/engine_signals.py:967` |
| `SURFACE_2D_FALLBACK_1D` | `'true'` |  |  | `calibration/surface_2d.py:52` |
| `SURFACE_2D_TIME_BINS` | `'6'` |  |  | `calibration/surface_2d.py:50` |
| `SURFACE_2D_WEIGHT` | `'0.12'` |  | ✅ | `calibration/surface_2d.py:48` |
| `TAKER_STUCK_TIMEOUT_SEC` | `'120'` | ✅ |  | `core/engine_fills.py:223` |
| `TECHNICAL_INDICATORS_ENABLED` | `'false'` |  | ✅ | `core/ai_brain.py:739`, `core/signal_fusion.py:556` |
| `THOMPSON_TOP_PCT` | `'0.40'` |  |  | `core/strategy_selector.py:26` |
| `TRADE_MEMORY_BOOST_MAX` | `'0.15'` |  | ✅ | `core/trade_memory.py:43` |
| `TRADE_MEMORY_ENABLED` | `'true'` |  | ✅ | `core/engine.py:241`, `core/trade_memory.py:40` |
| `TRADE_MEMORY_LOOKBACK_DAYS` | `'30'` |  | ✅ | `core/trade_memory.py:42` |
| `TRADE_MEMORY_MIN_TRADES` | `'5'` |  | ✅ | `core/trade_memory.py:41` |
| `TRADE_MEMORY_PENALTY_MAX` | `'0.20'` |  | ✅ | `core/trade_memory.py:44` |
| `TRADE_REASONING_LOG` | `'true'` | ✅ |  | `core/engine_signals.py:1690` |
| `UNSELLABLE_CHECK_ENABLED` | `'true'` | ✅ |  | `core/engine_signals.py:1144` |
| `UNSELLABLE_CLOSE_WARNING_MINS` | `'2.0'` |  |  | `core/risk_manager.py:561` |
| `UNSELLABLE_MIN_ENTRY_DEPTH` | `'5.0'` |  |  | `core/risk_manager.py:560` |
| `WAL_CHECKPOINT_FIRST_SEC` | `'1200'` |  |  | `telegram_bot/bot.py:703` |
| `WAL_CHECKPOINT_INTERVAL_HOURS` | `'6'` |  |  | `telegram_bot/bot.py:701` |
| `WARMUP_MAX_WAIT` | `'120'` |  |  | `core/engine.py:781` |
| `WEEKEND_SAT_MULT` | `'2.4'` |  | ✅ | `core/signal_fusion.py:535` |
| `WEEKEND_SUN_MULT` | `'2.1'` |  | ✅ | `core/signal_fusion.py:536` |
| `WHALE_LOOKBACK_SECONDS` | `None` |  | ✅ | `core/signals/whale_flow.py:51` |
| `WHALE_MIN_TRADES` | `None` |  | ✅ | `core/signals/whale_flow.py:58` |
| `WHALE_MIN_VOLUME_USD` | `None` |  | ✅ | `core/signals/whale_flow.py:65` |
| `WHALE_SIGNAL_ENABLED` | `'false'` |  | ✅ | `core/ai_brain.py:737`, `core/signal_fusion.py:543` |
| `WHALE_SIGNAL_WEIGHT` | `None` |  | ✅ | `core/signal_fusion.py:123` |
| `WHALE_USD_THRESHOLD` | `'1000'` |  | ✅ | `data/market_recorder.py:677` |
| `WHIPSAW_BAND_HI` | `'0.60'` |  |  | `core/engine_signals.py:414` |
| `WHIPSAW_BAND_LO` | `'0.40'` |  |  | `core/engine_signals.py:413` |
| `WS_FORCE_RECONNECT_SEC` | `'300'` |  |  | `data/websocket_client.py:226` |
| `WS_STALE_MIN_THRESHOLD` | `'0.70'` | ✅ |  | `core/engine_signals.py:405` |
| `WS_STALE_SEC` | `'60'` |  |  | `data/websocket_client.py:404` |
| `WS_STALE_THRESHOLD` | `'60.0'` | ✅ |  | `core/engine.py:988`, `data/websocket_client.py:404`, `telegram_bot/handlers/diagnose_handler.py:172` (+2 more) |

## Drift Detection

Run in CI:
```bash
python scripts/gen_env_reference.py --check
```
Exits 1 if this document is stale relative to current `os.getenv(...)` scan. Fix: re-run without `--check` to regenerate + commit.

## Scope

Production dirs scanned: `core/`, `data/`, `telegram_bot/`, `db/`, `calibration/`, `backtest/`, `data_feeds/`, `indicators/`

Excluded: `tests/`, `scripts/`, `_archive/`, project root.

