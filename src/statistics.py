"""Statistical analysis for ATM IV abnormality detection."""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional, Tuple
import numpy as np


@dataclass
class IVStatistics:
    """
    Enhanced IV statistics with Z-score, percentile, and skew analysis.

    Provides complete context for abnormal IV alerts including:
    - Z-score analysis (how many standard deviations from mean)
    - IV percentile (where in 24h range)
    - Skew direction (calls vs puts sentiment)
    """
    expiry_date: str

    # Z-score analysis
    current_iv: float  # Percentage (50.93, not 0.5093)
    mean_iv: float
    std_dev: float
    z_score: float

    # Percentile analysis
    iv_percentile: float  # 0-100, where in 24h range
    daily_low_iv: float
    daily_high_iv: float

    # Skew analysis
    call_25d_iv: float
    put_25d_iv: float
    skew_direction: str

    sample_count: int
    is_abnormal: bool

    def get_iv_rank_label(self) -> str:
        """
        Return human-readable IV rank label.

        Returns:
            String like "92% (Near Daily Highs)" or "45% (Mid-Range)"
        """
        if self.iv_percentile > 80:
            return f"{self.iv_percentile:.0f}% (Near Daily Highs)"
        elif self.iv_percentile < 20:
            return f"{self.iv_percentile:.0f}% (Near Daily Lows)"
        else:
            return f"{self.iv_percentile:.0f}% (Mid-Range)"

    def get_skew_analysis(self) -> str:
        """
        Analyze volatility skew for market sentiment.

        Returns:
            String describing skew direction and interpretation
        """
        if self.call_25d_iv > self.put_25d_iv * 1.05:
            return "Calls > Puts (Bullish Vol / Rally Fear)"
        elif self.put_25d_iv > self.call_25d_iv * 1.05:
            return "Puts > Calls (Bearish Vol / Panic/Hedging)"
        else:
            return "Balanced (No Directional Skew)"


