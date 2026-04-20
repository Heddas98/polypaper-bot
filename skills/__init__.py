"""
Phase 73: Skill Module System
==============================
Source: AI-Trader (6 reusable skills), R2 (Plugin-based composable)

Shared calculation modules that multiple strategies can reuse.
Eliminates code duplication and makes new strategy creation easier.

Available skills:
    - ema_skill: EMA calculation, crossover detection
    - volatility_skill: Volatility measurement, regime classification
    - orderbook_skill: Orderbook depth, imbalance, microprice
    - price_skill: Price transforms, normalization, technical features
    - timing_skill: Time-based calculations, market phase detection
"""
