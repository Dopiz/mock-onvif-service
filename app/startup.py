#!/usr/bin/env python3
"""
Startup utilities for Mock Camera Service
Handles automatic startup of dependency services
"""

from app.log_cleanup_scheduler import start_log_cleanup_scheduler


def startup_dependencies():
    """Start required services"""
    print("Starting services...")

    # Start log cleanup scheduler (runs every 24 hours, keeps logs for 3 days)
    print("Starting log cleanup scheduler...")
    try:
        start_log_cleanup_scheduler(logs_dir="./logs", interval_hours=24)
        print("✓ Log cleanup scheduler started (runs every 24 hours, keeps logs for 3 days)")
    except Exception as e:
        print(f"⚠ Failed to start log cleanup scheduler: {e}")


if __name__ == '__main__':
    # Test startup
    startup_dependencies()
    print("\n✓ Startup complete")
