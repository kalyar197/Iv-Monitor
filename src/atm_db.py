"""SQLite database manager for synthetic ATM IV time series."""
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any
import aiosqlite


class ATMDatabase:
    """
    Manages SQLite database for storing synthetic ATM IV history.

    Stores linearly interpolated ATM IV values per expiry (not specific strikes)
    to create smooth time series unaffected by shifting ATM strikes.
    """

    def __init__(
        self,
        db_path: str = "data/atm_iv.sqlite",
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize ATM database manager.

        Args:
            db_path: Path to SQLite database file
            logger: Logger instance
        """
        self.db_path = Path(db_path)
        self.logger = logger or logging.getLogger(__name__)
        self.connection: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """
        Connect to database and create schema if needed.

        Creates the atm_iv_history table with schema for synthetic ATM IV,
        spot price, ATM strike reference, and 25-delta skew metrics.
        """
        # Ensure data directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connect to SQLite
        self.connection = await aiosqlite.connect(str(self.db_path))

        # Enable Write-Ahead Logging for better concurrency
        await self.connection.execute("PRAGMA journal_mode=WAL")

        # Create schema (idempotent)
        await self.connection.execute("""
            CREATE TABLE IF NOT EXISTS atm_iv_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expiry_date TEXT NOT NULL,
                timestamp DATETIME NOT NULL,

                -- Synthetic ATM IV (linearly interpolated from nearest strikes)
                synthetic_atm_iv REAL NOT NULL,

                -- Context for analysis
                spot_price REAL,
                perpetual_price REAL,
                basis REAL,
                basis_pct REAL,
                funding_rate REAL,
                atm_strike_price REAL,

                -- Skew metrics for sentiment analysis
                call_25d_iv REAL,
                put_25d_iv REAL,

                UNIQUE(expiry_date, timestamp)
            )
        """)

        # Create index for fast queries
        await self.connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_expiry_timestamp
            ON atm_iv_history(expiry_date, timestamp DESC)
        """)

        await self.connection.commit()

        self.logger.info(f"Connected to ATM database at {self.db_path}")

    async def disconnect(self) -> None:
        """Close database connection."""
        if self.connection:
            await self.connection.close()
            self.connection = None
            self.logger.info("Disconnected from ATM database")

    async def insert_atm_record(
        self,
        expiry_date: str,
        synthetic_atm_iv: float,
        spot_price: float,
        atm_strike_price: float,
        call_25d_iv: float,
        put_25d_iv: float,
        perpetual_price: Optional[float] = None,
        funding_rate: Optional[float] = None,
        timestamp: Optional[datetime] = None
    ) -> None:
        """
        Store synthetic ATM IV record with skew metrics and basis tracking.

        Args:
            expiry_date: Expiry in YYMMDD format (e.g., "251226")
            synthetic_atm_iv: Linearly interpolated ATM IV (decimal, e.g., 0.5093)
            spot_price: BTC spot price at time of measurement
            atm_strike_price: Strike price used as ATM reference
            call_25d_iv: 25-delta call IV for skew analysis (decimal)
            put_25d_iv: 25-delta put IV for skew analysis (decimal)
            perpetual_price: Perpetual futures mark price (optional)
            funding_rate: 8-hour funding rate (optional, decimal)
            timestamp: Record timestamp (defaults to now)
        """
        if not self.connection:
            raise RuntimeError("Database not connected. Call connect() first.")

        timestamp = timestamp or datetime.utcnow()

        # Calculate basis if perpetual price provided
        basis = None
        basis_pct = None
        if perpetual_price is not None and spot_price > 0:
            basis = perpetual_price - spot_price
            basis_pct = (basis / spot_price) * 100

        try:
            await self.connection.execute("""
                INSERT INTO atm_iv_history (
                    expiry_date, timestamp, synthetic_atm_iv,
                    spot_price, perpetual_price, basis, basis_pct, funding_rate,
                    atm_strike_price, call_25d_iv, put_25d_iv
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (expiry_date, timestamp) DO UPDATE SET
                    synthetic_atm_iv = excluded.synthetic_atm_iv,
                    spot_price = excluded.spot_price,
                    perpetual_price = excluded.perpetual_price,
                    basis = excluded.basis,
                    basis_pct = excluded.basis_pct,
                    funding_rate = excluded.funding_rate,
                    atm_strike_price = excluded.atm_strike_price,
                    call_25d_iv = excluded.call_25d_iv,
                    put_25d_iv = excluded.put_25d_iv
            """, [
                expiry_date,
                timestamp,
                synthetic_atm_iv,
                spot_price,
                perpetual_price,
                basis,
                basis_pct,
                funding_rate,
                atm_strike_price,
                call_25d_iv,
                put_25d_iv
            ])

            await self.connection.commit()

            self.logger.debug(
                f"Inserted ATM record: {expiry_date} @ {timestamp.strftime('%H:%M:%S')} "
                f"IV={synthetic_atm_iv*100:.2f}%"
                + (f", Basis={basis_pct:+.2f}%" if basis_pct is not None else "")
            )

        except Exception as e:
            self.logger.error(f"Failed to insert ATM record: {e}", exc_info=True)
            raise

    async def get_atm_history(
        self,
        expiry_date: str,
        hours: int = 24
    ) -> List[Dict[str, Any]]:
        """
        Get synthetic ATM IV time series for statistical analysis.

        Args:
            expiry_date: Expiry in YYMMDD format
            hours: Number of hours of history to retrieve

        Returns:
            List of records with keys: expiry_date, timestamp, synthetic_atm_iv,
            spot_price, perpetual_price, basis, basis_pct, funding_rate,
            atm_strike_price, call_25d_iv, put_25d_iv
            Sorted by timestamp ascending (oldest first)
        """
        if not self.connection:
            raise RuntimeError("Database not connected. Call connect() first.")

        cutoff_time = datetime.utcnow() - timedelta(hours=hours)

        try:
            async with self.connection.execute("""
                SELECT
                    expiry_date,
                    timestamp,
                    synthetic_atm_iv,
                    spot_price,
                    perpetual_price,
                    basis,
                    basis_pct,
                    funding_rate,
                    atm_strike_price,
                    call_25d_iv,
                    put_25d_iv
                FROM atm_iv_history
                WHERE expiry_date = ?
                  AND timestamp >= ?
                ORDER BY timestamp ASC
            """, [expiry_date, cutoff_time]) as cursor:
                rows = await cursor.fetchall()

            # Convert to list of dicts
            records = []
            for row in rows:
                records.append({
                    'expiry_date': row[0],
                    'timestamp': row[1],
                    'synthetic_atm_iv': row[2],
                    'spot_price': row[3],
                    'perpetual_price': row[4],
                    'basis': row[5],
                    'basis_pct': row[6],
                    'funding_rate': row[7],
                    'atm_strike_price': row[8],
                    'call_25d_iv': row[9],
                    'put_25d_iv': row[10]
                })

            self.logger.debug(
                f"Retrieved {len(records)} ATM records for {expiry_date} "
                f"(last {hours}h)"
            )

            return records

        except Exception as e:
            self.logger.error(f"Failed to get ATM history: {e}", exc_info=True)
            raise

    async def get_iv_percentile(
        self,
        expiry_date: str,
        current_iv: float,
        hours: int = 24
    ) -> Optional[float]:
        """
        Calculate where current IV sits in the historical range (0-100%).

        Args:
            expiry_date: Expiry in YYMMDD format
            current_iv: Current synthetic ATM IV (decimal)
            hours: Number of hours to use for range calculation

        Returns:
            Percentile (0-100) or None if insufficient data
        """
        if not self.connection:
            raise RuntimeError("Database not connected. Call connect() first.")

        cutoff_time = datetime.utcnow() - timedelta(hours=hours)

        try:
            async with self.connection.execute("""
                SELECT
                    MIN(synthetic_atm_iv) as min_iv,
                    MAX(synthetic_atm_iv) as max_iv
                FROM atm_iv_history
                WHERE expiry_date = ?
                  AND timestamp >= ?
            """, [expiry_date, cutoff_time]) as cursor:
                result = await cursor.fetchone()

            if not result or result[0] is None or result[1] is None:
                return None

            min_iv, max_iv = result[0], result[1]

            # Calculate percentile
            iv_range = max_iv - min_iv
            if iv_range < 1e-6:  # Essentially no variance
                return 50.0

            percentile = ((current_iv - min_iv) / iv_range) * 100
            percentile = max(0.0, min(100.0, percentile))  # Clamp to [0, 100]

            return percentile

        except Exception as e:
            self.logger.error(f"Failed to calculate IV percentile: {e}", exc_info=True)
            raise

    async def cleanup_old_records(self, hours: int = 48) -> int:
        """
        Delete records older than specified hours.

        Args:
            hours: Age threshold for deletion

        Returns:
            Number of records deleted
        """
        if not self.connection:
            raise RuntimeError("Database not connected. Call connect() first.")

        cutoff_time = datetime.utcnow() - timedelta(hours=hours)

        try:
            cursor = await self.connection.execute("""
                DELETE FROM atm_iv_history
                WHERE timestamp < ?
            """, [cutoff_time])

            await self.connection.commit()

            deleted_count = cursor.rowcount

            if deleted_count > 0:
                self.logger.info(f"Cleaned up {deleted_count} old ATM records")

            return deleted_count

        except Exception as e:
            self.logger.error(f"Failed to cleanup old records: {e}", exc_info=True)
            raise

    async def get_record_count(self, expiry_date: Optional[str] = None) -> int:
        """
        Get total record count (optionally filtered by expiry).

        Args:
            expiry_date: Optional expiry filter

        Returns:
            Number of records
        """
        if not self.connection:
            raise RuntimeError("Database not connected. Call connect() first.")

        try:
            if expiry_date:
                async with self.connection.execute("""
                    SELECT COUNT(*) FROM atm_iv_history
                    WHERE expiry_date = ?
                """, [expiry_date]) as cursor:
                    result = await cursor.fetchone()
            else:
                async with self.connection.execute("""
                    SELECT COUNT(*) FROM atm_iv_history
                """) as cursor:
                    result = await cursor.fetchone()

            return result[0] if result else 0

        except Exception as e:
            self.logger.error(f"Failed to get record count: {e}", exc_info=True)
            raise

    async def get_all_expiries(self) -> List[str]:
        """
        Get list of all expiry dates currently in database.

        Returns:
            List of expiry dates in YYMMDD format
        """
        if not self.connection:
            raise RuntimeError("Database not connected. Call connect() first.")

        try:
            async with self.connection.execute("""
                SELECT DISTINCT expiry_date
                FROM atm_iv_history
                ORDER BY expiry_date
            """) as cursor:
                rows = await cursor.fetchall()

            return [row[0] for row in rows]

        except Exception as e:
            self.logger.error(f"Failed to get expiries: {e}", exc_info=True)
            raise

    # Context manager support
    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
