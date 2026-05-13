"""Camera lifecycle: create, delete, restore. ExitStack-based rollback.

The previous implementation duplicated create_camera and a batch variant, each
with ~120 lines of nested rollback. This consolidates them into a single
:func:`create_camera` plus a thin batch wrapper.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import (
    BATCH_MAX_WORKERS,
    CAMERAS_DIR,
    MACVLAN_ENABLED,
    MEDIAMTX_RTSP_PORT,
    ONVIF_DISPATCHER_ENABLED,
    SNAPSHOTS_DIR,
    VIDEOS_DIR,
    ensure_dirs,
)
from app.db import CameraRecord, CameraRepository, get_repository, migrate_yaml_configs
from app.exceptions import (
    CameraNotFoundError,
    MacvlanError,
    VideoSaveError,
)
from app.port_allocator import PortAllocator, get_default_allocator
from app.process_supervisor import (
    reap_defunct_children,
    release_camera_loggers,
    start_ffmpeg,
    start_onvif_subprocess,
    stop_ffmpeg,
    stop_onvif,
)
from app.schemas import EditParams, VideoParams
from app.transcoder import generate_snapshot, transcode
from app.utils import get_server_ip

logger = logging.getLogger(__name__)


# ── Runtime registry ───────────────────────────────────────────────────────
@dataclass
class RuntimeState:
    """In-memory runtime view of a live camera (PIDs etc, not persisted)."""
    record: CameraRecord
    ffmpeg_pid: int
    onvif_pid: int
    ffmpeg_pid_sub: Optional[int] = None
    # Width/height/fps materialised for cheap lookup in list_cameras
    width: int = 0
    height: int = 0
    fps: float = 0.0
    video_bitrate_kbps: int = 0

    def to_info_dict(self) -> dict:
        rec = self.record
        server_ip = get_server_ip()
        onvif_url = (
            f"{rec.camera_ip}:80"
            if MACVLAN_ENABLED and rec.camera_ip
            else f"{server_ip}:{rec.onvif_port}"
        )
        info = {
            "id": rec.camera_id,
            "video_path": rec.video_path,
            "rtsp_port": MEDIAMTX_RTSP_PORT,
            "onvif_port": rec.onvif_port,
            "ffmpeg_pid": self.ffmpeg_pid,
            "onvif_pid": self.onvif_pid,
            "rtsp_url": f"rtsp://{server_ip}:{MEDIAMTX_RTSP_PORT}/{rec.camera_id}",
            "onvif_url": onvif_url,
            "username": "test",
            "password": "pass",
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "video_bitrate_mbps": round(self.video_bitrate_kbps / 1000, 2),
            "sub_profile": rec.sub_profile,
            "manufacturer": rec.manufacturer,
            "created_at": rec.created_at,
        }
        if rec.shared_video_id:
            info["shared_video_id"] = rec.shared_video_id
        if MACVLAN_ENABLED and rec.camera_ip:
            info["camera_ip"] = rec.camera_ip
        if rec.sub_profile and self.ffmpeg_pid_sub:
            info["ffmpeg_pid_sub"] = self.ffmpeg_pid_sub
            info["rtsp_url_sub"] = f"rtsp://{server_ip}:{MEDIAMTX_RTSP_PORT}/{rec.camera_id}_sub"
        return info


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


# ── Helpers ────────────────────────────────────────────────────────────────
def _extract_onvif_params(params: VideoParams) -> tuple[int, int, float, int, int]:
    video_kbps = int(float(params.video_bitrate.rstrip("M")) * 1024)
    audio_kbps = int(params.audio_bitrate.rstrip("k"))
    return params.width, params.height, params.fps, video_kbps, audio_kbps


def _macvlan_manager():
    """Lazy-load the macvlan manager so non-macvlan mode skips the import."""
    from app.macvlan_manager import MacvlanManager
    from app.config import (
        MACVLAN_DHCP,
        MACVLAN_GATEWAY,
        MACVLAN_IP_END,
        MACVLAN_IP_START,
        MACVLAN_PARENT_IFACE,
        MACVLAN_SUBNET,
    )
    if not hasattr(_macvlan_manager, "_inst"):
        _macvlan_manager._inst = MacvlanManager(
            subnet=MACVLAN_SUBNET,
            gateway=MACVLAN_GATEWAY,
            ip_start=MACVLAN_IP_START,
            ip_end=MACVLAN_IP_END,
            parent_iface=MACVLAN_PARENT_IFACE,
            use_dhcp=MACVLAN_DHCP,
        )
    return _macvlan_manager._inst


def _save_upload(video_file, destination: Path) -> None:
    try:
        video_file.save(str(destination))
    except Exception as e:
        raise VideoSaveError(f"Failed to save video: {e}") from e


def _start_onvif(
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
    camera_name: str,
    camera_ip: Optional[str],
) -> int:
    """Start an ONVIF endpoint. Returns PID (or 0 in dispatcher mode)."""
    rtsp_url = f"rtsp://{get_server_ip()}:{MEDIAMTX_RTSP_PORT}/{camera_id}"
    if ONVIF_DISPATCHER_ENABLED:
        from app.onvif_dispatcher import get_dispatcher
        from app.onvif_handlers import OnvifContext
        ctx = OnvifContext(
            camera_id=camera_id,
            rtsp_url=rtsp_url,
            width=width, height=height, fps=fps,
            video_bitrate_kbps=video_bitrate_kbps,
            audio_bitrate_kbps=audio_bitrate_kbps,
            sub_profile=sub_profile,
            manufacturer=camera_name,
            shared_video_id=shared_video_id,
            server_port=onvif_port,
        )
        get_dispatcher().add_camera(ctx, bind_ip=camera_ip)
        return 0  # No subprocess PID in dispatcher mode
    return start_onvif_subprocess(
        camera_id=camera_id,
        onvif_port=onvif_port,
        rtsp_url=rtsp_url,
        width=width, height=height, fps=fps,
        video_bitrate_kbps=video_bitrate_kbps,
        audio_bitrate_kbps=audio_bitrate_kbps,
        shared_video_id=shared_video_id,
        sub_profile=sub_profile,
        camera_name=camera_name,
        camera_ip=camera_ip,
    )


def _stop_onvif(camera_id: str, pid: int) -> None:
    if ONVIF_DISPATCHER_ENABLED:
        from app.onvif_dispatcher import get_dispatcher
        get_dispatcher().remove_camera(camera_id)
        return
    stop_onvif(pid)


def _allocate_endpoint(camera_id: str, allocator: PortAllocator,
                       stack: ExitStack) -> tuple[int, Optional[str]]:
    """Allocate either an ONVIF port (standard) or a macvlan IP. Registers rollback."""
    if MACVLAN_ENABLED:
        try:
            ip = _macvlan_manager().create_interface(camera_id)
        except Exception as e:
            raise MacvlanError(f"Failed to create macvlan interface: {e}") from e
        stack.callback(lambda: _macvlan_manager().delete_interface(camera_id, ip))
        return 80, ip
    onvif_port = allocator.allocate()
    stack.callback(allocator.release, onvif_port)
    return onvif_port, None


# ── Create ─────────────────────────────────────────────────────────────────
def create_camera(
    video_file,
    video_params: VideoParams,
    *,
    sub_profile: bool = False,
    camera_name: str = "MockONVIF",
    edit_params: Optional[EditParams] = None,
    allocator: Optional[PortAllocator] = None,
    repo: Optional[CameraRepository] = None,
) -> dict:
    """Create a single camera. ExitStack ensures atomic rollback on failure."""
    allocator = allocator or get_default_allocator()
    repo = repo or get_repository()
    ensure_dirs()

    camera_id = str(uuid.uuid4())
    temp_path = VIDEOS_DIR / f"{camera_id}_temp.mp4"
    final_path = VIDEOS_DIR / f"{camera_id}.mp4"

    with ExitStack() as stack:
        # 1) Save upload
        _save_upload(video_file, temp_path)
        stack.callback(lambda: temp_path.unlink(missing_ok=True))

        # 2) Transcode
        final_path, sub_path = transcode(
            input_path=temp_path,
            output_path=final_path,
            params=video_params,
            sub_profile=sub_profile,
            edits=edit_params,
        )
        stack.callback(lambda: final_path.unlink(missing_ok=True))
        if sub_path:
            stack.callback(lambda: sub_path.unlink(missing_ok=True))
        # Drop the temp upload now that transcode succeeded
        temp_path.unlink(missing_ok=True)

        # 3) Snapshot
        snap_path = generate_snapshot(final_path, camera_id)
        stack.callback(lambda: snap_path.unlink(missing_ok=True))

        # 4) Endpoint (port or macvlan IP)
        onvif_port, camera_ip = _allocate_endpoint(camera_id, allocator, stack)

        # 5) Build & persist DB record
        rec = CameraRecord(
            camera_id=camera_id,
            video_path=str(final_path),
            onvif_port=onvif_port,
            video_params=video_params.to_dict(),
            created_at=int(time.time()),
            sub_profile=sub_profile,
            manufacturer=camera_name,
            shared_video_id=None,
            camera_ip=camera_ip,
        )
        repo.upsert(rec)
        stack.callback(repo.delete, camera_id)

        # 6) Start streaming
        ffmpeg_pid = start_ffmpeg(final_path, camera_id)
        stack.callback(stop_ffmpeg, ffmpeg_pid)
        ffmpeg_pid_sub: Optional[int] = None
        if sub_path:
            ffmpeg_pid_sub = start_ffmpeg(sub_path, f"{camera_id}_sub")
            stack.callback(stop_ffmpeg, ffmpeg_pid_sub)

        # 7) Start ONVIF (subprocess or in-process dispatcher)
        width, height, fps, vkbps, akbps = _extract_onvif_params(video_params)
        onvif_pid = _start_onvif(
            camera_id=camera_id,
            onvif_port=onvif_port,
            width=width, height=height, fps=fps,
            video_bitrate_kbps=vkbps, audio_bitrate_kbps=akbps,
            shared_video_id=None,
            sub_profile=sub_profile,
            camera_name=camera_name,
            camera_ip=camera_ip,
        )
        stack.callback(_stop_onvif, camera_id, onvif_pid)
        stack.callback(release_camera_loggers, camera_id)

        # 8) Register runtime
        state = RuntimeState(
            record=rec,
            ffmpeg_pid=ffmpeg_pid,
            onvif_pid=onvif_pid,
            ffmpeg_pid_sub=ffmpeg_pid_sub,
            width=width, height=height, fps=fps,
            video_bitrate_kbps=vkbps,
        )
        _registry.put(state)
        stack.callback(_registry.remove, camera_id)

        # Disarm rollback — everything succeeded
        stack.pop_all()
        return state.to_info_dict()


def create_cameras_batch(
    video_file,
    video_params: VideoParams,
    count: int = 50,
    *,
    sub_profile: bool = False,
    camera_name: str = "MockONVIF",
    edit_params: Optional[EditParams] = None,
) -> list[dict]:
    """Transcode once, then create N camera runtimes sharing the same file."""
    ensure_dirs()
    repo = get_repository()
    allocator = get_default_allocator()
    allocator.prime(_registry.used_ports())

    shared_id = str(uuid.uuid4())
    temp_path = VIDEOS_DIR / f"{shared_id}_temp.mp4"
    shared_path = VIDEOS_DIR / f"{shared_id}_shared.mp4"
    shared_sub_path: Optional[Path] = None

    # 1) Save + 2) Transcode once
    try:
        _save_upload(video_file, temp_path)
        shared_path, shared_sub_path = transcode(
            input_path=temp_path,
            output_path=shared_path,
            params=video_params,
            sub_profile=sub_profile,
            edits=edit_params,
        )
        temp_path.unlink(missing_ok=True)
    except Exception:
        temp_path.unlink(missing_ok=True)
        if shared_path.exists():
            shared_path.unlink()
        if shared_sub_path and shared_sub_path.exists():
            shared_sub_path.unlink()
        raise

    # 3) Shared snapshot
    try:
        generate_snapshot(shared_path, shared_id)
    except Exception as e:
        logger.warning("Shared snapshot failed: %s", e)

    # 4) Parallel per-camera setup
    results: list[dict] = []
    failed = 0
    width, height, fps, vkbps, akbps = _extract_onvif_params(video_params)

    def _spawn_one() -> Optional[dict]:
        cam_id = str(uuid.uuid4())
        with ExitStack() as stack:
            onvif_port, camera_ip = _allocate_endpoint(cam_id, allocator, stack)
            rec = CameraRecord(
                camera_id=cam_id,
                video_path=str(shared_path),
                onvif_port=onvif_port,
                video_params=video_params.to_dict(),
                created_at=int(time.time()),
                sub_profile=sub_profile,
                manufacturer=camera_name,
                shared_video_id=shared_id,
                camera_ip=camera_ip,
            )
            repo.upsert(rec)
            stack.callback(repo.delete, cam_id)

            ffmpeg_pid = start_ffmpeg(shared_path, cam_id)
            stack.callback(stop_ffmpeg, ffmpeg_pid)
            ffmpeg_pid_sub = None
            if shared_sub_path:
                ffmpeg_pid_sub = start_ffmpeg(shared_sub_path, f"{cam_id}_sub")
                stack.callback(stop_ffmpeg, ffmpeg_pid_sub)

            onvif_pid = _start_onvif(
                camera_id=cam_id,
                onvif_port=onvif_port,
                width=width, height=height, fps=fps,
                video_bitrate_kbps=vkbps, audio_bitrate_kbps=akbps,
                shared_video_id=shared_id,
                sub_profile=sub_profile,
                camera_name=camera_name,
                camera_ip=camera_ip,
            )
            stack.callback(_stop_onvif, cam_id, onvif_pid)
            stack.callback(release_camera_loggers, cam_id)

            state = RuntimeState(
                record=rec, ffmpeg_pid=ffmpeg_pid, onvif_pid=onvif_pid,
                ffmpeg_pid_sub=ffmpeg_pid_sub,
                width=width, height=height, fps=fps, video_bitrate_kbps=vkbps,
            )
            _registry.put(state)
            stack.callback(_registry.remove, cam_id)

            stack.pop_all()
            return state.to_info_dict()

    max_workers = min(BATCH_MAX_WORKERS, count)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_spawn_one) for _ in range(count)]
        completed = 0
        for fut in as_completed(futures):
            completed += 1
            try:
                info = fut.result()
                if info:
                    results.append(info)
            except Exception as e:
                failed += 1
                logger.warning("Batch camera failed: %s", e)
            if completed % 20 == 0 or completed == count:
                logger.info("Batch progress: %d/%d (%d ok, %d failed)",
                            completed, count, len(results), failed)

    logger.info("Batch deployment complete: %d/%d cameras", len(results), count)
    return results


# ── Delete ─────────────────────────────────────────────────────────────────
def delete_camera(camera_id: str) -> dict:
    state = _registry.get(camera_id)
    if state is None:
        # Fallback: look in DB (might be a stale row whose runtime never came up)
        if get_repository().get(camera_id) is None:
            raise CameraNotFoundError(f"Camera {camera_id} not found")
    repo = get_repository()
    logger.info("Deleting camera %s", camera_id[:8])

    if state is not None:
        stop_ffmpeg(state.ffmpeg_pid)
        if state.ffmpeg_pid_sub:
            stop_ffmpeg(state.ffmpeg_pid_sub)
        _stop_onvif(camera_id, state.onvif_pid)
        release_camera_loggers(camera_id)

    # Release endpoint
    rec = state.record if state else repo.get(camera_id)
    if rec is not None:
        if MACVLAN_ENABLED and rec.camera_ip:
            try:
                _macvlan_manager().delete_interface(camera_id, rec.camera_ip)
            except Exception as e:
                logger.warning("Failed to delete macvlan interface: %s", e)
        else:
            get_default_allocator().release(rec.onvif_port)

        # Delete or retain video based on shared-usage
        try:
            vp = Path(rec.video_path)
            if vp.exists():
                if rec.shared_video_id:
                    remaining = repo.count_using_shared_video(rec.shared_video_id, exclude_id=camera_id)
                    if remaining == 0:
                        vp.unlink(missing_ok=True)
                        if rec.sub_profile:
                            Path(str(vp).replace(".mp4", "_sub.mp4")).unlink(missing_ok=True)
                else:
                    vp.unlink(missing_ok=True)
                    if rec.sub_profile:
                        Path(str(vp).replace(".mp4", "_sub.mp4")).unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Failed to delete video file: %s", e)

        # Delete snapshot
        try:
            snap_id = rec.shared_video_id or camera_id
            if rec.shared_video_id:
                remaining = repo.count_using_shared_video(rec.shared_video_id, exclude_id=camera_id)
                if remaining == 0:
                    (SNAPSHOTS_DIR / f"{snap_id}.jpg").unlink(missing_ok=True)
            else:
                (SNAPSHOTS_DIR / f"{snap_id}.jpg").unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Failed to delete snapshot file: %s", e)

    # Persist
    repo.delete(camera_id)
    _registry.remove(camera_id)

    # Clean up legacy YAML file if present
    legacy = CAMERAS_DIR / f"config_{camera_id}.yaml"
    if legacy.exists():
        legacy.unlink(missing_ok=True)

    return {"status": "deleted", "id": camera_id}


# ── Restore ────────────────────────────────────────────────────────────────
def _restore_one(rec: CameraRecord, allocator: PortAllocator) -> Optional[RuntimeState]:
    video_path = Path(rec.video_path)
    if not video_path.exists():
        logger.warning("Skipping %s: video file missing (%s)", rec.camera_id[:8], video_path)
        return None

    # Snapshot recreate if missing
    snap_id = rec.shared_video_id or rec.camera_id
    snap_path = SNAPSHOTS_DIR / f"{snap_id}.jpg"
    if not snap_path.exists():
        try:
            generate_snapshot(video_path, snap_id)
        except Exception as e:
            logger.warning("Snapshot regen failed for %s: %s", rec.camera_id[:8], e)

    sub_profile = rec.sub_profile
    sub_path: Optional[Path] = None
    if sub_profile:
        candidate = Path(str(video_path).replace(".mp4", "_sub.mp4"))
        if candidate.exists():
            sub_path = candidate
        else:
            logger.warning("Sub-profile file missing for %s; disabling sub", rec.camera_id[:8])
            sub_profile = False

    with ExitStack() as stack:
        # Endpoint
        camera_ip: Optional[str] = None
        onvif_port: int
        if MACVLAN_ENABLED and rec.camera_ip:
            try:
                new_ip = _macvlan_manager().restore_interface(rec.camera_id, rec.camera_ip)
            except Exception as e:
                logger.warning("Macvlan restore failed for %s: %s", rec.camera_id[:8], e)
                return None
            stack.callback(lambda: _macvlan_manager().delete_interface(rec.camera_id, new_ip))
            if new_ip != rec.camera_ip:
                get_repository().update_camera_ip(rec.camera_id, new_ip)
                rec.camera_ip = new_ip
            camera_ip = new_ip
            onvif_port = 80
        else:
            if allocator.reserve(rec.onvif_port):
                onvif_port = rec.onvif_port
            else:
                onvif_port = allocator.allocate()
                get_repository().update_onvif_port(rec.camera_id, onvif_port)
                rec.onvif_port = onvif_port
            stack.callback(allocator.release, onvif_port)

        # FFmpeg
        ffmpeg_pid = start_ffmpeg(video_path, rec.camera_id)
        stack.callback(stop_ffmpeg, ffmpeg_pid)
        ffmpeg_pid_sub: Optional[int] = None
        if sub_path:
            ffmpeg_pid_sub = start_ffmpeg(sub_path, f"{rec.camera_id}_sub")
            stack.callback(stop_ffmpeg, ffmpeg_pid_sub)

        # ONVIF
        from app.schemas import VideoParams  # local — VideoParams isn't reconstructable elsewhere
        params = VideoParams(**rec.video_params)
        width, height, fps, vkbps, akbps = _extract_onvif_params(params)
        onvif_pid = _start_onvif(
            camera_id=rec.camera_id,
            onvif_port=onvif_port,
            width=width, height=height, fps=fps,
            video_bitrate_kbps=vkbps, audio_bitrate_kbps=akbps,
            shared_video_id=rec.shared_video_id,
            sub_profile=sub_profile,
            camera_name=rec.manufacturer,
            camera_ip=camera_ip,
        )
        stack.callback(_stop_onvif, rec.camera_id, onvif_pid)
        stack.callback(release_camera_loggers, rec.camera_id)

        # Update record if sub_profile got disabled
        if sub_profile != rec.sub_profile:
            rec.sub_profile = sub_profile
            get_repository().upsert(rec)

        state = RuntimeState(
            record=rec,
            ffmpeg_pid=ffmpeg_pid,
            onvif_pid=onvif_pid,
            ffmpeg_pid_sub=ffmpeg_pid_sub,
            width=width, height=height, fps=fps, video_bitrate_kbps=vkbps,
        )
        _registry.put(state)
        stack.callback(_registry.remove, rec.camera_id)

        stack.pop_all()
        return state


def restore_cameras() -> None:
    reap_defunct_children()
    repo = get_repository()
    migrated = migrate_yaml_configs(repo)
    if migrated:
        logger.info("Migrated %d legacy YAML camera config(s) into SQLite", migrated)

    records = repo.all()
    if not records:
        logger.info("No existing cameras to restore")
        return

    allocator = get_default_allocator()
    max_workers = min(BATCH_MAX_WORKERS, len(records))
    logger.info("Restoring %d cameras (workers=%d)", len(records), max_workers)

    restored = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_restore_one, rec, allocator): rec for rec in records}
        for fut in as_completed(futures):
            rec = futures[fut]
            try:
                state = fut.result()
                if state is not None:
                    restored += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                logger.warning("Restore exception for %s: %s", rec.camera_id[:8], e)
    logger.info("Restoration complete: %d/%d active (%d failed)",
                restored, len(records), failed)


# ── Cleanup on shutdown ────────────────────────────────────────────────────
def cleanup_all() -> None:
    """Tear everything down quickly. Called from shutdown handler."""
    states = _registry.all()
    if not states:
        return
    logger.info("Cleaning up %d camera(s)", len(states))

    # Send SIGTERM to everything FIRST (parallel), THEN reap.
    # This avoids the serial 0.5s sleep that previously dominated shutdown time.
    pids_main: list[int] = []
    pids_sub: list[int] = []
    pids_onvif: list[int] = []
    for s in states:
        if s.ffmpeg_pid:
            pids_main.append(s.ffmpeg_pid)
        if s.ffmpeg_pid_sub:
            pids_sub.append(s.ffmpeg_pid_sub)
        # Only subprocess-mode ONVIFs have a PID to kill
        if s.onvif_pid and not ONVIF_DISPATCHER_ENABLED:
            pids_onvif.append(s.onvif_pid)

    # Dispatcher mode: stop all in-process port servers
    if ONVIF_DISPATCHER_ENABLED:
        try:
            from app.onvif_dispatcher import get_dispatcher
            get_dispatcher().stop_all()
        except Exception as e:
            logger.warning("Failed to stop ONVIF dispatcher: %s", e)

    def _signal_all(pids: list[int], group: bool) -> None:
        for pid in pids:
            try:
                if group:
                    os.killpg(os.getpgid(pid), 15)
                else:
                    os.kill(pid, 15)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    _signal_all(pids_main + pids_sub, group=False)
    _signal_all(pids_onvif, group=True)
    time.sleep(0.6)
    # Force-kill stragglers
    for pid in pids_main + pids_sub:
        try:
            os.kill(pid, 0)
            os.kill(pid, 9)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    for pid in pids_onvif:
        try:
            os.kill(pid, 0)
            os.killpg(os.getpgid(pid), 9)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    # Reap them all
    for pid in pids_main + pids_sub + pids_onvif:
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass

    # Macvlan cleanup
    if MACVLAN_ENABLED:
        try:
            _macvlan_manager().cleanup_all()
        except Exception as e:
            logger.warning("Macvlan cleanup_all failed: %s", e)
