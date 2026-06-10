"""Watchdog: periodically check that each camera's FFmpeg/ONVIF subprocess is
still alive and re-spawn it if not.

Backoff: every restart *attempt* (successful or failed) increments a per-camera
counter; the counter resets once the camera has gone
``WATCHDOG_RESTART_COOLDOWN_SECONDS`` without another attempt. After
``WATCHDOG_MAX_RESTARTS`` consecutive attempts the camera is parked (no further
auto-restart) — it remains in the registry but is logged loudly so a human
can investigate.

ONVIF liveness/restart is delegated to the active :mod:`app.onvif_endpoint`
strategy; in dispatcher mode there is no subprocess, so nothing to check.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import (
    WATCHDOG_INTERVAL_SECONDS,
    WATCHDOG_MAX_RESTARTS,
    WATCHDOG_RESTART_COOLDOWN_SECONDS,
)
from app.onvif_endpoint import get_onvif_endpoint
from app.process_supervisor import is_process_alive, start_ffmpeg
from app.runtime_registry import RuntimeState, get_registry

logger = logging.getLogger(__name__)


@dataclass
class _RestartTracker:
    count: int = 0
    last_restart_ts: float = 0.0
    parked: bool = False


_trackers: dict[str, _RestartTracker] = {}
_stop_event = threading.Event()
_thread: Optional[threading.Thread] = None


def _restart_ffmpeg_main(state: RuntimeState) -> bool:
    """Re-spawn the main FFmpeg streaming process for an existing camera."""
    rec = state.record
    video_path = Path(rec.video_path)
    if not video_path.exists():
        logger.error("Watchdog: video file gone for %s; cannot restart", rec.camera_id[:8])
        return False
    try:
        state.ffmpeg_pid = start_ffmpeg(video_path, rec.camera_id)
    except Exception as e:
        logger.error("Watchdog: failed to restart ffmpeg for %s: %s", rec.camera_id[:8], e)
        return False
    return True


def _restart_ffmpeg_sub(state: RuntimeState) -> bool:
    rec = state.record
    sub_path = Path(str(rec.video_path).replace(".mp4", "_sub.mp4"))
    if not sub_path.exists():
        logger.warning("Watchdog: sub-stream file missing for %s", rec.camera_id[:8])
        return False
    try:
        state.ffmpeg_pid_sub = start_ffmpeg(sub_path, f"{rec.camera_id}_sub")
    except Exception as e:
        logger.error("Watchdog: failed to restart sub ffmpeg for %s: %s", rec.camera_id[:8], e)
        return False
    return True


def _check_once() -> None:
    registry = get_registry()
    endpoint = get_onvif_endpoint()
    now = time.monotonic()
    for state in registry.all():
        camera_id = state.record.camera_id
        tracker = _trackers.setdefault(camera_id, _RestartTracker())
        if tracker.parked:
            continue

        # Cool the counter if the last attempt was long ago
        if tracker.count and now - tracker.last_restart_ts > WATCHDOG_RESTART_COOLDOWN_SECONDS:
            tracker.count = 0

        if tracker.count >= WATCHDOG_MAX_RESTARTS:
            tracker.parked = True
            logger.error("Watchdog: camera %s parked after %d restart attempts",
                         camera_id[:8], tracker.count)
            continue

        restarted = False
        restart_failed = False

        if not is_process_alive(state.ffmpeg_pid):
            logger.warning("Watchdog: ffmpeg dead for %s — restarting", camera_id[:8])
            if _restart_ffmpeg_main(state):
                restarted = True
            else:
                restart_failed = True
        if state.ffmpeg_pid_sub and not is_process_alive(state.ffmpeg_pid_sub):
            logger.warning("Watchdog: sub-ffmpeg dead for %s — restarting", camera_id[:8])
            if _restart_ffmpeg_sub(state):
                restarted = True
            else:
                restart_failed = True
        if not endpoint.is_alive(state):
            logger.warning("Watchdog: ONVIF dead for %s — restarting", camera_id[:8])
            if endpoint.restart(state):
                restarted = True
            else:
                restart_failed = True

        # Count failed attempts too, so persistently-broken cameras eventually park.
        if restarted or restart_failed:
            tracker.count += 1
            tracker.last_restart_ts = now


def _loop(interval: int) -> None:
    logger.info("Watchdog started (interval=%ds, max_restarts=%d)",
                interval, WATCHDOG_MAX_RESTARTS)
    # Skip the very first interval — restore_cameras is still warming up
    if _stop_event.wait(interval):
        return
    while not _stop_event.is_set():
        try:
            _check_once()
        except Exception as e:
            logger.exception("Watchdog tick failed: %s", e)
        if _stop_event.wait(interval):
            break
    logger.info("Watchdog stopped")


def start_watchdog(interval: int = WATCHDOG_INTERVAL_SECONDS) -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, args=(interval,), daemon=True, name="Watchdog")
    _thread.start()


def stop_watchdog() -> None:
    if _thread is None:
        return
    _stop_event.set()
    _thread.join(timeout=5)
