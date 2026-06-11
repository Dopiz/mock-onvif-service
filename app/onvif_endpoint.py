"""ONVIF endpoint strategies.

Two ways to serve a camera's ONVIF SOAP API:

- :class:`SubprocessOnvifEndpoint` (default): one ``onvif_server.py`` process
  per camera, supervised via PIDs.
- :class:`DispatcherOnvifEndpoint` (``ONVIF_DISPATCHER_ENABLED=true``): a single
  in-process Flask app served from one werkzeug server per port. There is no
  subprocess, hence no PID and no liveness/restart concern.

The active strategy is selected once at import time from config. Callers
(camera_lifecycle, watchdog) hold the strategy object instead of branching on
``ONVIF_DISPATCHER_ENABLED`` or relying on PID sentinel values.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from app.config import MEDIAMTX_RTSP_PORT, ONVIF_DISPATCHER_ENABLED
from app.schemas import VideoParams
from app.utils import get_server_ip

if TYPE_CHECKING:
    from app.runtime_registry import RuntimeState

logger = logging.getLogger(__name__)


def extract_onvif_params(params: VideoParams) -> tuple[int, int, float, int, int]:
    """Return ``(width, height, fps, video_kbps, audio_kbps)`` for ONVIF use."""
    video_kbps = int(float(params.video_bitrate.rstrip("M")) * 1024)
    audio_kbps = int(params.audio_bitrate.rstrip("k"))
    return params.width, params.height, params.fps, video_kbps, audio_kbps


def build_rtsp_url(camera_id: str) -> str:
    return f"rtsp://{get_server_ip()}:{MEDIAMTX_RTSP_PORT}/{camera_id}"


class SubprocessOnvifEndpoint:
    """One onvif_server.py subprocess per camera."""

    def start(
        self,
        *,
        camera_id: str,
        onvif_port: int,
        width: int,
        height: int,
        fps: float,
        video_bitrate_kbps: int,
        audio_bitrate_kbps: int,
        shared_video_id: Optional[str],
        sub_profile: bool,
        manufacturer: str,
        camera_ip: Optional[str],
    ) -> Optional[int]:
        from app.process_supervisor import start_onvif_subprocess
        return start_onvif_subprocess(
            camera_id=camera_id,
            onvif_port=onvif_port,
            rtsp_url=build_rtsp_url(camera_id),
            width=width, height=height, fps=fps,
            video_bitrate_kbps=video_bitrate_kbps,
            audio_bitrate_kbps=audio_bitrate_kbps,
            shared_video_id=shared_video_id,
            sub_profile=sub_profile,
            manufacturer=manufacturer,
            camera_ip=camera_ip,
        )

    def stop(self, camera_id: str, pid: Optional[int]) -> None:
        from app.process_supervisor import stop_onvif
        if pid:
            stop_onvif(pid)

    def is_alive(self, state: "RuntimeState") -> bool:
        from app.process_supervisor import is_process_alive
        return bool(state.onvif_pid) and is_process_alive(state.onvif_pid)

    def restart(self, state: "RuntimeState") -> bool:
        """Re-spawn the ONVIF subprocess for an existing camera."""
        from app.process_supervisor import release_camera_loggers
        rec = state.record
        try:
            # Release stale log handlers — start() will recreate them
            release_camera_loggers(rec.camera_id)
            width, height, fps, vkbps, akbps = extract_onvif_params(rec.parsed_params())
            state.onvif_pid = self.start(
                camera_id=rec.camera_id,
                onvif_port=rec.onvif_port,
                width=width, height=height, fps=fps,
                video_bitrate_kbps=vkbps,
                audio_bitrate_kbps=akbps,
                shared_video_id=rec.shared_video_id,
                sub_profile=rec.sub_profile,
                manufacturer=rec.manufacturer,
                camera_ip=rec.camera_ip,
            )
        except Exception as e:
            logger.error("Failed to restart ONVIF for %s: %s", rec.camera_id[:8], e)
            return False
        return True

    def stop_all(self, states: list["RuntimeState"]) -> None:
        from app.process_supervisor import terminate_many
        terminate_many([s.onvif_pid for s in states if s.onvif_pid], kill_group=True)


class DispatcherOnvifEndpoint:
    """All cameras served by the single in-process dispatcher (no subprocess)."""

    def start(
        self,
        *,
        camera_id: str,
        onvif_port: int,
        width: int,
        height: int,
        fps: float,
        video_bitrate_kbps: int,
        audio_bitrate_kbps: int,
        shared_video_id: Optional[str],
        sub_profile: bool,
        manufacturer: str,
        camera_ip: Optional[str],
    ) -> Optional[int]:
        from app.onvif_dispatcher import get_dispatcher
        from app.onvif_handlers import OnvifContext
        ctx = OnvifContext(
            camera_id=camera_id,
            rtsp_url=build_rtsp_url(camera_id),
            width=width, height=height, fps=fps,
            video_bitrate_kbps=video_bitrate_kbps,
            audio_bitrate_kbps=audio_bitrate_kbps,
            sub_profile=sub_profile,
            manufacturer=manufacturer,
            shared_video_id=shared_video_id,
            server_port=onvif_port,
        )
        get_dispatcher().add_camera(ctx, bind_ip=camera_ip)
        return None  # in-process — no subprocess PID

    def stop(self, camera_id: str, pid: Optional[int] = None) -> None:
        from app.onvif_dispatcher import get_dispatcher
        get_dispatcher().remove_camera(camera_id)

    def is_alive(self, state: "RuntimeState") -> bool:
        return True  # in-process; nothing that can die independently

    def restart(self, state: "RuntimeState") -> bool:
        return True  # nothing to restart

    def stop_all(self, states: list["RuntimeState"]) -> None:
        from app.onvif_dispatcher import get_dispatcher
        try:
            get_dispatcher().stop_all()
        except Exception as e:
            logger.warning("Failed to stop ONVIF dispatcher: %s", e)


# Selected once from config; both implementations are stateless.
_endpoint = DispatcherOnvifEndpoint() if ONVIF_DISPATCHER_ENABLED else SubprocessOnvifEndpoint()


def get_onvif_endpoint():
    return _endpoint
