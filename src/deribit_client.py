"""Deribit Options API client for PUBLIC (unauthenticated) REST API requests.

This client uses ONLY public endpoints - no API keys required!
Works from any IP address including cloud datacenters.
"""
import logging
from typing import Any, Dict, List, Optional
import aiohttp


class DeribitOptionsClient:
    """
    Client for Deribit Options PUBLIC API.

    NO AUTHENTICATION REQUIRED - all endpoints are public.
    Perfect for cloud deployment without IP restrictions.
    """

    def __init__(
        self,
        base_url: str = "https://www.deribit.com",
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize the Deribit Options client.

        Args:
            base_url: Base URL for Deribit API
            logger: Logger instance
        """
        self.base_url = base_url.rstrip('/')
        self.logger = logger or logging.getLogger(__name__)
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        """Async context manager entry."""
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def _ensure_session(self):
        """Ensure aiohttp session is created."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def close(self):
        """Close all connections."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(
        self,
        endpoint: str,
        **params
    ) -> Dict[str, Any]:
        """
        Make a PUBLIC API request (no authentication needed).

        Args:
            endpoint: API endpoint path
            **params: Query parameters

        Returns:
            Response data

        Raises:
            aiohttp.ClientError: If request fails
        """
        await self._ensure_session()

        url = f"{self.base_url}{endpoint}"

        self.logger.debug(f"GET {endpoint} - Params: {params}")

        async with self._session.get(url, params=params) as response:
            response.raise_for_status()
            data = await response.json()

            # Deribit returns {jsonrpc, id, result} or {jsonrpc, id, error}
            if 'error' in data:
                error_msg = data['error'].get('message', 'Unknown error')
                raise Exception(f"Deribit API error: {error_msg}")

            return data.get('result', data)

    async def get_index_price(self, index_name: str = "btc_usd") -> float:
        """
        Get index price (spot price equivalent).

        Args:
            index_name: Index name (btc_usd, eth_usd, etc.)

        Returns:
            Current index price
        """
        self.logger.debug(f"Fetching index price for {index_name}")
        result = await self._request("/api/v2/public/get_index_price", index_name=index_name)
        return float(result['index_price'])

    async def get_instruments(
        self,
        currency: str = "BTC",
        kind: str = "option",
        expired: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get all available instruments for a currency.

        Args:
            currency: Currency code (BTC, ETH, etc.)
            kind: Instrument type (option, future, spot)
            expired: Include expired instruments

        Returns:
            List of instrument data
        """
        self.logger.info(f"Fetching {kind} instruments for {currency}")
        result = await self._request(
            "/api/v2/public/get_instruments",
            currency=currency,
            kind=kind,
            expired="true" if expired else "false"  # Convert bool to string for API
        )
        return result

    async def get_ticker(self, instrument_name: str) -> Dict[str, Any]:
        """
        Get ticker data for a specific instrument including IV and Greeks.

        This is the key endpoint for IV monitoring!

        Args:
            instrument_name: Instrument name (e.g., BTC-27DEC24-100000-C)

        Returns:
            Ticker data including:
            - mark_iv: Mark implied volatility (percentage, e.g., 0.5025 = 50.25%)
            - bid_iv: Bid IV
            - ask_iv: Ask IV
            - greeks: delta, gamma, theta, vega, rho
            - open_interest: Open interest
            - last_price: Last traded price
            - mark_price: Mark price
            - underlying_price: Underlying asset price
        """
        self.logger.debug(f"Fetching ticker for {instrument_name}")
        result = await self._request(
            "/api/v2/public/ticker",
            instrument_name=instrument_name
        )
        return result

    async def get_book_summary_by_currency(
        self,
        currency: str = "BTC",
        kind: str = "option"
    ) -> List[Dict[str, Any]]:
        """
        Get order book summaries for all instruments of a currency.

        MORE EFFICIENT than calling get_ticker for each instrument!
        Returns IV and Greeks for ALL instruments in one call.

        Args:
            currency: Currency code (BTC, ETH, etc.)
            kind: Instrument type (option, future, etc.)

        Returns:
            List of book summaries, each containing:
            - instrument_name
            - mark_iv, bid_iv, ask_iv
            - delta, gamma, theta, vega
            - open_interest
            - mark_price
            - underlying_price
        """
        self.logger.info(f"Fetching book summary for {currency} {kind}s")
        result = await self._request(
            "/api/v2/public/get_book_summary_by_currency",
            currency=currency,
            kind=kind
        )
        return result

    async def get_perpetual_data(self, currency: str = "BTC") -> tuple[float, float]:
        """
        Get perpetual futures data (for compatibility with existing code).

        Note: Deribit doesn't have traditional perpetuals, but we can use
        the index price and funding rate approximation.

        Args:
            currency: Currency code

        Returns:
            Tuple of (index_price, estimated_funding_rate)
        """
        index_price = await self.get_index_price(f"{currency.lower()}_usd")

        # Deribit doesn't have traditional funding rates like Binance
        # Return 0.0 as placeholder
        funding_rate = 0.0

        self.logger.debug(
            f"Fetched index data for {currency}: "
            f"Index=${index_price:,.2f}"
        )

        return (index_price, funding_rate)
