"""
PolyPaper Bot - Database (v34)
================================
Async SQLite katmanı. aiosqlite 0.20.0, WAL modu etkin.

Tablolar:
  users           — Kullanıcı profilleri
  wallets         — Bakiye ve simülasyon fonu
  strategies      — Strateji konfigürasyonları (status: active/stopped/paused)
  executions      — Tamamlanan trade kayıtları
  pending_orders  — Bekleyen VWAP emirleri
  trade_log       — Ham event akışı (trade_journal.py çift yazımı)
  bot_settings    — Kalıcı key-value ayarlar (risk limitleri, AI kararları)
  ai_decisions    — AI Brain karar + 24h sonuç ölçümü

~30 public async metod. WAL modu eş zamanlı okuma/yazma sağlar.
Atomik bakiye düşme için get_and_deduct_balance() kullan.
"""
import asyncio
import aiosqlite
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from db.models import (
    User, Wallet, Strategy, Execution,
    StrategyStatus, ExecutionStatus, Asset, Timeframe, Direction,
)
from db.migrations import run_migrations, grandfather_deploy_stage

logger = logging.getLogger("polypaper.db")


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        # Phase 57: WAL optimization — prevent "database is locked" errors
        await self._conn.execute("PRAGMA busy_timeout=10000")      # Phase 62: 5s→10s to prevent lock during concurrent jobs
        await self._conn.execute("PRAGMA synchronous=NORMAL")      # safe with WAL, faster writes
        await self._conn.execute("PRAGMA wal_autocheckpoint=5000") # Phase 65: 1000→5000 prevent WAL bloat
        await self._create_tables()
        await run_migrations(self.conn)
        await grandfather_deploy_stage(self.conn)

    async def close(self):
        if self._conn:
            await self._conn.close()

    @property
    def conn(self):
        return self._conn

    async def _create_tables(self):
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT, first_name TEXT, accepted_terms INTEGER DEFAULT 0,
                default_wallet_id TEXT, notify_buy INTEGER DEFAULT 1,
                notify_stop_loss INTEGER DEFAULT 1, notify_take_profit INTEGER DEFAULT 1,
                notify_claim INTEGER DEFAULT 1, notify_no_buy INTEGER DEFAULT 0,
                created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS wallets (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                label TEXT DEFAULT 'primary', balance REAL DEFAULT 0.0,
                is_primary INTEGER DEFAULT 0, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS strategies (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                wallet_id TEXT NOT NULL REFERENCES wallets(id),
                label TEXT, asset TEXT NOT NULL DEFAULT 'BTC',
                timeframe TEXT NOT NULL DEFAULT '15m', direction TEXT NOT NULL DEFAULT 'any',
                trade_amount REAL NOT NULL DEFAULT 1.0, odds_threshold REAL DEFAULT 0.80,
                price_difference REAL, minutes_before_end REAL DEFAULT 2.0,
                minutes_after_start REAL DEFAULT 0.0,
                stop_loss_percent REAL, stop_loss_odds REAL,
                take_profit_percent REAL, take_profit_odds REAL,
                max_executions_per_event INTEGER, max_losses_per_event INTEGER,
                max_entry_slippage REAL, ma_filter_enabled INTEGER DEFAULT 0,
                min_volatility REAL, strategy_type TEXT DEFAULT 'fusion',
                status TEXT NOT NULL DEFAULT 'stopped',
                started_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS executions (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                wallet_id TEXT NOT NULL REFERENCES wallets(id),
                strategy_id TEXT REFERENCES strategies(id),
                event_slug TEXT NOT NULL, market_token_id TEXT,
                direction TEXT NOT NULL, trade_amount REAL NOT NULL,
                fee_amount REAL DEFAULT 0.0, odds_threshold REAL, execution_price REAL,
                status TEXT NOT NULL DEFAULT 'pending',
                stop_loss_percent REAL, stop_loss_odds REAL,
                take_profit_percent REAL, take_profit_odds REAL,
                pnl REAL DEFAULT 0.0, payout REAL DEFAULT 0.0,
                result TEXT, closed_at TEXT, error_message TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS market_events (
                slug TEXT PRIMARY KEY, asset TEXT NOT NULL, timeframe TEXT NOT NULL,
                condition_id TEXT, up_token_id TEXT, down_token_id TEXT,
                up_odds REAL, down_odds REAL, price_to_beat REAL, current_price REAL,
                start_time TEXT, end_time TEXT, resolved INTEGER DEFAULT 0,
                resolution TEXT, last_updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS odds_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event_slug TEXT NOT NULL,
                up_odds REAL, down_odds REAL, timestamp TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                slug TEXT,
                direction TEXT,
                strategy_id TEXT,
                price REAL,
                amount REAL,
                pnl REAL,
                fee REAL,
                reason TEXT,
                metadata TEXT,
                ts TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status);
            CREATE INDEX IF NOT EXISTS idx_executions_user ON executions(user_id);
            CREATE INDEX IF NOT EXISTS idx_strategies_user ON strategies(user_id);
            CREATE INDEX IF NOT EXISTS idx_trade_log_ts ON trade_log(ts);
            CREATE INDEX IF NOT EXISTS idx_trade_log_event ON trade_log(event);
            CREATE INDEX IF NOT EXISTS idx_trade_log_slug ON trade_log(slug);
            CREATE TABLE IF NOT EXISTS live_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_label TEXT NOT NULL,
                slug TEXT NOT NULL,
                direction TEXT NOT NULL,
                token_id TEXT,
                entry_price REAL,
                exit_price REAL,
                amount REAL NOT NULL,
                shares REAL,
                fee_entry REAL DEFAULT 0,
                fee_exit REAL DEFAULT 0,
                pnl REAL,
                result TEXT,
                order_id TEXT,
                paper_pnl REAL,
                signal_score REAL,
                created_at TEXT NOT NULL,
                settled_at TEXT);
            CREATE INDEX IF NOT EXISTS idx_live_trades_ts ON live_trades(created_at);
        """)
        await self.conn.commit()
        # All schema migrations (v1-v5) are now managed by db.migrations module
        # run_migrations() is called in initialize() and grandfather_deploy_stage()
        # is called separately to maintain idempotency and transactional safety.

    # ── USER ──
    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        async with self.conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)) as c:
            row = await c.fetchone()
            return self._row_to_user(row) if row else None

    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        async with self.conn.execute("SELECT * FROM users WHERE id=?", (user_id,)) as c:
            row = await c.fetchone()
            return self._row_to_user(row) if row else None

    async def create_user(self, user: User) -> User:
        await self.conn.execute(
            """INSERT INTO users (id,telegram_id,username,first_name,accepted_terms,
               default_wallet_id,notify_buy,notify_stop_loss,notify_take_profit,
               notify_claim,notify_no_buy,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (user.id, user.telegram_id, user.username, user.first_name,
             int(user.accepted_terms), user.default_wallet_id,
             int(user.notify_buy), int(user.notify_stop_loss),
             int(user.notify_take_profit), int(user.notify_claim),
             int(user.notify_no_buy), user.created_at.isoformat()))
        await self.conn.commit()
        return user

    async def update_user(self, user: User):
        await self.conn.execute(
            """UPDATE users SET username=?,first_name=?,accepted_terms=?,
               default_wallet_id=?,notify_buy=?,notify_stop_loss=?,
               notify_take_profit=?,notify_claim=?,notify_no_buy=? WHERE id=?""",
            (user.username, user.first_name, int(user.accepted_terms),
             user.default_wallet_id, int(user.notify_buy), int(user.notify_stop_loss),
             int(user.notify_take_profit), int(user.notify_claim),
             int(user.notify_no_buy), user.id))
        await self.conn.commit()

    # ── WALLET ──
    async def create_wallet(self, wallet: Wallet) -> Wallet:
        await self.conn.execute(
            "INSERT INTO wallets (id,user_id,label,balance,is_primary,created_at) VALUES (?,?,?,?,?,?)",
            (wallet.id, wallet.user_id, wallet.label, wallet.balance,
             int(wallet.is_primary), wallet.created_at.isoformat()))
        await self.conn.commit()
        return wallet

    async def get_wallets_by_user(self, user_id: str) -> list[Wallet]:
        ws = []
        async with self.conn.execute(
            "SELECT * FROM wallets WHERE user_id=? ORDER BY is_primary DESC", (user_id,)) as c:
            async for row in c:
                ws.append(self._row_to_wallet(row))
        return ws

    async def get_wallet(self, wallet_id: str) -> Optional[Wallet]:
        async with self.conn.execute("SELECT * FROM wallets WHERE id=?", (wallet_id,)) as c:
            row = await c.fetchone()
            return self._row_to_wallet(row) if row else None

    async def get_active_wallet(self, user_id: str) -> Optional[Wallet]:
        user = await self.get_user_by_id(user_id)
        if user and user.default_wallet_id:
            w = await self.get_wallet(user.default_wallet_id)
            if w:
                return w
        async with self.conn.execute(
            "SELECT * FROM wallets WHERE user_id=? ORDER BY is_primary DESC, created_at LIMIT 1",
            (user_id,)) as c:
            row = await c.fetchone()
            return self._row_to_wallet(row) if row else None

    async def update_wallet_balance(self, wallet_id: str, new_balance: float):
        """Legacy method — still used by non-engine code (add_funds, withdraw)."""
        await self._db_write(
            "UPDATE wallets SET balance=? WHERE id=?", (new_balance, wallet_id))

    async def atomic_deduct_balance(self, wallet_id: str, amount: float) -> bool:
        """F-02: Atomic deduction. Returns True if successful, False if insufficient.
        Single SQL with WHERE balance >= amount prevents race conditions."""
        try:
            cursor = await self.conn.execute(
                "UPDATE wallets SET balance = balance - ? WHERE id = ? AND balance >= ?",
                (amount, wallet_id, amount))
            await self.conn.commit()
            return cursor.rowcount > 0  # True if row was updated
        except Exception as e:
            logger.error(f"Atomic deduct failed: {e}")
            return False

    async def _db_write(self, sql: str, params: tuple = (), max_retries: int = 3):
        """F-05: Write retry wrapper for SQLite concurrent write resilience."""
        for attempt in range(max_retries):
            try:
                await self.conn.execute(sql, params)
                await self.conn.commit()
                return
            except Exception as e:
                if "locked" in str(e).lower() and attempt < max_retries - 1:
                    logger.warning(f"DB locked, retry {attempt+1}/{max_retries}")
                    await asyncio.sleep(0.1 * (attempt + 1))
                else:
                    raise

    # ── STRATEGY ──
    async def create_strategy(self, strategy: Strategy) -> Strategy:
        await self.conn.execute(
            """INSERT INTO strategies (id,user_id,wallet_id,label,asset,timeframe,
               direction,trade_amount,odds_threshold,price_difference,
               minutes_before_end,minutes_after_start,stop_loss_percent,
               stop_loss_odds,take_profit_percent,take_profit_odds,
               max_executions_per_event,max_losses_per_event,max_entry_slippage,
               ma_filter_enabled,min_volatility,strategy_type,status,started_at,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (strategy.id, strategy.user_id, strategy.wallet_id, strategy.label,
             strategy.asset.value, strategy.timeframe.value, strategy.direction.value,
             strategy.trade_amount, strategy.odds_threshold, strategy.price_difference,
             strategy.minutes_before_end, strategy.minutes_after_start,
             strategy.stop_loss_percent, strategy.stop_loss_odds,
             strategy.take_profit_percent, strategy.take_profit_odds,
             strategy.max_executions_per_event, strategy.max_losses_per_event,
             strategy.max_entry_slippage, int(strategy.ma_filter_enabled),
             strategy.min_volatility, strategy.strategy_type, strategy.status.value,
             strategy.started_at.isoformat() if strategy.started_at else None,
             strategy.created_at.isoformat(), strategy.updated_at.isoformat()))
        await self.conn.commit()
        return strategy

    async def get_strategies_by_user(self, user_id: str, wallet_id: Optional[str] = None) -> list[Strategy]:
        # Phase 52 ÖNERİ #5 — newest strategies first so /quick_strategy
        # creations surface at the top of /strategies instead of burying
        # them at the bottom of a long list.
        ss = []
        if wallet_id:
            q = "SELECT * FROM strategies WHERE user_id=? AND wallet_id=? ORDER BY created_at DESC"
            p = (user_id, wallet_id)
        else:
            q = "SELECT * FROM strategies WHERE user_id=? ORDER BY created_at DESC"
            p = (user_id,)
        async with self.conn.execute(q, p) as c:
            async for row in c:
                ss.append(self._row_to_strategy(row))
        return ss

    async def get_strategy(self, sid: str) -> Optional[Strategy]:
        async with self.conn.execute("SELECT * FROM strategies WHERE id=?", (sid,)) as c:
            row = await c.fetchone()
            return self._row_to_strategy(row) if row else None

    async def update_strategy_status(self, sid: str, status: StrategyStatus):
        now = datetime.utcnow().isoformat()
        started = now if status == StrategyStatus.ACTIVE else None
        await self.conn.execute("UPDATE strategies SET status=?,started_at=?,updated_at=? WHERE id=?",
                                (status.value, started, now, sid))
        await self.conn.commit()

    async def update_strategy_field(self, sid: str, field: str, value):
        """Phase 19: Update a single strategy field safely."""
        allowed = {"label", "trade_amount", "odds_threshold", "direction",
                   "price_difference", "minutes_before_end", "minutes_after_start",
                   "stop_loss_percent", "stop_loss_odds", "take_profit_percent",
                   "take_profit_odds", "max_executions_per_event", "max_losses_per_event",
                   "max_entry_slippage", "ma_filter_enabled", "min_volatility", "strategy_type"}
        if field not in allowed:
            return False
        # P2-04 FIX: Defense-in-depth — reject field names with non-alphanumeric chars
        # even if they somehow pass the whitelist (e.g. via future code changes)
        import re
        if not re.match(r'^[a-z_]+$', field):
            return False
        now = datetime.now(timezone.utc).isoformat()  # P3-02 FIX: was utcnow()
        await self.conn.execute(
            f"UPDATE strategies SET {field}=?, updated_at=? WHERE id=?",
            (value, now, sid))
        await self.conn.commit()
        return True

    async def delete_strategy(self, sid: str):
        """Soft-delete: stop strategy and nullify FK references, keep data."""
        # First nullify any execution references to avoid FK constraint
        await self.conn.execute(
            "UPDATE executions SET strategy_id=NULL WHERE strategy_id=?", (sid,))
        # Then delete the strategy
        await self.conn.execute("DELETE FROM strategies WHERE id=?", (sid,))
        await self.conn.commit()

    async def get_active_strategies(self) -> list[Strategy]:
        ss = []
        async with self.conn.execute("SELECT * FROM strategies WHERE status='active'") as c:
            async for row in c:
                ss.append(self._row_to_strategy(row))
        return ss

    async def get_per_strategy_stats(self, user_id: str) -> list[dict]:
        """Get win/loss/PnL breakdown per strategy."""
        results = []
        try:
            async with self.conn.execute(
                """SELECT
                    s.id, s.asset, s.timeframe, s.direction, s.trade_amount,
                    s.odds_threshold, s.status, s.label, s.strategy_type,
                    COUNT(e.id) as total_trades,
                    COALESCE(SUM(CASE WHEN e.result IS NOT NULL THEN 1 ELSE 0 END),0) as completed,
                    COALESCE(SUM(CASE WHEN e.status='bet_placed' THEN 1 ELSE 0 END),0) as open_trades,
                    COALESCE(SUM(CASE WHEN e.pnl>0 AND e.result IS NOT NULL THEN 1 ELSE 0 END),0) as wins,
                    COALESCE(SUM(CASE WHEN e.pnl<=0 AND e.result IS NOT NULL THEN 1 ELSE 0 END),0) as losses,
                    COALESCE(SUM(CASE WHEN e.result IS NOT NULL THEN e.pnl ELSE 0 END),0) as realized_pnl,
                    COALESCE(SUM(e.trade_amount),0) as total_volume,
                    COALESCE(SUM(e.fee_amount),0) as total_fees
                   FROM strategies s
                   LEFT JOIN executions e ON e.strategy_id = s.id
                   WHERE s.user_id = ?
                   GROUP BY s.id
                   ORDER BY s.created_at DESC""",
                (user_id,)) as c:
                async for row in c:
                    results.append(dict(row))
        except Exception as e:
            logger.error(f"Per-strategy stats: {e}")
        return results

    # ── EXECUTION ──
    async def create_execution(self, execution: Execution) -> Execution:
        await self.conn.execute(
            """INSERT INTO executions (id,user_id,wallet_id,strategy_id,event_slug,
               market_token_id,direction,trade_amount,fee_amount,odds_threshold,
               execution_price,status,stop_loss_percent,stop_loss_odds,
               take_profit_percent,take_profit_odds,pnl,payout,result,closed_at,
               error_message,created_at,updated_at,signal_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (execution.id, execution.user_id, execution.wallet_id,
             execution.strategy_id, execution.event_slug,
             execution.market_token_id, execution.direction.value,
             execution.trade_amount, execution.fee_amount,
             execution.odds_threshold, execution.execution_price,
             execution.status.value, execution.stop_loss_percent,
             execution.stop_loss_odds, execution.take_profit_percent,
             execution.take_profit_odds, execution.pnl, execution.payout,
             execution.result,
             execution.closed_at.isoformat() if execution.closed_at else None,
             execution.error_message,
             execution.created_at.isoformat(), execution.updated_at.isoformat(),
             execution.signal_score))
        await self.conn.commit()
        return execution

    # ── STATS (FIXED: counts ALL trades) ──
    async def get_user_stats(self, user_id: str) -> dict:
        stats = {"total_pnl": 0.0, "total_trades": 0, "open_trades": 0,
                 "wins": 0, "losses": 0, "total_volume": 0.0, "win_rate": 0.0}
        try:
            # Completed trades
            async with self.conn.execute(
                """SELECT COUNT(*) as total, COALESCE(SUM(trade_amount),0) as vol,
                          COALESCE(SUM(pnl),0) as pnl,
                          COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) as wins,
                          COALESCE(SUM(CASE WHEN pnl<=0 THEN 1 ELSE 0 END),0) as losses
                   FROM executions WHERE user_id=? AND result IS NOT NULL""",
                (user_id,)) as c:
                row = await c.fetchone()
                if row:
                    stats["total_trades"] = row["total"]
                    stats["total_volume"] = row["vol"]
                    stats["total_pnl"] = row["pnl"]
                    stats["wins"] = row["wins"]
                    stats["losses"] = row["losses"]
                    t = stats["wins"] + stats["losses"]
                    stats["win_rate"] = (stats["wins"] / t * 100) if t > 0 else 0
            # Open trades
            async with self.conn.execute(
                "SELECT COUNT(*) FROM executions WHERE user_id=? AND status='bet_placed'",
                (user_id,)) as c:
                row = await c.fetchone()
                stats["open_trades"] = row[0] if row else 0
        except Exception as e:
            logger.error(f"Stats error: {e}")
        return stats

    async def get_open_positions(self, user_id: str) -> list[dict]:
        """Get all open (bet_placed) positions with strategy info."""
        rows = []
        async with self.conn.execute(
            """SELECT e.*, s.label as strategy_label, s.strategy_type
               FROM executions e LEFT JOIN strategies s ON e.strategy_id=s.id
               WHERE e.user_id=? AND e.status='bet_placed'
               ORDER BY e.created_at DESC""", (user_id,)) as c:
            async for row in c:
                rows.append(dict(row))
        return rows

    async def get_recent_bets(self, user_id: str, limit: int = 5) -> list[Execution]:
        """Get recent COMPLETED bets."""
        exs = []
        async with self.conn.execute(
            """SELECT * FROM executions WHERE user_id=? AND result IS NOT NULL
               ORDER BY closed_at DESC LIMIT ?""", (user_id, limit)) as c:
            async for row in c:
                exs.append(self._row_to_execution(row))
        return exs

    async def get_all_user_executions(self, user_id: str, limit: int = 20) -> list[dict]:
        """Get ALL executions (open + closed) for display."""
        rows = []
        async with self.conn.execute(
            "SELECT * FROM executions WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)) as c:
            async for row in c:
                rows.append(dict(row))
        return rows

    # ── ROW CONVERTERS ──
    @staticmethod
    def _row_to_user(row) -> User:
        return User(
            id=row["id"], telegram_id=row["telegram_id"],
            username=row["username"], first_name=row["first_name"],
            accepted_terms=bool(row["accepted_terms"]),
            default_wallet_id=row["default_wallet_id"],
            notify_buy=bool(row["notify_buy"]), notify_stop_loss=bool(row["notify_stop_loss"]),
            notify_take_profit=bool(row["notify_take_profit"]),
            notify_claim=bool(row["notify_claim"]), notify_no_buy=bool(row["notify_no_buy"]),
            created_at=datetime.fromisoformat(row["created_at"]))

    @staticmethod
    def _row_to_wallet(row) -> Wallet:
        return Wallet(
            id=row["id"], user_id=row["user_id"], label=row["label"],
            balance=row["balance"], is_primary=bool(row["is_primary"]),
            created_at=datetime.fromisoformat(row["created_at"]))

    @staticmethod
    def _row_to_strategy(row) -> Strategy:
        return Strategy(
            id=row["id"], user_id=row["user_id"], wallet_id=row["wallet_id"],
            label=row["label"], asset=Asset(row["asset"]),
            timeframe=Timeframe(row["timeframe"]), direction=Direction(row["direction"]),
            trade_amount=row["trade_amount"], odds_threshold=row["odds_threshold"],
            price_difference=row["price_difference"],
            minutes_before_end=row["minutes_before_end"],
            minutes_after_start=row["minutes_after_start"],
            stop_loss_percent=row["stop_loss_percent"], stop_loss_odds=row["stop_loss_odds"],
            take_profit_percent=row["take_profit_percent"], take_profit_odds=row["take_profit_odds"],
            max_executions_per_event=row["max_executions_per_event"],
            max_losses_per_event=row["max_losses_per_event"],
            max_entry_slippage=row["max_entry_slippage"],
            ma_filter_enabled=bool(row["ma_filter_enabled"]),
            min_volatility=row["min_volatility"],
            strategy_type=row["strategy_type"] if "strategy_type" in row.keys() else "fusion",
            deploy_stage=(row["deploy_stage"] if "deploy_stage" in row.keys() and row["deploy_stage"] else "canary"),
            status=StrategyStatus(row["status"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]))

    @staticmethod
    def _row_to_execution(row) -> Execution:
        return Execution(
            id=row["id"], user_id=row["user_id"], wallet_id=row["wallet_id"],
            strategy_id=row["strategy_id"], event_slug=row["event_slug"],
            market_token_id=row["market_token_id"], direction=Direction(row["direction"]),
            trade_amount=row["trade_amount"], fee_amount=row["fee_amount"],
            odds_threshold=row["odds_threshold"], execution_price=row["execution_price"],
            status=ExecutionStatus(row["status"]),
            stop_loss_percent=row["stop_loss_percent"], stop_loss_odds=row["stop_loss_odds"],
            take_profit_percent=row["take_profit_percent"], take_profit_odds=row["take_profit_odds"],
            pnl=row["pnl"], payout=row["payout"], result=row["result"],
            closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]))

    # ═══ Phase 22: Persistent Settings ═══

    async def get_setting(self, key: str, default: str = None) -> str:
        """Get a persistent setting by key."""
        try:
            row = await self.conn.execute_fetchall(
                "SELECT value FROM bot_settings WHERE key=?", (key,))
            return row[0][0] if row else default
        except Exception:
            return default

    async def set_setting(self, key: str, value: str):
        """Set a persistent setting (upsert)."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        await self.conn.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now))
        await self.conn.commit()

    async def get_all_settings(self, prefix: str = "") -> dict:
        """Get all settings matching prefix."""
        try:
            rows = await self.conn.execute_fetchall(
                "SELECT key, value FROM bot_settings WHERE key LIKE ?",
                (prefix + "%",))
            return {r[0]: r[1] for r in rows}
        except Exception:
            return {}
