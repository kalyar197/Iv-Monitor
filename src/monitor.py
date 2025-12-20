"""Main monitoring orchestrator for Options IV (Binance or Deribit)."""
import asyncio
import fnmatch
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from .binance_client import BinanceOptionsClient
from .deribit_client import DeribitOptionsClient
from .discord_notifier import DiscordNotifier
from .atm_db import ATMDatabase
from .statistics import StatisticalAnalyzer


class IVMonitor:
    """
    Main orchestrator for monitoring Binance Options IV.

    Coordinates symbol filtering, WebSocket streaming, IV extraction,
    and Discord notifications.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize the IV monitor.

        Args:
            config: Configuration dictionary
            logger: Logger instance
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)

        # Initialize exchange client based on configuration
        self.exchange_name = config.get('exchange', 'binance').lower()

        if self.exchange_name == 'deribit':
            deribit_config = config['deribit']
            self.client = DeribitOptionsClient(
                base_url=deribit_config['base_url'],
                logger=self.logger
            )
            self.logger.info("Using Deribit exchange (PUBLIC API - no authentication)")
        else:  # binance
            binance_config = config['binance']
            self.client = BinanceOptionsClient(
                api_key=binance_config['api_key'],
                api_secret=binance_config['api_secret'],
                base_url=binance_config['base_url'],
                websocket_url=binance_config['websocket_url'],
                logger=self.logger
            )
            self.logger.info("Using Binance exchange")

        discord_config = config['discord']
        self.notifier = DiscordNotifier(
            webhook_url=discord_config['webhook_url'],
            mention_role_id=discord_config.get('mention_role_id'),
            logger=self.logger
        )

        # Initialize database and statistical analyzer (conditional based on mode)
        database_config = config.get('database', {})
        statistics_config = config.get('statistics', {})
        self.statistics_mode = statistics_config.get('mode', 'statistical')

        if self.statistics_mode == 'statistical':
            self.atm_db = ATMDatabase(
                db_path=database_config.get('path', 'data/atm_iv.sqlite'),
                logger=self.logger
            )
            self.analyzer = StatisticalAnalyzer(
                z_score_threshold=statistics_config.get('z_score_threshold', 2.0),
                min_samples=statistics_config.get('min_samples', 10),
                min_history_hours=statistics_config.get('min_history_hours', 4.0),
                logger=self.logger
            )
        else:
            self.atm_db = None
            self.analyzer = None
            self.logger.info("Running in SIMPLE mode - threshold-based alerts only")

        # Monitoring configuration
        monitoring = config['monitoring']
        self.symbol_patterns = monitoring['symbols']
        self.iv_threshold = monitoring['iv_threshold']
        self.min_open_interest = monitoring.get('min_open_interest', 10000)
        self.iv_increase_threshold = monitoring.get('iv_increase_threshold', 1.0)
        self.atm_range_percent = monitoring.get('atm_range_percent', 5.0)
        self.min_days_to_expiry = monitoring.get('min_days_to_expiry')
        self.max_days_to_expiry = monitoring.get('max_days_to_expiry')

        # Filtering configuration
        filtering_config = config.get('filtering', {})
        self.delta_min = filtering_config.get('delta_min', 0.05)
        self.delta_max = filtering_config.get('delta_max', 0.65)

        # State tracking
        self.monitored_symbols: List[str] = []
        self.last_alerted_iv: Dict[str, float] = {}  # Track last alerted IV per expiry
        self.initial_alert_iv: Dict[str, float] = {}  # Track first alert IV per expiry (for reset detection)
        self.spot_prices: Dict[str, float] = {}
        self.perpetual_mark_prices: Dict[str, float] = {}
        self.funding_rates: Dict[str, float] = {}

        # Account data cache (for Greeks/IV if available)
        self._account_data: Optional[Dict[str, Any]] = None

    async def start(self):
        """Start the IV monitoring system."""
        try:
            self.logger.info("=== Binance Options IV Monitor Starting ===")

            # Initialize connections (including database if in statistical mode)
            if self.atm_db:
                await self.atm_db.connect()

            async with self.client, self.notifier:
                # Fetch account info to check for Greeks/IV
                try:
                    self._account_data = await self.client.get_account_info()
                    self.logger.info(f"Account data fetched: {list(self._account_data.keys())}")
                except Exception as e:
                    self.logger.warning(f"Could not fetch account data: {e}")

                # Fetch initial prices FIRST (needed for ATM filtering)
                await self._update_prices()

                # Discover and filter symbols (uses spot prices for ATM filtering)
                await self._discover_symbols()

                if not self.monitored_symbols:
                    self.logger.error("No symbols to monitor! Check your configuration.")
                    return

                self.logger.info(f"Monitoring {len(self.monitored_symbols)} symbols")

                # Send startup notification
                if self.config.get('discord', {}).get('send_startup_notification', True):
                    await self.notifier.send_startup_notification(len(self.monitored_symbols))

                # Main polling loop - check mark prices for IV
                check_interval = self.config['monitoring'].get('check_interval', 10)
                self.logger.info(f"Starting IV monitoring (polling every {check_interval}s)")

                iteration = 0
                while True:
                    iteration += 1

                    try:
                        # Fetch mark price data for all monitored symbols
                        await self._check_all_symbols_iv()

                        # Periodically refresh prices and symbols (every 5 minutes)
                        if iteration % (300 // check_interval) == 0:
                            await self._update_prices()
                            await self._discover_symbols()

                        # Periodically cleanup old database records (every hour, if using database)
                        if self.atm_db and iteration % (3600 // check_interval) == 0:
                            await self.atm_db.cleanup_old_records(hours=48)

                    except Exception as e:
                        self.logger.error(f"Error in monitoring loop: {e}", exc_info=True)

                    # Wait before next check
                    await asyncio.sleep(check_interval)

        except KeyboardInterrupt:
            self.logger.info("Shutting down gracefully...")
        except Exception as e:
            self.logger.error(f"Fatal error in monitor: {e}", exc_info=True)
            await self.notifier.send_error_notification(f"Monitor crashed: {str(e)}")
            raise
        finally:
            # Cleanup database connection (if exists)
            if self.atm_db:
                await self.atm_db.disconnect()

    async def _discover_symbols(self):
        """Discover and filter option symbols based on configured patterns."""
        self.logger.info("Discovering option symbols...")

        try:
            # Fetch symbols based on exchange
            if self.exchange_name == 'deribit':
                # Deribit: get_instruments returns list of instruments
                instruments = await self.client.get_instruments(currency="BTC", kind="option", expired=False)
                all_symbols = [inst['instrument_name'] for inst in instruments]
            else:
                # Binance: get_exchange_info returns dict with optionSymbols array
                exchange_info = await self.client.get_exchange_info()
                all_symbols = [s['symbol'] for s in exchange_info.get('optionSymbols', [])]

            self.logger.info(f"Found {len(all_symbols)} total option symbols")

            # Filter symbols based on patterns
            filtered_symbols = self._filter_symbols(all_symbols, self.symbol_patterns)

            # Update monitored symbols if changed
            if set(filtered_symbols) != set(self.monitored_symbols):
                added = set(filtered_symbols) - set(self.monitored_symbols)
                removed = set(self.monitored_symbols) - set(filtered_symbols)

                if added:
                    self.logger.info(f"Added {len(added)} new symbols: {added}")
                if removed:
                    self.logger.info(f"Removed {len(removed)} expired symbols: {removed}")

                self.monitored_symbols = filtered_symbols

        except Exception as e:
            self.logger.error(f"Error discovering symbols: {e}", exc_info=True)

    def _filter_symbols(self, all_symbols: List[str], patterns: List[str]) -> List[str]:
        """
        Filter symbols based on configured patterns.

        Supports:
        - Exact match: BTC-250131-50000-C
        - Wildcards: BTC-250131-*-C, BTC-*-50000-C
        - ATM pattern: BTC-*-ATM-C

        Args:
            all_symbols: List of all available symbols
            patterns: List of patterns to match

        Returns:
            Filtered list of symbols
        """
        matched_symbols: Set[str] = set()

        for pattern in patterns:
            if 'ATM' in pattern:
                # Handle ATM pattern specially
                matched_symbols.update(self._filter_atm_symbols(all_symbols, pattern))
            else:
                # Use fnmatch for wildcard patterns
                pattern_lower = pattern.replace('*', '*').lower()
                for symbol in all_symbols:
                    if fnmatch.fnmatch(symbol.lower(), pattern_lower):
                        matched_symbols.add(symbol)

        return sorted(matched_symbols)

    def _parse_expiry_date(self, expiry_str: str) -> Optional[datetime]:
        """
        Parse expiry date from symbol format.

        Supports both formats:
        - Binance: YYMMDD (e.g., "260130" for Jan 30, 2026)
        - Deribit: DDMMMYY (e.g., "27DEC24" for Dec 27, 2024)

        Args:
            expiry_str: Expiry date string

        Returns:
            Datetime object or None if parsing fails
        """
        try:
            # Try Binance format first: YYMMDD (6 digits)
            if expiry_str.isdigit() and len(expiry_str) == 6:
                year = 2000 + int(expiry_str[:2])
                month = int(expiry_str[2:4])
                day = int(expiry_str[4:6])
                return datetime(year, month, day)

            # Try Deribit format: DDMMMYY (e.g., "27DEC24")
            elif len(expiry_str) == 7:
                day = int(expiry_str[:2])
                month_str = expiry_str[2:5].upper()
                year = 2000 + int(expiry_str[5:7])

                month_map = {
                    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
                    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
                }
                month = month_map.get(month_str)
                if month is None:
                    return None

                return datetime(year, month, day)
            else:
                return None

        except (ValueError, IndexError):
            return None

    def _get_days_to_expiry(self, expiry_str: str) -> Optional[int]:
        """
        Calculate days to expiry from today.

        Args:
            expiry_str: Expiry date string (e.g., "260130")

        Returns:
            Number of days to expiry, or None if parsing fails
        """
        expiry_date = self._parse_expiry_date(expiry_str)
        if expiry_date is None:
            return None

        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        delta = expiry_date - today
        return delta.days

    def _filter_atm_symbols(self, all_symbols: List[str], pattern: str) -> List[str]:
        """
        Filter symbols for ATM (At-The-Money) options.

        ATM is defined as the 4 strikes above and 4 strikes below the ATM strike.

        Args:
            all_symbols: List of all available symbols
            pattern: ATM pattern (e.g., BTC-*-ATM-C)

        Returns:
            List of ATM symbols (4 above + ATM + 4 below = up to 9 per expiry)
        """
        parts = pattern.split('-')
        if len(parts) != 4:
            return []

        underlying, expiry, _, option_type = parts

        # Get spot price for underlying
        spot_price = self.spot_prices.get(f"{underlying}USDT", 0)
        if spot_price == 0:
            self.logger.warning(f"No spot price available for {underlying}")
            return []

        # Group symbols by expiry
        expiry_groups: Dict[str, List[tuple]] = {}

        for symbol in all_symbols:
            sym_parts = symbol.split('-')
            if len(sym_parts) != 4:
                continue

            sym_underlying, sym_expiry, sym_strike, sym_type = sym_parts

            # Match underlying and option type
            if sym_underlying != underlying or sym_type != option_type:
                continue

            # Match expiry (if not wildcard)
            if expiry != '*' and sym_expiry != expiry:
                continue

            # Filter by days to expiry if configured
            if self.min_days_to_expiry is not None or self.max_days_to_expiry is not None:
                days_to_expiry = self._get_days_to_expiry(sym_expiry)
                if days_to_expiry is None:
                    continue

                if self.min_days_to_expiry is not None and days_to_expiry < self.min_days_to_expiry:
                    continue

                if self.max_days_to_expiry is not None and days_to_expiry > self.max_days_to_expiry:
                    continue

            # Parse strike price
            try:
                strike = float(sym_strike)
                if sym_expiry not in expiry_groups:
                    expiry_groups[sym_expiry] = []
                expiry_groups[sym_expiry].append((strike, symbol))
            except ValueError:
                continue

        # For each expiry, select 4 strikes above and 4 below ATM
        atm_symbols = []

        for expiry_date, strikes_data in expiry_groups.items():
            # Sort by strike price
            strikes_data.sort(key=lambda x: x[0])

            # Find ATM strike (closest to spot price)
            atm_idx = min(range(len(strikes_data)), key=lambda i: abs(strikes_data[i][0] - spot_price))

            # Select 2 strikes below, ATM, and 2 strikes above (5 total)
            start_idx = max(0, atm_idx - 2)
            end_idx = min(len(strikes_data), atm_idx + 3)  # +3 to include ATM + 2 above

            selected = strikes_data[start_idx:end_idx]
            selected_symbols = [sym for _, sym in selected]
            atm_symbols.extend(selected_symbols)

            days_to_expiry = self._get_days_to_expiry(expiry_date)
            self.logger.info(
                f"Expiry {expiry_date} ({days_to_expiry} days): ATM strike={strikes_data[atm_idx][0]}, "
                f"selected {len(selected_symbols)} strikes from {strikes_data[start_idx][0]} to {strikes_data[end_idx-1][0]}"
            )

        self.logger.debug(f"Found {len(atm_symbols)} ATM symbols for {pattern}")
        return atm_symbols

    async def _update_prices(self):
        """Update spot prices, perpetual mark prices, and funding rates for underlying assets."""
        try:
            # Extract unique underlyings from configured patterns AND monitored symbols
            underlyings = set()

            # Get underlyings from patterns (for initial run before symbols are discovered)
            for pattern in self.symbol_patterns:
                parts = pattern.split('-')
                if parts:
                    underlyings.add(parts[0])

            # Get underlyings from already-monitored symbols
            for symbol in self.monitored_symbols:
                parts = symbol.split('-')
                if parts:
                    underlyings.add(parts[0])

            # Fetch spot, perpetual mark, and funding rate for all underlyings in parallel
            async def fetch_prices_for_underlying(underlying: str):
                """Fetch spot and perpetual data for a single underlying."""
                spot_symbol = f"{underlying}USDT"
                perp_symbol = f"{underlying}USDT"

                try:
                    if self.exchange_name == 'deribit':
                        # Deribit: use get_index_price and get_perpetual_data
                        index_price, (perp_mark_price, funding_rate) = await asyncio.gather(
                            self.client.get_index_price(f"{underlying.lower()}_usd"),
                            self.client.get_perpetual_data(underlying)
                        )
                        spot_price = index_price
                    else:
                        # Binance: use get_spot_price and get_perpetual_data
                        spot_price, (perp_mark_price, funding_rate) = await asyncio.gather(
                            self.client.get_spot_price(spot_symbol),
                            self.client.get_perpetual_data(perp_symbol)
                        )

                    self.spot_prices[spot_symbol] = spot_price
                    self.perpetual_mark_prices[perp_symbol] = perp_mark_price
                    self.funding_rates[perp_symbol] = funding_rate

                    # Calculate metrics
                    basis = perp_mark_price - spot_price
                    basis_pct = (basis / spot_price) * 100
                    funding_rate_annualized = funding_rate * 3 * 365  # 8h funding × 3 per day × 365

                    self.logger.info(
                        f"{underlying}: Spot=${spot_price:,.2f}, "
                        f"Perp Mark=${perp_mark_price:,.2f}, "
                        f"Basis={basis_pct:+.2f}%, "
                        f"Funding={funding_rate*100:.4f}% (8h) = {funding_rate_annualized*100:.2f}% (annual)"
                    )

                except Exception as e:
                    self.logger.warning(f"Could not fetch prices for {underlying}: {e}")

            # Create tasks for all underlyings and fetch in parallel
            tasks = [fetch_prices_for_underlying(underlying) for underlying in underlyings]
            await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            self.logger.error(f"Error updating prices: {e}", exc_info=True)

    async def _check_all_symbols_iv(self):
        """
        Fetch mark price data and check for high IV.

        Behavior depends on statistics mode:
        - 'simple': Direct threshold check on markIV
        - 'statistical': Full Z-score analysis with database tracking
        """
        try:
            # Fetch mark prices based on exchange
            if self.exchange_name == 'deribit':
                # Deribit: get_book_summary_by_currency returns all options at once
                mark_data_list = await self.client.get_book_summary_by_currency(currency="BTC", kind="option")

                # Normalize field names to match Binance format for compatibility
                normalized_marks = []
                for mark in mark_data_list:
                    # Deribit greeks can be nested or at top level depending on endpoint
                    greeks = mark.get('greeks', {})

                    # Deribit returns mark_iv as percentage (43.12 = 43.12%), not decimal!
                    # Convert to decimal to match Binance format (0.4312)
                    mark_iv = mark.get('mark_iv', 0)
                    if mark_iv is not None:
                        mark_iv = mark_iv / 100.0  # Convert percentage to decimal
                    else:
                        mark_iv = 0

                    bid_iv = mark.get('bid_iv', 0)
                    if bid_iv is not None:
                        bid_iv = bid_iv / 100.0
                    else:
                        bid_iv = 0

                    ask_iv = mark.get('ask_iv', 0)
                    if ask_iv is not None:
                        ask_iv = ask_iv / 100.0
                    else:
                        ask_iv = 0

                    normalized = {
                        'symbol': mark.get('instrument_name', ''),
                        'markIV': mark_iv,  # Now in decimal format like Binance
                        'bidIV': bid_iv,
                        'askIV': ask_iv,
                        'sumOpenInterest': mark.get('open_interest', 0),
                        'markPrice': mark.get('mark_price', 0),
                        # Try nested greeks first, fallback to top-level
                        'delta': greeks.get('delta', mark.get('delta', 0)),
                        'gamma': greeks.get('gamma', mark.get('gamma', 0)),
                        'theta': greeks.get('theta', mark.get('theta', 0)),
                        'vega': greeks.get('vega', mark.get('vega', 0)),
                    }
                    normalized_marks.append(normalized)
                mark_data_list = normalized_marks
            else:
                # Binance: get_mark_price returns list with standard fields
                mark_data_list = await self.client.get_mark_price()

            # Filter to only our monitored symbols
            monitored_set = set(self.monitored_symbols)
            relevant_marks = [m for m in mark_data_list if m['symbol'] in monitored_set]

            self.logger.info(f"Checking IV for {len(relevant_marks)}/{len(self.monitored_symbols)} symbols")

            # Group by expiry
            expiry_groups = self._group_by_expiry(relevant_marks)

            # Branch based on mode
            if self.statistics_mode == 'simple':
                await self._check_simple_threshold(expiry_groups)
            else:
                await self._check_statistical_abnormality(expiry_groups)

        except Exception as e:
            self.logger.error(f"Error checking symbols IV: {e}", exc_info=True)

    async def _check_statistical_abnormality(self, expiry_groups: Dict[str, List[Dict[str, Any]]]):
        """
        Statistical Z-score based IV abnormality detection (original complex logic).

        This is the existing logic, just extracted into its own method for cleaner separation.
        """
        try:

            # Get spot price, perpetual price, and funding rate for BTC
            spot_price = self.spot_prices.get('BTCUSDT', 0)
            perp_mark_price = self.perpetual_mark_prices.get('BTCUSDT', 0)
            funding_rate = self.funding_rates.get('BTCUSDT', 0)

            if spot_price == 0 or perp_mark_price == 0:
                self.logger.warning("No price data available - skipping ATM analysis")
                return

            # Process each expiry
            for expiry_date, marks in expiry_groups.items():
                try:
                    # 1. Calculate synthetic ATM IV using linear interpolation
                    synthetic_atm_iv, atm_strike = self.analyzer.find_synthetic_atm_iv(
                        marks,
                        spot_price
                    )

                    if synthetic_atm_iv == 0:
                        continue  # No valid IV data

                    # 2. Calculate time to expiry in years
                    days_to_expiry = self.analyzer._get_days_to_expiry(marks[0]['symbol'])
                    if days_to_expiry is None or days_to_expiry <= 0:
                        self.logger.warning(f"Invalid expiry for {expiry_date}")
                        continue
                    time_to_expiry_years = days_to_expiry / 365.0

                    # 3. Get dual-system 25-delta skew comparison
                    skew_comparison = self.analyzer.find_25delta_ivs_dual_system(
                        marks,
                        perp_mark_price,
                        funding_rate,
                        time_to_expiry_years
                    )

                    # Extract values for database (keep using spot delta IVs for historical consistency)
                    call_25d_iv = skew_comparison['spot_call_25d_iv'] / 100  # Convert back to decimal
                    put_25d_iv = skew_comparison['spot_put_25d_iv'] / 100

                    # 4. Store synthetic ATM + skew to database with basis tracking
                    await self.atm_db.insert_atm_record(
                        expiry_date=expiry_date,
                        synthetic_atm_iv=synthetic_atm_iv,
                        spot_price=spot_price,
                        atm_strike_price=atm_strike,
                        call_25d_iv=call_25d_iv,
                        put_25d_iv=put_25d_iv,
                        perpetual_price=perp_mark_price,
                        funding_rate=funding_rate
                    )

                    # 5. Check if ATM is abnormal
                    await self._check_atm_abnormality(
                        expiry_date,
                        synthetic_atm_iv,
                        call_25d_iv,
                        put_25d_iv,
                        skew_comparison,
                        marks,
                        spot_price,
                        perp_mark_price,
                        funding_rate
                    )

                except Exception as e:
                    self.logger.error(
                        f"Error processing expiry {expiry_date}: {e}",
                        exc_info=True
                    )

        except Exception as e:
            self.logger.error(f"Error in statistical abnormality check: {e}", exc_info=True)

    async def _check_simple_threshold(self, expiry_groups: Dict[str, List[Dict[str, Any]]]):
        """
        Simple threshold-based IV checking (no database, no Z-scores).

        For each expiry:
        1. Check if ANY ATM strike has markIV > threshold AND openInterest > min AND delta in range
        2. If yes, send alert with list of triggered strikes
        3. Apply progressive threshold tracking (+1% increments)
        4. Auto-reset when IV drops 2% below initial alert

        Args:
            expiry_groups: Dict mapping expiry_date to list of mark data
        """
        for expiry_date, marks in expiry_groups.items():
            try:
                # Find all strikes that exceed threshold with sufficient liquidity AND sellable delta
                triggered_strikes = []

                for mark in marks:
                    mark_iv = float(mark.get('markIV', 0)) * 100  # Convert to percentage
                    open_interest = float(mark.get('sumOpenInterest', 0))
                    delta = abs(float(mark.get('delta', 0)))  # Absolute value for puts/calls

                    # Check IV threshold AND liquidity AND delta range (sellable options only)
                    if (mark_iv > self.iv_threshold and
                        open_interest > self.min_open_interest and
                        self.delta_min <= delta <= self.delta_max):

                        triggered_strikes.append({
                            'symbol': mark['symbol'],
                            'markIV': mark_iv,
                            'openInterest': open_interest,
                            'delta': mark.get('delta', 'N/A'),
                            'mark_price': mark.get('markPrice', 'N/A')
                        })

                # If no strikes exceeded threshold, skip
                if not triggered_strikes:
                    self.logger.debug(f"Expiry {expiry_date}: No strikes exceed {self.iv_threshold}%")
                    continue

                # Find highest IV among triggered strikes
                max_iv = max(s['markIV'] for s in triggered_strikes)

                # Progressive IV tracking with reset mechanism
                last_iv = self.last_alerted_iv.get(expiry_date, 0)
                initial_iv = self.initial_alert_iv.get(expiry_date, 0)

                # Reset detection: If IV dropped 2% below initial alert, reset tracking
                if initial_iv > 0 and max_iv < (initial_iv - 2.0):
                    self.logger.info(
                        f"Expiry {expiry_date}: IV dropped to {max_iv:.2f}% "
                        f"(reset - was tracking from {initial_iv:.2f}%)"
                    )
                    self.last_alerted_iv[expiry_date] = 0
                    self.initial_alert_iv[expiry_date] = 0
                    continue  # Don't alert on reset

                # Alert if:
                # 1. First time seeing this expiry above threshold (last_iv == 0)
                # 2. IV increased by at least iv_increase_threshold % points
                if last_iv == 0 or max_iv >= (last_iv + self.iv_increase_threshold):
                    # Log alert
                    if last_iv == 0:
                        self.logger.warning(
                            f"Expiry {expiry_date}: {len(triggered_strikes)} ATM strikes exceed "
                            f"{self.iv_threshold}% (max IV: {max_iv:.2f}%)"
                        )
                    else:
                        self.logger.warning(
                            f"Expiry {expiry_date}: IV increased from {last_iv:.2f}% to {max_iv:.2f}% "
                            f"(+{max_iv - last_iv:.2f}%)"
                        )

                    # Send simplified Discord alert
                    await self.notifier.send_simple_atm_alert(
                        expiry_date=expiry_date,
                        triggered_strikes=triggered_strikes,
                        threshold=self.iv_threshold,
                        previous_iv=last_iv if last_iv > 0 else None
                    )

                    # Update tracking
                    self.last_alerted_iv[expiry_date] = max_iv
                    if last_iv == 0:  # First alert - remember as initial
                        self.initial_alert_iv[expiry_date] = max_iv
                else:
                    self.logger.debug(
                        f"Expiry {expiry_date}: IV at {max_iv:.2f}% "
                        f"(no alert - need +{self.iv_increase_threshold}% from {last_iv:.2f}%)"
                    )

            except Exception as e:
                self.logger.error(f"Error checking expiry {expiry_date}: {e}", exc_info=True)

    def _group_by_expiry(self, marks: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Group mark data by expiry date.

        Args:
            marks: List of mark data dicts

        Returns:
            Dict mapping expiry_date (YYMMDD) to list of marks for that expiry
        """
        expiry_groups: Dict[str, List[Dict[str, Any]]] = {}

        for mark in marks:
            symbol = mark['symbol']
            parts = symbol.split('-')

            if len(parts) != 4:
                continue

            expiry_date = parts[1]  # YYMMDD

            if expiry_date not in expiry_groups:
                expiry_groups[expiry_date] = []

            expiry_groups[expiry_date].append(mark)

        return expiry_groups

    async def _check_atm_abnormality(
        self,
        expiry_date: str,
        synthetic_atm_iv: float,
        call_25d_iv: float,
        put_25d_iv: float,
        skew_comparison: Dict[str, Any],
        all_marks: List[Dict[str, Any]],
        spot_price: float,
        perp_mark_price: float,
        funding_rate: float
    ):
        """
        Check if synthetic ATM IV is abnormal using statistical analysis.

        Args:
            expiry_date: Expiry in YYMMDD format
            synthetic_atm_iv: Current synthetic ATM IV (decimal)
            call_25d_iv: 25-delta call IV (decimal)
            put_25d_iv: 25-delta put IV (decimal)
            skew_comparison: Dual skew comparison dict from find_25delta_ivs_dual_system()
            all_marks: All mark data for this expiry
            spot_price: Current BTC spot price
            perp_mark_price: Perpetual futures mark price
            funding_rate: Current 8-hour funding rate
        """
        # Get historical data
        atm_history = await self.atm_db.get_atm_history(expiry_date, hours=24)

        if len(atm_history) < self.analyzer.min_samples:
            self.logger.debug(
                f"Expiry {expiry_date}: Insufficient samples "
                f"({len(atm_history)} < {self.analyzer.min_samples})"
            )
            return  # Not enough data yet

        # Calculate enhanced statistics
        stats = self.analyzer.calculate_statistics(
            atm_history,
            synthetic_atm_iv,
            call_25d_iv,
            put_25d_iv,
            expiry_date
        )

        if not stats:
            return  # Failed stats calculation (time-span check failed, etc.)

        if not stats.is_abnormal:
            self.logger.debug(
                f"Expiry {expiry_date}: IV normal - "
                f"Z-score={stats.z_score:.2f} (threshold={self.analyzer.z_score_threshold})"
            )
            return  # Normal, skip

        # Get smart sellable strikes (sorted by opportunity)
        sellable_strikes = self.analyzer.get_smart_sellable_strikes(
            all_marks,
            spot_price,
            self.delta_min,
            self.delta_max
        )

        if not sellable_strikes:
            self.logger.info(
                f"Expiry {expiry_date}: Abnormal IV but no sellable strikes "
                f"in delta range {self.delta_min}-{self.delta_max}"
            )
            return

        # Send consolidated alert
        self.logger.warning(
            f"Expiry {expiry_date}: ABNORMAL IV DETECTED - "
            f"Z-score={stats.z_score:.2f}σ, IV={stats.current_iv:.2f}%, "
            f"IV Rank={stats.iv_percentile:.0f}%"
        )

        await self.notifier.send_expiry_abnormal_alert(
            expiry_date,
            stats,
            skew_comparison,
            sellable_strikes,
            spot_price,
            perp_mark_price,
            funding_rate
        )
