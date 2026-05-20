"""
PolyPaper Bot - Backtest Package (2026-05-21 yeni minimal motor)

Cleanup tarihçesi:
  • 2026-05-20: engine_v2 (sentetik snapshot motoru) silindi.
  • 2026-05-21: replay_engine.py (1101 sat eski schema bekleyen) silindi
    — modern ob_snapshots schema'sina UYUMSUZ, /backtest_replay 3+ gun
    boyunca "no such column: up_token_id" hatasiyla patliyordu.
  • 2026-05-21: data_sources/ paketi (polybacktest + gamma_hist +
    binance_hist + collector) silinmisti zaten.
  • 2026-05-21: 11 hazir Python strategy class (hour_edge / taker_flow /...)
    silindi — sadece RuleBasedStrategy kaldi.

Yeni motor:
  backtest/runner.py — BacktestRunner + RunConfig + RunSummary
    • modern ob_snapshots schema (condition_id + asset_id + asset + tf + slug)
    • RuleBasedStrategy uzerinde calisir
    • UP+DOWN snapshot ts_ms'de merged (her token ayri row)
    • binary settle (market sonundaki up_best_ask>down_best_ask)

Backtest komutlari (bot.py):
  /backtest  /bt  /lab    → LAB tek kapi (handlers.backtest_lab)
  /backtest_v2  /bt2      → LAB'a yonlendiren deprecation shim
  /backtest_replay        → BacktestRunner ile rule_based koşar
  /compare strat1 strat2  → multi-ruleset karşılaştırma (BacktestRunner)
"""

from backtest.runner import BacktestRunner, RunConfig, RunSummary

__all__ = ["BacktestRunner", "RunConfig", "RunSummary"]
