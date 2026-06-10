"""Periodic orphan scanner for ``./data/videos`` and ``./data/snapshots``.

Files in those directories that are not referenced by any row in the cameras
table get deleted. A grace period (default 5 minutes) avoids racing with
in-flight transcodes — only files older than the grace window are considered.

This is the back-stop for ``data/`` retention: under normal operation
``delete_camera`` already removes its files, but crashes, manual deletes, or
prior bugs can leave .mp4 / .jpg behind. The scanner is what guarantees
``data/`` does not grow unbounded.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import (
    DATA_CLEANUP_INTERVAL_HOURS,
    DATA_ORPHAN_GRACE_SECONDS,
    SNAPSHOTS_DIR,
    VIDEOS_DIR,
)
from app.db import get_repository

logger = logging.getLogger(__name__)


@dataclass
class CleanupStats:
    videos_deleted: int = 0
    snapshots_deleted: int = 0
    bytes_freed: int = 0
    errors: int = 0


def _build_expected_sets() -> tuple[set[str], set[str]]:
    """Compute the set of filenames that legitimately belong to live cameras."""
    repo = get_repository()
    expected_videos: set[str] = set()
    expected_snapshots: set[str] = set()

    seen_shared: set[str] = set()
    for rec in repo.all():
        vp = Path(rec.video_path)
        expected_videos.add(vp.name)
        if rec.sub_profile:
            expected_videos.add(vp.name.replace(".mp4", "_sub.mp4"))

        if rec.shared_video_id:
            if rec.shared_video_id not in seen_shared:
                expected_snapshots.add(f"{rec.shared_video_id}.jpg")
                seen_shared.add(rec.shared_video_id)
        else:
            expected_snapshots.add(f"{rec.camera_id}.jpg")

    return expected_videos, expected_snapshots


def scan_orphans(grace_seconds: int = DATA_ORPHAN_GRACE_SECONDS,
                 dry_run: bool = False) -> CleanupStats:
    """Delete files in ``data/videos`` and ``data/snapshots`` that no DB row claims.

    Args:
        grace_seconds: skip files whose mtime is within the last ``grace_seconds``.
        dry_run: when True, log what would be deleted but don't actually remove.
    """
    stats = CleanupStats()
    expected_videos, expected_snapshots = _build_expected_sets()
    cutoff = time.time() - grace_seconds

    def _sweep(directory: Path, glob: str, expected: set[str], kind: str) -> None:
        if not directory.exists():
            return
        for path in directory.glob(glob):
            if not path.is_file():
                continue
            try:
                st = path.stat()
            except OSError:
                stats.errors += 1
                continue
            if st.st_mtime > cutoff:
                continue  # too new — possibly in flight
            if path.name in expected:
                continue  # legitimately owned by a camera
            # Orphan
            logger.info("Orphan %s: %s (%.1f KB, age %.0fs)",
                        kind, path.name, st.st_size / 1024,
                        time.time() - st.st_mtime)
            if dry_run:
                continue
            try:
                path.unlink()
                stats.bytes_freed += st.st_size
                if kind == "video":
                    stats.videos_deleted += 1
                else:
                    stats.snapshots_deleted += 1
            except OSError as e:
                logger.warning("Failed to delete orphan %s: %s", path, e)
                stats.errors += 1

    _sweep(VIDEOS_DIR, "*.mp4", expected_videos, "video")
    _sweep(SNAPSHOTS_DIR, "*.jpg", expected_snapshots, "snapshot")

    logger.info(
        "Orphan scan complete: videos=%d snapshots=%d freed=%.2fMB errors=%d%s",
        stats.videos_deleted, stats.snapshots_deleted,
        stats.bytes_freed / (1024 * 1024), stats.errors,
        " (dry-run)" if dry_run else "",
    )
    return stats


# ── Scheduler ──────────────────────────────────────────────────────────────
class DataCleanupScheduler:
    def __init__(self, interval_hours: float = DATA_CLEANUP_INTERVAL_HOURS):
        self.interval_seconds = max(60.0, interval_hours * 3600)
        self._stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive() and not self._stop_event.is_set()

    def _loop(self) -> None:
        logger.info("Data cleanup scheduler started (interval=%.2fh)",
                    self.interval_seconds / 3600)
        while not self._stop_event.is_set():
            if self._stop_event.wait(self.interval_seconds):
                break
            try:
                scan_orphans()
            except Exception as e:
                logger.warning("Data cleanup tick failed: %s", e)
        logger.info("Data cleanup scheduler stopped")

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self.thread = threading.Thread(
            target=self._loop, daemon=True, name="DataCleanupScheduler"
        )
        self.thread.start()
        # Run one immediate scan to catch crash-leftover state at boot
        try:
            scan_orphans()
        except Exception as e:
            logger.warning("Initial data cleanup failed: %s", e)

    def stop(self) -> None:
        if self.thread is None:
            return
        self._stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        self.thread = None


_scheduler: DataCleanupScheduler | None = None


def start_data_cleanup_scheduler(interval_hours: float = DATA_CLEANUP_INTERVAL_HOURS) -> DataCleanupScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = DataCleanupScheduler(interval_hours)
    _scheduler.start()
    return _scheduler


def stop_data_cleanup_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.stop()
