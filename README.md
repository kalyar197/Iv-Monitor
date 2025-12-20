# Binance Options IV Monitor

A real-time monitoring system for Binance Options Implied Volatility (IV) with Discord notifications. Perfect for option sellers who need to identify high IV opportunities.

## Features

- **Real-time Monitoring**: WebSocket-based continuous IV tracking
- **Flexible Symbol Patterns**: Monitor specific contracts, expiries, or ATM options
- **IV Detection**: Attempts to extract IV from API, falls back to Black-Scholes calculation
- **Discord Alerts**: Rich embedded notifications when IV exceeds your threshold
- **Smart Cooldowns**: Prevents notification spam with configurable per-symbol cooldowns
- **Auto-Reconnect**: Robust error handling with automatic WebSocket reconnection
- **24/7 Ready**: Designed for continuous operation on servers or cloud VMs

## Quick Start

### 1. Prerequisites

- Python 3.8 or higher
- Binance account with API keys ([Create here](https://www.binance.com/en/my/settings/api-management))
- Discord webhook URL ([Setup guide](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks))

### 2. Installation

```bash
# Clone or download this repository
cd Binance\ IV

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration

```bash
# Create environment file
copy .env.example .env  # Windows
cp .env.example .env    # Linux/Mac

# Edit .env and add your credentials
```

Edit `.env`:
```env
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_api_secret_here
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_TOKEN
```

```bash
# Create config file
copy config\config.yaml.example config\config.yaml  # Windows
cp config/config.yaml.example config/config.yaml    # Linux/Mac

# Edit config.yaml to customize monitoring
```

### 4. Run

```bash
python -m src.main
```

## Configuration Guide

### Symbol Patterns

Configure which options to monitor in `config/config.yaml`:

```yaml
monitoring:
  symbols:
    # Monitor all BTC ATM calls (strikes within 5% of spot)
    - "BTC-*-ATM-C"

    # Monitor all options expiring Jan 31, 2025
    - "BTC-250131-*-C"
    - "BTC-250131-*-P"

    # Monitor specific contracts
    - "BTC-250131-50000-C"
    - "BTC-250228-55000-P"

    # Monitor all strikes for a specific expiry
    - "ETH-250131-*-C"
```

**Pattern Format**: `UNDERLYING-EXPIRY-STRIKE-TYPE`
- `UNDERLYING`: BTC, ETH, etc.
- `EXPIRY`: YYMMDD or `*` for all
- `STRIKE`: Price, `*` for all, or `ATM` for at-the-money
- `TYPE`: `C` for calls, `P` for puts

### IV Threshold

```yaml
monitoring:
  # Alert when IV exceeds 80%
  iv_threshold: 80.0

  # Minimum minutes between alerts for same symbol
  alert_cooldown: 15

  # ATM range (% of spot price)
  atm_range_percent: 5.0
```

### Discord Settings

```yaml
discord:
  webhook_url: "${DISCORD_WEBHOOK_URL}"

  # Optional: Mention a role (use role ID or "@everyone")
  mention_role_id: "123456789012345678"
  # Or: mention_role_id: "@everyone"
  # Or: mention_role_id: null  # No mentions

  # Send notification when bot starts
  send_startup_notification: true
```

## How It Works

### IV Detection Strategy

1. **Primary**: Checks undocumented Binance API endpoints for IV/Greeks
   - Inspects `/eapi/v1/account` response for Greeks fields
   - Checks `/eapi/v1/ticker` for hidden IV fields

2. **Fallback**: Calculates IV using Black-Scholes model
   - Fetches option price, strike, and expiry from ticker
   - Gets real-time spot price from Binance Spot API
   - Solves for IV using `py_vollib` library

### WebSocket Streaming

- Maintains persistent connection to `wss://vstream.binance.com/ws`
- Subscribes to ticker streams for each monitored symbol
- Auto-reconnects with exponential backoff on disconnection
- Processes real-time ticker updates asynchronously

### Alert System

When IV exceeds threshold:
1. Checks cooldown (default: 15 minutes per symbol)
2. Sends rich embed to Discord with:
   - Current IV vs threshold
   - Strike price, last price, volume
   - Bid/ask spread, 24h price change
   - Underlying spot price estimate
3. Records alert time to prevent spam

## Project Structure

```
Binance IV/
├── config/
│   ├── config.yaml.example    # Configuration template
│   └── config.yaml            # Your config (gitignored)
├── src/
│   ├── __init__.py
│   ├── main.py                # Entry point
│   ├── binance_client.py      # Binance API client
│   ├── iv_extractor.py        # IV extraction/calculation
│   ├── discord_notifier.py    # Discord webhook integration
│   ├── monitor.py             # Main orchestrator
│   └── utils/
│       ├── logger.py          # Logging setup
│       └── validators.py      # Config validation
├── logs/                      # Log files
├── .env                       # Environment variables (gitignored)
├── .env.example              # Environment template
├── .gitignore
├── requirements.txt
└── README.md
```

## Deployment

### Run Locally (Development)

```bash
python -m src.main
```

### Run as Background Process (Linux)

```bash
# Using nohup
nohup python -m src.main > output.log 2>&1 &

# Or with screen
screen -S iv-monitor
python -m src.main
# Detach with Ctrl+A, D
```

### Run as Systemd Service (Linux)

Create `/etc/systemd/system/binance-iv-monitor.service`:

```ini
[Unit]
Description=Binance Options IV Monitor
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/Binance IV
ExecStart=/path/to/Binance IV/venv/bin/python -m src.main
Restart=always
RestartSec=10
Environment="PATH=/path/to/Binance IV/venv/bin"

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable binance-iv-monitor
sudo systemctl start binance-iv-monitor
sudo systemctl status binance-iv-monitor
```

### Run on Windows (Startup)

1. Create `run_monitor.bat`:
```bat
@echo off
cd /d "C:\path\to\Binance IV"
call venv\Scripts\activate
python -m src.main
pause
```

2. Press `Win+R`, type `shell:startup`, press Enter
3. Create shortcut to `run_monitor.bat` in Startup folder

## Troubleshooting

### "Configuration file not found"
- Ensure `config/config.yaml` exists (copy from `config.yaml.example`)

### "Environment variable X is not set"
- Check `.env` file has all required variables
- Ensure `.env` is in the project root directory

### "Discord webhook connection failed"
- Verify webhook URL is correct in `.env`
- Test webhook manually in Discord server settings

### "No symbols to monitor"
- Check symbol patterns in `config.yaml`
- Ensure patterns match available Binance options
- Check logs for symbol discovery results

### WebSocket keeps disconnecting
- This is normal for network issues - auto-reconnect will handle it
- If persistent, check Binance API status
- Verify firewall/proxy settings

### IV always shows as "None"
- Check if `py_vollib` is installed: `pip install py-vollib`
- Verify option prices are valid (not 0)
- Check logs for calculation errors

## API Rate Limits

- **Binance REST API**: Weight-based limits per minute
- **Binance WebSocket**: No explicit rate limits, but avoid too many subscriptions
- **Discord Webhooks**: 30 requests per minute

The monitor respects these limits with:
- Cooldown periods between alerts
- Efficient WebSocket streaming (no polling)
- Periodic batch updates for spot prices

## Security Notes

- Never commit `.env` or `config.yaml` to version control
- Keep API keys secure with read-only permissions
- Binance API keys don't need trading permissions for this tool
- Use IP whitelisting on Binance API keys for extra security

## Use Case: Option Selling Strategy

This tool is designed for **option sellers** who profit from high IV:

1. **High IV = High Premiums**: When IV spikes, option premiums increase
2. **Sell at Peak**: Get alerted when IV exceeds your threshold
3. **Collect Premium**: Sell options at inflated prices
4. **Let Theta Decay**: Profit as IV normalizes and time passes

**Example Workflow**:
- Set threshold at 80% IV
- Monitor BTC ATM calls and puts
- Receive Discord alert when IV hits 85%
- Sell options on Binance to collect high premiums
- Profit as IV mean-reverts

## Contributing

Contributions welcome! Areas for enhancement:
- Support for more exchanges (Deribit, OKX)
- Historical IV tracking and charting
- IV percentile calculations (current vs average)
- Telegram notifications
- Web dashboard

## License

MIT License - feel free to use and modify.

## Disclaimer

This tool is for educational and informational purposes. Trading options involves substantial risk. Always do your own research and never risk more than you can afford to lose.

## Support

For issues or questions:
1. Check this README and logs first
2. Review Binance Options API docs
3. Open an issue on GitHub (if applicable)

## Links

- [Binance Options](https://www.binance.com/en/options)
- [Binance Options API Docs](https://www.binance.com/en/support/faq/binance-options-api-interface-and-websocket-fe0be251ac014a8082e702f83d089e54)
- [Discord Webhooks Guide](https://support.discord.com/hc/en-us/articles/228383668)
- [Black-Scholes Model](https://en.wikipedia.org/wiki/Black%E2%80%93Scholes_model)
