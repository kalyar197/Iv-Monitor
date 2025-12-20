# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A real-time Binance Options IV (Implied Volatility) monitoring system that sends Discord alerts when IV exceeds configurable thresholds. Designed for option sellers who need to identify high IV opportunities.

**Critical Design Principle**: ZERO ASSUMPTIONS - all data must come directly from Binance API. Never calculate or estimate values that can be fetched from Binance.

## Running the Application

```bash
# Start the monitor
python -m src.main

# Development with dependencies
pip install -r requirements.txt
```

**Prerequisites**:
- `.env` file with `BINANCE_API_KEY`, `BINANCE_API_SECRET`, `DISCORD_WEBHOOK_URL`
- `config/config.yaml` configured (copy from `.env.example` and `config.yaml.example`)

## Core Architecture

### Data Flow (REST Polling - NOT WebSocket)

The system uses **REST API polling** (every 10 seconds), NOT WebSocket streaming:

```
1. IVMonitor orchestrates the polling loop
2. BinanceOptionsClient.get_mark_price() fetches ALL mark prices
3. IVExtractor.get_iv_from_mark_data() extracts real markIV from response
4. Compare IV against threshold
5. DiscordNotifier sends alerts for symbols exceeding threshold
```

**Why REST over WebSocket**: Binance Options WebSocket ticker streams do not include IV data. The `/eapi/v1/mark` endpoint provides real `markIV`, `bidIV`, `askIV`, and all Greeks (delta, gamma, theta, vega).

### Component Responsibilities

**src/monitor.py** (`IVMonitor`)
- Main orchestrator - owns the polling loop
- Symbol filtering (patterns + ATM + expiry filtering)
- Manages `monitored_symbols` list based on patterns
- Implements alert cooldown (15 min per symbol by default)
- **ATM filtering**: Selects 2 strikes below + ATM + 2 strikes above for each expiry
- **Expiry filtering**: Only monitors options with `min_days_to_expiry <= days <= max_days_to_expiry`

**src/binance_client.py** (`BinanceOptionsClient`)
- REST API wrapper with HMAC SHA256 authentication
- Key endpoint: `get_mark_price()` - returns list of mark data with IV and Greeks
- `get_exchange_info()` - fetches all available option symbols from Binance
- `get_spot_price(symbol)` - fetches BTC/ETH spot prices for ATM calculations
- Async context manager pattern for connection management

**src/iv_extractor.py** (`IVExtractor`)
- Extracts IV from mark price data: `get_iv_from_mark_data(mark_data)`
- Returns `markIV` (primary) or average of `bidIV`/`askIV` (fallback)
- Converts from decimal (0.5093) to percentage (50.93%)
- **NO CALCULATION** - only extraction from real Binance data

**src/discord_notifier.py** (`DiscordNotifier`)
- Sends rich Discord embeds with IV alerts
- Shows: markIV, bidIV/askIV, Greeks (delta, gamma, theta, vega), mark price, price limits
- Async webhook using discord.py library

### Symbol Pattern System

Format: `UNDERLYING-EXPIRY-STRIKE-TYPE`

Examples:
- `BTC-*-ATM-C` - All BTC ATM calls (automatically finds strikes within `atm_range_percent` of spot)
- `BTC-250131-*-C` - All Jan 31, 2025 BTC calls
- `BTC-250131-50000-C` - Specific contract

**ATM Pattern Logic**:
1. Fetch spot price from Binance Spot API
2. Group symbols by expiry
3. For each expiry, find ATM strike (closest to spot)
4. Select 2 strikes below ATM, ATM itself, and 2 strikes above ATM
5. Result: Up to 5 strikes per expiry per option type

**Expiry Filtering**:
- `min_days_to_expiry` / `max_days_to_expiry` in config
- System calculates days to expiry from today for each symbol
- Only symbols within the range are monitored
- Auto-adjusts as days pass (expired options drop off, new ones added)

### Configuration (config/config.yaml)

```yaml
monitoring:
  symbols: ["BTC-*-ATM-C", "BTC-*-ATM-P"]  # Patterns to monitor
  iv_threshold: 55.0  # Alert when IV > this %
  alert_cooldown: 15  # Minutes between alerts per symbol
  check_interval: 10  # Seconds between mark price polls
  atm_range_percent: 5.0  # Unused with new ATM logic (kept for compatibility)
  min_days_to_expiry: 0  # Filter: minimum days to expiry
  max_days_to_expiry: 30  # Filter: maximum days to expiry
```

