"""
PolyPaper Bot - Backtest Package (replay-only)

2026-05-21 (Heddas direktifi tam temizlik):
  • engine_v2 (sentetik snapshot motoru) silindi.
  • replay_engine (gercek L2 ob_snapshots) tek backtest yolu.
  • data_sources/ paketi (polybacktest/gamma_hist/binance_hist/collector)
    silindi — gercek L2 zaten ob_snapshots tablosunda.

Backtest komutlari:
  /backtest  /bt  /lab           → LAB tek kapi (handlers.backtest_lab)
  /backtest_v2 + /bt2            → LAB'a yonlendiren deprecation shim
  /backtest_replay               → /backtest_replay panel + button flow
  /compare strat1 strat2         → multi-strategy replay karsilastirma

Hepsi backtest.replay_engine uzerinde calisir.
"""

from backtest.replay_engine import ReplayConfig, ReplayEngine

__all__ = ["ReplayConfig", "ReplayEngine"]
