"""
PolyPaper Bot - Giriş Noktası (v34)
======================================
Botu başlat: start.bat dosyasına çift tıkla (Windows)
             veya: py -3.11 main.py

Başlangıç sırası:
  1. .env yükle (python-dotenv)
  2. DB bağlantısı kur (SQLite WAL)
  3. WebSocket feed başlat (ws_feed.py)
  4. TradingEngine başlat (engine.py)
  5. AIBrain başlat (ai_brain.py, 10dk döngü)
  6. Telegram Bot başlat (bot.py, polling)

Durdurma: /kill komutu (Telegram) veya data_store/polypaper.stop dosyası oluştur.
Platform: Windows PC, Python 3.11.x
"""

import asyncio
import logging
import logging.handlers
import os
import sys

# Load .env file if exists (PC/Cowork) — Replit uses Secrets instead
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — env vars must be set manually

from config.settings import Settings
from core.engine import TradingEngine
from data.binance_multistream import BinanceMultiStream  # Phase 44a

# Phase 65: keepalive removed (Replit-only, Windows'ta gereksiz)
# from core.keepalive import KeepAlive
from data.candle_collector import CandleCollector
from data.chainlink_oracle import ChainlinkOracle  # Phase 44b
from data.external_feed import ExternalFeed
from data.market_recorder import MarketRecorder
from data.market_scanner import MarketScanner
from data.odds_feed import OddsFeed
from data.polymarket_client import PolymarketClient
from data.websocket_client import PolymarketWebSocket
from db.database import Database
from telegram_bot.bot import PolyPaperBot
from telegram_bot.version import BOT_VERSION  # T0.2: single source of truth

LOG_DIR = "data_store"
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, "polypaper.log")

# Phase 38e: force UTF-8 on stdout/stderr so emoji log lines don't crash
# the Windows cp1252 console handler with UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logging.basicConfig(
    # Phase 48: %(cid)s = correlation id from core.observability (default "-")
    format="%(asctime)s [%(name)s] %(levelname)s [cid=%(cid)s]: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        ),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)  # HOTFIX: suppress WS task_wakeup spam


# Heddas 2026-05-09 LogCleanup-c: py_clob_client_v2 transient "Server
# disconnected" HTTP/2 drops are recoverable (caught + retried by the
# library). The library logs them at ERROR level which pollutes INFO log
# scans. Filter drops the specific transient message; real errors still pass.
class _PyClobTransientFilter(logging.Filter):
    """Drop transient HTTP/2 noise from py_clob_client_v2."""

    _NOISE_PATTERNS = (
        "Server disconnected",
        "RemoteProtocolError",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        # Return False to drop, True to keep
        if record.name.startswith("py_clob_client_v2"):
            msg = record.getMessage()
            for pat in self._NOISE_PATTERNS:
                if pat in msg:
                    return False
        return True


# NOTE: A logger-level filter only intercepts records logged DIRECTLY on
# that logger; records from child loggers (e.g. py_clob_client_v2.http_helpers
# .helpers — where the "Server disconnected" line actually originates) bypass
# parent filters. So attach the filter to the ROOT HANDLERS instead, which
# every record passes through on its way out.
_pyclob_transient_filter = _PyClobTransientFilter()
for _h in logging.getLogger().handlers:
    _h.addFilter(_pyclob_transient_filter)

# Phase 48: install correlation-id filter on every root handler
try:
    from core.observability import CorrelationFilter

    _cfilter = CorrelationFilter()
    for _h in logging.getLogger().handlers:
        _h.addFilter(_cfilter)
except Exception as _e:
    # Fall back to a neutral placeholder so %(cid)s doesn't crash formatters
    class _DefaultCidFilter(logging.Filter):
        def filter(self, record):
            if not hasattr(record, "cid"):
                record.cid = "-"
            return True

    for _h in logging.getLogger().handlers:
        _h.addFilter(_DefaultCidFilter())

logger = logging.getLogger("polypaper")

# ═══ P1-06 (2026-05-09) — Structured JSON logging ═══
# Default ON. Writes data_store/structured.jsonl (100MB × 10 backup, ~1GB cap).
# Each line is a valid JSON object with secret scrubbing (PK, API keys, tokens).
# Console logs continue in human-readable format alongside.
# Disable: STRUCTURED_LOG_ENABLED=false
# Custom path: STRUCTURED_LOG_FILE=path/to/file.jsonl
# Disable scrub (NOT recommended): LOG_SECRET_SCRUB=false
try:
    from core.structured_logging import setup_structured_logging

    _jsonl_handler = setup_structured_logging()
    if _jsonl_handler is not None:
        logger.info(
            "📝 Structured JSON logging active " "(data_store/structured.jsonl, 100MB×10 rotate)"
        )
except Exception as _slog_err:  # noqa: BLE001
    # Defensive: never let logging setup crash the bot. Existing console
    # logs continue to work even if structured layer fails.
    logger.warning(f"structured_logging setup failed: " f"{type(_slog_err).__name__}: {_slog_err}")

# ═══ Phase 48 — Sentry (env-gated, optional) ═══
# Set SENTRY_DSN in .env to enable. Without it, Sentry is fully no-op.
_SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
            # T0.2: BOT_VERSION already starts with "v" (e.g. "v9.7.9"),
            # so no extra "v" prefix in the release tag.
            release=os.getenv("SENTRY_RELEASE", f"polypaper-bot@{BOT_VERSION}"),
            # Breadcrumbs from INFO, events from ERROR+
            integrations=[
                LoggingIntegration(
                    level=logging.INFO,
                    event_level=logging.ERROR,
                )
            ],
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
            # PII hygiene: we handle financial data, never send user content
            send_default_pii=False,
            max_breadcrumbs=50,
        )
        logger.info("🛰 Sentry initialized (env=%s)", os.getenv("SENTRY_ENVIRONMENT", "production"))
    except ImportError:
        logger.warning(
            "⚠️ SENTRY_DSN set but sentry-sdk not installed; " "run: pip install sentry-sdk"
        )
    except Exception as _e:
        logger.warning(f"⚠️ Sentry init failed: {_e}")
