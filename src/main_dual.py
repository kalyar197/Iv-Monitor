"""
Dual-threshold IV monitoring system entry point.

Runs two independent IV monitors concurrently:
- 42% threshold monitor (more frequent alerts)
- 45% threshold monitor (less frequent alerts)

Each monitor sends alerts to separate Discord webhooks.
"""

import asyncio
import logging
from src.monitor import IVMonitor
from src.utils.logger import setup_logger
from src.utils.validators import load_config


async def main():
    """Run two IV monitors with different thresholds concurrently."""
    # Setup initial logger for startup messages
    logger = setup_logger()
    logger.info("=" * 80)
    logger.info("Starting dual-threshold IV monitoring system...")
    logger.info("=" * 80)

    # Load configurations for both monitors
    try:
        config_42 = load_config("config/config-42.yaml")
        config_45 = load_config("config/config.yaml")
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}", exc_info=True)
        raise

    # Create separate loggers for each monitor
    logger_42 = setup_logger(
        level=config_42.get('logging', {}).get('level', 'INFO'),
        log_file=config_42.get('logging', {}).get('file', 'logs/iv_monitor_42.log'),
        console_colors=config_42.get('logging', {}).get('console_colors', True)
    )
    logger_42.name = "Monitor-42%"

    logger_45 = setup_logger(
        level=config_45.get('logging', {}).get('level', 'INFO'),
        log_file=config_45.get('logging', {}).get('file', 'logs/iv_monitor_45.log'),
        console_colors=config_45.get('logging', {}).get('console_colors', True)
    )
    logger_45.name = "Monitor-45%"

    # Create monitor instances
    monitor_42 = IVMonitor(config_42, logger_42)
    monitor_45 = IVMonitor(config_45, logger_45)

    logger.info("Initialized both monitors:")
    logger.info(f"  - 42% threshold -> {config_42['discord']['webhook_url'][:50]}...")
    logger.info(f"  - 45% threshold -> {config_45['discord']['webhook_url'][:50]}...")
    logger.info("")
    logger.info("Both monitors will run concurrently in this process.")
    logger.info("Check separate log files for each monitor's activity:")
    logger.info(f"  - {config_42.get('logging', {}).get('file', 'logs/iv_monitor_42.log')}")
    logger.info(f"  - {config_45.get('logging', {}).get('file', 'logs/iv_monitor_45.log')}")
    logger.info("=" * 80)

    # Run both monitors concurrently
    try:
        await asyncio.gather(
            monitor_42.start(),
            monitor_45.start(),
            return_exceptions=False  # Crash both if one fails
        )
    except KeyboardInterrupt:
        logger.info("Received shutdown signal. Stopping both monitors...")
        raise
    except Exception as e:
        logger.error(f"Fatal error in monitoring system: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete.")
    except Exception:
        print("\nFatal error occurred. Check logs for details.")
        raise
