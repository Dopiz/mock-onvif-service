"""Video transcoding and snapshot generation."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from app.config import MAX_VIDEO_SIZE_BYTES, MAX_VIDEO_SIZE_MB, SNAPSHOTS_DIR
from app.exceptions import SnapshotError, TranscodeError
from app.ffmpeg_builder import (
    build_atempo_chain,
    build_edit_description,
    build_freeze_frame_cmd,
    build_snapshot_cmd,
    build_sub_profile_cmd,
    build_transcode_cmd,
)
from app.schemas import EditParams, VideoParams

logger = logging.getLogger(__name__)


# Hard timeout on a single ffmpeg invocation. Long enough for a 4K reencode of a 3-min
# clip on slow hardware; short enough to not hang the worker forever if ffmpeg deadlocks.
_FFMPEG_TIMEOUT_SECONDS = 30 * 60


def _enforce_size_limit(path: Path) -> None:
    """Raise if the transcoded file exceeds the configured per-video cap."""
    try:
        size = path.stat().st_size
    except OSError as e:
        raise TranscodeError(f"Could not stat transcoded file: {e}") from e
    if size > MAX_VIDEO_SIZE_BYTES:
        actual_mb = size / (1024 * 1024)
        path.unlink(missing_ok=True)
        raise TranscodeError(
            f"Transcoded video exceeds {MAX_VIDEO_SIZE_MB} MB limit "
            f"(actual: {actual_mb:.1f} MB). Reduce bitrate or trim length."
        )


def _run_ffmpeg(cmd: list[str], op: str) -> None:
    logger.info("[%s] %s", op, " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=_FFMPEG_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as e:
        raise TranscodeError(f"{op} timed out after {_FFMPEG_TIMEOUT_SECONDS}s") from e
    if result.returncode != 0:
        logger.error("[%s] failed: %s", op, result.stderr[-2000:])
        raise TranscodeError(f"{op} failed: {result.stderr.strip()[:500]}")


def apply_freeze_frame(video_path: Path) -> Path:
    """Append a freeze frame to the end of a video. Replaces the file in place."""
    video_path = Path(video_path)
    temp_path = video_path.with_suffix(".temp.mp4")
    try:
        _run_ffmpeg(build_freeze_frame_cmd(input_path=video_path, output_path=temp_path),
                    "freeze_frame")
        temp_path.replace(video_path)
        return video_path
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def transcode(
    *,
    input_path: Path,
    output_path: Path,
    params: VideoParams,
    sub_profile: bool = False,
    edits: Optional[EditParams] = None,
) -> tuple[Path, Optional[Path]]:
    """Transcode an input video to the configured params.

    Returns ``(main_path, sub_path_or_None)``.
    """
    # Edit params
    trim_start = 0.0
    trim_duration: Optional[float] = None
    speed = 1.0
    extend_last_frame = False
    if edits is not None:
        trim_start = edits.trim_start
        if edits.trim_end and edits.trim_end > edits.trim_start:
            trim_duration = edits.trim_end - edits.trim_start
        speed = edits.speed
        extend_last_frame = edits.extend_last_frame

    # Speed filters
    video_filters_extra: list[str] = []
    audio_filters: list[str] = []
    if speed != 1.0:
        video_filters_extra.append(f"setpts={1 / speed}*PTS")
        audio_filters.extend(build_atempo_chain(speed))

    cmd = build_transcode_cmd(
        input_path=input_path,
        output_path=output_path,
        width=params.width,
        height=params.height,
        fps=params.fps,
        video_bitrate=params.video_bitrate,
        audio_bitrate=params.audio_bitrate,
        trim_start=trim_start,
        trim_duration=trim_duration,
        video_filters_extra=video_filters_extra or None,
        audio_filters=audio_filters or None,
    )

    edit_desc = build_edit_description(trim_start, trim_duration, speed, extend_last_frame)
    logger.info("Transcoding %sx%s%s", params.width, params.height, edit_desc)
    _run_ffmpeg(cmd, "transcode")

    if extend_last_frame:
        apply_freeze_frame(output_path)

    # Size cap on the main output (after any freeze-frame extension)
    _enforce_size_limit(output_path)

    if not sub_profile:
        return output_path, None

    # Build a 360p sub-stream from the freshly-transcoded main file
    aspect_ratio = params.width / params.height
    sub_height = 360
    sub_width = int(round(sub_height * aspect_ratio / 2) * 2)
    sub_path = Path(str(output_path).replace(".mp4", "_sub.mp4"))
    _run_ffmpeg(
        build_sub_profile_cmd(
            input_path=output_path, output_path=sub_path,
            width=sub_width, height=sub_height,
        ),
        "sub_profile",
    )
    # Sub-stream is fixed 0.75M bitrate × 180s ≈ 17 MB so it can't realistically
    # blow the cap, but check defensively in case future params change.
    _enforce_size_limit(sub_path)
    return output_path, sub_path


def generate_snapshot(video_path: Path, snapshot_id: str) -> Path:
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOTS_DIR / f"{snapshot_id}.jpg"
    cmd = build_snapshot_cmd(input_path=video_path, output_path=snapshot_path)
    try:
        subprocess.run(cmd, capture_output=True, check=True, text=True, timeout=60)
    except subprocess.CalledProcessError as e:
        logger.error("snapshot failed: %s", e.stderr)
        raise SnapshotError(f"Failed to generate snapshot: {e.stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise SnapshotError("Snapshot generation timed out") from e
    return snapshot_path
