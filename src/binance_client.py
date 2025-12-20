"""Binance Options API client for REST API requests."""
import hashlib
import hmac
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp


class BinanceOptionsClient:
    """
    Client for interacting with Binance Options API via REST.

    Uses polling strategy as WebSocket ticker streams don't provide IV data.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://eapi.binance.com",
        websocket_url: str = None,  # Deprecated parameter, kept for compatibility
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize the Binance Options client.

        Args:
            api_key: Binance API key
            api_secret: Binance API secret
            base_url: Base URL for REST API
            websocket_url: Deprecated, not used
            logger: Logger instance
        """
        self.api_key = api_key
        self.api_secret = api_secret
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

    def _generate_signature(self, params: Dict[str, Any]) -> str:
        """
        Generate HMAC SHA256 signature for authenticated requests.

        Args:
            params: Request parameters

        Returns:
            Hex-encoded signature
        """
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    async def _request(
        self,
        method: str,
        endpoint: str,
        signed: bool = False,
        **params
    ) -> Dict[str, Any]:
        """
        Make an authenticated REST API request.

        Args:
            method: HTTP method (GET, POST, DELETE)
            endpoint: API endpoint
            signed: Whether request requires signature
            **params: Query parameters

        Returns:
            Response JSON

        Raises:
            aiohttp.ClientError: If request fails
        """
        await self._ensure_session()

        url = f"{self.base_url}{endpoint}"
        headers = {"X-MBX-APIKEY": self.api_key}

        if signed:
            params['timestamp'] = int(time.time() * 1000)
            params['signature'] = self._generate_signature(params)

        self.logger.debug(f"{method} {endpoint} - Params: {params}")

        async with self._session.request(method, url, params=params, headers=headers) as response:
            response.raise_for_status()
            data = await response.json()
            return data

    async def get_exchange_info(self) -> Dict[str, Any]:
        """
        Get current exchange trading rules and symbol information.

        Returns:
            Exchange info including all option symbols
        """
        self.logger.info("Fetching exchange info")
        return await self._request("GET", "/eapi/v1/exchangeInfo")

    async def get_ticker(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get 24hr ticker price change statistics.

        Args:
            symbol: Specific symbol (if None, returns all symbols)

        Returns:
            List of ticker data
        """
        params = {}
        if symbol:
            params['symbol'] = symbol

        self.logger.debug(f"Fetching ticker for {symbol or 'all symbols'}")
        result = await self._request("GET", "/eapi/v1/ticker", **params)

        # API returns single object if symbol specified, list otherwise
        if isinstance(result, dict):
            return [result]
        return result

    async def get_account_info(self) -> Dict[str, Any]:
        """
        Get account information including Greeks (if available).

        This endpoint is documented to include "Greeks" but the exact
        structure is unclear. We'll investigate the response.

        Returns:
            Account information
        """
        self.logger.info("Fetching account info (checking for Greeks/IV)")
        return await self._request("GET", "/eapi/v1/account", signed=True)

    async def get_spot_price(self, symbol: str = "BTCUSDT") -> float:
        """
        Get current spot price for an underlying asset.

        Args:
            symbol: Spot trading pair (e.g., BTCUSDT)

        Returns:
            Current price
        """
        # Use Binance Spot API for spot price
        url = f"https://api.binance.com/api/v3/ticker/price"
        params = {"symbol": symbol}

        async with self._session.get(url, params=params) as response:
            response.raise_for_status()
            data = await response.json()
            return float(data['price'])

    async def get_perpetual_data(self, symbol: str = "BTCUSDT") -> tuple[float, float]:
        """
        Get both perpetual mark price and funding rate in single API call.

        IMPORTANT: Uses mark price, not last price. Mark price is the "fair value"
        used for liquidations and represents the true underlying in crypto markets.

        The funding rate acts as a continuous dividend yield in crypto markets.
        Positive rate = longs pay shorts (bullish sentiment, Contango).
        Negative rate = shorts pay longs (bearish sentiment, Backwardation).

        Uses /fapi/v1/premiumIndex endpoint which provides both mark price
        and funding rate in a single call, more efficient than separate requests.

        Args:
            symbol: Perpetual symbol (e.g., BTCUSDT)

        Returns:
            Tuple of (mark_price, funding_rate)
            - mark_price: Current perpetual futures mark price
            - funding_rate: Last funding rate as decimal (e.g., 0.0001 = 0.01%)
        """
        await self._ensure_session()

        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        params = {"symbol": symbol}

        async with self._session.get(url, params=params) as response:
            response.raise_for_status()
            data = await response.json()
            mark_price = float(data['markPrice'])
            funding_rate = float(data['lastFundingRate'])

            self.logger.debug(
                f"Fetched perpetual data for {symbol}: "
                f"Mark=${mark_price:,.2f}, Funding={funding_rate*100:.4f}%"
            )

            return (mark_price, funding_rate)

    async def get_mark_price(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get mark price data including IV and Greeks for options.

        This endpoint provides:
        - markIV, bidIV, askIV (implied volatility)
        - delta, gamma, theta, vega (Greeks)
        - markPrice, price limits

        Args:
            symbol: Specific symbol (if None, returns all symbols)

        Returns:
            List of mark price data with IV and Greeks
        """
        params = {}
        if symbol:
            params['symbol'] = symbol

        self.logger.debug(f"Fetching mark price data for {symbol or 'all symbols'}")
        result = await self._request("GET", "/eapi/v1/mark", **params)

        # API returns single object if symbol specified, list otherwise
        if isinstance(result, dict):
            return [result]
        return result
