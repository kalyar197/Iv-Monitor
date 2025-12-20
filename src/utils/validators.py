"""Configuration validation utilities."""
import os
import re
from typing import Any, Dict
import yaml
from dotenv import load_dotenv


def load_config(config_path: str = "config/config.yaml") -> Dict[str, Any]:
    """
    Load and validate configuration from YAML file with environment variable substitution.

    Args:
        config_path: Path to the configuration file

    Returns:
        Validated configuration dictionary

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If configuration is invalid
    """
    # Load environment variables from .env file
    load_dotenv()

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, 'r') as f:
        config_content = f.read()

    # Substitute environment variables
    config_content = substitute_env_vars(config_content)

    # Parse YAML
    config = yaml.safe_load(config_content)

    # Validate configuration
    validate_config(config)

    return config


def substitute_env_vars(content: str) -> str:
    """
    Substitute environment variables in format ${VAR_NAME} with their values.

    Args:
        content: String containing ${VAR_NAME} placeholders

    Returns:
        String with substituted values
    """
    pattern = re.compile(r'\$\{([^}]+)\}')

    def replace(match):
        var_name = match.group(1)
        value = os.getenv(var_name)
        if value is None:
            raise ValueError(f"Environment variable {var_name} is not set")
        return value

    return pattern.sub(replace, content)


def validate_config(config: Dict[str, Any]) -> None:
    """
    Validate the configuration dictionary.

    Args:
        config: Configuration dictionary to validate

    Raises:
        ValueError: If configuration is invalid
    """
    # Validate Binance section
    if 'binance' not in config:
        raise ValueError("Missing 'binance' section in configuration")

    binance = config['binance']
    required_binance_fields = ['api_key', 'api_secret', 'base_url', 'websocket_url']
    for field in required_binance_fields:
        if field not in binance or not binance[field]:
            raise ValueError(f"Missing or empty 'binance.{field}' in configuration")

    # Validate monitoring section
    if 'monitoring' not in config:
        raise ValueError("Missing 'monitoring' section in configuration")

    monitoring = config['monitoring']
    if 'symbols' not in monitoring or not monitoring['symbols']:
        raise ValueError("'monitoring.symbols' must contain at least one symbol pattern")

    if 'iv_threshold' not in monitoring:
        raise ValueError("Missing 'monitoring.iv_threshold' in configuration")

    iv_threshold = monitoring['iv_threshold']
    if not isinstance(iv_threshold, (int, float)) or iv_threshold <= 0 or iv_threshold > 500:
        raise ValueError("'monitoring.iv_threshold' must be a number between 0 and 500")

    # Validate Discord section
    if 'discord' not in config:
        raise ValueError("Missing 'discord' section in configuration")

    discord = config['discord']
    if 'webhook_url' not in discord or not discord['webhook_url']:
        raise ValueError("Missing or empty 'discord.webhook_url' in configuration")

    if not discord['webhook_url'].startswith('https://discord.com/api/webhooks/'):
        raise ValueError("'discord.webhook_url' must be a valid Discord webhook URL")

    # Validate logging section (optional, with defaults)
    if 'logging' in config:
        logging = config['logging']
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if 'level' in logging and logging['level'].upper() not in valid_levels:
            raise ValueError(f"'logging.level' must be one of: {', '.join(valid_levels)}")


def validate_symbol_pattern(pattern: str) -> bool:
    """
    Validate a symbol pattern.

    Valid patterns:
    - BTC-250131-50000-C (specific contract)
    - BTC-*-50000-C (all expiries for a strike)
    - BTC-250131-*-C (all strikes for an expiry)
    - BTC-*-ATM-C (all ATM calls)

    Args:
        pattern: Symbol pattern to validate

    Returns:
        True if valid, False otherwise
    """
    # Pattern format: UNDERLYING-EXPIRY-STRIKE-TYPE
    # Where * and ATM are wildcards
    parts = pattern.split('-')

    if len(parts) != 4:
        return False

    underlying, expiry, strike, option_type = parts

    # Validate underlying (should be alphanumeric)
    if not underlying.isalnum():
        return False

    # Validate expiry (YYMMDD or *)
    if expiry != '*' and (not expiry.isdigit() or len(expiry) != 6):
        return False

    # Validate strike (number, *, or ATM)
    if strike not in ('*', 'ATM'):
        try:
            float(strike)
        except ValueError:
            return False

    # Validate option type (C for call, P for put)
    if option_type not in ('C', 'P'):
        return False

    return True
