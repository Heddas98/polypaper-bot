"""
PolyPaper Bot - Becker prediction-market dataset loader (Phase 44c skeleton)

The Becker dataset (Jon-Becker/prediction-market-analysis on GitHub) is the
largest publicly available archive of Polymarket + Kalshi market and trade
data — Polymarket alone exceeds 400M trades back to 2020, ~36GB compressed.
Distribution is a single `data.tar.zst` blob hosted on Cloudflare R2,
extracting into:

    data/
      kalshi/markets/      *.parquet
      kalshi/trades/       *.parquet  ← partitioned, used for calibration
      polymarket/markets/  *.parquet
      polymarket/trades/   *.parquet
      polymarket/blocks/   *.parquet

We use DuckDB instead of pandas/pyarrow because:
  - Single 25MB Python wheel, zero native deps on Windows
  - Reads parquet glob patterns natively (`SELECT … FROM 'foo/*.parquet'`)
  - Filter pushdown lets us crypto-subset 36GB → ~2-4GB on disk
  - Becker's own analysis scripts use DuckDB → query parity

Pipeline:
  1. Heddas downloads data.tar.zst via scripts/download_becker.bat
  2. extract to data_store/becker_raw/
  3. becker_loader.build_calibration_db() runs DuckDB query that filters
     trades to crypto Up/Down markets and writes data_store/becker_calibration.db
  4. Engine reads calibration.db at startup for mispricing curve δ(p) lookup

Usage from CLI / Telegram:
    from data.becker_loader import (
        BeckerLoader, build_calibration_db,
        is_dataset_present, dataset_status,
    )
    loader = BeckerLoader()
    if not is_dataset_present():
        print(loader.download_instructions())
    else:
        loader.build_calibration_db()
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("polypaper.data.becker")

# ── Paths (relative to project root) ────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RAW_ROOT = PROJECT_ROOT / "data_store" / "becker_raw"
# The Becker archive extracts with an extra top-level `data/` directory, so the
# real parquet root lives one level deeper. Prefer the nested path when present
# to stay compatible with both the raw tarball layout and any manual layouts.
RAW_DIR = (_RAW_ROOT / "data") if (_RAW_ROOT / "data").exists() else _RAW_ROOT
CALIB_DB = PROJECT_ROOT / "data_store" / "becker_calibration.db"
ARCHIVE_PATH = PROJECT_ROOT / "data_store" / "becker_data.tar.zst"

# Canonical URL verified from Jon-Becker/prediction-market-analysis
# scripts/download.sh on 2026-04-08. Override via BECKER_DATA_URL env var.
DEFAULT_DATA_URL = os.environ.get(
    "BECKER_DATA_URL",
    "https://s3.jbecker.dev/data.tar.zst",
)

# Crypto Up/Down ticker prefixes used in Kalshi/Polymarket — used by the
# DuckDB filter to pull just the relevant trades for our calibration curve.
CRYPTO_TICKERS = (
    "KXBTC", "KXETH", "KXSOL", "KXXRP",            # Kalshi crypto event tickers
    "btc-up-or-down", "eth-up-or-down",            # Polymarket slug roots
    "sol-up-or-down", "xrp-up-or-down",
)


def is_dataset_present() -> bool:
    """True if the extracted Becker raw dataset is on disk."""
    if not RAW_DIR.exists():
        return False
    # Smoke test: at least one parquet file under kalshi/trades
    kalshi_trades = RAW_DIR / "kalshi" / "trades"
    if not kalshi_trades.exists():
        return False
    try:
        return any(kalshi_trades.glob("*.parquet"))
    except OSError:
        # T11.8-B (2026-04-24): narrow from bare Exception. Path.glob() can
        # raise OSError on permission errors / removed mount.
        return False


def dataset_status() -> dict:
    """Return a structured status dict for /becker_status command."""
    archive = ARCHIVE_PATH
    return {
        "archive_present": archive.exists(),
        "archive_bytes": archive.stat().st_size if archive.exists() else 0,
        "raw_present": RAW_DIR.exists(),
        "raw_files": _count_parquet(RAW_DIR) if RAW_DIR.exists() else 0,
        "calib_present": CALIB_DB.exists(),
        "calib_bytes": CALIB_DB.stat().st_size if CALIB_DB.exists() else 0,
    }


def _count_parquet(root: Path) -> int:
    if not root.exists():
        return 0
    try:
        return sum(1 for _ in root.rglob("*.parquet"))
    except OSError:
        # T11.8-B (2026-04-24): narrow from bare Exception. Path.rglob() can
        # raise OSError when traversing a removed/permission-denied subtree.
        return 0


class BeckerLoader:
    """Wraps DuckDB queries against the extracted dataset."""

    def __init__(self, raw_dir: Path = RAW_DIR, calib_db: Path = CALIB_DB):
        self.raw_dir = raw_dir
        self.calib_db = calib_db

    @staticmethod
    def download_instructions() -> str:
        return (
            "Becker dataset is not yet downloaded. To fetch:\n"
            "  1. cd Polyscout31 && scripts\\download_becker.bat\n"
            "  2. Wait ~30-60 min (36GB Cloudflare R2 download)\n"
            "  3. The .bat extracts to data_store/becker_raw/\n"
            "  4. Run /becker_build in Telegram to materialize the\n"
            "     crypto-only calibration DB (data_store/becker_calibration.db)"
        )

    def build_calibration_db(self) -> Optional[dict]:
        """Filter the raw parquet to crypto Up/Down trades only and write
        a compact SQLite calibration DB. Stub for now — needs DuckDB import
        which we lazy-load to avoid import-time failure when DuckDB is not
        installed yet on the target machine."""
        import time

        def _stage(msg: str) -> None:
            line = f"[becker {time.strftime('%H:%M:%S')}] {msg}"
            logger.info(line)
            try:
                print(line, flush=True)
            except (OSError, ValueError):
                # T11.8-B (2026-04-24): narrow from bare Exception. print()
                # raises OSError on closed/broken pipe and ValueError when the
                # underlying stream is detached. Silent swallow keeps the
                # build progressing even if Telegram launcher closes stdout.
                pass

        if not is_dataset_present():
            logger.warning("becker: raw dataset not present, skipping build")
            return None
        try:
            import duckdb  # type: ignore
        except ImportError:
            logger.warning(
                "becker: DuckDB not installed. "
                "pip install duckdb (~25MB, zero deps)")
            return None

        # AppleDouble cleanup is sentinel-guarded so reruns are instant.
        sentinel = self.raw_dir / ".appledouble_cleaned"
        if not sentinel.exists():
            _stage("AppleDouble cleanup starting (rglob ._*.parquet) ...")
            removed = 0
            for p in self.raw_dir.rglob("._*.parquet"):
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
            try:
                sentinel.write_text(f"removed={removed}\n")
            except OSError:
                pass
            _stage(f"AppleDouble cleanup done — removed {removed} stubs")
        else:
            _stage("AppleDouble cleanup skipped (sentinel present)")

        con = duckdb.connect(str(self.calib_db))
        skip_poly = os.environ.get("BECKER_SKIP_POLY", "").lower() in ("1", "true", "yes")
        try:

            # Forward-slash paths so DuckDB's glob behaves on Windows.
            def _glob(*parts: str) -> str:
                return str(self.raw_dir.joinpath(*parts)).replace("\\", "/")

            kalshi_trades_glob = _glob("kalshi", "trades", "*.parquet")
            kalshi_markets_glob = _glob("kalshi", "markets", "*.parquet")
            _stage("kalshi: building kalshi_crypto (markets filter + trades JOIN) ...")
            con.execute("DROP TABLE IF EXISTS kalshi_crypto")
            con.execute(f"""
                CREATE TABLE kalshi_crypto AS
                WITH crypto_markets AS (
                    SELECT ticker, event_ticker, result, close_time
                    FROM read_parquet('{kalshi_markets_glob}')
                    WHERE
                        starts_with(event_ticker, 'KXBTC')
                     OR starts_with(event_ticker, 'KXETH')
                     OR starts_with(event_ticker, 'KXSOL')
                     OR starts_with(event_ticker, 'KXXRP')
                )
                SELECT
                    t.trade_id,
                    t.ticker,
                    m.event_ticker,
                    t.count,
                    t.yes_price,
                    t.no_price,
                    t.taker_side,
                    t.created_time,
                    m.result AS market_result,
                    m.close_time
                FROM read_parquet('{kalshi_trades_glob}') AS t
                JOIN crypto_markets AS m USING (ticker)
            """)
            n_kalshi = con.execute(
                "SELECT COUNT(*) FROM kalshi_crypto"
            ).fetchone()[0]
            _stage(f"kalshi_crypto materialized n={n_kalshi}")

            if skip_poly:
                _stage("polymarket build SKIPPED (BECKER_SKIP_POLY=1)")
                return {"kalshi_crypto": n_kalshi, "poly_crypto": 0}

            # Polymarket trades parquet is EVM contract-log data with columns:
            #   block_number, transaction_hash, log_index, order_hash, maker,
            #   taker, maker_asset_id, taker_asset_id, maker_amount,
            #   taker_amount, fee, timestamp, _fetched_at, _contract
            # To get the market slug we join on clob_token_ids from
            # polymarket/markets. clob_token_ids is a JSON-encoded VARCHAR
            # string like '["tok_a","tok_b"]' — unnest via json_extract.
            poly_trades_glob = _glob("polymarket", "trades", "*.parquet")
            poly_markets_glob = _glob("polymarket", "markets", "*.parquet")
            _stage("poly: materializing crypto_markets token_id list ...")
            # First materialize the small crypto_markets table — this lets
            # DuckDB hash-join the trades scan instead of nested-looping it.
            con.execute("DROP TABLE IF EXISTS poly_crypto_markets")
            # NOTE: clob_token_ids is JSON like '["1234...","5678..."]'.
            # json_extract($[*]) leaves JSON-quoted strings ('"1234..."')
            # but trades store the plain numeric form ('1234...').
            # Use json_extract_string with explicit indices to get unquoted
            # values, then UNION the two outcome ids per market.
            con.execute(f"""
                CREATE TABLE poly_crypto_markets AS
                WITH base AS (
                    SELECT
                        condition_id,
                        slug,
                        outcome_prices,
                        closed,
                        end_date,
                        json_extract_string(clob_token_ids, '$[0]') AS yes_id,
                        json_extract_string(clob_token_ids, '$[1]') AS no_id
                    FROM read_parquet('{poly_markets_glob}')
                    WHERE
                        slug ILIKE '%btc-up-or-down%'
                     OR slug ILIKE '%eth-up-or-down%'
                     OR slug ILIKE '%sol-up-or-down%'
                     OR slug ILIKE '%xrp-up-or-down%'
                )
                SELECT condition_id, slug, outcome_prices, closed, end_date,
                       yes_id AS token_id, 'yes' AS side
                FROM base WHERE yes_id IS NOT NULL
                UNION ALL
                SELECT condition_id, slug, outcome_prices, closed, end_date,
                       no_id AS token_id, 'no' AS side
                FROM base WHERE no_id IS NOT NULL
            """)
            n_markets = con.execute(
                "SELECT COUNT(*) FROM poly_crypto_markets"
            ).fetchone()[0]
            _stage(f"poly: crypto_markets materialized n={n_markets} token_ids")

            _stage("poly: building poly_crypto via UNION ALL of equi-joins ...")
            con.execute("DROP TABLE IF EXISTS poly_crypto")
            # OR-joins force nested-loop scan over 40k+ trade parquet files.
            # Split into UNION ALL of two equi-joins so DuckDB hash-joins each
            # side independently — orders of magnitude faster.
            con.execute(f"""
                CREATE TABLE poly_crypto AS
                SELECT * FROM (
                    SELECT
                        t.block_number,
                        t.transaction_hash,
                        t.log_index,
                        t.maker,
                        t.taker,
                        t.maker_asset_id,
                        t.taker_asset_id,
                        t.maker_amount,
                        t.taker_amount,
                        t.fee,
                        t.timestamp,
                        m.condition_id,
                        m.slug,
                        m.outcome_prices,
                        m.closed,
                        m.end_date
                    FROM read_parquet('{poly_trades_glob}') AS t
                    JOIN poly_crypto_markets AS m
                      ON t.maker_asset_id = m.token_id
                    UNION ALL
                    SELECT
                        t.block_number,
                        t.transaction_hash,
                        t.log_index,
                        t.maker,
                        t.taker,
                        t.maker_asset_id,
                        t.taker_asset_id,
                        t.maker_amount,
                        t.taker_amount,
                        t.fee,
                        t.timestamp,
                        m.condition_id,
                        m.slug,
                        m.outcome_prices,
                        m.closed,
                        m.end_date
                    FROM read_parquet('{poly_trades_glob}') AS t
                    JOIN poly_crypto_markets AS m
                      ON t.taker_asset_id = m.token_id
                )
            """)
            n_poly = con.execute(
                "SELECT COUNT(*) FROM poly_crypto"
            ).fetchone()[0]
            _stage(f"poly_crypto materialized n={n_poly}")

            return {"kalshi_crypto": n_kalshi, "poly_crypto": n_poly}
        finally:
            con.close()

    def calibration_curve(self, source: str = "kalshi") -> Optional[list]:
        """Return a [(implied_p, actual_wr, n_trades), ...] mispricing curve
        binned at 5% intervals from the calibration DB. Used by signal_fusion
        to apply δ(p) correction to raw probability estimates.

        Schema notes (verified 2026-04-08 against materialized DB):
          - kalshi.yes_price is INTEGER cents 1-99 → divide by 100 for fraction
          - kalshi.market_result is VARCHAR 'yes' / 'no' / '' (unsettled)
          - poly: USDC sentinel asset_id = '0'. Token asset_ids are 76+ char.
              token_in_maker (1.1M trades) → maker SOLD token for USDC →
                  token_price = taker_amount / maker_amount  (USDC/token)
              token_in_taker (4.6M trades) → maker BOUGHT token with USDC →
                  token_price = maker_amount / taker_amount
              poly_crypto_markets.side reveals if the token is the YES or NO
              outcome of its market. yes_price = token_price if side='yes'
              else 1 - token_price. Resolution: outcome_prices JSON like
              '["1","0"]' (yes won) or '["0","1"]' (no won) or
              '["0.5","0.5"]' (push). resolved_yes = $[0] cast to double.
        """
        if not self.calib_db.exists():
            return None
        try:
            import duckdb  # type: ignore
        except ImportError:
            return None

        con = duckdb.connect(str(self.calib_db), read_only=True)
        try:
            if source == "kalshi":
                return con.execute("""
                    WITH bins AS (
                        SELECT
                            FLOOR(yes_price / 5.0) * 5 / 100.0 AS bin,
                            AVG(CASE WHEN market_result = 'yes' THEN 1.0 ELSE 0.0 END) AS actual,
                            COUNT(*) AS n
                        FROM kalshi_crypto
                        WHERE market_result IN ('yes', 'no')
                          AND yes_price BETWEEN 5 AND 95
                        GROUP BY bin
                    )
                    SELECT bin, actual, n FROM bins ORDER BY bin
                """).fetchall()
            elif source == "poly":
                # poly_crypto stores trades but does NOT carry the m.side
                # column (it's in poly_crypto_markets only). Re-join via
                # UNION ALL of two equi-joins so each branch handles one
                # token-position case independently:
                #   token in maker → token_price = taker_amount / maker_amount
                #   token in taker → token_price = maker_amount / taker_amount
                # Then flip if side='no' to get yes_price.
                return con.execute("""
                    WITH priced AS (
                        SELECT m.side, t.outcome_prices,
                               CAST(t.taker_amount AS DOUBLE) / NULLIF(t.maker_amount, 0) AS token_price
                        FROM poly_crypto AS t
                        JOIN poly_crypto_markets AS m
                          ON t.maker_asset_id = m.token_id
                        WHERE t.maker_amount > 0
                          AND t.taker_amount > 0
                          AND t.outcome_prices IS NOT NULL
                        UNION ALL
                        SELECT m.side, t.outcome_prices,
                               CAST(t.maker_amount AS DOUBLE) / NULLIF(t.taker_amount, 0) AS token_price
                        FROM poly_crypto AS t
                        JOIN poly_crypto_markets AS m
                          ON t.taker_asset_id = m.token_id
                        WHERE t.maker_amount > 0
                          AND t.taker_amount > 0
                          AND t.outcome_prices IS NOT NULL
                    ),
                    yes_priced AS (
                        SELECT
                            CASE WHEN side = 'yes' THEN token_price
                                 ELSE 1.0 - token_price END AS yes_price,
                            CAST(json_extract_string(outcome_prices, '$[0]') AS DOUBLE) AS resolved_yes
                        FROM priced
                        WHERE token_price BETWEEN 0.0 AND 1.0
                    ),
                    bins AS (
                        SELECT
                            FLOOR(yes_price * 20) / 20 AS bin,
                            AVG(resolved_yes) AS actual,
                            COUNT(*) AS n
                        FROM yes_priced
                        WHERE yes_price BETWEEN 0.05 AND 0.95
                          AND resolved_yes IS NOT NULL
                        GROUP BY bin
                    )
                    SELECT bin, actual, n FROM bins ORDER BY bin
                """).fetchall()
            else:
                logger.warning(f"becker: unknown calibration source {source!r}")
                return None
        except Exception as e:  # noqa: BLE001
            # T11.8-B (2026-04-24): bare Exception kept on purpose. DuckDB
            # raises ~12 distinct typed exceptions (CatalogException,
            # BinderException, IOException, ConversionException, ...) all
            # subclassed off duckdb.Error which would require a duckdb-specific
            # import. We string-match "does not exist" to detect the missing-
            # table happy path; other failures fall through to error log. Wide
            # catch is acceptable here because path is read-only diagnostic.
            # Phase 57: downgrade to warning — missing poly_crypto table is
            # expected when BECKER_SKIP_POLY=1 or dataset not built yet.
            if "does not exist" in str(e):
                logger.warning(f"becker calibration: table not built yet ({source}). Run /becker_build to create.")
            else:
                logger.error(f"becker calibration query failed: {e}")
            return None
        finally:
            con.close()


def build_calibration_db() -> Optional[dict]:
    """Module-level convenience for /becker_build."""
    return BeckerLoader().build_calibration_db()