**Environment variable substitution**: `${VAR_NAME}` in YAML is replaced from `.env`

## Critical Implementation Details

### Why No WebSocket?

Initial implementation used WebSocket ticker streams (`wss://nbstream.binance.com/eoptions/ws`), but Binance Options ticker data **does not include IV**. Switched to REST polling of `/eapi/v1/mark` which provides real IV and Greeks.

### Why No IV Calculation?

Original plan included Black-Scholes IV calculation as fallback, but this violates the "zero assumptions" principle. The `/eapi/v1/mark` endpoint provides real market IV, so calculation is unnecessary and less accurate.

### Binance API Endpoints Used

- `GET /eapi/v1/exchangeInfo` - Fetch all available option symbols
- `GET /eapi/v1/mark` - **Primary data source** - mark prices with IV and Greeks
- `GET /api/v3/ticker/price` (Spot API) - Fetch BTC/ETH spot prices for ATM filtering

Note: `GET /eapi/v1/account` returns 404 and is not used (account data not needed).

### Alert Cooldown Mechanism

```python
last_alert_time[symbol] = datetime.utcnow()
# Next alert only if (now - last_alert_time) > alert_cooldown
```

Prevents spam when IV stays elevated. Per-symbol tracking ensures one high-IV option doesn't silence alerts for others.

### ATM Strike Selection Algorithm

```python
# For each expiry:
1. Sort all strikes by price
2. Find ATM index: closest strike to spot price
3. Select strikes[atm_idx - 2 : atm_idx + 3]
   # This gives 2 below, ATM, and 2 above
```

All strikes come from Binance `/eapi/v1/exchangeInfo` - no assumptions or calculations.

### Expiry Date Parsing

Symbol format: `BTC-YYMMDD-STRIKE-TYPE`
- Extract `YYMMDD` substring
- Convert to datetime: `year = 2000 + YY, month = MM, day = DD`
- Calculate `days_to_expiry = (expiry_date - today).days`

## Common Development Tasks

### Adjusting IV Threshold

Edit `config/config.yaml`:
```yaml
monitoring:
  iv_threshold: 60.0  # New threshold
```

Restart the monitor to apply changes.

### Changing Monitored Symbols

Edit patterns in `config/config.yaml`:
```yaml
monitoring:
  symbols:
    - "BTC-*-ATM-C"  # All BTC ATM calls
    - "ETH-260131-*-P"  # All ETH puts expiring Jan 31, 2026
```

System refreshes symbols every 5 minutes automatically, or restart for immediate effect.

### Adjusting Expiry Window

Edit `config/config.yaml`:
```yaml
monitoring:
  min_days_to_expiry: 0   # From today
  max_days_to_expiry: 30  # Next 30 days
```

Set to `null` to disable expiry filtering.

### Testing Discord Notifications

The `DiscordNotifier` class has a `send_test_message()` method for testing webhooks.

### Debugging Symbol Discovery

Check logs for:
- "Found X total option symbols" - Raw count from Binance
- "Expiry YYMMDD (N days): ATM strike=X, selected Y strikes from A to B" - Per-expiry filtering results
- "Monitoring X symbols" - Final count after all filtering

## Code Conventions

- All components use async/await (asyncio-based)
- Async context managers (`async with`) for resource cleanup
- Type hints on all function signatures
- Docstrings in Google style
- Logging at appropriate levels (DEBUG for details, INFO for key events, WARNING for alerts, ERROR for failures)

## Testing Notes

The system is designed for 24/7 operation but currently has no automated tests. Manual testing workflow:
1. Start monitor with low IV threshold (e.g., 40%)
2. Verify Discord alerts are received
3. Check logs for correct symbol counts and IV values
4. Verify cooldown prevents alert spam

## Important Constraints

1. **No calculations** - Extract all data from Binance API
2. **No WebSocket** - Use REST polling of `/eapi/v1/mark`
3. **Only BTC options** - User trades only BTC (system supports others but not actively used)
4. **Strikes from Binance** - Never generate or assume strike prices
5. **Real IV only** - Use `markIV`, `bidIV`, `askIV` from mark data

## Dependencies Note

- `py-vollib`, `scipy`, `numpy` are installed but **not used** (legacy from IV calculation approach)
- Could be removed but kept for potential future enhancements
- `discord.py` uses webhooks asynchronously (not bot commands)
- `websockets` library installed but not actively used (legacy from WebSocket approach)