class StatisticalAnalyzer:
    """
    Analyzes ATM IV time series for abnormal spikes.

    Uses Z-score analysis with enhanced context:
    - Time-span check prevents meaningless Z-scores on startup
    - IV percentile shows where in 24h range
    - Skew analysis shows market sentiment
    """

    def __init__(
        self,
        z_score_threshold: float = 2.0,
        min_samples: int = 10,
        min_history_hours: float = 4.0,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize statistical analyzer.

        Args:
            z_score_threshold: Z-score above which IV is considered abnormal
            min_samples: Minimum number of samples required
            min_history_hours: Minimum hours of history required (prevents
                               meaningless Z-scores during startup)
            logger: Logger instance
        """
        self.z_score_threshold = z_score_threshold
        self.min_samples = min_samples
        self.min_history_hours = min_history_hours
        self.logger = logger or logging.getLogger(__name__)

    def calculate_statistics(
        self,
        iv_history: List[Dict],
        current_iv: float,
        current_call_25d_iv: float,
        current_put_25d_iv: float,
        expiry_date: str
    ) -> Optional[IVStatistics]:
        """
        Calculate enhanced statistics with percentile and skew.

        Args:
            iv_history: List of {'synthetic_atm_iv': 0.51, 'timestamp': ...}
            current_iv: Current synthetic ATM IV (decimal, e.g., 0.5093)
            current_call_25d_iv: Current 25-delta call IV (decimal)
            current_put_25d_iv: Current 25-delta put IV (decimal)
            expiry_date: Expiry in YYMMDD format

        Returns:
            IVStatistics or None if insufficient data
        """
        # Check sample count
        if len(iv_history) < self.min_samples:
            self.logger.debug(
                f"Insufficient samples for {expiry_date}: "
                f"{len(iv_history)} < {self.min_samples}"
            )
            return None

        # Time-span check (prevents meaningless Z-scores on startup)
        if iv_history:
            timestamps = [r['timestamp'] for r in iv_history]
            oldest_timestamp = min(timestamps)
            newest_timestamp = max(timestamps)
            time_span_hours = (newest_timestamp - oldest_timestamp).total_seconds() / 3600

            if time_span_hours < self.min_history_hours:
                self.logger.debug(
                    f"Insufficient time span for {expiry_date}: "
                    f"{time_span_hours:.1f}h < {self.min_history_hours}h"
                )
                return None

        # Extract IV values
        iv_values = np.array([r['synthetic_atm_iv'] for r in iv_history])

        # Z-score calculation
        mean_iv = np.mean(iv_values)
        std_dev = np.std(iv_values, ddof=1)

        if std_dev > 1e-6:
            z_score = (current_iv - mean_iv) / std_dev
        else:
            z_score = 0.0

        # IV percentile (where in 24h range)
        daily_low = float(np.min(iv_values))
        daily_high = float(np.max(iv_values))
        iv_range = daily_high - daily_low

        if iv_range > 1e-6:
            iv_percentile = ((current_iv - daily_low) / iv_range) * 100
            iv_percentile = max(0.0, min(100.0, iv_percentile))  # Clamp
        else:
            iv_percentile = 50.0

        # Skew direction
        if current_call_25d_iv > current_put_25d_iv * 1.05:
            skew_direction = "Calls > Puts (Bullish Vol)"
        elif current_put_25d_iv > current_call_25d_iv * 1.05:
            skew_direction = "Puts > Calls (Bearish Vol)"
        else:
            skew_direction = "Balanced"

        return IVStatistics(
            expiry_date=expiry_date,
            current_iv=current_iv * 100,  # Convert to percentage
            mean_iv=mean_iv * 100,
            std_dev=std_dev * 100,
            z_score=z_score,
            iv_percentile=iv_percentile,
            daily_low_iv=daily_low * 100,
            daily_high_iv=daily_high * 100,
            call_25d_iv=current_call_25d_iv * 100,
            put_25d_iv=current_put_25d_iv * 100,
            skew_direction=skew_direction,
            sample_count=len(iv_values),
            is_abnormal=z_score > self.z_score_threshold
        )

    def find_synthetic_atm_iv(
        self,
        marks_for_expiry: List[Dict],
        spot_price: float
    ) -> Tuple[float, float]:
        """
        Calculate synthetic ATM IV using LINEAR INTERPOLATION.

        Instead of picking closest strike, interpolate between the two
        strikes surrounding spot price for a perfectly smooth IV curve.

        Args:
            marks_for_expiry: List of mark data dicts with 'symbol', 'markIV'
            spot_price: Current BTC spot price

        Returns:
            Tuple of (synthetic_atm_iv, interpolated_strike_reference)
            Both as decimals (e.g., 0.5093, 97400.0)
        """
        # Extract strikes with valid IVs
        strikes_data = []
        for mark in marks_for_expiry:
            strike = self._get_strike_from_symbol(mark['symbol'])
            iv = float(mark.get('markIV', 0))
            if iv > 0 and strike is not None:
                strikes_data.append((strike, iv))

        if len(strikes_data) < 2:
            # Fallback: use closest strike if insufficient data
            if len(strikes_data) == 1:
                return (strikes_data[0][1], strikes_data[0][0])

            # No valid strikes - find any with markIV
            atm_mark = min(
                marks_for_expiry,
                key=lambda m: abs(self._get_strike_from_symbol(m['symbol']) - spot_price),
                default=None
            )
            if atm_mark:
                return (float(atm_mark.get('markIV', 0)), spot_price)

            # Complete failure
            self.logger.warning(f"No valid IVs found for interpolation at spot={spot_price}")
            return (0.0, spot_price)

        # Sort by strike price
        strikes_data.sort(key=lambda x: x[0])

        # Find two strikes surrounding spot price
        lower_strike, lower_iv = None, None
        upper_strike, upper_iv = None, None

        for strike, iv in strikes_data:
            if strike <= spot_price:
                lower_strike, lower_iv = strike, iv
            elif strike > spot_price and upper_strike is None:
                upper_strike, upper_iv = strike, iv
                break

        # Edge case: spot below all strikes
        if lower_strike is None:
            return (strikes_data[0][1], strikes_data[0][0])

        # Edge case: spot above all strikes
        if upper_strike is None:
            return (strikes_data[-1][1], strikes_data[-1][0])

        # LINEAR INTERPOLATION
        # Weight by distance from spot
        total_range = upper_strike - lower_strike
        weight_lower = (upper_strike - spot_price) / total_range
        weight_upper = (spot_price - lower_strike) / total_range

        synthetic_iv = (weight_lower * lower_iv) + (weight_upper * upper_iv)

        self.logger.debug(
            f"Interpolated ATM IV at spot={spot_price:.0f}: "
            f"{lower_strike:.0f} ({lower_iv*100:.2f}%) + "
            f"{upper_strike:.0f} ({upper_iv*100:.2f}%) = "
            f"{synthetic_iv*100:.2f}%"
        )

        # Return interpolated IV and spot price as reference
        return (synthetic_iv, spot_price)

    def find_25delta_ivs(
        self,
        marks_for_expiry: List[Dict]
    ) -> Tuple[float, float]:
        """
        Find 25-delta call and put IVs for skew analysis.

        Args:
            marks_for_expiry: List of mark data dicts with 'symbol', 'delta', 'markIV'

        Returns:
            Tuple of (call_25d_iv, put_25d_iv) as decimals (e.g., 0.5093)
        """
        # Separate calls and puts
        calls = [m for m in marks_for_expiry if m['symbol'].endswith('-C')]
        puts = [m for m in marks_for_expiry if m['symbol'].endswith('-P')]

        # Find closest to 0.25 delta (absolute value)
        call_25d = None
        if calls:
            call_25d = min(
                calls,
                key=lambda m: abs(abs(float(m.get('delta', 0))) - 0.25),
                default=None
            )

        put_25d = None
        if puts:
            put_25d = min(
                puts,
                key=lambda m: abs(abs(float(m.get('delta', 0))) - 0.25),
                default=None
            )

        call_iv = float(call_25d.get('markIV', 0)) if call_25d else 0.0
        put_iv = float(put_25d.get('markIV', 0)) if put_25d else 0.0

        self.logger.debug(
            f"25-delta IVs: Call={call_iv*100:.2f}%, Put={put_iv*100:.2f}%"
        )

        return (call_iv, put_iv)

    def calculate_forward_price(
        self,
        perp_mark_price: float,
        funding_rate: float,
        time_to_expiry_years: float
    ) -> float:
        """
        Calculate forward price using perpetual mark price and funding rate.

        Formula: F = S × e^((r - f) × T)
        Where:
        - S = Perpetual mark price (the "true" underlying in crypto)
        - r = Risk-free rate (use 0 for crypto)
        - f = Annualized funding rate (acts as dividend yield)
        - T = Time to expiry in years

        Args:
            perp_mark_price: Perpetual futures mark price
            funding_rate: Current 8-hour funding rate (e.g., 0.0001)
            time_to_expiry_years: Time to expiry in years

        Returns:
            Forward price
        """
        import math

        # Annualize funding rate (8h funding × 3 per day × 365)
        funding_rate_annual = funding_rate * 3 * 365

        # F = S × e^((0 - f) × T)
        forward_price = perp_mark_price * math.exp((0 - funding_rate_annual) * time_to_expiry_years)

        self.logger.debug(
            f"Forward Price: Perp={perp_mark_price:.0f}, "
            f"Funding={funding_rate*100:.4f}% (8h) = {funding_rate_annual*100:.2f}% (annual), "
            f"T={time_to_expiry_years:.4f}y → F={forward_price:.0f}"
        )

        return forward_price

    def calculate_forward_delta(
        self,
        spot_delta: float,
        perp_mark_price: float,
        forward_price: float
    ) -> float:
        """
        Convert Spot Delta to Forward Delta.

        Formula: Forward_Δ = Spot_Δ × (S / F)

        Args:
            spot_delta: Delta from Binance (spot-referenced)
            perp_mark_price: Perpetual mark price
            forward_price: Calculated forward price

        Returns:
            Forward Delta (basis-adjusted)
        """
        forward_delta = spot_delta * (perp_mark_price / forward_price)
        return forward_delta

    def find_25delta_ivs_dual_system(
        self,
        marks_for_expiry: List[Dict],
        perp_mark_price: float,
        funding_rate: float,
        time_to_expiry_years: float
    ) -> Dict:
        """
        Calculate BOTH Spot Delta and Forward Delta 25-delta skews.

        Returns comprehensive comparison showing:
        - Spot Delta skew (Binance's reality)
        - Forward Delta skew (research-based adjustment)
        - Divergence (Ghost Skew indicator)

        Args:
            marks_for_expiry: List of mark data from Binance
            perp_mark_price: Perpetual futures mark price
            funding_rate: Current funding rate
            time_to_expiry_years: Time to expiry in years

        Returns:
            Dict with keys:
            - spot_call_25d_iv: Spot Delta 25-delta call IV (percentage)
            - spot_put_25d_iv: Spot Delta 25-delta put IV (percentage)
            - spot_skew: Spot Delta skew (call - put) in percentage points
            - forward_call_25d_iv: Forward Delta 25-delta call IV (percentage)
            - forward_put_25d_iv: Forward Delta 25-delta put IV (percentage)
            - forward_skew: Forward Delta skew (call - put) in percentage points
            - ghost_skew: Divergence (spot_skew - forward_skew) in percentage points
            - forward_price: Calculated forward price
            - spot_call_strike: Strike selected by Spot Delta
            - spot_put_strike: Strike selected by Spot Delta
            - forward_call_strike: Strike selected by Forward Delta
            - forward_put_strike: Strike selected by Forward Delta
        """
        calls = [m for m in marks_for_expiry if m['symbol'].endswith('-C')]
        puts = [m for m in marks_for_expiry if m['symbol'].endswith('-P')]

        # Calculate forward price
        forward_price = self.calculate_forward_price(
            perp_mark_price,
            funding_rate,
            time_to_expiry_years
        )

        # ===== SPOT DELTA SYSTEM (Current/Binance) =====
        spot_call_25d = min(
            calls,
            key=lambda m: abs(abs(float(m.get('delta', 0))) - 0.25),
            default=None
        ) if calls else None

        spot_put_25d = min(
            puts,
            key=lambda m: abs(abs(float(m.get('delta', 0))) - 0.25),
            default=None
        ) if puts else None

        spot_call_iv = float(spot_call_25d.get('markIV', 0)) if spot_call_25d else 0.0
        spot_put_iv = float(spot_put_25d.get('markIV', 0)) if spot_put_25d else 0.0
        spot_call_strike = self._get_strike_from_symbol(spot_call_25d['symbol']) if spot_call_25d else 0
        spot_put_strike = self._get_strike_from_symbol(spot_put_25d['symbol']) if spot_put_25d else 0

        # ===== FORWARD DELTA SYSTEM (Research-based) =====
        # Convert all deltas to Forward Delta
        calls_forward = []
        for m in calls:
            spot_delta = abs(float(m.get('delta', 0)))
            forward_delta = self.calculate_forward_delta(spot_delta, perp_mark_price, forward_price)
            calls_forward.append((m, forward_delta))

        puts_forward = []
        for m in puts:
            spot_delta = abs(float(m.get('delta', 0)))
            forward_delta = self.calculate_forward_delta(spot_delta, perp_mark_price, forward_price)
            puts_forward.append((m, forward_delta))

        # Select closest to 0.25 Forward Delta
        forward_call_25d = min(
            calls_forward,
            key=lambda x: abs(x[1] - 0.25),
            default=(None, 0)
        )[0] if calls_forward else None

        forward_put_25d = min(
            puts_forward,
            key=lambda x: abs(x[1] - 0.25),
            default=(None, 0)
        )[0] if puts_forward else None

        forward_call_iv = float(forward_call_25d.get('markIV', 0)) if forward_call_25d else 0.0
        forward_put_iv = float(forward_put_25d.get('markIV', 0)) if forward_put_25d else 0.0
        forward_call_strike = self._get_strike_from_symbol(forward_call_25d['symbol']) if forward_call_25d else 0
        forward_put_strike = self._get_strike_from_symbol(forward_put_25d['symbol']) if forward_put_25d else 0

        # Calculate skews (in percentage points)
        spot_skew = (spot_call_iv - spot_put_iv) * 100
        forward_skew = (forward_call_iv - forward_put_iv) * 100
        ghost_skew = spot_skew - forward_skew

        self.logger.debug(
            f"Dual Skew: Spot={spot_skew:+.2f}pp, Forward={forward_skew:+.2f}pp, "
            f"Ghost={ghost_skew:+.2f}pp"
        )

        return {
            'spot_call_25d_iv': spot_call_iv * 100,
            'spot_put_25d_iv': spot_put_iv * 100,
            'spot_skew': spot_skew,
            'forward_call_25d_iv': forward_call_iv * 100,
            'forward_put_25d_iv': forward_put_iv * 100,
            'forward_skew': forward_skew,
            'ghost_skew': ghost_skew,
            'forward_price': forward_price,
            'spot_call_strike': spot_call_strike,
            'spot_put_strike': spot_put_strike,
            'forward_call_strike': forward_call_strike,
            'forward_put_strike': forward_put_strike
        }

    def _get_strike_from_symbol(self, symbol: str) -> Optional[float]:
        """
        Extract strike price from Binance option symbol.

        Args:
            symbol: Format "BTC-YYMMDD-STRIKE-TYPE" (e.g., "BTC-251226-88000-C")

        Returns:
            Strike price as float or None if parsing fails
        """
        try:
            parts = symbol.split('-')
            if len(parts) == 4:
                return float(parts[2])
        except (ValueError, IndexError):
            self.logger.warning(f"Failed to parse strike from symbol: {symbol}")

        return None

    def get_smart_sellable_strikes(
        self,
        all_marks: List[Dict],
        spot_price: float,
        delta_min: float = 0.05,
        delta_max: float = 0.65
    ) -> List[Dict]:
        """
        Filter strikes by Delta and calculate Daily Rent % for opportunities.

        Args:
            all_marks: List of all mark data for expiry
            spot_price: Current BTC spot price
            delta_min: Minimum delta filter
            delta_max: Maximum delta filter

        Returns:
            List of dicts sorted by opportunity score (best first)
            Each dict contains: symbol, iv, delta, theta, vega, mark_price,
            days_to_expiry, daily_rent_pct, opportunity_score
        """
        sellable = []

        for mark in all_marks:
            delta = abs(float(mark.get('delta', 0)))

            # Delta filter
            if delta < delta_min or delta > delta_max:
                continue

            # Extract metrics
            symbol = mark['symbol']
            iv = float(mark.get('markIV', 0)) * 100
            theta = abs(float(mark.get('theta', 0)))
            vega = float(mark.get('vega', 0))
            mark_price = float(mark.get('markPrice', 0))

            # Parse days to expiry from symbol (BTC-YYMMDD-STRIKE-TYPE)
            days_to_expiry = self._get_days_to_expiry(symbol)

            if days_to_expiry is None or days_to_expiry <= 0:
                continue

            # Daily Rent % - intuitive metric showing daily yield
            # "Earn X% of BTC price per day in time decay"
            if spot_price > 0:
                daily_rent_pct = (mark_price / spot_price) / days_to_expiry * 100
            else:
                daily_rent_pct = 0.0

            # Opportunity score (Theta/Vega weighted by IV)
            if vega > 0:
                opportunity_score = (theta / vega) * iv
            else:
                opportunity_score = iv

            sellable.append({
                'symbol': symbol,
                'iv': iv,
                'delta': delta,
                'theta': theta,
                'vega': vega,
                'mark_price': mark_price,
                'days_to_expiry': days_to_expiry,
                'daily_rent_pct': daily_rent_pct,
                'opportunity_score': opportunity_score
            })

        # Sort by opportunity score (best first)
        sellable.sort(key=lambda x: x['opportunity_score'], reverse=True)

        return sellable

    def _get_days_to_expiry(self, symbol: str) -> Optional[int]:
        """
        Extract days to expiry from symbol.

        Args:
            symbol: Format "BTC-YYMMDD-STRIKE-TYPE"

        Returns:
            Days to expiry or None if parsing fails
        """
        try:
            parts = symbol.split('-')
            if len(parts) == 4:
                expiry_str = parts[1]  # YYMMDD
                year = 2000 + int(expiry_str[:2])
                month = int(expiry_str[2:4])
                day = int(expiry_str[4:6])
                expiry_date = datetime(year, month, day)
                days = (expiry_date - datetime.utcnow()).days
                return max(1, days)  # At least 1 day
        except (ValueError, IndexError):
            self.logger.warning(f"Failed to parse expiry from symbol: {symbol}")

        return None
