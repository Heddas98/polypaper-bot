# PolyPaper Bot — Environment Variable Reference

> **Auto-generated** by `scripts/gen_env_reference.py` (T11.7 doctrine).
> Do not hand-edit. Run the generator after adding any new `os.getenv(...)` call in production code.
>
> **Total keys:** 303 distinct env vars read across production dirs.
> **Whitelist coverage:** 41 / 41 whitelisted keys have at least one reader.
> **`.env.example` coverage:** 133 / 303 runtime keys are documented in `.env.example`.

## Legend

- **Key:** the environment variable name
- **Default:** default value when unset (`None` if no fallback, `<expr>` if non-literal)
- **Whitelist:** `/env_toggle` runtime-tuneable? (`✅` = in `config/env_whitelist.py`)
- **`.env.example`:** documented in the template file? (`✅` = yes)
- **Readers:** first 3 call sites (see full scan for more)

## Reference Table

| Key | Default | Whitelist | `.env.example` | Readers |
|-----|---------|-----------|----------------|---------|
| `ADAPTIVE_DEAD_THRESHOLD` | `'0.85'` |  |  | `core/auto_optimizer.py:786` |
| `ADAPTIVE_MAKER_ENABLED` | `'true'` | ✅ | ✅ | `core/engine_signals.py:1640` |
| `ADAPTIVE_MAKER_IMPROVE_TICKS` | `'1'` | ✅ |  | `core/engine_signals.py:1681` |
| `ADAPTIVE_MAKER_MAX_SIGNAL` | `'0.45'` | ✅ |  | `core/engine_signals.py:1670` |
| `ADAPTIVE_MAKER_MIN_MINS` | `'2.0'` | ✅ |  | `core/engine_signals.py:1669` |
| `ADAPTIVE_MAX_THRESHOLD` | `'0.85'` |  |  | `core/ai_brain.py:708`, `core/auto_optimizer.py:785` |
| `ADAPTIVE_PNL_ENABLED` | `'true'` |  | ✅ | `core/auto_optimizer.py:111` |
| `ADAPTIVE_PNL_FLOOR` | `'-10.0'` |  | ✅ | `core/auto_optimizer.py:114` |
| `ADAPTIVE_PNL_STEP` | `'0.5'` |  | ✅ | `core/auto_optimizer.py:112` |
| `ADAPTIVE_PNL_TRADES_PER_STEP` | `'20'` |  | ✅ | `core/auto_optimizer.py:113` |
| `ADMIN_CHAT_ID` | `None` |  |  | `core/engine.py:724`, `core/engine.py:1035`, `core/engine_settlement.py:363` (+7 more) |
| `ADMIN_TELEGRAM_ID` | `'0'` |  | ✅ | `core/auto_optimizer.py:634`, `core/engine.py:724`, `core/engine.py:1035` (+16 more) |
| `AI_ADVISOR_ENABLED` | `'false'` | ✅ |  | `core/ai_brain_client.py:50` |
| `AI_ADVISOR_INTERNAL_KEY` | `''` |  |  | `core/ai_brain_client.py:72` |
| `AI_ADVISOR_TIMEOUT_S` | `'8.0'` |  |  | `core/ai_brain_client.py:61` |
| `AI_ADVISOR_URL` | `'http://127.0.0.1:8001'` |  |  | `core/ai_brain_client.py:56` |
| `AI_AUTO_CONFIDENCE` | `'0.70'` |  | ✅ | `core/ai_brain.py:331`, `core/ai_brain.py:1964` |
| `AI_BRAIN_CYCLE` | `'3600'` |  | ✅ | `core/ai_brain.py:104` |
| `AI_MIN_TRADES` | `'15'` |  | ✅ | `core/ai_brain.py:106` |
| `AI_STRATEGY_SUGGEST_INTERVAL` | `'14400'` |  |  | `core/strategy_suggester.py:25` |
| `AI_TWO_AGENT_MODE` | `'true'` |  | ✅ | `core/ai_brain.py:283` |
| `ALERT_DAILY_PNL_PCT` | `'0.4'` |  |  | `core/risk_manager.py:451` |
| `ALERT_LOSS_STREAK` | `'5'` |  |  | `core/risk_manager.py:431` |
| `ALLOWANCE_MIN_USD` | `'1000'` |  |  | `core/allowance_preflight.py:73` |
| `ALLOWANCE_PREFLIGHT_ENABLED` | `'false'` |  |  | `core/engine.py:571` |
| `ALLOWED_ZONES` | `'(tumu)'` |  | ✅ | `core/ai_brain.py:706`, `core/engine_signals.py:68` |
| `ANTHROPIC_API_KEY` | `''` |  | ✅ | `core/ai_brain.py:33`, `core/intent_parser.py:47` |
| `ARCHIVE_DIR` | `<expr>` |  |  | `backtest/archive_reader.py:110` |
| `AUTO_PROMOTE_ENABLED` | `'1'` |  |  | `telegram_bot/bot.py:1151` |
| `AUTO_PROMOTE_FIRST_SEC` | `'1200'` |  |  | `telegram_bot/bot.py:1153` |
| `AUTO_PROMOTE_INTERVAL_SEC` | `'86400'` |  |  | `telegram_bot/bot.py:1152` |
| `AUTO_REDEEM_ENABLED` | `'false'` |  | ✅ | `telegram_bot/bot.py:1101`, `telegram_bot/handlers/main_dashboard.py:312`, `telegram_bot/jobs/auto_redeem_job.py:37` |
| `AUTO_REDEEM_FIRST_SEC` | `'120'` |  | ✅ | `telegram_bot/bot.py:1106` |
| `AUTO_REDEEM_INTERVAL_SEC` | `'300'` |  | ✅ | `telegram_bot/bot.py:1105` |
| `AUTO_REDEEM_MIN_VALUE_USD` | `'0.10'` |  | ✅ | `telegram_bot/jobs/auto_redeem_job.py:50` |
| `AUTO_RESUME_MIN_PNL` | `'0.0'` |  | ✅ | `core/auto_optimizer.py:472` |
| `AUTO_RESUME_MIN_WR` | `'50.0'` |  | ✅ | `core/auto_optimizer.py:473` |
| `AUTO_RESUME_ON_STARTUP` | `'false'` |  | ✅ | `core/auto_optimizer.py:469` |
| `BALANCE_PREFLIGHT` | `'true'` |  |  | `core/live_trader.py:812` |
| `BAYESIAN_SIGNAL_ACCURACY` | `'0.60'` |  | ✅ | `core/signal_fusion.py:552` |
| `BAYESIAN_UPDATER_ENABLED` | `'false'` |  | ✅ | `core/ai_brain.py:697`, `core/signal_fusion.py:551` |
| `BECKER_DECISION_MODE` | `'boost'` |  |  | `telegram_bot/jobs/shadow_report_job.py:256` |
| `BG_TASK_HISTORY_SIZE` | `'50'` |  |  | `core/bg_task.py:58` |
| `BG_TASK_NOTIFY_COOLDOWN_SEC` | `'300'` |  |  | `core/bg_task.py:98` |
| `BG_TASK_NOTIFY_ENABLED` | `'1'` |  |  | `core/bg_task.py:61` |
| `BONDING_CONFIDENCE_BASE` | `'0.80'` |  | ✅ | `backtest/strategies/bonding_yield.py:29`, `core/strategy_plugins.py:751` |
| `BONDING_MAX_HOURS_LEFT` | `'48'` |  | ✅ | `backtest/strategies/bonding_yield.py:28`, `core/strategy_plugins.py:750` |
| `BONDING_MAX_PRICE` | `'0.99'` |  | ✅ | `backtest/strategies/bonding_yield.py:25`, `core/strategy_plugins.py:748` |
| `BONDING_MIN_PRICE` | `'0.90'` |  | ✅ | `backtest/strategies/bonding_yield.py:24`, `core/strategy_plugins.py:747` |
| `BONDING_MIN_YIELD` | `'0.01'` |  | ✅ | `backtest/strategies/bonding_yield.py:26`, `core/strategy_plugins.py:749` |
| `BONDING_TIME_WEIGHT` | `'true'` |  | ✅ | `backtest/strategies/bonding_yield.py:27` |
| `BRIER_ALARM_ENABLED` | `'true'` | ✅ |  | `core/engine_signals.py:1245` |
| `BRIER_GAP_MAX` | `'0.30'` |  | ✅ | `core/engine_signals.py:78` |
| `CALENDAR_MULT_ENABLED` | `'false'` |  | ✅ | `core/ai_brain.py:699`, `core/signal_fusion.py:538` |
| `CANARY_SIZE_MULT` | `'1.0'` |  | ✅ | `core/engine_signals.py:1540` |
| `CHANGELOG_DEFAULT_LIMIT` | `'20'` |  |  | `telegram_bot/handlers/changelog_handler.py:36` |
| `CHANGELOG_MAX_LIMIT` | `'100'` |  |  | `telegram_bot/handlers/changelog_handler.py:37` |
| `CLASSIC_BYPASS_ALL_GATES` | `'true'` | ✅ | ✅ | `core/engine_signals.py:155` |
| `CLASSIC_NOTIFY_ALL_STYPES` | `'false'` |  |  | `core/engine_settlement.py:346`, `core/engine_settlement.py:437` |
| `CLASSIC_NOTIFY_RESOLUTION` | `'true'` | ✅ |  | `core/engine_settlement.py:107`, `core/engine_settlement.py:196` |
| `CLASSIC_RESPECT_FEE_TAIL` | `'false'` | ✅ |  | `core/engine_signals.py:1756` |
| `CLASSIC_RESPECT_TOKEN_CAP` | `'false'` | ✅ |  | `core/engine_signals.py:1825` |
| `CLASSIC_RESPECT_UNSELLABLE` | `'false'` | ✅ | ✅ | `core/engine_signals.py:1267` |
| `CLASSIC_RESPECT_ZONES` | `'false'` | ✅ | ✅ | `core/engine_signals.py:866` |
| `CLASSIC_TAKER_LIMIT_CEIL` | `'0.99'` | ✅ |  | `core/engine_signals.py:1721` |
| `CLOB_CLIENT_CACHE_TTL_S` | `'3600'` |  |  | `data/polymarket_portfolio.py:521` |
| `CLOB_FORCE_DERIVE` | `'false'` |  |  | `core/live_trader.py:283` |
| `CLOB_SIGNATURE_TYPE` | `'2'` |  | ✅ | `core/live_trader.py:264`, `core/live_trader.py:752`, `data/polymarket_actions.py:67` (+2 more) |
| `CLOB_TIMEOUT` | `'5.0'` |  | ✅ | `data/polymarket_client.py:22` |
| `CONFLUENCE_K` | `'3'` |  | ✅ | `core/ai_brain.py:702`, `core/signal_fusion.py:556` |
| `CONFLUENCE_MODE` | `'true'` |  | ✅ | `core/signal_fusion.py:555` |
| `CONFLUENCE_PENALTY` | `'0.3'` |  | ✅ | `core/ai_brain.py:703`, `core/signal_fusion.py:559` |
| `CONFLUENCE_SIGNAL_THRESHOLD` | `'0.05'` |  |  | `core/signal_fusion.py:557` |
| `CONVICTION_ENABLED` | `'true'` |  |  | `core/engine_signals.py:1428` |
| `CONVICTION_MIN` | `'0.3'` | ✅ | ✅ | `core/engine_signals.py:1444` |
| `DB_ARCHIVE_ENABLED` | `'1'` |  |  | `telegram_bot/bot.py:1180` |
| `DB_ARCHIVE_FIRST_SEC` | `'600'` |  |  | `telegram_bot/bot.py:1182` |
| `DB_ARCHIVE_INTERVAL_SEC` | `'86400'` |  |  | `telegram_bot/bot.py:1181` |
| `DB_RETENTION_FIRST_SEC` | `'900'` |  | ✅ | `telegram_bot/bot.py:1034` |
| `DB_RETENTION_INTERVAL_SEC` | `'86400'` |  | ✅ | `telegram_bot/bot.py:1033` |
| `DB_RETENTION_MODE` | `'report'` |  | ✅ | `telegram_bot/jobs/db_retention_job.py:239` |
| `DB_RETENTION_NOTIFY` | `'1'` |  | ✅ | `telegram_bot/jobs/db_retention_job.py:353` |
| `DB_RETENTION_VACUUM_ENABLED` | `'1'` |  | ✅ | `telegram_bot/jobs/db_retention_job.py:318` |
| `DB_SNAPSHOT_FIRST_SEC` | `'300'` |  |  | `telegram_bot/bot.py:1003` |
| `DB_SNAPSHOT_INTERVAL_SEC` | `'86400'` |  |  | `telegram_bot/bot.py:1002` |
| `DEBUG_SHOW_EXC` | `'false'` |  |  | `telegram_bot/handlers/_exc_render.py:55` |
| `DECISION_EXPLAINER_ENABLED` | `'true'` |  | ✅ | `core/decision_explainer.py:29`, `core/engine.py:244` |
| `DECISION_NOTIFY_DETAIL` | `'medium'` |  | ✅ | `core/decision_explainer.py:30` |
| `DISPOSITION_TRACKING` | `'true'` |  | ✅ | `core/engine_monitor.py:52` |
| `EDGE_ZONE_5065_MIN` | `'0.45'` | ✅ | ✅ | `core/engine_signals.py:1203` |
| `ENABLE_DAILY_DB_SNAPSHOT` | `'true'` |  |  | `telegram_bot/jobs/maintenance_jobs.py:123` |
| `ENABLE_WAL_CHECKPOINT` | `'true'` |  |  | `telegram_bot/jobs/maintenance_jobs.py:310` |
| `ENGINE_STALL_ENABLED` | `'1'` |  |  | `core/engine.py:698` |
| `ENGINE_STALL_TIMEOUT` | `'90'` |  |  | `core/engine.py:701` |
| `EVENT_CALENDAR_ENABLED` | `'true'` |  |  | `data/event_monitor.py:69` |
| `EV_FEE_OVERRIDE` | `'0.0'` |  |  | `calibration/ev_threshold.py:42` |
| `EV_MINIMUM` | `'0.005'` |  | ✅ | `calibration/ev_threshold.py:39` |
| `EV_SIZE_PENALTY` | `'0.50'` |  | ✅ | `calibration/ev_threshold.py:41` |
| `EV_STRICT_MODE` | `'false'` |  | ✅ | `calibration/ev_threshold.py:40` |
| `EV_THRESHOLD_ENABLED` | `'true'` |  | ✅ | `calibration/ev_threshold.py:38`, `core/engine_signals.py:1480` |
| `EXPERIMENT_ENABLED` | `'true'` |  | ✅ | `core/engine.py:256`, `core/experiment_runner.py:27` |
| `EXPERIMENT_MAX_PARAMS` | `'5'` |  | ✅ | `core/experiment_runner.py:28` |
| `FEE_GATE_ENABLED` | `'true'` | ✅ |  | `core/engine_signals.py:1229` |
| `FILL_IMPACT` | `<expr>` |  |  | `core/calibration/fill_heuristic_recalibrate.py:62` |
| `FILL_IMPACT_MIN_FLOOR` | `'0.001'` |  |  | `backtest/simulation/fill_model.py:510` |
| `FILL_IMPACT_SCALE` | `'0.025'` |  |  | `backtest/simulation/fill_model.py:507` |
| `FILL_LATENCY_DRIFT_BPS_PER_MS` | `'0.04'` |  |  | `backtest/simulation/fill_model.py:240` |
| `FILL_SPREAD_COST` | `'0.023'` |  |  | `backtest/simulation/fill_model.py:75`, `core/calibration/fill_heuristic_recalibrate.py:61` |
| `FORCE_EXIT_SECONDS` | `'0'` |  | ✅ | `core/engine_monitor.py:35` |
| `FUSION_BLOCKED_ZONES` | `'30-40'` |  |  | `core/engine_signals.py:75` |
| `GROK_API_KEY` | `''` |  | ✅ | `core/ai_brain.py:34` |
| `HEARTBEAT_ENABLED` | `'false'` |  |  | `core/engine.py:602` |
| `HEARTBEAT_INTERVAL_SEC` | `'600'` |  |  | `telegram_bot/bot.py:998` |
| `INTENT_PARSER_MODEL` | `'claude-haiku-4-5-20251001'` |  |  | `core/intent_parser.py:48` |
| `KELLY_BANKROLL_MAX` | `'10000.0'` |  | ✅ | `core/kelly.py:83` |
| `KELLY_BANKROLL_MIN` | `'10.0'` |  | ✅ | `core/kelly.py:82` |
| `KELLY_DECAY_ENABLED` | `'true'` |  | ✅ | `core/kelly.py:56` |
| `KELLY_DECAY_RANGING` | `'0.167'` |  | ✅ | `core/kelly.py:59` |
| `KELLY_DECAY_TRENDING` | `'0.25'` |  | ✅ | `core/kelly.py:58` |
| `KELLY_DECAY_VOLATILE` | `'0.125'` |  | ✅ | `core/kelly.py:60` |
| `KELLY_EXPLORATION_MIN_WR` | `'0.50'` |  |  | `core/kelly.py:136` |
| `KELLY_MAX_BET_PCT` | `'0.05'` |  | ✅ | `core/kelly.py:47` |
| `LATENCY_DRIFT` | `<expr>` |  |  | `core/calibration/fill_heuristic_recalibrate.py:63` |
| `LIFECYCLE_EVALUATION_MAX` | `'50'` |  |  | `core/strategy_lifecycle.py:39` |
| `LIFECYCLE_EXPLORATION_MAX` | `'20'` |  |  | `core/strategy_lifecycle.py:38` |
| `LIFECYCLE_MIN_OVERRIDE` | `'false'` |  |  | `core/engine_signals.py:785` |
| `LIQUIDITY_MIN_DEPTH_PCT` | `'0.50'` |  |  | `core/risk_manager.py:568` |
| `LIVE_BUDGET` | `'1.49'` | ✅ |  | `core/live_trader.py:115` |
| `LIVE_ENABLED` | `'false'` |  | ✅ | `core/live_trader.py:209`, `telegram_bot/handlers/live_guards_handler.py:77`, `telegram_bot/handlers/main_dashboard.py:310` (+1 more) |
| `LIVE_MAX_DAILY_LOSS` | `'1.00'` | ✅ |  | `core/live_trader.py:85` |
| `LIVE_MAX_MARKET_TRADE` | `'25.0'` |  |  | `core/live_trader.py:637` |
| `LIVE_MAX_TRADE` | `'1.00'` | ✅ |  | `core/live_trader.py:77` |
| `LIVE_MIN_ODDS` | `'0.75'` | ✅ |  | `core/live_trader.py:101` |
| `LIVE_MIN_SIGNAL` | `'0.75'` | ✅ |  | `core/live_trader.py:93` |
| `LIVE_SLIPPAGE_PCT` | `'2.0'` |  |  | `telegram_bot/handlers/live_handler.py:807` |
| `LLM_RATELIMIT_BACKOFF_SEC` | `'60'` | ✅ |  | `core/ai_brain.py:60` |
| `LLM_RATELIMIT_MIN_COST` | `'0.001'` | ✅ |  | `core/ai_brain.py:68` |
| `LOG_SECRET_SCRUB` | `'true'` |  |  | `core/structured_logging.py:151` |
| `MAKER_MODE_ENABLED` | `'false'` |  |  | `core/maker_taker_decision.py:48` |
| `MAKER_REBATE_ENABLED` | `'true'` |  | ✅ | `core/engine_settlement.py:134` |
| `MAKER_SPREAD_THRESHOLD_TICKS` | `'2'` |  |  | `core/maker_taker_decision.py:41` |
| `MAKER_WIDE_SPREAD` | `'0.03'` |  | ✅ | `core/engine_support.py:37` |
| `MAX_429_RETRIES` | `'3'` |  | ✅ | `data/polymarket_client.py:24` |
| `MAX_CALENDAR_MULT` | `'2.5'` |  | ✅ | `core/signal_fusion.py:539` |
| `MAX_CROSS_ASSET_EXPOSURE` | `'0'` |  |  | `core/risk_manager.py:365` |
| `MAX_DAILY_LOSS` | `'500'` |  | ✅ | `core/engine.py:104`, `core/engine.py:105`, `telegram_bot/handlers/diagnose_handler.py:169` (+1 more) |
| `MAX_DAILY_TRADES` | `None` |  | ✅ | `core/engine.py:108`, `core/engine.py:109` |
| `MAX_LOSS_STREAK` | `'5'` |  | ✅ | `core/engine.py:106`, `core/engine.py:107`, `telegram_bot/handlers/diagnose_handler.py:170` (+1 more) |
| `MAX_OB_LEVELS` | `'50'` |  | ✅ | `data/market_recorder.py:47` |
| `MAX_OPEN_POSITIONS` | `'10'` |  | ✅ | `core/engine.py:116`, `core/engine.py:117`, `telegram_bot/handlers/diagnose_handler.py:167` (+1 more) |
| `MAX_POSITION_SIZE` | `None` |  | ✅ | `core/engine.py:118`, `core/engine.py:119` |
| `MAX_STOPS_PER_CYCLE` | `'2'` |  |  | `core/ai_brain.py:1120` |
| `MAX_TOTAL_EXPOSURE` | `'5000'` |  | ✅ | `core/engine.py:110`, `core/engine.py:111`, `telegram_bot/handlers/diagnose_handler.py:168` (+1 more) |
| `MAX_WS_TOKENS` | `'200'` |  |  | `data/websocket_client.py:121` |
| `MCI_ANTISYM_WEIGHT` | `'0.35'` |  |  | `calibration/coherence.py:52` |
| `MCI_COVERAGE_WEIGHT` | `'0.25'` |  |  | `calibration/coherence.py:53` |
| `MCI_ENABLED` | `'true'` |  | ✅ | `calibration/coherence.py:49`, `core/signal_fusion.py:390` |
| `MCI_ERROR_WEIGHT` | `'0.25'` |  |  | `calibration/coherence.py:54` |
| `MCI_MINIMUM` | `'0.40'` |  | ✅ | `calibration/coherence.py:50` |
| `MCI_SIZE_PENALTY` | `'0.60'` |  |  | `calibration/coherence.py:51` |
| `MCI_VOLUME_WEIGHT` | `'0.15'` |  |  | `calibration/coherence.py:55` |
| `MIN_BALANCE_FLOOR` | `None` |  | ✅ | `core/engine.py:120`, `core/engine.py:121` |
| `MIN_COMPOSITE` | `'0.30'` | ✅ | ✅ | `core/ai_brain.py:687`, `core/engine_signals.py:789`, `core/signal_fusion.py:119` |
| `MIN_EDGE_OVER_FEE` | `'2.0'` | ✅ |  | `core/engine_signals.py:1231` |
| `MIN_ORDER_SHARES` | `'1.0'` |  | ✅ | `core/engine.py:1385`, `core/engine_signals.py:65`, `telegram_bot/handlers/diagnose_handler.py:172` (+1 more) |
| `MIN_ORDER_USD` | `'2.0'` |  |  | `telegram_bot/handlers/diagnose_handler.py:171`, `telegram_bot/handlers/diagnose_handler.py:346` |
| `MIN_TRADES_BEFORE_PAUSE` | `'20'` |  |  | `core/auto_optimizer.py:33` |
| `MODE_DEFAULT` | `'paper'` |  |  | `telegram_bot/handlers/main_dashboard.py:311` |
| `NEWS_LOOKBACK_MINUTES` | `'30'` |  |  | `data_feeds/news_scanner.py:37` |
| `NEWS_MAX_ENTRIES` | `'50'` |  |  | `data_feeds/news_scanner.py:38` |
| `NEWS_MIN_SCORE` | `'0.3'` |  |  | `data_feeds/news_scanner.py:36` |
| `NEWS_POLL_INTERVAL` | `'60'` |  |  | `data_feeds/news_scanner.py:34` |
| `NEWS_SCANNER_ENABLED` | `'true'` |  |  | `data_feeds/news_scanner.py:33` |
| `NEWS_SIGNAL_WEIGHT` | `'0.08'` |  |  | `data_feeds/news_scanner.py:35` |
| `OB_CACHE_TTL` | `'2.0'` |  |  | `core/engine.py:152` |
| `OB_SIGNAL_DECAY` | `'0.85'` |  | ✅ | `core/signal_fusion.py:407` |
| `OB_SIGNAL_LEVELS` | `'20'` |  | ✅ | `core/signal_fusion.py:406` |
| `OPENROUTER_API_KEY` | `''` |  |  | `core/ai_brain.py:124` |
| `OPTIMISM_TAX_ENABLED` | `'true'` | ✅ | ✅ | `core/engine_signals.py:1698` |
| `OPTIMISM_TAX_TICKS` | `'1'` | ✅ | ✅ | `core/engine_signals.py:1699` |
| `PAPER_BUDGET_DISPLAY` | `'10386.0'` |  |  | `telegram_bot/handlers/main_dashboard.py:159` |
| `PATTERN_DISCOVERY_ENABLED` | `'1'` |  |  | `telegram_bot/bot.py:1162` |
| `PATTERN_DISCOVERY_FIRST_SEC` | `'3600'` |  |  | `telegram_bot/bot.py:1169` |
| `PATTERN_DISCOVERY_INTERVAL_SEC` | `'604800'` |  |  | `telegram_bot/bot.py:1166` |
| `PENNY_ZONE_ENABLED` | `'true'` |  | ✅ | `core/strategy_plugins.py:652` |
| `PENNY_ZONE_MAX_CONFIDENCE` | `'0.70'` |  |  | `core/strategy_plugins.py:656` |
| `PENNY_ZONE_MAX_PRICE` | `'0.05'` |  |  | `core/strategy_plugins.py:653` |
| `PENNY_ZONE_MIN_HIGH_PRICE` | `'0.95'` |  |  | `core/strategy_plugins.py:654` |
| `PENNY_ZONE_MIN_SPREAD` | `'0.01'` |  |  | `core/strategy_plugins.py:655` |
| `PNL_DIVERGENCE_ALERT_PCT` | `'5.0'` | ✅ |  | `telegram_bot/handlers/live_guards_handler.py:188`, `telegram_bot/jobs/pnl_divergence_job.py:57` |
| `PNL_DIVERGENCE_ENABLED` | `'true'` | ✅ |  | `telegram_bot/bot.py:1123`, `telegram_bot/handlers/live_guards_handler.py:186`, `telegram_bot/jobs/pnl_divergence_job.py:47` |
| `PNL_DIVERGENCE_FIRST_SEC` | `'3600'` |  |  | `telegram_bot/bot.py:1128` |
| `PNL_DIVERGENCE_INTERVAL_SEC` | `'86400'` |  |  | `telegram_bot/bot.py:1125` |
| `PNL_DIVERGENCE_MIN_TRADES` | `'5'` | ✅ |  | `telegram_bot/handlers/live_guards_handler.py:189`, `telegram_bot/jobs/pnl_divergence_job.py:58` |
| `PNL_DIVERGENCE_WINDOW_H` | `'24'` | ✅ |  | `telegram_bot/handlers/live_guards_handler.py:187`, `telegram_bot/jobs/pnl_divergence_job.py:56` |
| `PNL_PAUSE_THRESHOLD` | `'-8.0'` | ✅ |  | `core/auto_optimizer.py:45` |
| `POLYBACKTEST_API_KEY` | `''` |  | ✅ | `backtest/data_sources/polybacktest.py:47` |
| `POLYGON_PRIVATE_KEY` | `''` |  | ✅ | `core/live_trader.py:207`, `core/live_trader.py:744`, `data/polymarket_actions.py:168` (+4 more) |
| `POLYGON_RPC_URL` | `'https://polygon-rpc.com'` |  |  | `core/reconciliation/onchain_sync.py:76` |
| `POLYGON_WALLET` | `''` |  | ✅ | `core/engine.py:626`, `core/live_trader.py:1096`, `core/live_trader.py:208` (+7 more) |
| `POLYMARKET_API_KEY` | `''` |  | ✅ | `core/live_trader.py:280`, `data/polymarket_actions.py:92`, `data/polymarket_portfolio.py:621` |
| `POLYMARKET_API_SECRET` | `''` |  | ✅ | `core/live_trader.py:281`, `data/polymarket_actions.py:93`, `data/polymarket_portfolio.py:622` |
| `POLYMARKET_BUILDER_CODE` | `''` |  | ✅ | `core/live_trader.py:899` |
| `POLYMARKET_CLOB_HOST` | `'https://clob.polymarket.com'` |  | ✅ | `data/polymarket_actions.py:45`, `data/polymarket_portfolio.py:45` |
| `POLYMARKET_DATA_API` | `'https://data-api.polymarket.com'` |  | ✅ | `data/polymarket_portfolio.py:44` |
| `POLYMARKET_PASSPHRASE` | `''` |  | ✅ | `core/live_trader.py:282`, `data/polymarket_actions.py:94`, `data/polymarket_portfolio.py:623` |
| `POLYPAPER_DB` | `'data_store/polypaper.db'` |  |  | `backtest/archive_reader.py:109`, `core/calibration/fill_heuristic_recalibrate.py:164` |
| `PORT` | `8080` |  |  | `core/keepalive.py:23` |
| `PORTFOLIO_ALERT_COOLDOWN_SEC` | `'1800'` |  | ✅ | `telegram_bot/jobs/polymarket_portfolio_job.py:111` |
| `PORTFOLIO_FAIL_ALERT_THRESHOLD` | `'5'` |  | ✅ | `telegram_bot/jobs/polymarket_portfolio_job.py:64` |
| `PORTFOLIO_HTTP_TIMEOUT` | `'10.0'` |  | ✅ | `data/polymarket_portfolio.py:46` |
| `PORTFOLIO_REFRESH_ENABLED` | `'true'` |  | ✅ | `telegram_bot/bot.py:1086`, `telegram_bot/jobs/polymarket_portfolio_job.py:40` |
| `PORTFOLIO_REFRESH_FIRST_SEC` | `'30'` |  | ✅ | `telegram_bot/bot.py:1088` |
| `PORTFOLIO_REFRESH_SEC` | `'60'` |  | ✅ | `telegram_bot/bot.py:1087` |
| `PRICE_ALERT_ENABLED` | `'1'` |  |  | `telegram_bot/bot.py:1142` |
| `PRICE_ALERT_INTERVAL_SEC` | `'30'` |  |  | `telegram_bot/bot.py:1143` |
| `PRICE_SANITY_HI` | `'0.99'` |  |  | `core/engine_signals.py:854` |
| `PRICE_SANITY_LO` | `'0.02'` |  |  | `core/engine_signals.py:853` |
| `PROTECTED_STRATEGY_TYPES` | `'classic'` |  |  | `core/auto_optimizer.py:85`, `core/strategy_lifecycle.py:46`, `telegram_bot/handlers/live_guards_handler.py:212` |
| `REALITY_GAP_ALERT_PCT` | `'10.0'` |  |  | `telegram_bot/handlers/reality_gap_handler.py:37` |
| `REALITY_GAP_ENABLED` | `'true'` |  |  | `telegram_bot/bot.py:1061`, `telegram_bot/handlers/reality_gap_handler.py:39` |
| `REALITY_GAP_MULT` | `'0.66'` |  |  | `telegram_bot/handlers/reality_gap_handler.py:36` |
| `REALITY_GAP_TIME_HHMM` | `'03:00'` |  |  | `telegram_bot/bot.py:1064` |
| `REALITY_GAP_WINDOW_H` | `'168'` |  |  | `telegram_bot/handlers/reality_gap_handler.py:38` |
| `RECON_ENABLED` | `''` |  |  | `core/reconciliation/onchain_sync.py:200` |
| `REFERENCE_PRICE_AUDIT_ENABLED` | `'true'` |  |  | `core/engine_settlement.py:210` |
| `RELAYER_API_KEY` | `''` |  | ✅ | `data/polymarket_actions.py:186`, `data/polymarket_actions.py:469`, `telegram_bot/jobs/auto_redeem_job.py:45` |
| `RELAYER_API_KEY_ADDRESS` | `''` |  | ✅ | `data/polymarket_actions.py:187`, `data/polymarket_actions.py:470` |
| `RELAYER_HOST` | `'https://relayer-v2.polymarket.com'` |  | ✅ | `data/polymarket_actions.py:188`, `data/polymarket_actions.py:471` |
| `REMAINING_EDGE_MIN` | `'0.05'` |  | ✅ | `core/engine_monitor.py:46` |
| `REPLIT_DEV_DOMAIN` | `''` |  |  | `core/keepalive.py:48`, `core/keepalive.py:233` |
| `REPLIT_DOMAINS` | `''` |  |  | `core/keepalive.py:47` |
| `REST_TIMING_BUFFER_SIZE` | `'10000'` |  |  | `core/observability/rest_timing.py:76` |
| `REST_TIMING_TELEMETRY` | `'false'` | ✅ |  | `core/observability/rest_timing.py:74` |
| `ROLLING_WR_KILL` | `'40.0'` | ✅ |  | `core/auto_optimizer.py:68` |
| `ROLLING_WR_WINDOW` | `'20'` | ✅ |  | `core/auto_optimizer.py:60` |
| `ROUND_NUMBER_ENABLED` | `'true'` |  | ✅ | `core/signal_fusion.py:543` |
| `ROUND_NUM_ALPHA` | `'0.024'` |  | ✅ | `core/signal_fusion.py:544` |
| `ROUND_NUM_BETA` | `'0.03'` |  | ✅ | `core/signal_fusion.py:545` |
| `SCAN_INTERVAL_S` | `'5'` |  |  | `data/market_scanner.py:32` |
| `SENTRY_DSN` | `''` |  | ✅ | `core/observability/sentry_tx.py:54` |
| `SHADOW_COMPARE_FIRST_SEC` | `'1800'` |  |  | `telegram_bot/bot.py:1044` |
| `SHADOW_COMPARE_INTERVAL_SEC` | `'3600'` |  |  | `telegram_bot/bot.py:1043` |
| `SHADOW_COMPARE_MIN_TRADES` | `'10'` |  | ✅ | `telegram_bot/jobs/shadow_vs_paper_job.py:93` |
| `SHADOW_COMPARE_PNL_ALERT` | `'5.0'` |  | ✅ | `telegram_bot/jobs/shadow_vs_paper_job.py:91` |
| `SHADOW_COMPARE_WINDOW_H` | `'24'` |  | ✅ | `telegram_bot/jobs/shadow_vs_paper_job.py:90` |
| `SHADOW_COMPARE_WR_ALERT` | `'15.0'` |  | ✅ | `telegram_bot/jobs/shadow_vs_paper_job.py:92` |
| `SHADOW_REPORT_FIRST_SEC` | `'60'` |  |  | `telegram_bot/bot.py:989` |
| `SHADOW_REPORT_INTERVAL_SEC` | `'1800'` |  |  | `telegram_bot/bot.py:988` |
| `SHADOW_WATCHED_TYPES` | `None` |  |  | `telegram_bot/jobs/shadow_report_job.py:72` |
| `SIGNAL_DRIFT_WINDOW` | `'100'` |  |  | `core/regime.py:138` |
| `SIGNAL_W_EMA` | `'0.25'` |  |  | `core/ai_brain.py:690`, `core/signal_fusion.py:60` |
| `SIGNAL_W_MOMENTUM` | `'0.30'` |  |  | `core/ai_brain.py:691`, `core/signal_fusion.py:61` |
| `SIGNAL_W_ODDS` | `'0.05'` |  |  | `core/ai_brain.py:689`, `core/signal_fusion.py:59` |
| `SIGNAL_W_ORDERBOOK` | `'0.20'` |  |  | `core/ai_brain.py:693`, `core/signal_fusion.py:64` |
| `SIGNAL_W_TIME` | `'0.10'` |  |  | `core/ai_brain.py:692`, `core/signal_fusion.py:63` |
| `SIGNAL_W_VOLATILITY` | `'0.00'` |  |  | `core/signal_fusion.py:62` |
| `SIGNAL_W_WHALE` | `'0.00'` |  |  | `core/signal_fusion.py:65` |
| `SLIPPAGE_GATE_ENABLED` | `'true'` | ✅ | ✅ | `core/engine_signals.py:1190` |
| `SMART_EXIT_ENABLED` | `'true'` |  | ✅ | `core/ai_brain.py:705`, `core/engine_monitor.py:42` |
| `SMART_EXIT_GRACE_SEC` | `'60'` |  | ✅ | `core/engine_monitor.py:54` |
| `STOP_LOSS_DELTA` | `'0.12'` |  | ✅ | `core/engine_monitor.py:50` |
| `STRATEGY_SUGGESTER_ENABLED` | `'false'` |  |  | `telegram_bot/bot.py:1195` |
| `STRATS_ZERO_WARN_MINUTES` | `'10'` |  |  | `core/engine.py:1021` |
| `STREAK_COOLDOWN_HOURS` | `'6'` |  |  | `core/risk_manager.py:287`, `core/risk_manager.py:701` |
| `STRUCTURED_LOG_ENABLED` | `'true'` |  |  | `core/structured_logging.py:174` |
| `STRUCTURED_LOG_FILE` | `'data_store/structured.jsonl'` |  |  | `core/structured_logging.py:178` |
| `SURFACE_2D_ANTISYM_THRESHOLD` | `'0.03'` |  |  | `calibration/surface_2d.py:53` |
| `SURFACE_2D_CLAMP` | `'0.20'` |  | ✅ | `calibration/surface_2d.py:51` |
| `SURFACE_2D_ENABLED` | `'true'` |  | ✅ | `calibration/surface_2d.py:49`, `core/engine.py:268`, `core/engine_signals.py:1076` |
| `SURFACE_2D_FALLBACK_1D` | `'true'` |  |  | `calibration/surface_2d.py:54` |
| `SURFACE_2D_TIME_BINS` | `'6'` |  |  | `calibration/surface_2d.py:52` |
| `SURFACE_2D_WEIGHT` | `'0.12'` |  | ✅ | `calibration/surface_2d.py:50` |
| `TAKER_STUCK_TIMEOUT_SEC` | `'120'` | ✅ |  | `core/engine_fills.py:242` |
| `TECHNICAL_INDICATORS_ENABLED` | `'false'` |  | ✅ | `core/ai_brain.py:698`, `core/signal_fusion.py:563` |
| `THOMPSON_TOP_PCT` | `'0.40'` |  |  | `core/strategy_selector.py:27` |
| `TRADE_MEMORY_BOOST_MAX` | `'0.15'` |  | ✅ | `core/trade_memory.py:42` |
| `TRADE_MEMORY_ENABLED` | `'true'` |  | ✅ | `core/engine.py:232`, `core/trade_memory.py:39` |
| `TRADE_MEMORY_LOOKBACK_DAYS` | `'30'` |  | ✅ | `core/trade_memory.py:41` |
| `TRADE_MEMORY_MIN_TRADES` | `'5'` |  | ✅ | `core/trade_memory.py:40` |
| `TRADE_MEMORY_PENALTY_MAX` | `'0.20'` |  | ✅ | `core/trade_memory.py:43` |
| `TRADE_REASONING_LOG` | `'true'` | ✅ |  | `core/engine_signals.py:1869` |
| `UMA_SETTLEMENT_BUFFER_MIN` | `<expr>` |  |  | `core/uma_dispute.py:65` |
| `UNSELLABLE_CHECK_ENABLED` | `'true'` | ✅ |  | `core/engine_signals.py:1270` |
| `UNSELLABLE_CLOSE_WARNING_MINS` | `'2.0'` |  |  | `core/risk_manager.py:609` |
| `UNSELLABLE_MIN_ENTRY_DEPTH` | `'5.0'` |  |  | `core/risk_manager.py:608` |
| `WAL_CHECKPOINT_FIRST_SEC` | `'1200'` |  |  | `telegram_bot/bot.py:1019` |
| `WAL_CHECKPOINT_INTERVAL_HOURS` | `'6'` |  |  | `telegram_bot/bot.py:1016` |
| `WARMUP_MAX_WAIT` | `'120'` |  |  | `core/engine.py:918` |
| `WEEKEND_SAT_MULT` | `'2.4'` |  | ✅ | `core/signal_fusion.py:540` |
| `WEEKEND_SUN_MULT` | `'2.1'` |  | ✅ | `core/signal_fusion.py:541` |
| `WHALE_LOOKBACK_SECONDS` | `None` |  | ✅ | `core/signals/whale_flow.py:48` |
| `WHALE_MIN_TRADES` | `None` |  | ✅ | `core/signals/whale_flow.py:55` |
| `WHALE_MIN_VOLUME_USD` | `None` |  | ✅ | `core/signals/whale_flow.py:62` |
| `WHALE_SIGNAL_ENABLED` | `'false'` |  | ✅ | `core/ai_brain.py:696`, `core/signal_fusion.py:548` |
| `WHALE_SIGNAL_WEIGHT` | `None` |  | ✅ | `core/signal_fusion.py:126` |
| `WHALE_USD_THRESHOLD` | `'1000'` |  | ✅ | `data/market_recorder.py:717` |
| `WHIPSAW_BAND_HI` | `'0.60'` |  |  | `core/engine_signals.py:431` |
| `WHIPSAW_BAND_LO` | `'0.40'` |  |  | `core/engine_signals.py:430` |
| `WS_FORCE_RECONNECT_SEC` | `'300'` |  |  | `data/websocket_client.py:241` |
| `WS_STALE_MIN_THRESHOLD` | `'0.70'` | ✅ |  | `core/engine_signals.py:422` |
| `WS_STALE_SEC` | `'60'` |  |  | `data/websocket_client.py:704` |
| `WS_STALE_THRESHOLD` | `'60.0'` | ✅ |  | `core/engine.py:1161`, `data/websocket_client.py:704`, `telegram_bot/handlers/diagnose_handler.py:173` (+2 more) |

## Drift Detection

Run in CI:
```bash
python scripts/gen_env_reference.py --check
```
Exits 1 if this document is stale relative to current `os.getenv(...)` scan. Fix: re-run without `--check` to regenerate + commit.

## Scope

Production dirs scanned: `core/`, `data/`, `telegram_bot/`, `db/`, `calibration/`, `backtest/`, `data_feeds/`, `indicators/`

Excluded: `tests/`, `scripts/`, `_archive/`, project root.

