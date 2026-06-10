"""Argv assertions for app/ffmpeg_builder.py — pure functions, zero mocks."""
from pathlib import Path

from app.constants import (
    EXTEND_FRAME_DURATION,
    SUB_PROFILE_BITRATE_KBPS,
    SUB_PROFILE_FPS,
)
from app.ffmpeg_builder import (
    build_atempo_chain,
    build_edit_description,
    build_freeze_frame_cmd,
    build_snapshot_cmd,
    build_streaming_cmd,
    build_sub_profile_cmd,
    build_transcode_cmd,
)

IN = Path("/tmp/in.mp4")
OUT = Path("/tmp/out.mp4")


def _pair(cmd: list[str], flag: str) -> str:
    """Return the value following `flag` in an argv list."""
    return cmd[cmd.index(flag) + 1]


def _base_transcode(**overrides) -> list[str]:
    kwargs = dict(
        input_path=IN, output_path=OUT,
        width=1920, height=1080, fps=30.0,
        video_bitrate="4M", audio_bitrate="128k",
        trim_start=0.0, trim_duration=None,
    )
    kwargs.update(overrides)
    return build_transcode_cmd(**kwargs)


# ── build_transcode_cmd ─────────────────────────────────────────────────────
class TestTranscodeCmd:
    def test_basic_shape(self):
        cmd = _base_transcode()
        assert cmd[0] == "ffmpeg"
        assert _pair(cmd, "-i") == str(IN)
        assert cmd[-2:] == ["-y", str(OUT)]
        assert "-ss" not in cmd
        assert "-t" not in cmd
        assert "-af" not in cmd

    def test_resolution_and_bitrate_placement(self):
        cmd = _base_transcode(width=1280, height=720, video_bitrate="2.5M")
        assert _pair(cmd, "-vf") == "scale=1280x720"
        assert _pair(cmd, "-b:v") == "2.5M"
        assert _pair(cmd, "-maxrate") == "3.0M"   # 1.2x
        assert _pair(cmd, "-bufsize") == "5.0M"   # 2x
        assert _pair(cmd, "-b:a") == "128k"

    def test_fps_and_gop(self):
        cmd = _base_transcode(fps=25.0)
        assert _pair(cmd, "-r") == "25.0"
        assert _pair(cmd, "-g") == "25"
        assert _pair(cmd, "-keyint_min") == "25"

    def test_trim_flags_precede_input(self):
        cmd = _base_transcode(trim_start=2.5, trim_duration=10.0)
        assert _pair(cmd, "-ss") == "2.5"
        assert _pair(cmd, "-t") == "10.0"
        # input seeking: -ss/-t must come before -i
        assert cmd.index("-ss") < cmd.index("-i")
        assert cmd.index("-t") < cmd.index("-i")

    def test_no_trim_start_flag_when_zero(self):
        cmd = _base_transcode(trim_start=0.0, trim_duration=10.0)
        assert "-ss" not in cmd
        assert "-t" in cmd

    def test_extra_video_filters_appended(self):
        cmd = _base_transcode(video_filters_extra=["setpts=0.5*PTS"])
        assert _pair(cmd, "-vf") == "scale=1920x1080,setpts=0.5*PTS"

    def test_audio_filters_flag(self):
        cmd = _base_transcode(audio_filters=["atempo=2.0", "atempo=1.5000"])
        assert _pair(cmd, "-af") == "atempo=2.0,atempo=1.5000"

    def test_h264_baseline_profile(self):
        cmd = _base_transcode()
        assert _pair(cmd, "-c:v") == "libx264"
        assert _pair(cmd, "-profile:v") == "baseline"
        assert _pair(cmd, "-pix_fmt") == "yuv420p"
        assert _pair(cmd, "-c:a") == "aac"


# ── build_atempo_chain (speed flag plumbing) ────────────────────────────────
class TestAtempoChain:
    def test_unity_speed_is_empty(self):
        assert build_atempo_chain(1.0) == []

    def test_fast_speed_chains(self):
        # 4.0 = 2.0 x 2.0; the residual factor is emitted with 4 decimals
        assert build_atempo_chain(4.0) == ["atempo=2.0", "atempo=2.0000"]

    def test_slow_speed(self):
        assert build_atempo_chain(0.5) == ["atempo=0.5000"]

    def test_intermediate_speed(self):
        assert build_atempo_chain(3.0) == ["atempo=2.0", "atempo=1.5000"]


# ── build_edit_description ──────────────────────────────────────────────────
class TestEditDescription:
    def test_empty_when_no_edits(self):
        assert build_edit_description(0.0, None, 1.0, False) == ""

    def test_all_edits_mentioned(self):
        desc = build_edit_description(2.0, 8.0, 2.0, True)
        assert "trim:2.0-10.0s" in desc
        assert "speed:2.0x" in desc
        assert f"extend:+{EXTEND_FRAME_DURATION}s" in desc


# ── build_sub_profile_cmd ───────────────────────────────────────────────────
class TestSubProfileCmd:
    def test_uses_sub_profile_constants(self):
        cmd = build_sub_profile_cmd(input_path=IN, output_path=OUT,
                                    width=640, height=360)
        assert _pair(cmd, "-b:v") == f"{SUB_PROFILE_BITRATE_KBPS}k"
        assert _pair(cmd, "-r") == str(float(SUB_PROFILE_FPS))
        assert _pair(cmd, "-g") == str(SUB_PROFILE_FPS)
        assert _pair(cmd, "-vf") == "scale=640x360"
        assert _pair(cmd, "-b:a") == "64k"


# ── build_freeze_frame_cmd / build_snapshot_cmd ─────────────────────────────
def test_freeze_frame_default_duration():
    cmd = build_freeze_frame_cmd(input_path=IN, output_path=OUT)
    assert _pair(cmd, "-vf") == (
        f"tpad=stop_mode=clone:stop_duration={EXTEND_FRAME_DURATION}"
    )
    assert _pair(cmd, "-af") == f"apad=pad_dur={EXTEND_FRAME_DURATION}"


def test_snapshot_single_frame():
    cmd = build_snapshot_cmd(input_path=IN, output_path=OUT)
    assert _pair(cmd, "-frames:v") == "1"
    assert _pair(cmd, "-i") == str(IN)


# ── build_streaming_cmd ─────────────────────────────────────────────────────
class TestStreamingCmd:
    def test_explicit_host_port(self):
        cmd = build_streaming_cmd(IN, "cam123", host="10.0.0.5", port=9554)
        assert cmd[-1] == "rtsp://10.0.0.5:9554/cam123"

    def test_copy_mode_and_loop(self):
        cmd = build_streaming_cmd(IN, "cam123", host="h", port=1)
        assert _pair(cmd, "-stream_loop") == "-1"
        assert _pair(cmd, "-c:v") == "copy"
        assert _pair(cmd, "-c:a") == "copy"
        assert _pair(cmd, "-rtsp_transport") == "tcp"

    def test_defaults_come_from_config(self):
        from app.config import MEDIAMTX_HOST, MEDIAMTX_RTSP_PORT
        cmd = build_streaming_cmd(IN, "camX")
        assert cmd[-1] == f"rtsp://{MEDIAMTX_HOST}:{MEDIAMTX_RTSP_PORT}/camX"
