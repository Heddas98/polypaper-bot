"""
PolyPaper Bot - KeepAlive + Monitoring Dashboard (Phase 33)
HTTP server on port 8080:
  /           → simple alive text
  /health     → JSON health check (for cron-job.org)
  /status     → plain text status
  /dashboard  → FULL HTML monitoring dashboard (auto-refresh 30s)
  /api/data   → JSON API for dashboard data
"""

import asyncio
import logging
import os
from datetime import UTC, datetime

import aiosqlite
from aiohttp import web

from core.bg_task import safe_create_task  # Phase 82e Sprint 2.1

logger = logging.getLogger("polypaper.keepalive")

PORT = int(os.getenv("PORT", 8080))
SELF_PING_INTERVAL = 240


class KeepAlive:
    def __init__(self, engine=None, db=None):
        self.engine = engine
        self.db = db
        self._runner = None
        self._self_ping_task = None

    async def start(self):
        app = web.Application()
        app.router.add_get("/", self._handle_root)
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/status", self._handle_status)
        app.router.add_get("/dashboard", self._handle_dashboard)
        app.router.add_get("/api/data", self._handle_api_data)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", PORT)
        try:
            await site.start()
            replit_domains = os.getenv("REPLIT_DOMAINS", "")
            replit_url = os.getenv("REPLIT_DEV_DOMAIN", "")
            logger.info(f"🌐 KeepAlive: HTTP on port {PORT}")
            if replit_domains:
                domain = replit_domains.split(",")[0].strip()
                logger.info(f"🌐 DASHBOARD: https://{domain}/dashboard")
                logger.info(f"🌐 HEALTH: https://{domain}/health")
            elif replit_url:
                logger.info(f"🌐 DASHBOARD: https://{replit_url}/dashboard")
                logger.info("🌐 ⚠️ Dev URL — Deploy yap for public access")
            else:
                logger.info(f"🌐 DASHBOARD: http://localhost:{PORT}/dashboard")
        except OSError as e:
            logger.warning(f"KeepAlive port {PORT}: {e}")
            return
        # Phase 82e Sprint 2.1: keepalive self-ping guarded
        self._self_ping_task = safe_create_task(self._self_ping_loop(), name="keepalive_self_ping")

    async def _handle_root(self, request):
        return web.Response(
            text="PolyPaper Bot v33 — /dashboard for monitoring", content_type="text/plain"
        )

    async def _handle_health(self, request):
        data = {
            "status": "ok",
            "version": "v33",
            "engine": "running" if self.engine and self.engine._running else "stopped",
        }
        if self.engine:
            data["cycle"] = self.engine._cycle
            data["regime"] = self.engine.regime.regime
        return web.json_response(data)

    async def _handle_status(self, request):
        lines = ["PolyPaper v33 Status", "=" * 30]
        if self.engine:
            lines.append(f"Cycle: {self.engine._cycle}")
            lines.append(f"Regime: {self.engine.regime.regime}")
            lines.append(f"Open: {len(self.engine._open_positions)}")
            if self.engine.analyst:
                st = self.engine.analyst.get_status()
                lines.append(f"AI Brain: ${st['spent']:.2f}/{st['budget']} cycle#{st['cycle']}")
        return web.Response(text="\n".join(lines), content_type="text/plain")

    async def _handle_api_data(self, request):
        """JSON API with all monitoring data."""
        data = {"ts": datetime.now(UTC).isoformat(), "error": None}
        try:
            if not self.db:
                data["error"] = "No DB"
                return web.json_response(data)

            # Balance
            bal = await self.db.conn.execute_fetchall("SELECT balance FROM wallets LIMIT 1")
            data["balance"] = bal[0][0] if bal else 0

            # All-time
            at = await self.db.conn.execute_fetchall(
                "SELECT COALESCE(SUM(pnl),0), COUNT(*), COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) FROM executions WHERE result IS NOT NULL"
            )
            data["alltime_pnl"] = at[0][0] if at else 0
            data["total_trades"] = at[0][1] if at else 0
            data["total_wins"] = at[0][2] if at else 0

            # Strategies
            strats = await self.db.conn.execute_fetchall(
                """SELECT s.label, s.strategy_type, s.trade_amount, s.odds_threshold, s.status,
                    COUNT(CASE WHEN e.result IS NOT NULL THEN 1 END) as t,
                    COALESCE(SUM(CASE WHEN e.pnl>0 AND e.result IS NOT NULL THEN 1 ELSE 0 END),0) as w,
                    COALESCE(SUM(CASE WHEN e.result IS NOT NULL THEN e.pnl ELSE 0 END),0) as pnl
                FROM strategies s LEFT JOIN executions e ON e.strategy_id=s.id
                GROUP BY s.id ORDER BY pnl DESC"""
            )
            data["strategies"] = [
                {
                    "label": s[0],
                    "type": s[1],
                    "amount": s[2],
                    "threshold": s[3],
                    "status": s[4],
                    "trades": s[5],
                    "wins": s[6],
                    "pnl": round(s[7], 2),
                    "wr": round(s[6] / s[5] * 100, 1) if s[5] > 0 else 0,
                }
                for s in (strats or [])
            ]

            # Engine data
            if self.engine:
                data["cycle"] = self.engine._cycle
                data["running"] = self.engine._running
                data["regime"] = self.engine.regime.get_status()
                data["ts_rankings"] = self.engine.selector.get_rankings()[:10]
                data["drift"] = self.engine.drift.get_status()
                if self.engine.analyst:
                    data["ai_brain"] = self.engine.analyst.get_status()
                if self.engine.external_feed and self.engine.external_feed.is_available:
                    data["btc_price"] = self.engine.external_feed.get_price("BTC")
                # Phase 34: Live trader status
                if hasattr(self.engine, "live"):
                    data["live"] = self.engine.live.get_status()
                    data["live_comparison"] = await self.engine.live.get_comparison()

            # Recent trades
            recent = await self.db.conn.execute_fetchall(
                """SELECT s.label, e.direction, e.execution_price, e.trade_amount, e.pnl, e.result, e.created_at
                FROM executions e JOIN strategies s ON e.strategy_id=s.id
                WHERE e.result IS NOT NULL ORDER BY e.created_at DESC LIMIT 15"""
            )
            data["recent_trades"] = [
                {
                    "label": r[0],
                    "dir": r[1],
                    "price": r[2],
                    "amount": r[3],
                    "pnl": round(r[4], 2),
                    "result": r[5],
                    "time": str(r[6])[11:16],
                }
                for r in (recent or [])
            ]

            # AI decisions
            decisions = await self.db.conn.execute_fetchall(
                "SELECT ts, actions_executed, outcome_24h, was_correct FROM ai_decisions ORDER BY ts DESC LIMIT 5"
            )
            data["ai_decisions"] = [
                {"ts": str(d[0])[:16], "actions": d[1], "outcome": d[2], "correct": d[3]}
                for d in (decisions or [])
            ]

        except (
            aiosqlite.Error,
            ValueError,
            TypeError,
            ArithmeticError,
            IndexError,
            AttributeError,
            KeyError,
        ) as e:
            # T1.4 Faz 3: Large monitoring query block — multiple
            # execute_fetchall calls, per-row unpack (bal[0][0], at[0][1]),
            # arithmetic in strategy WR comprehension (s[6]/s[5]*100),
            # and engine attribute chain (self.engine._cycle, .regime,
            # .drift, .analyst, .external_feed, .live). Realistic failure
            # modes:
            #   - aiosqlite.Error: DB/table/column missing or schema drift
            #   - ValueError/TypeError: row coercion, None vs int math
            #   - ArithmeticError: ZeroDivisionError in wr comprehension
            #     when s[5] guard races with future edits (defensive)
            #   - IndexError: empty rows[] accessed as rows[0][0]
            #     (existing `if rows` guards, but belt-and-braces)
            #   - AttributeError: engine sub-object missing (.analyst,
            #     .external_feed, .live are optional — defensive for
            #     partial initialization)
            #   - KeyError: dict lookups on status dicts
            data["error"] = str(e)
        return web.json_response(data)

    async def _handle_dashboard(self, request):
        """Full HTML monitoring dashboard."""
        html = DASHBOARD_HTML
        return web.Response(text=html, content_type="text/html")

    async def _self_ping_loop(self):
        await asyncio.sleep(60)
        while True:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._do_ping)
            except (RuntimeError, OSError):
                # T1.4 Faz 3: asyncio.get_event_loop() can raise
                # RuntimeError on unusual shutdown paths; executor can
                # raise OSError if thread pool is broken. _do_ping has
                # its own httpx/OSError catch so those won't bubble.
                # CancelledError intentionally NOT caught — shutdown
                # must propagate (not in Exception hierarchy on 3.8+).
                pass
            await asyncio.sleep(SELF_PING_INTERVAL)

    def _do_ping(self):
        import httpx as _httpx

        try:
            url = os.getenv("REPLIT_DEV_DOMAIN", "")
            target = f"https://{url}/health" if url else f"http://localhost:{PORT}/health"
            _httpx.get(target, timeout=5.0)
        except (_httpx.HTTPError, OSError):
            # T1.4 Faz 3: self-ping fire-and-forget. httpx.HTTPError
            # umbrella covers Connect/Timeout/RequestError/
            # HTTPStatusError. OSError covers low-level socket/DNS
            # failures that can leak past httpx.
            pass

    async def stop(self):
        if self._self_ping_task:
            self._self_ping_task.cancel()
        if self._runner:
            await self._runner.cleanup()


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PolyPaper Monitor</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0e17;color:#e0e0e0;font-family:'Segoe UI',system-ui,sans-serif;font-size:14px}
.container{max-width:1200px;margin:0 auto;padding:12px}
h1{color:#00d4aa;font-size:22px;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;margin-bottom:12px}
.card{background:#141b2d;border:1px solid #1e2940;border-radius:10px;padding:14px}
.card h2{color:#4fc3f7;font-size:14px;margin-bottom:10px;text-transform:uppercase;letter-spacing:1px}
.metric{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #1a2235}
.metric:last-child{border:none}
.metric .label{color:#8892a4}
.metric .value{font-weight:600;font-family:'Courier New',monospace}
.green{color:#00e676}.red{color:#ff5252}.yellow{color:#ffd740}.blue{color:#42a5f5}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:6px 8px;color:#4fc3f7;border-bottom:2px solid #1e2940;font-size:11px;text-transform:uppercase}
td{padding:5px 8px;border-bottom:1px solid #1a2235}
tr:hover{background:#1a2235}
.badge{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.badge-active{background:#00e67622;color:#00e676;border:1px solid #00e67644}
.badge-stopped{background:#ff525222;color:#ff5252;border:1px solid #ff525244}
.badge-ai{background:#7c4dff22;color:#b388ff;border:1px solid #7c4dff44}
.bar{height:6px;border-radius:3px;background:#1e2940;overflow:hidden;margin-top:3px}
.bar-fill{height:100%;border-radius:3px;transition:width 0.5s}
.refresh{color:#8892a4;font-size:11px;text-align:right}
.regime-box{display:inline-block;padding:4px 12px;border-radius:6px;font-weight:700;font-size:16px}
.regime-trending{background:#00e67622;color:#00e676;border:1px solid #00e676}
.regime-ranging{background:#ffd74022;color:#ffd740;border:1px solid #ffd740}
.regime-volatile{background:#ff525222;color:#ff5252;border:1px solid #ff5252}
</style>
</head>
<body>
<div class="container">
<h1>📊 PolyPaper Monitor <span id="status" style="font-size:12px;color:#8892a4">loading...</span></h1>
<div class="grid" id="top-cards"></div>
<div class="grid">
<div class="card" style="grid-column:1/-1"><h2>🎰 Stratejiler</h2><div id="strat-table"></div></div>
</div>
<div class="grid">
<div class="card"><h2>🏆 Thompson Sampling</h2><div id="ts-table"></div></div>
<div class="card"><h2>📉 Sinyal Drift</h2><div id="drift-table"></div></div>
</div>
<div class="grid">
<div class="card"><h2>📋 Son Trade'ler</h2><div id="recent-table"></div></div>
<div class="card"><h2>🧠 AI Kararları</h2><div id="ai-table"></div></div>
</div>
<p class="refresh">Her 30 saniyede yenilenir | <span id="last-update"></span></p>
</div>
<script>
async function load(){
  try{
    const r=await fetch('/api/data');
    const d=await r.json();
    if(d.error){document.getElementById('status').textContent='Error: '+d.error;return}

    const wr=d.total_trades>0?(d.total_wins/d.total_trades*100).toFixed(0):0;
    const regime=d.regime||{regime:'?',confidence:0};
    const rc='regime-'+(regime.regime||'ranging');
    const ai=d.ai_brain||{spent:0,budget:15,cycle:0};
    const btc=d.btc_price?'$'+d.btc_price.toLocaleString():'--';

    document.getElementById('top-cards').innerHTML=`
      <div class="card">
        <h2>💰 Hesap</h2>
        <div class="metric"><span class="label">Bakiye</span><span class="value green">$${(d.balance||0).toFixed(2)}</span></div>
        <div class="metric"><span class="label">All-time PnL</span><span class="value ${d.alltime_pnl>=0?'green':'red'}">${d.alltime_pnl>=0?'+':''}${(d.alltime_pnl||0).toFixed(2)}</span></div>
        <div class="metric"><span class="label">Toplam</span><span class="value">${d.total_trades||0} trade</span></div>
        <div class="metric"><span class="label">Win Rate</span><span class="value ${wr>=55?'green':'yellow'}">${wr}%</span></div>
      </div>
      <div class="card">
        <h2>🌐 Market</h2>
        <div class="metric"><span class="label">Regime</span><span class="value"><span class="regime-box ${rc}">${(regime.regime||'?').toUpperCase()}</span></span></div>
        <div class="metric"><span class="label">Guven</span><span class="value">${((regime.confidence||0)*100).toFixed(0)}%</span></div>
        <div class="metric"><span class="label">BTC</span><span class="value blue">${btc}</span></div>
        <div class="metric"><span class="label">Cycle</span><span class="value">#${d.cycle||0}</span></div>
      </div>
      <div class="card">
        <h2>🧠 AI Brain</h2>
        <div class="metric"><span class="label">Harcanan</span><span class="value">$${(ai.spent||0).toFixed(2)} / $${ai.budget||15}</span></div>
        <div class="metric"><span class="label">Kalan</span><span class="value green">$${((ai.remaining)||0).toFixed(2)}</span></div>
        <div class="metric"><span class="label">Cycle</span><span class="value">#${ai.cycle||0}</span></div>
        <div class="bar"><div class="bar-fill" style="width:${((ai.spent||0)/(ai.budget||15)*100)}%;background:${ai.spent>12?'#ff5252':ai.spent>8?'#ffd740':'#00e676'}"></div></div>
      </div>
    `;

    // Strategies table
    const strats=d.strategies||[];
    let sh='<table><tr><th>Strateji</th><th>Tip</th><th>$</th><th>Trade</th><th>WR</th><th>PnL</th><th>Durum</th></tr>';
    strats.forEach(s=>{
      const isAi=s.label.startsWith('AI_');
      const badge=s.status==='active'?'badge-active':'badge-stopped';
      const aiBadge=isAi?' <span class="badge badge-ai">AI</span>':'';
      sh+=`<tr><td>${s.label}${aiBadge}</td><td>${s.type}</td><td>$${s.amount}</td>
        <td>${s.trades}</td><td class="${s.wr>=55?'green':s.wr>=45?'yellow':'red'}">${s.wr}%</td>
        <td class="${s.pnl>=0?'green':'red'}">${s.pnl>=0?'+':''}${s.pnl}</td>
        <td><span class="badge ${badge}">${s.status}</span></td></tr>`;
    });
    sh+='</table>';
    document.getElementById('strat-table').innerHTML=sh;

    // Thompson Sampling
    const ts=d.ts_rankings||[];
    let th='<table><tr><th>#</th><th>ID</th><th>α</th><th>β</th><th>WR</th><th>PnL</th></tr>';
    ts.forEach((t,i)=>{
      const medals=['🥇','🥈','🥉'];
      th+=`<tr><td>${medals[i]||i+1}</td><td><code>${t.id.slice(0,8)}</code></td>
        <td class="green">${t.alpha}</td><td class="red">${t.beta}</td>
        <td>${t.win_rate}%</td><td class="${t.pnl>=0?'green':'red'}">${t.pnl>=0?'+':''}${t.pnl}</td></tr>`;
    });
    th+='</table>';
    document.getElementById('ts-table').innerHTML=th;

    // Drift
    const drift=d.drift||{};
    let dh='<table><tr><th>Sinyal</th><th>Accuracy</th><th>Weight</th><th>Durum</th></tr>';
    Object.entries(drift).forEach(([name,v])=>{
      const color=v.drifting?'red':'green';
      dh+=`<tr><td>${name}</td><td>${v.accuracy}%</td>
        <td class="${color}">×${v.weight}</td>
        <td>${v.drifting?'⚠️ DRIFT':'✅ OK'}</td></tr>`;
    });
    if(!Object.keys(drift).length) dh+='<tr><td colspan="4" style="color:#8892a4">Henuz veri yok</td></tr>';
    dh+='</table>';
    document.getElementById('drift-table').innerHTML=dh;

    // Recent trades
    const trades=d.recent_trades||[];
    let rh='<table><tr><th>Saat</th><th>Strateji</th><th>Yon</th><th>$</th><th>PnL</th></tr>';
    trades.forEach(t=>{
      const emoji=t.pnl>0?'🟢':'🔴';
      rh+=`<tr><td>${t.time}</td><td>${t.label}</td><td>${t.dir.toUpperCase()}</td>
        <td>$${t.amount}</td><td class="${t.pnl>=0?'green':'red'}">${emoji} ${t.pnl>=0?'+':''}${t.pnl}</td></tr>`;
    });
    rh+='</table>';
    document.getElementById('recent-table').innerHTML=rh;

    // AI decisions
    const ais=d.ai_decisions||[];
    let ah='';
    ais.forEach(a=>{
      const icon=a.correct===1?'✅':a.correct===0?'❌':'⏳';
      const outcome=a.outcome||'bekliyor';
      ah+=`<div class="metric"><span class="label">${a.ts} ${icon}</span><span class="value" style="font-size:11px">${outcome}</span></div>`;
    });
    if(!ais.length) ah='<div class="metric"><span class="label" style="color:#8892a4">Henuz karar yok</span></div>';
    document.getElementById('ai-table').innerHTML=ah;

    document.getElementById('status').textContent='🟢 Live';
    document.getElementById('status').style.color='#00e676';
    document.getElementById('last-update').textContent=new Date().toLocaleTimeString();
  }catch(e){
    document.getElementById('status').textContent='⚫ Offline';
    document.getElementById('status').style.color='#ff5252';
  }
}
load();
setInterval(load,30000);
</script>
</body>
</html>"""
