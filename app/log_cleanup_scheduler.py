"""
Scheduled log cleanup service
Runs periodic cleanup of old log files
"""
import threading
import time
from pathlib import Path

from app.log_manager import LogManager


class LogCleanupScheduler:
    """Scheduler for periodic log cleanup"""

    def __init__(self, logs_dir, interval_hours=24):
        """
        Initialize log cleanup scheduler

        Args:
            logs_dir: Base logs directory path
            interval_hours: Hours between cleanup runs (default: 24)
        """
        self.logs_dir = Path(logs_dir)
        self.interval_seconds = interval_hours * 3600
        self.running = False
        self.thread = None

    def _cleanup_loop(self):
        """Main cleanup loop running in background thread"""
        print(f"\n🕐 Log cleanup scheduler started (runs every {self.interval_seconds / 3600:.0f} hours)")

        while self.running:
            try:
                # Wait for interval (check every 60 seconds if we should stop)
                for _ in range(int(self.interval_seconds / 60)):
                    if not self.running:
                        break
                    time.sleep(60)

                if not self.running:
                    break

                # Run cleanup
                print(f"\n⏰ Scheduled log cleanup triggered...")
                LogManager.cleanup_all_log_directories(self.logs_dir)

            except Exception as e:
                print(f"⚠️  Error in log cleanup scheduler: {e}")
                # Continue running despite errors
                time.sleep(60)

    def start(self):
        """Start the cleanup scheduler"""
        if self.running:
            print("⚠️  Log cleanup scheduler already running")
            return

        self.running = True
        self.thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="LogCleanupScheduler"
        )
        self.thread.start()

        # Run initial cleanup immediately
        try:
            print("\n🧹 Running initial log cleanup...")
            LogManager.cleanup_all_log_directories(self.logs_dir)
        except Exception as e:
            print(f"⚠️  Error in initial cleanup: {e}")

    def stop(self):
        """Stop the cleanup scheduler"""
        if not self.running:
            return

        print("\n🛑 Stopping log cleanup scheduler...")
        self.running = False

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)

        print("✓ Log cleanup scheduler stopped")

    def get_status(self):
        """Get scheduler status"""
        return {
            "running": self.running,
            "interval_hours": self.interval_seconds / 3600,
            "thread_alive": self.thread.is_alive() if self.thread else False
        }


# Global scheduler instance
_scheduler = None


def get_scheduler(logs_dir="./logs", interval_hours=24):
    """
    Get or create global scheduler instance

    Args:
        logs_dir: Base logs directory path
        interval_hours: Hours between cleanup runs

    Returns:
        LogCleanupScheduler instance
    """
    global _scheduler
    if _scheduler is None:
        _scheduler = LogCleanupScheduler(logs_dir, interval_hours)
    return _scheduler


def start_log_cleanup_scheduler(logs_dir="./logs", interval_hours=24):
    """
    Start the global log cleanup scheduler

    Args:
        logs_dir: Base logs directory path
        interval_hours: Hours between cleanup runs (default: 24)
    """
    scheduler = get_scheduler(logs_dir, interval_hours)
    scheduler.start()
    return scheduler


def stop_log_cleanup_scheduler():
    """Stop the global log cleanup scheduler"""
    global _scheduler
    if _scheduler:
        _scheduler.stop()
