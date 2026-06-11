"""In-memory runtime registry for live cameras.

PIDs and other ephemeral state are deliberately NOT persisted — on startup
``restore_cameras`` re-spawns subprocesses and registers fresh state here.
Split out of ``camera_lifecycle`` so consumers like the watchdog don't need to
import the whole orchestration layer.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from app.db import CameraRecord


@dataclass
class RuntimeState:
    """In-memory runtime view of a live camera (PIDs etc, not persisted).

    ``onvif_pid`` is ``None`` in dispatcher mode (no subprocess exists).
    """
    record: CameraRecord
    ffmpeg_pid: int
    onvif_pid: Optional[int]
    ffmpeg_pid_sub: Optional[int] = None
    # Width/height/fps materialised for cheap lookup in list_cameras
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_bitrate_kbps: int = 0


class RuntimeRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, RuntimeState] = {}

    def get(self, camera_id: str) -> Optional[RuntimeState]:
        with self._lock:
            return self._state.get(camera_id)

    def put(self, state: RuntimeState) -> None:
        with self._lock:
            self._state[state.record.camera_id] = state

    def remove(self, camera_id: str) -> Optional[RuntimeState]:
        with self._lock:
            return self._state.pop(camera_id, None)

    def all(self) -> list[RuntimeState]:
        with self._lock:
            return list(self._state.values())

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._state.keys())

    def used_ports(self) -> set[int]:
        with self._lock:
            return {s.record.onvif_port for s in self._state.values()}


_registry = RuntimeRegistry()


def get_registry() -> RuntimeRegistry:
    return _registry
