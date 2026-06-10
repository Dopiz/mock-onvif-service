"""Watchdog: periodically check that each camera's FFmpeg/ONVIF subprocess is
still alive and re-spawn it if not.

Backoff: per-camera restart counter resets when uptime > 5 minutes. After
``WATCHDOG_MAX_RESTARTS`` consecutive failures the camera is parked (no further
auto-restart) — it remains in the registry but is logged loudly so a human
can investigate.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.camera_lifecycle import RuntimeState, get_registry
from app.config import (
    MEDIAMTX_RTSP_PORT,
    WATCHDOG_MAX_RESTARTS,
)
from app.process_supervisor import (
    is_process_alive,
    release_camera_loggers,
    start_ffmpeg,
    start_onvif_subprocess,
)
from app.utils import get_server_ip

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


def _restart_onvif(state: RuntimeState) -> bool:
    rec = state.record
    rtsp_url = f"rtsp://{get_server_ip()}:{MEDIAMTX_RTSP_PORT}/{rec.camera_id}"
    try:
        # Release stale log handlers — start_onvif_subprocess will recreate them
        release_camera_loggers(rec.camera_id)
        state.onvif_pid = start_onvif_subprocess(
            camera_id=rec.camera_id,
            onvif_port=rec.onvif_port,
            rtsp_url=rtsp_url,
            width=state.width,
            height=state.height,
            fps=state.fps,
            video_bitrate_kbps=state.video_bitrate_kbps,
            audio_bitrate_kbps=int(rec.video_params.get("audio_bitrate", "128k").rstrip("k")),
            shared_video_id=rec.shared_video_id,
            sub_profile=rec.sub_profile,
            camera_name=rec.manufacturer,
            camera_ip=rec.camera_ip,
        )
    except Exception as e:
        logger.error("Watchdog: failed to restart ONVIF for %s: %s", rec.camera_id[:8], e)
        return False
    return True


def _check_once() -> None:
    registry = get_registry()
    now = time.monotonic()
    for state in registry.all():
        cam_id = state.record.camera_id
        tracker = _trackers.setdefault(cam_id, _RestartTracker())
        if tracker.parked:
            continue

        # Cool the counter if last restart was long ago
        if tracker.count and now - tracker.last_restart_ts > 300:
            tracker.count = 0

        if tracker.count >= WATCHDOG_MAX_RESTARTS:
            tracker.parked = True
            logger.error("Watchdog: camera %s parked after %d failed restarts",
                         cam_id[:8], tracker.count)
            continue

        needs_restart = False
        if not is_process_alive(state.ffmpeg_pid):
            logger.warning("Watchdog: ffmpeg dead for %s — restarting", cam_id[:8])
            if _restart_ffmpeg_main(state):
                needs_restart = True
        if state.ffmpeg_pid_sub and not is_process_alive(state.ffmpeg_pid_sub):
            logger.warning("Watchdog: sub-ffmpeg dead for %s — restarting", cam_id[:8])
            if _restart_ffmpeg_sub(state):
                needs_restart = True
        # ONVIF subprocess check only meaningful in subprocess mode (PID > 0)
        if state.onvif_pid and not is_process_alive(state.onvif_pid):
            logger.warning("Watchdog: ONVIF dead for %s — restarting", cam_id[:8])
            if _restart_onvif(state):
                needs_restart = True

        if needs_restart:
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


def start_watchdog(interval: int = 15) -> None:
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
