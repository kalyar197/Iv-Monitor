"""Discord notification module for IV alerts."""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp
import discord

from .statistics import IVStatistics


class DiscordNotifier:
    """
    Send IV alerts and system notifications to Discord via webhook.
    """

    def __init__(
        self,
        webhook_url: str,
        mention_role_id: Optional[str] = None,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize the Discord notifier.

        Args:
            webhook_url: Discord webhook URL
            mention_role_id: Optional role ID to mention, or "@everyone"
            logger: Logger instance
        """
        self.webhook_url = webhook_url
        self.mention_role_id = mention_role_id
        self.logger = logger or logging.getLogger(__name__)

        # Create async webhook
        self._session: Optional[aiohttp.ClientSession] = None
        self._webhook: Optional[discord.Webhook] = None

    async def __aenter__(self):
        """Async context manager entry."""
        await self._ensure_webhook()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def _ensure_webhook(self):
        """Ensure webhook is initialized."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._webhook = discord.Webhook.from_url(
                self.webhook_url,
                session=self._session
            )

    async def close(self):
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def send_iv_alert(
        self,
        symbol: str,
        iv: float,
        threshold: float,
        mark_data: Dict[str, Any]
    ):
        """
        Send high IV alert to Discord.

        Args:
            symbol: Option symbol
            iv: Current IV (percentage)
            threshold: Configured IV threshold
            mark_data: Mark price data including Greeks and IV
        """
        try:
            await self._ensure_webhook()

            # Create rich embed
            embed = discord.Embed(
                title="🚨 High IV Alert!",
                description=f"Implied Volatility threshold exceeded for **{symbol}**",
                color=0xFF5733,  # Orange-red
                timestamp=datetime.utcnow()
            )

            # IV Information
            embed.add_field(
                name="Mark IV",
                value=f"**{iv:.2f}%**",
                inline=True
            )
            embed.add_field(
                name="Threshold",
                value=f"{threshold:.2f}%",
                inline=True
            )
            embed.add_field(
                name="Exceeded By",
                value=f"+{(iv - threshold):.2f}%",
                inline=True
            )

            # Bid/Ask IV spread
            bid_iv = float(mark_data.get('bidIV', 0)) * 100
            ask_iv = float(mark_data.get('askIV', 0)) * 100
            embed.add_field(
                name="Bid IV / Ask IV",
                value=f"{bid_iv:.2f}% / {ask_iv:.2f}%",
                inline=True
            )

            # Mark Price
            mark_price = mark_data.get('markPrice', 'N/A')
            embed.add_field(
                name="Mark Price",
                value=f"${mark_price}",
                inline=True
            )

            # Greeks
            delta = mark_data.get('delta', 'N/A')
            gamma = mark_data.get('gamma', 'N/A')
            theta = mark_data.get('theta', 'N/A')
            vega = mark_data.get('vega', 'N/A')

            embed.add_field(
                name="Delta",
                value=f"{delta}",
                inline=True
            )
            embed.add_field(
                name="Gamma",
                value=f"{gamma}",
                inline=True
            )
            embed.add_field(
                name="Theta",
                value=f"{theta}",
                inline=True
            )
            embed.add_field(
                name="Vega",
                value=f"{vega}",
                inline=True
            )

            # Price limits
            high_limit = mark_data.get('highPriceLimit', 'N/A')
            low_limit = mark_data.get('lowPriceLimit', 'N/A')
            embed.add_field(
                name="Price Limits",
                value=f"${low_limit} - ${high_limit}",
                inline=True
            )

            # Footer
            embed.set_footer(text="Binance Options IV Monitor | Real Binance Data")

            # Prepare content (with optional role mention)
            content = None
            if self.mention_role_id:
                if self.mention_role_id.lower() == "@everyone":
                    content = "@everyone"
                else:
                    content = f"<@&{self.mention_role_id}>"

            # Send message
            await self._webhook.send(
                content=content,
                embed=embed,
                username="IV Monitor"
            )

            self.logger.info(f"Sent IV alert for {symbol} to Discord (IV: {iv:.2f}%)")

        except Exception as e:
            self.logger.error(f"Failed to send IV alert to Discord: {e}", exc_info=True)

    async def send_expiry_abnormal_alert(
        self,
        expiry_date: str,
        stats: IVStatistics,
        skew_comparison: Dict[str, Any],
        sellable_strikes: List[Dict],
        spot_price: float,
        perp_mark_price: float,
        funding_rate: float
    ):
        """
        Send comprehensive alert with dual-system skew comparison.

        Shows:
        - ATM IV statistics (Z-score, percentile)
        - Spot Delta skew (Binance's reality)
        - Forward Delta skew (research-based)
        - Ghost Skew analysis
        - Market structure (basis, funding)
        - Sellable strikes ranked by opportunity

        Args:
            expiry_date: Expiry in YYMMDD format
            stats: ATM IV statistics
            skew_comparison: Dual skew data from find_25delta_ivs_dual_system()
            sellable_strikes: Ranked opportunities
            spot_price: Spot price
            perp_mark_price: Perpetual mark price
            funding_rate: Current funding rate
        """
        try:
            await self._ensure_webhook()

            # Calculate metrics
            basis = perp_mark_price - spot_price
            basis_pct = (basis / spot_price) * 100
            funding_annualized = funding_rate * 3 * 365 * 100  # Convert to %

            expiry_display = self._format_expiry(expiry_date)
            days_to_expiry = self._get_days_to_expiry(expiry_date)

            # Create embed
            embed = discord.Embed(
                title=f"🚨 Abnormal IV Alert: {expiry_display}",
                description=(
                    f"ATM volatility spike detected - premium selling opportunity\n"
                    f"**Expiry:** {days_to_expiry} days  |  **Spot:** ${spot_price:,.0f}  |  **Perp:** ${perp_mark_price:,.0f}"
                ),
                color=0xFF5733,
                timestamp=datetime.utcnow()
            )

            # ===== ATM IV ANALYSIS (Existing) =====
            embed.add_field(
                name="📊 ATM IV Statistics",
                value=(
                    f"**Current ATM IV:** {stats.current_iv:.2f}%\n"
                    f"**24h Average:** {stats.mean_iv:.2f}%\n"
                    f"**Z-Score:** {stats.z_score:.2f}σ {'(ABNORMAL)' if stats.is_abnormal else ''}\n"
                    f"**IV Rank:** {stats.get_iv_rank_label()}\n"
                    f"**24h Range:** {stats.daily_low_iv:.2f}% - {stats.daily_high_iv:.2f}%\n"
                    f"**Samples:** {stats.sample_count} data points"
                ),
                inline=False
            )

            # ===== SPOT DELTA SKEW (Binance's Reality) =====
            spot_skew_emoji = "📉" if skew_comparison['spot_skew'] < 0 else "📈" if skew_comparison['spot_skew'] > 0 else "➖"
            spot_sentiment = (
                "Bearish (Puts > Calls)" if skew_comparison['spot_skew'] < -1
                else "Bullish (Calls > Puts)" if skew_comparison['spot_skew'] > 1
                else "Neutral"
            )

            embed.add_field(
                name=f"{spot_skew_emoji} Binance Spot Delta Skew (Reality)",
                value=(
                    f"**25Δ Call IV:** {skew_comparison['spot_call_25d_iv']:.2f}% @ ${skew_comparison['spot_call_strike']:,.0f}\n"
                    f"**25Δ Put IV:** {skew_comparison['spot_put_25d_iv']:.2f}% @ ${skew_comparison['spot_put_strike']:,.0f}\n"
                    f"**Skew:** {skew_comparison['spot_skew']:+.2f} pp\n"
                    f"**Sentiment:** {spot_sentiment}\n"
                    f"_This is what Binance shows - your trading reality_"
                ),
                inline=True
            )

            # ===== FORWARD DELTA SKEW (Research-based) =====
            forward_skew_emoji = "📉" if skew_comparison['forward_skew'] < 0 else "📈" if skew_comparison['forward_skew'] > 0 else "➖"
            forward_sentiment = (
                "Bearish (Puts > Calls)" if skew_comparison['forward_skew'] < -1
                else "Bullish (Calls > Puts)" if skew_comparison['forward_skew'] > 1
                else "Neutral"
            )

            embed.add_field(
                name=f"{forward_skew_emoji} Forward Delta Skew (Adjusted)",
                value=(
                    f"**25Δ Call IV:** {skew_comparison['forward_call_25d_iv']:.2f}% @ ${skew_comparison['forward_call_strike']:,.0f}\n"
                    f"**25Δ Put IV:** {skew_comparison['forward_put_25d_iv']:.2f}% @ ${skew_comparison['forward_put_strike']:,.0f}\n"
                    f"**Skew:** {skew_comparison['forward_skew']:+.2f} pp\n"
                    f"**Sentiment:** {forward_sentiment}\n"
                    f"_Research-based, accounts for funding/basis_"
                ),
                inline=True
            )

            # ===== GHOST SKEW ANALYSIS =====
            ghost_skew = skew_comparison['ghost_skew']
            ghost_magnitude = abs(ghost_skew)

            if ghost_magnitude < 1:
                ghost_assessment = "✅ Minimal Ghost Skew - sentiment is genuine"
                ghost_color = "🟢"
            elif ghost_magnitude < 2:
                ghost_assessment = "⚠️ Moderate Ghost Skew - some basis distortion"
                ghost_color = "🟡"
            else:
                ghost_assessment = "🚨 Significant Ghost Skew - heavy basis/funding distortion"
                ghost_color = "🔴"

            ghost_interpretation = (
                "Most of the skew is real volatility sentiment" if ghost_magnitude < 1
                else "Skew partly caused by market structure (basis/funding)" if ghost_magnitude < 2
                else "Skew heavily distorted by basis/funding - use caution"
            )

            embed.add_field(
                name=f"{ghost_color} Ghost Skew Analysis",
                value=(
                    f"**Divergence:** {ghost_skew:+.2f} pp (Spot - Forward)\n"
                    f"**Assessment:** {ghost_assessment}\n"
                    f"**Interpretation:** {ghost_interpretation}\n"
                    f"_Ghost Skew = apparent sentiment from basis, not true fear/greed_"
                ),
                inline=False
            )

            # ===== MARKET STRUCTURE =====
            basis_emoji = "🟢" if basis > 0 else "🔴" if basis < 0 else "⚪"
            funding_emoji = "🟢" if funding_rate > 0 else "🔴" if funding_rate < 0 else "⚪"

            basis_signal = "Contango (Bullish)" if basis > 0 else "Backwardation (Bearish)" if basis < 0 else "Flat"
            funding_signal = "Longs Pay Shorts (Bullish)" if funding_rate > 0 else "Shorts Pay Longs (Bearish)" if funding_rate < 0 else "Neutral"

            embed.add_field(
                name="🏗️ Market Structure",
                value=(
                    f"{basis_emoji} **Basis:** ${basis:+,.2f} ({basis_pct:+.2f}%)\n"
                    f"└─ {basis_signal}\n"
                    f"{funding_emoji} **Funding Rate:** {funding_rate*100:.4f}% (8h) = {funding_annualized:.2f}% (annual)\n"
                    f"└─ {funding_signal}\n"
                    f"**Forward Price:** ${skew_comparison['forward_price']:,.0f}\n"
                    f"_Basis & funding affect forward price used in research model_"
                ),
                inline=False
            )

            # ===== SELLABLE STRIKES (Top 10) =====
            if sellable_strikes:
                top_strikes = sellable_strikes[:10]
                strikes_text = "**Top Opportunities (Θ/IV rank):**\n\n"

                for i, strike in enumerate(top_strikes):
                    marker = "⭐" if i < 3 else "•"
                    strikes_text += (
                        f"{marker} `{strike['symbol']}`\n"
                        f"   IV: {strike['iv']:.1f}% | Δ: {strike['delta']:.2f} | "
                        f"θ: {strike['theta']:.2f} | Daily Rent: {strike['daily_rent_pct']:.3f}%\n"
                    )

                if len(strikes_text) > 1000:
                    strikes_text = strikes_text[:997] + "..."

                embed.add_field(
                    name="💰 Best Strikes to Sell (Δ 0.05-0.65)",
                    value=strikes_text,
                    inline=False
                )

            # ===== FOOTER =====
            embed.set_footer(
                text=(
                    f"Z-Score > 2.0 = Abnormal | Ghost Skew = Basis/Funding Distortion | "
                    f"Spot Delta = Binance Reality | Forward Delta = Research Model"
                )
            )

            # Send
            content = None
            if self.mention_role_id:
                content = f"<@&{self.mention_role_id}>" if self.mention_role_id != "@everyone" else "@everyone"

            await self._webhook.send(
                content=content,
                embed=embed,
                username="IV Monitor"
            )

            self.logger.info(
                f"Sent dual-system alert: {expiry_date}, Z={stats.z_score:.2f}σ, "
                f"Spot Skew={skew_comparison['spot_skew']:+.2f}pp, "
                f"Forward Skew={skew_comparison['forward_skew']:+.2f}pp, "
                f"Ghost={ghost_skew:+.2f}pp"
            )

        except Exception as e:
            self.logger.error(f"Failed to send alert: {e}", exc_info=True)

    async def send_simple_atm_alert(
        self,
        expiry_date: str,
        triggered_strikes: List[Dict[str, Any]],
        threshold: float,
        previous_iv: Optional[float] = None
    ):
        """
        Send simple threshold-based alert.

        Shows: expiry date, days to expiry, triggered strikes, markIV values, IV increase.

        Args:
            expiry_date: Expiry in YYMMDD format
            triggered_strikes: List of strikes that exceeded threshold
            threshold: IV threshold
            previous_iv: Previous alerted IV (None for first alert)
        """
        try:
            await self._ensure_webhook()

            # Format expiry for display
            expiry_display = self._format_expiry(expiry_date)
            days_to_expiry = self._get_days_to_expiry(expiry_date)
            max_iv = max(s['markIV'] for s in triggered_strikes)

            # Different title/description based on first alert vs increase
            if previous_iv is None:
                # First alert for this expiry
                title = f"🚨 BTC ATM Options IV Spike: {expiry_display}"
                description = (
                    f"**{len(triggered_strikes)} ATM strikes** spiked above {threshold:.0f}% IV\n"
                    f"**Expiry:** {days_to_expiry} days\n"
                    f"**Current IV:** {max_iv:.1f}%"
                )
            else:
                # IV increased from previous alert
                increase = max_iv - previous_iv
                title = f"📈 BTC ATM IV Increasing: {expiry_display}"
                description = (
                    f"**IV continuing to rise** - increased by {increase:+.1f}%\n"
                    f"**Expiry:** {days_to_expiry} days\n"
                    f"**Previous:** {previous_iv:.1f}% → **Now:** {max_iv:.1f}%"
                )

            # Create embed
            embed = discord.Embed(
                title=title,
                description=description,
                color=0xFF5733,
                timestamp=datetime.utcnow()
            )

            # List triggered strikes
            strikes_text = ""
            for strike in triggered_strikes[:10]:  # Limit to 10
                strikes_text += (
                    f"**{strike['symbol']}**\n"
                    f"└─ IV: {strike['markIV']:.2f}% | OI: {strike['openInterest']:,.0f} | "
                    f"Δ: {strike['delta']} | Price: ${strike['mark_price']}\n\n"
                )

            if len(strikes_text) > 1000:
                strikes_text = strikes_text[:997] + "..."

            embed.add_field(
                name="Triggered Strikes",
                value=strikes_text,
                inline=False
            )

            # Footer
            embed.set_footer(text="Simple Threshold Alert - No Historical Tracking")

            # Send
            content = None
            if self.mention_role_id:
                content = f"<@&{self.mention_role_id}>" if self.mention_role_id != "@everyone" else "@everyone"

            await self._webhook.send(
                content=content,
                embed=embed,
                username="IV Monitor"
            )

            self.logger.info(
                f"Sent simple alert: {expiry_date}, "
                f"{len(triggered_strikes)} strikes > {threshold}%"
            )

        except Exception as e:
            self.logger.error(f"Failed to send simple alert: {e}", exc_info=True)

    def _format_expiry(self, expiry_date: str) -> str:
        """
        Format expiry date for display.

        Args:
            expiry_date: YYMMDD format (e.g., "251226")

        Returns:
            Human-readable format (e.g., "Dec 26, 2025")
        """
        try:
            year = 2000 + int(expiry_date[:2])
            month = int(expiry_date[2:4])
            day = int(expiry_date[4:6])

            date_obj = datetime(year, month, day)
            return date_obj.strftime("%b %d, %Y")
        except (ValueError, IndexError):
            return expiry_date

    def _get_days_to_expiry(self, expiry_date: str) -> int:
        """
        Calculate days to expiry.

        Args:
            expiry_date: YYMMDD format

        Returns:
            Days to expiry
        """
        try:
            year = 2000 + int(expiry_date[:2])
            month = int(expiry_date[2:4])
            day = int(expiry_date[4:6])

            expiry_datetime = datetime(year, month, day)
            delta = expiry_datetime - datetime.utcnow()
            return max(0, delta.days)
        except (ValueError, IndexError):
            return 0

    async def send_startup_notification(self, symbols_count: int):
        """
        Send notification when the monitor starts.

        Args:
            symbols_count: Number of symbols being monitored
        """
        try:
            await self._ensure_webhook()

            embed = discord.Embed(
                title="✅ IV Monitor Started",
                description=f"Monitoring **{symbols_count}** option symbols for high IV",
                color=0x28A745,  # Green
                timestamp=datetime.utcnow()
            )

            embed.set_footer(text="Binance Options IV Monitor")

            await self._webhook.send(
                embed=embed,
                username="IV Monitor"
            )

            self.logger.info("Sent startup notification to Discord")

        except Exception as e:
            self.logger.error(f"Failed to send startup notification: {e}", exc_info=True)

    async def send_error_notification(self, error_msg: str):
        """
        Send error notification to Discord.

        Args:
            error_msg: Error message to send
        """
        try:
            await self._ensure_webhook()

            embed = discord.Embed(
                title="❌ Monitor Error",
                description=error_msg,
                color=0xDC3545,  # Red
                timestamp=datetime.utcnow()
            )

            embed.set_footer(text="Binance Options IV Monitor")

            await self._webhook.send(
                embed=embed,
                username="IV Monitor"
            )

            self.logger.info("Sent error notification to Discord")

        except Exception as e:
            self.logger.error(f"Failed to send error notification: {e}", exc_info=True)

    async def send_test_message(self):
        """Send a test message to verify webhook is working."""
        try:
            await self._ensure_webhook()

            embed = discord.Embed(
                title="🧪 Test Message",
                description="Discord webhook connection is working!",
                color=0x17A2B8,  # Blue
                timestamp=datetime.utcnow()
            )

            embed.set_footer(text="Binance Options IV Monitor")

            await self._webhook.send(
                embed=embed,
                username="IV Monitor"
            )

            self.logger.info("Sent test message to Discord")
            return True

        except Exception as e:
            self.logger.error(f"Failed to send test message: {e}", exc_info=True)
            return False
