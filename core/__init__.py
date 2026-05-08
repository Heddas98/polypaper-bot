"""
PolyPaper core package.

2026-05-05 Heddas direktifi: P1.2 core/ refactor — Faz 1 backward-compat shim.

Hedef paket yapısı (gelecekte git mv ile fiziksel taşınacak):
  core/
  ├── signal_engine/   # SignalFusion, RegimeClassifier, StrategySelector, plugins, indicators
  ├── execution_engine/   # LiveTrader, Executor, MakerTakerDecision, Heartbeat, Reconciliation
  ├── risk_engine/   # RiskManager, KillSwitch, PortfolioKill, CircuitBreaker, Allowance, UMA, Fees
  ├── shared/   # stats, observability, error_handler, calibration, logging, bg_task
  └── orchestration/   # Engine, mixins, AI Brain, AutoOptimizer, Lifecycle

Bu __init__.py mevcut import'ları KORURken, gelecekte yeni paket
import path'leri eklenebilir. Şu an sadece module marker —
performans için lazy import yapılmıyor.

Detaylı plan: docs/refactor/core_refactor_plan_2026_05.md
"""
