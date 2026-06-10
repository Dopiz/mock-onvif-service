"""
Scheduled log cleanup service.
Runs periodic cleanup of old log files using a stoppable Event-based wait.
"""
import logging
import threading
from pathlib import Path

from app.log_manager import LogManager

logger = logging.getLogger(__name__)


class LogCleanupScheduler:
    """Scheduler for periodic log cleanup."""

    def __init__(self, logs_dir, interval_hours=24):
        self.logs_dir = Path(logs_dir)
        self.interval_seconds = interval_hours * 3600
        self._stop_event = threading.Event()
        self.thread = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive() and not self._stop_event.is_set()

    def _cleanup_loop(self):
        logger.info("Log cleanup scheduler started (runs every %.0f hours)", self.interval_seconds / 3600)
        while not self._stop_event.is_set():
            # Wait returns True if Event was set (stop requested), False on timeout
            if self._stop_event.wait(self.interval_seconds):
                break
            try:
                logger.info("Scheduled log cleanup triggered")
                LogManager.cleanup_all_log_directories(self.logs_dir)
            except Exception as e:
                logger.warning("Error in log cleanup scheduler: %s", e)
        logger.info("Log cleanup scheduler loop exited")

    def start(self):
        if self.running:
            logger.warning("Log cleanup scheduler already running")
            return

        self._stop_event.clear()
        self.thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="LogCleanupScheduler"
        )
        self.thread.start()

        # Run initial cleanup immediately (synchronously) so disk is sane at startup
        try:
            logger.info("Running initial log cleanup")
            LogManager.cleanup_all_log_directories(self.logs_dir)
        except Exception as e:
            logger.warning("Error in initial cleanup: %s", e)

    def stop(self):
        if not self.thread:
            return
        logger.info("Stopping log cleanup scheduler")
        self._stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        self.thread = None
        logger.info("Log cleanup scheduler stopped")

    def get_status(self):
        return {
            "running": self.running,
            "interval_hours": self.interval_seconds / 3600,
            "thread_alive": self.thread.is_alive() if self.thread else False,
        }


_scheduler = None


def get_scheduler(logs_dir="./logs", interval_hours=24):
    global _scheduler
    if _scheduler is None:
        _scheduler = LogCleanupScheduler(logs_dir, interval_hours)
    return _scheduler


def start_log_cleanup_scheduler(logs_dir="./logs", interval_hours=24):
    scheduler = get_scheduler(logs_dir, interval_hours)
    scheduler.start()
    return scheduler


def stop_log_cleanup_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.stop()
