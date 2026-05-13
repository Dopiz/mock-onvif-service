"""FFmpeg command construction. Single source of truth — no duplicate flags."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.config import MEDIAMTX_HOST, MEDIAMTX_RTSP_PORT
from app.constants import EXTEND_FRAME_DURATION


# ── Audio speed filter chain ───────────────────────────────────────────────
def build_atempo_chain(speed: float) -> list[str]:
    """atempo filter only supports 0.5–2.0; chain for wider ranges."""
    if speed == 1.0:
        return []
    filters: list[str] = []
    remaining = speed
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    if abs(remaining - 1.0) > 0.001:
        filters.append(f"atempo={remaining:.4f}")
    return filters


def build_edit_description(trim_start: float, trim_duration: Optional[float],
                            speed: float, extend_last_frame: bool) -> str:
    """Human-readable summary of edits for log messages."""
    parts: list[str] = []
    if trim_duration:
        parts.append(f"trim:{trim_start}-{trim_start + trim_duration}s")
    if speed != 1.0:
        parts.append(f"speed:{speed}x")
    if extend_last_frame:
        parts.append(f"extend:+{EXTEND_FRAME_DURATION}s")
    return f" ({', '.join(parts)})" if parts else ""


# ── Transcode commands ─────────────────────────────────────────────────────
def _h264_encoder_args(video_bitrate: str, maxrate: str, bufsize: str,
                       gop: int, fps: float) -> list[str]:
    return [
        "-c:v", "libx264",
        "-preset", "medium",
        "-profile:v", "baseline",
        "-level", "3.1",
        "-pix_fmt", "yuv420p",
        "-b:v", video_bitrate,
        "-maxrate", maxrate,
        "-bufsize", bufsize,
        "-g", str(gop),
        "-keyint_min", str(gop),
        "-sc_threshold", "0",
        "-r", str(fps),
    ]


def _aac_encoder_args(audio_bitrate: str) -> list[str]:
    return [
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        "-ar", "16000",
        "-ac", "1",
        "-profile:a", "aac_low",
    ]


def build_transcode_cmd(*, input_path: Path, output_path: Path,
                        width: int, height: int, fps: float,
                        video_bitrate: str, audio_bitrate: str,
                        trim_start: float, trim_duration: Optional[float],
                        video_filters_extra: Optional[list[str]] = None,
                        audio_filters: Optional[list[str]] = None) -> list[str]:
    """Build a transcode command. Returns the argv list."""
    bitrate_val = float(video_bitrate.rstrip("M"))
    maxrate = f"{bitrate_val * 1.2}M"
    bufsize = f"{bitrate_val * 2}M"
    gop = int(round(fps))

    cmd: list[str] = ["ffmpeg"]
    if trim_start > 0:
        cmd += ["-ss", str(trim_start)]
    if trim_duration:
        cmd += ["-t", str(trim_duration)]

    cmd += ["-i", str(input_path)]

    vfilters = [f"scale={width}x{height}"]
    if video_filters_extra:
        vfilters.extend(video_filters_extra)
    cmd += ["-vf", ",".join(vfilters)]

    cmd += _h264_encoder_args(video_bitrate, maxrate, bufsize, gop, fps)

    if audio_filters:
        cmd += ["-af", ",".join(audio_filters)]
    cmd += _aac_encoder_args(audio_bitrate)

    cmd += ["-y", str(output_path)]
    return cmd


def build_sub_profile_cmd(*, input_path: Path, output_path: Path,
                          width: int, height: int) -> list[str]:
    """Downscale an already-transcoded main stream into a 360p sub stream."""
    return [
        "ffmpeg",
        "-i", str(input_path),
        "-vf", f"scale={width}x{height}",
        *_h264_encoder_args("0.75M", "1M", "1.5M", gop=24, fps=24.0),
        *_aac_encoder_args("64k"),
        "-y", str(output_path),
    ]


def build_freeze_frame_cmd(*, input_path: Path, output_path: Path,
                           duration: float = EXTEND_FRAME_DURATION) -> list[str]:
    return [
        "ffmpeg",
        "-i", str(input_path),
        "-vf", f"tpad=stop_mode=clone:stop_duration={duration}",
        "-af", f"apad=pad_dur={duration}",
        "-c:v", "libx264",
        "-profile:v", "baseline",
        "-c:a", "aac",
        "-y", str(output_path),
    ]


def build_snapshot_cmd(*, input_path: Path, output_path: Path) -> list[str]:
    return [
        "ffmpeg",
        "-ss", "00:00:02",
        "-i", str(input_path),
        "-vf", "thumbnail=100,scale='min(iw,1280)':'min(ih,720)':force_original_aspect_ratio=decrease",
        "-frames:v", "1",
        "-q:v", "2",
        "-y", str(output_path),
    ]


def build_streaming_cmd(video_path: Path, camera_id: str) -> list[str]:
    """RTSP-out loop command using stream-copy mode (no re-encode)."""
    return [
        "ffmpeg",
        "-re",
        "-stream_loop", "-1",
        "-i", str(video_path),
        "-c:v", "copy",
        "-c:a", "copy",
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        f"rtsp://{MEDIAMTX_HOST}:{MEDIAMTX_RTSP_PORT}/{camera_id}",
    ]
