"""Camera lifecycle: create, delete, restore. ExitStack-based rollback.

All three startup paths (single create, batch create, restore) share the same
runtime-startup tail via :func:`_start_camera_runtime`. ONVIF mode differences
(subprocess vs dispatcher) live behind :mod:`app.onvif_endpoint`; process kill
mechanics live in :mod:`app.process_supervisor`.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from app.config import (
    BATCH_MAX_WORKERS,
    CAMERAS_DIR,
    MACVLAN_ENABLED,
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
from app.onvif_endpoint import extract_onvif_params, get_onvif_endpoint
from app.port_allocator import PortAllocator, get_default_allocator
from app.process_supervisor import (
    reap_defunct_children,
    release_camera_loggers,
    start_ffmpeg,
    stop_ffmpeg,
    terminate_many,
)
from app.runtime_registry import RuntimeState, get_registry
from app.schemas import EditParams, VideoParams
from app.transcoder import generate_snapshot, transcode

if TYPE_CHECKING:
    from werkzeug.datastructures import FileStorage

    from app.macvlan_manager import MacvlanManager

logger = logging.getLogger(__name__)


# ── Macvlan singleton (lazy, double-checked locking) ───────────────────────
_macvlan: "MacvlanManager | None" = None
_macvlan_lock = threading.Lock()


def _get_macvlan_manager() -> "MacvlanManager":
    """Lazy-load the macvlan manager so non-macvlan mode skips the import."""
    global _macvlan
    if _macvlan is None:
        with _macvlan_lock:
            if _macvlan is None:
                from app.config import (
                    MACVLAN_DHCP,
                    MACVLAN_GATEWAY,
                    MACVLAN_IP_END,
                    MACVLAN_IP_START,
                    MACVLAN_PARENT_IFACE,
                    MACVLAN_SUBNET,
                )
                from app.macvlan_manager import MacvlanManager
                _macvlan = MacvlanManager(
                    subnet=MACVLAN_SUBNET,
                    gateway=MACVLAN_GATEWAY,
                    ip_start=MACVLAN_IP_START,
                    ip_end=MACVLAN_IP_END,
                    parent_iface=MACVLAN_PARENT_IFACE,
                    use_dhcp=MACVLAN_DHCP,
                )
    return _macvlan


# ── Helpers ────────────────────────────────────────────────────────────────
def _save_upload(video_file: "FileStorage", destination: Path) -> None:
    try:
        video_file.save(str(destination))
    except Exception as e:
        raise VideoSaveError(f"Failed to save video: {e}") from e


def _allocate_endpoint(camera_id: str, allocator: PortAllocator,
                       stack: ExitStack) -> tuple[int, Optional[str]]:
    """Allocate either an ONVIF port (standard) or a macvlan IP. Registers rollback."""
    if MACVLAN_ENABLED:
        try:
            ip = _get_macvlan_manager().create_interface(camera_id)
        except Exception as e:
            raise MacvlanError(f"Failed to create macvlan interface: {e}") from e
        stack.callback(lambda: _get_macvlan_manager().delete_interface(camera_id, ip))
        return 80, ip
    onvif_port = allocator.allocate()
    stack.callback(allocator.release, onvif_port)
    return onvif_port, None


def _start_camera_runtime(
    rec: CameraRecord,
    *,
    video_path: Path,
    sub_path: Optional[Path],
    params: VideoParams,
    stack: ExitStack,
    repo: CameraRepository,
    delete_record_on_failure: bool = True,
) -> RuntimeState:
    """Shared startup tail used by create, batch-create, and restore.

    Sequence: persist record → start FFmpeg (+sub) → start ONVIF endpoint →
    register runtime state. Every acquisition pushes a rollback callback onto
    ``stack``; on success the stack is disarmed (``pop_all``).

    The caller must already have allocated the endpoint (``rec.onvif_port`` /
    ``rec.camera_ip``) and registered its rollback on ``stack``.
    """
    camera_id = rec.camera_id

    repo.upsert(rec)
    if delete_record_on_failure:
        stack.callback(repo.delete, camera_id)

    ffmpeg_pid = start_ffmpeg(video_path, camera_id)
    stack.callback(stop_ffmpeg, ffmpeg_pid)
    ffmpeg_pid_sub: Optional[int] = None
    if sub_path is not None:
        ffmpeg_pid_sub = start_ffmpeg(sub_path, f"{camera_id}_sub")
        stack.callback(stop_ffmpeg, ffmpeg_pid_sub)

    width, height, fps, vkbps, akbps = extract_onvif_params(params)
    endpoint = get_onvif_endpoint()
    onvif_pid = endpoint.start(
        camera_id=camera_id,
        onvif_port=rec.onvif_port,
        width=width, height=height, fps=fps,
        video_bitrate_kbps=vkbps,
        audio_bitrate_kbps=akbps,
        shared_video_id=rec.shared_video_id,
        sub_profile=rec.sub_profile,
        manufacturer=rec.manufacturer,
        camera_ip=rec.camera_ip,
    )
    stack.callback(endpoint.stop, camera_id, onvif_pid)
    stack.callback(release_camera_loggers, camera_id)

    state = RuntimeState(
        record=rec,
        ffmpeg_pid=ffmpeg_pid,
        onvif_pid=onvif_pid,
        ffmpeg_pid_sub=ffmpeg_pid_sub,
        width=width, height=height, fps=fps,
        video_bitrate_kbps=vkbps,
    )
    registry = get_registry()
    registry.put(state)
    stack.callback(registry.remove, camera_id)

    # Disarm rollback — everything succeeded
    stack.pop_all()
    return state


# ── Create ─────────────────────────────────────────────────────────────────
def create_camera(
    video_file: "FileStorage",
    video_params: VideoParams,
    *,
    sub_profile: bool = False,
    manufacturer: str = "MockONVIF",
    edit_params: Optional[EditParams] = None,
    allocator: Optional[PortAllocator] = None,
    repo: Optional[CameraRepository] = None,
) -> RuntimeState:
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

        # 5-8) Persist + start runtime (shared tail)
        rec = CameraRecord(
            camera_id=camera_id,
            video_path=str(final_path),
            onvif_port=onvif_port,
            video_params=video_params.to_dict(),
            created_at=int(time.time()),
            sub_profile=sub_profile,
            manufacturer=manufacturer,
            shared_video_id=None,
            camera_ip=camera_ip,
        )
        return _start_camera_runtime(
            rec, video_path=final_path, sub_path=sub_path,
            params=video_params, stack=stack, repo=repo,
        )


def _spawn_one(
    *,
    shared_id: str,
    shared_path: Path,
    shared_sub_path: Optional[Path],
    video_params: VideoParams,
    sub_profile: bool,
    manufacturer: str,
    allocator: PortAllocator,
    repo: CameraRepository,
    preallocated_port: Optional[int] = None,
) -> RuntimeState:
    """Create one camera runtime that re-uses an already-transcoded shared video.

    ``preallocated_port`` is set by the batch path, which reserves a contiguous
    port block up front so batch cameras get consecutive ports.
    """
    camera_id = str(uuid.uuid4())
    with ExitStack() as stack:
        if preallocated_port is not None:
            onvif_port, camera_ip = preallocated_port, None
            stack.callback(allocator.release, preallocated_port)
        else:
            onvif_port, camera_ip = _allocate_endpoint(camera_id, allocator, stack)
        rec = CameraRecord(
            camera_id=camera_id,
            video_path=str(shared_path),
            onvif_port=onvif_port,
            video_params=video_params.to_dict(),
            created_at=int(time.time()),
            sub_profile=sub_profile,
            manufacturer=manufacturer,
            shared_video_id=shared_id,
            camera_ip=camera_ip,
        )
        return _start_camera_runtime(
            rec, video_path=shared_path, sub_path=shared_sub_path,
            params=video_params, stack=stack, repo=repo,
        )


def create_cameras_batch(
    video_file: "FileStorage",
    video_params: VideoParams,
    count: int = 50,
    *,
    sub_profile: bool = False,
    manufacturer: str = "MockONVIF",
    edit_params: Optional[EditParams] = None,
) -> list[RuntimeState]:
    """Transcode once, then create N camera runtimes sharing the same file."""
    ensure_dirs()
    repo = get_repository()
    allocator = get_default_allocator()
    allocator.prime(get_registry().used_ports())

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

    # 4) Reserve a contiguous port block so batch cameras get consecutive
    #    ports (macvlan cameras all use port 80, so nothing to reserve there).
    ports: list[Optional[int]] = [None] * count
    if not MACVLAN_ENABLED:
        ports = list(allocator.allocate_block(count))

    # 5) Parallel per-camera setup
    results: list[RuntimeState] = []
    failed = 0
    max_workers = min(BATCH_MAX_WORKERS, count)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [
            ex.submit(
                _spawn_one,
                shared_id=shared_id,
                shared_path=shared_path,
                shared_sub_path=shared_sub_path,
                video_params=video_params,
                sub_profile=sub_profile,
                manufacturer=manufacturer,
                allocator=allocator,
                repo=repo,
                preallocated_port=ports[i],
            )
            for i in range(count)
        ]
        completed = 0
        for fut in as_completed(futures):
            completed += 1
            try:
                results.append(fut.result())
            except Exception as e:
                failed += 1
                logger.warning("Batch camera failed: %s", e)
            if completed % 20 == 0 or completed == count:
                logger.info("Batch progress: %d/%d (%d ok, %d failed)",
                            completed, count, len(results), failed)

    logger.info("Batch deployment complete: %d/%d cameras", len(results), count)
    return results


# ── Delete ─────────────────────────────────────────────────────────────────
def _delete_media_files(rec: CameraRecord, repo: CameraRepository) -> None:
    """Delete the camera's video (+sub) and snapshot files.

    Shared media is kept while any other camera still references the same
    ``shared_video_id`` (queried once for both video and snapshot).
    """
    if rec.shared_video_id:
        remaining = repo.count_using_shared_video(
            rec.shared_video_id, exclude_id=rec.camera_id
        )
        if remaining > 0:
            return  # other cameras still stream this file

    try:
        video_path = Path(rec.video_path)
        video_path.unlink(missing_ok=True)
        if rec.sub_profile:
            Path(str(video_path).replace(".mp4", "_sub.mp4")).unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Failed to delete video file: %s", e)

    try:
        snap_id = rec.shared_video_id or rec.camera_id
        (SNAPSHOTS_DIR / f"{snap_id}.jpg").unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Failed to delete snapshot file: %s", e)


def delete_camera(camera_id: str) -> dict:
    registry = get_registry()
    repo = get_repository()
    state = registry.get(camera_id)
    if state is None:
        # Fallback: look in DB (might be a stale row whose runtime never came up)
        if repo.get(camera_id) is None:
            raise CameraNotFoundError(f"Camera {camera_id} not found")
    logger.info("Deleting camera %s", camera_id[:8])

    if state is not None:
        stop_ffmpeg(state.ffmpeg_pid)
        if state.ffmpeg_pid_sub:
            stop_ffmpeg(state.ffmpeg_pid_sub)
        get_onvif_endpoint().stop(camera_id, state.onvif_pid)
        release_camera_loggers(camera_id)

    # Release endpoint + media files
    rec = state.record if state else repo.get(camera_id)
    if rec is not None:
        if MACVLAN_ENABLED and rec.camera_ip:
            try:
                _get_macvlan_manager().delete_interface(camera_id, rec.camera_ip)
            except Exception as e:
                logger.warning("Failed to delete macvlan interface: %s", e)
        else:
            get_default_allocator().release(rec.onvif_port)

        _delete_media_files(rec, repo)

    # Persist
    repo.delete(camera_id)
    registry.remove(camera_id)

    # Clean up legacy YAML file if present
    legacy = CAMERAS_DIR / f"config_{camera_id}.yaml"
    if legacy.exists():
        legacy.unlink(missing_ok=True)

    return {"status": "deleted", "id": camera_id}


# ── Restore ────────────────────────────────────────────────────────────────
def _restore_one(rec: CameraRecord, allocator: PortAllocator) -> Optional[RuntimeState]:
    repo = get_repository()
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

    sub_path: Optional[Path] = None
    if rec.sub_profile:
        candidate = Path(str(video_path).replace(".mp4", "_sub.mp4"))
        if candidate.exists():
            sub_path = candidate
        else:
            logger.warning("Sub-profile file missing for %s; disabling sub", rec.camera_id[:8])
            rec.sub_profile = False  # persisted by the upsert in _start_camera_runtime

    with ExitStack() as stack:
        # Endpoint: try to reattach to the persisted port/IP
        if MACVLAN_ENABLED and rec.camera_ip:
            try:
                new_ip = _get_macvlan_manager().restore_interface(rec.camera_id, rec.camera_ip)
            except Exception as e:
                logger.warning("Macvlan restore failed for %s: %s", rec.camera_id[:8], e)
                return None
            stack.callback(
                lambda: _get_macvlan_manager().delete_interface(rec.camera_id, new_ip)
            )
            rec.camera_ip = new_ip
            rec.onvif_port = 80
        else:
            if not allocator.reserve(rec.onvif_port):
                rec.onvif_port = allocator.allocate()
            stack.callback(allocator.release, rec.onvif_port)

        return _start_camera_runtime(
            rec, video_path=video_path, sub_path=sub_path,
            params=rec.parsed_params(), stack=stack, repo=repo,
            delete_record_on_failure=False,  # record pre-exists; keep it for retry
        )


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
    states = get_registry().all()
    if not states:
        return
    logger.info("Cleaning up %d camera(s)", len(states))

    # ONVIF endpoints first (dispatcher: in-process stop; subprocess: batch kill)
    get_onvif_endpoint().stop_all(states)

    # FFmpeg main + sub streams: batch SIGTERM → grace → SIGKILL → reap
    ffmpeg_pids = [s.ffmpeg_pid for s in states if s.ffmpeg_pid]
    ffmpeg_pids += [s.ffmpeg_pid_sub for s in states if s.ffmpeg_pid_sub]
    terminate_many(ffmpeg_pids)

    # Macvlan cleanup
    if MACVLAN_ENABLED:
        try:
            _get_macvlan_manager().cleanup_all()
        except Exception as e:
            logger.warning("Macvlan cleanup_all failed: %s", e)