else:
    logger.debug("Sentry disabled (no SENTRY_DSN)")


def _acquire_instance_lock() -> bool:
    """Phase 57: Prevent duplicate bot instances via lockfile.
    Creates data_store/polypaper.lock with current PID.
    Returns True if lock acquired, False if another instance is running.
    """
    lock_path = os.path.join(LOG_DIR, "polypaper.lock")
    try:
        if os.path.exists(lock_path):
            with open(lock_path) as f:
                old_pid = f.read().strip()
            # Check if the old PID is still alive (Windows compatible)
            if old_pid.isdigit():
                try:
                    os.kill(int(old_pid), 0)  # signal 0 = check existence
                    logger.error(
                        f"❌ Another bot instance is running (PID {old_pid})! "
                        f"Kill it first or delete {lock_path}"
                    )
                    return False
                except (OSError, ProcessLookupError):
                    # Old process is dead — stale lock, safe to overwrite
                    logger.warning(f"🔓 Stale lockfile (PID {old_pid} dead) — overwriting")
        # Write our PID
        with open(lock_path, "w") as f:
            f.write(str(os.getpid()))
        logger.info(f"🔒 Instance lock acquired (PID {os.getpid()})")
        return True
    except Exception as e:
        logger.warning(f"⚠️ Lock check failed ({e}) — proceeding anyway")
        return True


def _release_instance_lock():
    """Release the lockfile on shutdown."""
    lock_path = os.path.join(LOG_DIR, "polypaper.lock")
    try:
        if os.path.exists(lock_path):
            with open(lock_path) as f:
                pid = f.read().strip()
            if pid == str(os.getpid()):
                os.remove(lock_path)
                logger.info("🔓 Instance lock released")
    except Exception:
        pass


