"""Startup helpers — background services that must run alongside the HTTP server."""
from __future__ import annotations

import logging

from app.config import (
    DATA_CLEANUP_ENABLED,
    DATA_CLEANUP_INTERVAL_HOURS,
    WATCHDOG_ENABLED,
    WATCHDOG_INTERVAL_SECONDS,
)
from app.log_cleanup_scheduler import start_log_cleanup_scheduler

logger = logging.getLogger(__name__)


def startup_dependencies() -> None:
    logger.info("Starting background services")

    try:
        start_log_cleanup_scheduler(logs_dir="./logs", interval_hours=24)
    except Exception as e:
        logger.warning("Failed to start log cleanup scheduler: %s", e)

    if DATA_CLEANUP_ENABLED:
        try:
            from app.data_cleaner import start_data_cleanup_scheduler
            start_data_cleanup_scheduler(interval_hours=DATA_CLEANUP_INTERVAL_HOURS)
        except Exception as e:
            logger.warning("Failed to start data cleanup scheduler: %s", e)

    if WATCHDOG_ENABLED:
        try:
            from app.watchdog import start_watchdog
            start_watchdog(interval=WATCHDOG_INTERVAL_SECONDS)
        except Exception as e:
            logger.warning("Failed to start watchdog: %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    startup_dependencies()
