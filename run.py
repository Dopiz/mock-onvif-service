#!/usr/bin/env python3
"""Entry point for the Mock ONVIF Camera Service."""
from __future__ import annotations

import atexit
import logging
import signal
import sys

from dotenv import load_dotenv

load_dotenv()

# Configure root logging once, here.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def _shutdown() -> None:
    """Quiesce background services, kill child processes, release resources."""
    logger.info("Shutting down service")
    try:
        from app.log_cleanup_scheduler import stop_log_cleanup_scheduler
        stop_log_cleanup_scheduler()
    except Exception as e:
        logger.warning("Error stopping log cleanup scheduler: %s", e)

    try:
        from app.data_cleaner import stop_data_cleanup_scheduler
        stop_data_cleanup_scheduler()
    except Exception as e:
        logger.warning("Error stopping data cleanup scheduler: %s", e)

    try:
        from app.watchdog import stop_watchdog
        stop_watchdog()
    except Exception as e:
        logger.warning("Error stopping watchdog: %s", e)

    try:
        from app.camera_lifecycle import cleanup_all
        cleanup_all()
    except Exception as e:
        logger.warning("Error during camera cleanup: %s", e)

    logger.info("Shutdown complete")


def _signal_handler(signum, _frame) -> None:
    logger.info("Received signal %d, shutting down", signum)
    _shutdown()
    sys.exit(0)


def main() -> None:
    atexit.register(_shutdown)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    from app.config import DEBUG_MODE, SERVER_HOST, SERVER_PORT, ensure_dirs
    ensure_dirs()

    from app.startup import startup_dependencies
    from app.camera_lifecycle import restore_cameras
    from app.app import app

    startup_dependencies()
    restore_cameras()

    logger.info("Starting HTTP server on %s:%d (debug=%s)", SERVER_HOST, SERVER_PORT, DEBUG_MODE)
    try:
        app.run(host=SERVER_HOST, port=SERVER_PORT, debug=DEBUG_MODE, use_reloader=False)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
