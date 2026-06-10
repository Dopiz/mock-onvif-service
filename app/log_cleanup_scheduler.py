"""Scheduled log cleanup service.

Runs periodic cleanup of old log files using a stoppable Event-based wait.
Configuration (logs dir, interval) is read from :mod:`app.config`.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from app.config import LOG_CLEANUP_INTERVAL_HOURS, LOGS_DIR
from app.log_manager import LogManager

logger = logging.getLogger(__name__)


class LogCleanupScheduler:
    """Scheduler for periodic log cleanup."""

    def __init__(self, logs_dir=LOGS_DIR, interval_hours: float = LOG_CLEANUP_INTERVAL_HOURS) -> None:
        self.logs_dir = logs_dir
        self.interval_seconds: float = interval_hours * 3600
        self._stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive() and not self._stop_event.is_set()

    def _cleanup_loop(self) -> None:
        logger.info("Log cleanup scheduler started (runs every %.0f hours)",
                    self.interval_seconds / 3600)
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

    def start(self) -> None:
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

    def stop(self) -> None:
        if not self.thread:
            return
        logger.info("Stopping log cleanup scheduler")
        self._stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        self.thread = None
        logger.info("Log cleanup scheduler stopped")


# ── Singleton accessor (lazy, double-checked locking) ──────────────────────
_scheduler: Optional[LogCleanupScheduler] = None
_scheduler_lock = threading.Lock()


def get_scheduler() -> LogCleanupScheduler:
    global _scheduler
    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                _scheduler = LogCleanupScheduler()
    return _scheduler


def start_log_cleanup_scheduler() -> LogCleanupScheduler:
    scheduler = get_scheduler()
    scheduler.start()
    return scheduler


def stop_log_cleanup_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.stop()
