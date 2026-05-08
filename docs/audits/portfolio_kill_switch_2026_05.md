# Portfolio Kill-Switch — 2026-05 (P0.8 Closure)

**Tarih:** 2026-04-30
**Sahibi:** Claude (Lead Architect)
**Tetik:** YOL_HARITASI §5.1 P0.8 + Grok "Drawdown kill-switch hayati"

---

## 0 — TL;DR

| Madde | Status |
|---|---|
| `core/portfolio_kill_switch.py` modül | ✅ DONE (270 satır) |
| 3 katman: daily / consecutive / weekly | ✅ DONE |
| ENV runtime re-read (T6.1 doctrine) | ✅ DONE |
| Daily/weekly auto-baseline rotation | ✅ DONE |
| `record_trade()` + `evaluate()` API | ✅ DONE |
| `status_html()` Telegram render | ✅ DONE |
| Singleton accessor `get_kill_switch()` | ✅ DONE |

**Mevcut PNL_PAUSE_THRESHOLD ile fark:** PNL_PAUSE strateji-level pause (auto_optimizer); bu portfolio-level halt (tüm trading durur).

---

## 1 — Üç Katman Tasarımı

### Katman 1: Daily Loss HALT (`KILL_DAILY_MAX_LOSS_PCT`, default 0.10)

```
trigger: daily_pnl_pct <= -10%
behavior: halt for the rest of the day (auto-reset at next UTC midnight)
```

### Katman 2: Consecutive Loss Cooldown (`KILL_CONSECUTIVE_LOSS_LIMIT`, default 5)

```
trigger: 5 ardışık kayıp
behavior: cooldown 1h (env KILL_CONSECUTIVE_COOLDOWN_S=3600)
reset: kazanan trade veya manuel reset_consecutive()
```

### Katman 3: Weekly Drawdown Emergency (`KILL_WEEKLY_MAX_DD_PCT`, default 0.20)

```
trigger: weekly_pnl_pct <= -20%
behavior: emergency stop, MANUEL admin restart gerekir
reset: reset_weekly_emergency() admin command, veya hafta değişimi (otomatik)
```

---

## 2 — Public API

```python
from core.portfolio_kill_switch import get_kill_switch

ks = get_kill_switch()

# After every closed trade:
ks.record_trade(pnl=-1.5)

# Before opening new trade:
decision = ks.evaluate(current_equity=engine.risk.state.current_equity)
if decision.halted:
    logger.warning(f"🛑 Kill-switch: {decision.reason} — {decision.detail}")
    return SKIP

# Telegram /kill_switch:
html = ks.status_html(current_equity=...)
await update.effective_message.reply_html(html)
```

---

## 3 — ENV Variables (T6.1 hot-tune ready)

```
KILL_SWITCH_ENABLED=true              # bypass için "false"
KILL_DAILY_MAX_LOSS_PCT=0.10
KILL_CONSECUTIVE_LOSS_LIMIT=5
KILL_CONSECUTIVE_COOLDOWN_S=3600      # 1h
KILL_WEEKLY_MAX_DD_PCT=0.20
```

`/env_toggle KILL_DAILY_MAX_LOSS_PCT 0.05` ile runtime değiştirilebilir (whitelist eklenmesi gerek — `config/env_whitelist.py`).

---

## 4 — State Persistence

Şu an in-memory state (`KillSwitchState`). Bot restart'ta sıfırlanır.

**Forward work:** Engine `risk_manager.py` DB persist pattern ile entegrasyon:
- `consecutive_losses` → `risk_state` tablosu
- `daily_baseline_date`, `weekly_baseline_week` → settings
- Bot restart sonrası state recovery

---

## 5 — Heddas Yerel Apply

### 5.1 Engine Integration (`core/engine.py`)

```python
from core.portfolio_kill_switch import get_kill_switch

class Engine:
    def __init__(self, ...):
        self.kill_switch = get_kill_switch()
    
    async def _on_trade_closed(self, trade):
        self.kill_switch.record_trade(trade.pnl)
    
    async def _can_open_trade(self) -> tuple[bool, str]:
        equity = self.risk.state.current_equity
        decision = self.kill_switch.evaluate(equity)
        if decision.halted:
            return False, decision.reason
        return True, ""
```

### 5.2 Telegram Handler (`telegram_bot/handlers/kill_switch_handler.py`)

```python
@admin_only
async def cmd_kill_switch(update, ctx):
    engine = ctx.application.bot_data["engine"]
    equity = engine.risk.state.current_equity
    html = engine.kill_switch.status_html(equity)
    await update.effective_message.reply_html(html)

@admin_only
async def cmd_reset_weekly(update, ctx):
    engine = ctx.application.bot_data["engine"]
    engine.kill_switch.reset_weekly_emergency()
    await update.effective_message.reply_html("✅ Weekly emergency RESET")
```

### 5.3 Whitelist (`config/env_whitelist.py`)

```python
"KILL_DAILY_MAX_LOSS_PCT": {"type": "float", "min": 0.01, "max": 0.5, ...},
"KILL_WEEKLY_MAX_DD_PCT": {"type": "float", "min": 0.05, "max": 0.5, ...},
"KILL_CONSECUTIVE_LOSS_LIMIT": {"type": "int", "min": 2, "max": 20, ...},
"KILL_CONSECUTIVE_COOLDOWN_S": {"type": "int", "min": 60, "max": 86400, ...},
"KILL_SWITCH_ENABLED": {"type": "bool", ...},
```

---

## 6 — Memory Landmark

`memory/project_p08_portfolio_kill_switch_closure.md`:
```
P0.8 Portfolio kill-switch CLOSED 2026-04-30. core/portfolio_kill_switch.py 270 satır.
3 katman: daily -%10 HALT / consecutive 5 → 1h cooldown / weekly -%20 emergency.
ENV runtime re-read (T6.1 doctrine). KillSwitchState in-memory + auto-rotate baselines.
PNL_PAUSE_THRESHOLD strategy-level (auto_optimizer); kill-switch portfolio-level.
P1 forward work: engine.py wire + DB persist + /env_toggle whitelist + Telegram handler.
```

**Sonuç:** P0.8 KAPALI (modül hazır). Sıradaki: **P0.7 Fill heuristic recalibration**.