async def main():
    logger.info("=" * 50)
    logger.info("  PolyPaper Bot v34 - Mainnet Ready")
    logger.info(f"  Log: {os.path.abspath(log_file)}")
    logger.info("=" * 50)

    # Phase 57: Single-instance guard
    if not _acquire_instance_lock():
        logger.critical("Aborting: duplicate instance detected")
        raise SystemExit(1)

    settings = Settings()

    # Phase 48: validate env-derived settings before touching the DB / APIs.
    # Catches typos like LIVE_ENABLED=yep or KELLY_FRACTION=1.5 at startup
    # instead of mid-trade.
    try:
        from config.validator import validate_settings

        cfg_errors = validate_settings(settings)
        if cfg_errors:
            for _err in cfg_errors:
                logger.error("CONFIG: %s", _err)
            # Soft fail: in paper mode we still start, in LIVE mode we halt.
            if getattr(settings, "LIVE_ENABLED", False):
                logger.critical("LIVE_ENABLED with config errors → aborting")
                raise SystemExit(2)
            else:
                logger.warning(
                    "⚠️ Config errors detected but LIVE disabled — continuing in paper mode"
                )
        else:
            logger.info("✅ Config validated")
    except ImportError:
        logger.debug("config.validator not available; skipping validation")

    db = Database(settings.DATABASE_PATH)
    await db.initialize()

    # WebSocket client (optional, graceful fallback)
    # P0-08-E4 (2026-05-08): db reference geçilir → ob_deltas + ob_snapshots
    # event-driven persist (price_change + book event handler'ları aktif).
    ws_client = PolymarketWebSocket(db=db)
    ws_available = True
    try:
        import websockets

        logger.info("✅ websockets package found → dual-source mode")
    except ImportError:
        ws_available = False
        ws_client = None
        logger.info("⚠️ websockets not installed → REST-only mode")
        logger.info("  Install with: pip install websockets")

    poly_client = PolymarketClient(settings, ws_client=ws_client)
    server_time = await poly_client.get_server_time()
    logger.info(f"Polymarket connected. Server: {server_time}")

    odds_feed = OddsFeed(poly_client)

    await odds_feed.load_from_db(db)

    # Phase 24 + P0-08-E6 (2026-05-08): External price feed → external_prices DB persist
    external_feed = ExternalFeed(db=db)

    # Phase 44a: Binance multi-stream microstructure feed (depth+aggTrade+funding)
    binance_ms = None
    if getattr(settings, "BINANCE_MULTISTREAM_ENABLED", True):
        binance_ms = BinanceMultiStream(
            trade_window_seconds=getattr(settings, "BINANCE_TRADE_WINDOW_SECONDS", 60.0),
            enable_funding=getattr(settings, "BINANCE_FUTURES_FUNDING", True),
            db=db,  # P0-08-E6: external_prices persist (1s throttle)
        )

    # Phase 44b: Chainlink oracle parity check
    chainlink_oracle = None
    if getattr(settings, "CHAINLINK_ORACLE_ENABLED", False):
        chainlink_oracle = ChainlinkOracle(
            parity_bps=getattr(settings, "CHAINLINK_PARITY_BPS", 20.0),
            db=db,  # P0-08-E6: external_prices persist (60s)
        )

    scanner = MarketScanner(settings, poly_client, db, ws_client=ws_client, odds_feed=odds_feed)
    engine = TradingEngine(settings, db, scanner, odds_feed, external_feed=external_feed)
    engine.binance_multistream = binance_ms  # Phase 44a — engine reads features()
    engine.chainlink_oracle = chainlink_oracle  # Phase 44b — engine reads parity_break()

    # Phase 35 + P0-08-E3 (2026-05-08): multi-TF candle collection.
    # `scanner` attaches scanner.active_markets — per-(asset, tf) market list,
    # candle_collector uses it to record per-market odds candles in their own TF.
    candle_collector = CandleCollector(
        db=db,
        odds_feed=odds_feed,
        ws_client=ws_client,
        external_feed=external_feed,
        httpx_client=poly_client._client,
        scanner=scanner,
    )

    # Attach candle_collector to engine for bot access
    engine.candle_collector = candle_collector

    # Phase 36: High-fidelity market data recorder (10s L2 orderbook snapshots)
    market_recorder = MarketRecorder(
        db=db,
        polymarket_client=poly_client,
        scanner=scanner,
        external_feed=external_feed,
        ws_client=ws_client,
    )
    engine.market_recorder = market_recorder

    bot = PolyPaperBot(
        settings=settings,
        db=db,
        scanner=scanner,
        engine=engine,
        odds_feed=odds_feed,
        poly_client=poly_client,
        ws_client=ws_client,
    )

    # Phase 65: keepalive removed (Replit-only HTTP server, Windows'ta gereksiz)
    keepalive = None

    try:
        # Start WebSocket (if available)
        if ws_available and ws_client:
            await ws_client.connect()

        # Start Binance feed (graceful if unavailable)
        await external_feed.start(poly_client._client)

        # Phase 44a: Start Binance multi-stream
        if binance_ms is not None:
            await binance_ms.start()

        # Phase 44b: Start Chainlink oracle (off by default)
        if chainlink_oracle is not None:
            await chainlink_oracle.start(poly_client._client)

        # Start keep-alive BEFORE engine (Phase 50 P1-04: env-gated)
        if keepalive is not None:
            await keepalive.start()

        # Start candle collector (Phase 35)
        await candle_collector.start()

        # Phase 38d: scanner MUST start BEFORE market_recorder, so that
        # the scanner wires its _on_price_callback first and the recorder's
        # combined_callback can then wrap it. Previously the recorder started
        # first and scanner overwrote the recorder's callback, silently
        # breaking tick-level ob_trades recording.
        await scanner.start()

        # Start market recorder (Phase 36: hardcore backtest data)
        await market_recorder.start()
        await engine.start()
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await engine.stop()
        await scanner.stop()
        await market_recorder.stop()
        await candle_collector.stop()
        if binance_ms is not None:
            await binance_ms.stop()
        if chainlink_oracle is not None:
            await chainlink_oracle.stop()
        if keepalive is not None:
            await keepalive.stop()
        if ws_client:
            await ws_client.stop()
        await poly_client.close()
        await db.close()
        logger.info("Goodbye!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        _release_instance_lock()
