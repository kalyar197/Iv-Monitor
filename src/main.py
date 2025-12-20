"""Main entry point for Binance Options IV Monitor."""
import asyncio
import sys
from pathlib import Path

from .monitor import IVMonitor
from .utils.logger import setup_logger
from .utils.validators import load_config


async def main():
    """Main application entry point."""
    # Setup logger
    logger = setup_logger()

    try:
        # Load and validate configuration
        logger.info("Loading configuration...")
        config = load_config("config/config.yaml")

        # Setup logger with config settings
        logging_config = config.get('logging', {})
        logger = setup_logger(
            level=logging_config.get('level', 'INFO'),
            log_file=logging_config.get('file', 'logs/iv_monitor.log'),
            console_colors=logging_config.get('console_colors', True)
        )

        logger.info("Configuration loaded successfully")
        logger.info(f"IV Threshold: {config['monitoring']['iv_threshold']}%")
        logger.info(f"Monitoring patterns: {config['monitoring']['symbols']}")

        # Create and start monitor
        monitor = IVMonitor(config, logger)
        await monitor.start()

    except FileNotFoundError as e:
        logger.error(f"Configuration file not found: {e}")
        logger.error("Please copy config/config.yaml.example to config/config.yaml and configure it")
        sys.exit(1)

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        logger.error("Please check your config/config.yaml and .env files")
        sys.exit(1)

    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
        sys.exit(0)

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
