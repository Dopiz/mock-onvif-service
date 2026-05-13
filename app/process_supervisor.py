"""Subprocess lifecycle helpers: spawn, log-pipe, terminate, reap.

Centralised so logger handlers can be cleanly closed on camera deletion,
preventing the FD leak the previous design suffered from.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from app.config import (
    FFMPEG_LOGS_DIR,
    ONVIF_LOGS_DIR,
    PROCESS_KILL_GRACE_SECONDS,
)
from app.exceptions import FFmpegStartError, OnvifStartError
from app.ffmpeg_builder import build_streaming_cmd
from app.log_manager import LogManager

logger = logging.getLogger(__name__)


# Background log-reader threads, keyed by the log path so we can join on cleanup.
_log_threads: dict[str, threading.Thread] = {}
_log_threads_lock = threading.Lock()


def _pipe_stdout_to_logger(process: subprocess.Popen, target_logger: logging.Logger,
                           key: str) -> None:
    try:
        if process.stdout is None:
            return
        for line in process.stdout:
            if line:
                target_logger.info(line.rstrip())
    except Exception as e:
        target_logger.error("Error reading subprocess output: %s", e)
    finally:
        with _log_threads_lock:
            _log_threads.pop(key, None)


def _start_log_thread(process: subprocess.Popen, log_path: Path, key: str) -> None:
    target_logger, _ = LogManager.create_rotating_logger(log_path)
    t = threading.Thread(
        target=_pipe_stdout_to_logger,
        args=(process, target_logger, key),
        daemon=True,
        name=f"log-pipe-{key}",
    )
    with _log_threads_lock:
        _log_threads[key] = t
    t.start()


def start_ffmpeg(video_path: Path, camera_id: str) -> int:
    """Start an FFmpeg loop-stream process. Returns PID."""
    FFMPEG_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = FFMPEG_LOGS_DIR / f"ffmpeg_{camera_id[:8]}.log"
    cmd = build_streaming_cmd(video_path, camera_id)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            universal_newlines=True,
            bufsize=1,
        )
    except OSError as e:
        raise FFmpegStartError(f"Failed to spawn FFmpeg: {e}") from e

    _start_log_thread(proc, log_path, key=f"ffmpeg:{camera_id}")
    logger.info("FFmpeg started for camera %s (PID %d)", camera_id[:8], proc.pid)
    return proc.pid


def start_onvif_subprocess(
    *,
    camera_id: str,
    onvif_port: int,
    rtsp_url: str,
    width: int,
    height: int,
    fps: float,
    video_bitrate_kbps: int,
    audio_bitrate_kbps: int,
    shared_video_id: Optional[str] = None,
    sub_profile: bool = False,
    camera_name: str = "MockONVIF",
    camera_ip: Optional[str] = None,
) -> int:
    """Spawn a per-camera onvif_server.py process. Returns PID."""
    ONVIF_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = ONVIF_LOGS_DIR / f"onvif_{camera_id[:8]}.log"

    venv_python = os.path.join(os.getcwd(), ".venv", "bin", "python3")
    if not os.path.exists(venv_python):
        venv_python = "python3"
    onvif_server_path = os.path.join(os.getcwd(), "onvif_server.py")

    env = os.environ.copy()
    env.update({
        "ONVIF_CAMERA_ID": camera_id,
        "ONVIF_RTSP_URL": rtsp_url,
        "ONVIF_PORT": str(onvif_port),
        "ONVIF_SERVER_IP": camera_ip or "0.0.0.0",
        "ONVIF_WIDTH": str(width),
        "ONVIF_HEIGHT": str(height),
        "ONVIF_FPS": str(fps),
        "ONVIF_VIDEO_BITRATE_KBPS": str(video_bitrate_kbps),
        "ONVIF_AUDIO_BITRATE_KBPS": str(audio_bitrate_kbps),
        "ONVIF_SUB_PROFILE": "true" if sub_profile else "false",
        "ONVIF_MANUFACTURER": camera_name,
    })
    if shared_video_id:
        env["ONVIF_SHARED_VIDEO_ID"] = shared_video_id

    try:
        proc = subprocess.Popen(
            [venv_python, onvif_server_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=os.getcwd(),
            start_new_session=True,
            universal_newlines=True,
            bufsize=1,
        )
    except OSError as e:
        raise OnvifStartError(f"Failed to spawn ONVIF subprocess: {e}") from e

    _start_log_thread(proc, log_path, key=f"onvif:{camera_id}")

    # Give it a moment, then verify it's still alive
    time.sleep(1)
    if proc.poll() is not None:
        try:
            with open(log_path, "r") as f:
                tail = f.read()[-1000:]
        except Exception:
            tail = "<unreadable>"
        raise OnvifStartError(f"ONVIF subprocess exited immediately. Log tail: {tail}")

    logger.info("ONVIF server started for camera %s on port %d (PID %d)",
                camera_id[:8], onvif_port, proc.pid)
    return proc.pid


def _terminate_and_reap(pid: int, *, kill_group: bool = False) -> None:
    """SIGTERM → wait → SIGKILL → waitpid (no zombies)."""
    try:
        if kill_group:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass

    time.sleep(PROCESS_KILL_GRACE_SECONDS)

    try:
        os.kill(pid, 0)  # exists?
        if kill_group:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)
    except (ProcessLookupError, PermissionError, OSError):
        pass

    # Reap to prevent zombie accumulation
    try:
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        if reaped == 0:
            time.sleep(0.2)
            os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass


def stop_ffmpeg(pid: int) -> None:
    if pid:
        _terminate_and_reap(pid, kill_group=False)


def stop_onvif(pid: int) -> None:
    if pid:
        _terminate_and_reap(pid, kill_group=True)


def release_camera_loggers(camera_id: str) -> None:
    """Close rotating-file handlers and drop daemon threads for a camera."""
    LogManager.close_logger(FFMPEG_LOGS_DIR / f"ffmpeg_{camera_id[:8]}.log")
    LogManager.close_logger(ONVIF_LOGS_DIR / f"onvif_{camera_id[:8]}.log")
    with _log_threads_lock:
        _log_threads.pop(f"ffmpeg:{camera_id}", None)
        _log_threads.pop(f"onvif:{camera_id}", None)


def reap_defunct_children() -> None:
    """Best-effort reap of any orphaned child processes (called on startup)."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                break
            logger.debug("Reaped defunct PID %d", pid)
        except ChildProcessError:
            break
        except Exception:
            break


def is_process_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False
