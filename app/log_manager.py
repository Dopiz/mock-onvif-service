"""
Log management utilities for FFmpeg and ONVIF logs
Handles log rotation and cleanup
"""
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


class LogManager:
    """Manages log files with rotation and cleanup"""

    # Log configuration
    MAX_LOG_SIZE = 3 * 1024 * 1024  # 3 MB
    BACKUP_COUNT = 3  # Keep 3 backup files
    LOG_RETENTION_DAYS = 3  # Delete logs older than 3 days

    @staticmethod
    def create_rotating_logger(log_path, max_bytes=None, backup_count=None):
        """
        Create a logger with rotating file handler

        Args:
            log_path: Path to log file
            max_bytes: Maximum size in bytes before rotation (default: 10MB)
            backup_count: Number of backup files to keep (default: 3)

        Returns:
            tuple: (logger, file_handler)
        """
        if max_bytes is None:
            max_bytes = LogManager.MAX_LOG_SIZE
        if backup_count is None:
            backup_count = LogManager.BACKUP_COUNT

        # Create logger
        logger = logging.getLogger(str(log_path))
        logger.setLevel(logging.INFO)
        logger.handlers.clear()  # Clear any existing handlers

        # Create rotating file handler
        handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )

        # Set format
        formatter = logging.Formatter('%(message)s')
        handler.setFormatter(formatter)

        logger.addHandler(handler)
        logger.propagate = False  # Don't propagate to root logger

        return logger, handler

    @staticmethod
    def cleanup_old_logs(log_directory, days=None):
        """
        Delete log files older than specified days

        Args:
            log_directory: Directory containing log files
            days: Number of days to retain (default: 3)

        Returns:
            dict: Statistics about cleaned files
        """
        if days is None:
            days = LogManager.LOG_RETENTION_DAYS

        log_dir = Path(log_directory)
        if not log_dir.exists():
            return {"deleted": 0, "kept": 0, "errors": 0}

        cutoff_time = time.time() - (days * 86400)  # days * seconds_per_day
        stats = {"deleted": 0, "kept": 0, "errors": 0, "freed_bytes": 0}

        # Find all log files (including rotated ones)
        log_patterns = ['*.log', '*.log.*']
        log_files = []
        for pattern in log_patterns:
            log_files.extend(log_dir.glob(pattern))

        for log_file in log_files:
            try:
                # Get file modification time
                file_mtime = log_file.stat().st_mtime

                if file_mtime < cutoff_time:
                    # File is older than retention period
                    file_size = log_file.stat().st_size
                    log_file.unlink()
                    stats["deleted"] += 1
                    stats["freed_bytes"] += file_size
                    print(f"  🗑️  Deleted old log: {log_file.name} "
                          f"(age: {(time.time() - file_mtime) / 86400:.1f} days)")
                else:
                    stats["kept"] += 1

            except Exception as e:
                stats["errors"] += 1
                print(f"  ⚠️  Error processing {log_file.name}: {e}")

        return stats

    @staticmethod
    def get_log_directory_stats(log_directory):
        """
        Get statistics about log directory

        Args:
            log_directory: Directory containing log files

        Returns:
            dict: Statistics about the directory
        """
        log_dir = Path(log_directory)
        if not log_dir.exists():
            return {
                "total_files": 0,
                "total_size_bytes": 0,
                "total_size_mb": 0,
                "oldest_file": None,
                "newest_file": None
            }

        log_files = list(log_dir.glob('*.log*'))

        if not log_files:
            return {
                "total_files": 0,
                "total_size_bytes": 0,
                "total_size_mb": 0,
                "oldest_file": None,
                "newest_file": None
            }

        total_size = sum(f.stat().st_size for f in log_files)
        oldest = min(log_files, key=lambda f: f.stat().st_mtime)
        newest = max(log_files, key=lambda f: f.stat().st_mtime)

        return {
            "total_files": len(log_files),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "oldest_file": {
                "name": oldest.name,
                "age_days": round((time.time() - oldest.stat().st_mtime) / 86400, 1)
            },
            "newest_file": {
                "name": newest.name,
                "age_days": round((time.time() - newest.stat().st_mtime) / 86400, 1)
            }
        }

    @staticmethod
    def cleanup_all_log_directories(logs_dir):
        """
        Clean up all log directories (ffmpeg and onvif)

        Args:
            logs_dir: Base logs directory path

        Returns:
            dict: Combined statistics
        """
        logs_path = Path(logs_dir)

        print("\n" + "="*60)
        print(" LOG CLEANUP STARTED")
        print("="*60)

        total_stats = {
            "ffmpeg_logs": {"deleted": 0, "kept": 0, "errors": 0, "freed_bytes": 0},
            "onvif_logs": {"deleted": 0, "kept": 0, "errors": 0, "freed_bytes": 0}
        }

        # Clean FFmpeg logs
        ffmpeg_log_dir = logs_path / "ffmpeg"
        if ffmpeg_log_dir.exists():
            print(f"\n📁 Cleaning FFmpeg logs (older than {LogManager.LOG_RETENTION_DAYS} days)...")
            total_stats["ffmpeg_logs"] = LogManager.cleanup_old_logs(ffmpeg_log_dir)

        # Clean ONVIF logs
        onvif_log_dir = logs_path / "onvif"
        if onvif_log_dir.exists():
            print(f"\n📁 Cleaning ONVIF logs (older than {LogManager.LOG_RETENTION_DAYS} days)...")
            total_stats["onvif_logs"] = LogManager.cleanup_old_logs(onvif_log_dir)

        # Summary
        total_deleted = total_stats["ffmpeg_logs"]["deleted"] + total_stats["onvif_logs"]["deleted"]
        total_kept = total_stats["ffmpeg_logs"]["kept"] + total_stats["onvif_logs"]["kept"]
        total_freed = total_stats["ffmpeg_logs"]["freed_bytes"] + total_stats["onvif_logs"]["freed_bytes"]

        print("\n" + "="*60)
        print(" CLEANUP SUMMARY")
        print("="*60)
        print(f"  Deleted: {total_deleted} files")
        print(f"  Kept: {total_kept} files")
        print(f"  Space freed: {total_freed / (1024 * 1024):.2f} MB")
        print("="*60 + "\n")

        return total_stats
