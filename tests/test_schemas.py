"""Boundary tests for app/schemas.py — pure pydantic, no I/O."""
import pytest
from pydantic import ValidationError as PydanticValidationError

from app.constants import CUSTOM_PARAM_RANGES, EDIT_LIMITS
from app.schemas import EditParams, UploadRequest, VideoParams

W = CUSTOM_PARAM_RANGES["width"]
H = CUSTOM_PARAM_RANGES["height"]
FPS = CUSTOM_PARAM_RANGES["fps"]


# ── VideoParams ─────────────────────────────────────────────────────────────
class TestVideoParams:
    def test_defaults(self):
        p = VideoParams()
        assert (p.width, p.height, p.fps) == (1920, 1080, 30.0)
        assert p.video_bitrate == "4M"
        assert p.audio_bitrate == "128k"

    @pytest.mark.parametrize("width", [W["min"], W["max"]])
    def test_width_boundaries_accepted(self, width):
        assert VideoParams(width=width).width == width

    @pytest.mark.parametrize("width", [W["min"] - 1, W["max"] + 1])
    def test_width_out_of_range_rejected(self, width):
        with pytest.raises(PydanticValidationError):
            VideoParams(width=width)

    @pytest.mark.parametrize("height", [H["min"], H["max"]])
    def test_height_boundaries_accepted(self, height):
        assert VideoParams(height=height).height == height

    @pytest.mark.parametrize("height", [H["min"] - 1, H["max"] + 1])
    def test_height_out_of_range_rejected(self, height):
        with pytest.raises(PydanticValidationError):
            VideoParams(height=height)

    @pytest.mark.parametrize("fps", [FPS["min"], FPS["max"]])
    def test_fps_boundaries_accepted(self, fps):
        assert VideoParams(fps=fps).fps == fps

    @pytest.mark.parametrize("fps", [FPS["min"] - 0.1, FPS["max"] + 0.1])
    def test_fps_out_of_range_rejected(self, fps):
        with pytest.raises(PydanticValidationError):
            VideoParams(fps=fps)

    def test_fps_rounded_to_one_decimal(self):
        assert VideoParams(fps=29.97).fps == 30.0
        assert VideoParams(fps=23.976).fps == 24.0

    @pytest.mark.parametrize("bitrate", ["0.5M", "50M", "2.5M", "4"])
    def test_video_bitrate_valid(self, bitrate):
        assert VideoParams(video_bitrate=bitrate).video_bitrate == bitrate

    @pytest.mark.parametrize("bitrate", ["0.4M", "50.1M", "abc", "", "M"])
    def test_video_bitrate_invalid(self, bitrate):
        with pytest.raises(PydanticValidationError):
            VideoParams(video_bitrate=bitrate)

    @pytest.mark.parametrize("bitrate", ["64k", "128k", "192k", "256k"])
    def test_audio_bitrate_whitelist_accepted(self, bitrate):
        assert VideoParams(audio_bitrate=bitrate).audio_bitrate == bitrate

    @pytest.mark.parametrize("bitrate", ["100k", "64K", "64", ""])
    def test_audio_bitrate_rejected(self, bitrate):
        with pytest.raises(PydanticValidationError):
            VideoParams(audio_bitrate=bitrate)


# ── UploadRequest.manufacturer whitelist ────────────────────────────────────
class TestManufacturer:
    @pytest.mark.parametrize("name", [
        "MockONVIF",
        "Axis Communications",
        "cam-01.test",
        "a",
        "x" * 50,
        "front_door 2",
    ])
    def test_normal_names_accepted(self, name):
        req = UploadRequest(manufacturer=name, video_params=VideoParams())
        assert req.manufacturer == name

    @pytest.mark.parametrize("name", [
        "<script>alert(1)</script>",
        "a<b",
        "a&b",
        'quote"name',
        "semi;colon",
        "",
        "x" * 51,
    ])
    def test_hostile_or_invalid_names_rejected(self, name):
        with pytest.raises(PydanticValidationError):
            UploadRequest(manufacturer=name, video_params=VideoParams())


# ── EditParams ──────────────────────────────────────────────────────────────
class TestEditParams:
    def test_no_edits_by_default(self):
        p = EditParams()
        assert not p.has_edits()
        assert p.trim_end is None

    def test_trim_end_zero_normalised_to_none(self):
        assert EditParams(trim_end=0).trim_end is None
        assert EditParams(trim_end=-1).trim_end is None

    def test_trim_end_before_start_rejected(self):
        with pytest.raises(PydanticValidationError):
            EditParams(trim_start=10.0, trim_end=5.0)

    def test_trim_end_equal_to_start_rejected(self):
        with pytest.raises(PydanticValidationError):
            EditParams(trim_start=10.0, trim_end=10.0)

    def test_min_duration_boundary(self):
        # exactly min_duration is allowed (validated at construction)
        ok = EditParams(trim_start=0.0, trim_end=float(EDIT_LIMITS["min_duration"]))
        assert ok.trim_end == EDIT_LIMITS["min_duration"]
        with pytest.raises(PydanticValidationError):
            EditParams(trim_start=0.0, trim_end=EDIT_LIMITS["min_duration"] - 0.1)

    def test_max_duration_boundary(self):
        ok = EditParams(trim_start=0.0, trim_end=float(EDIT_LIMITS["max_duration"]))
        assert ok.trim_end == EDIT_LIMITS["max_duration"]
        with pytest.raises(PydanticValidationError):
            EditParams(trim_start=0.0, trim_end=EDIT_LIMITS["max_duration"] + 0.1)

    def test_speed_shrinks_output_below_min(self):
        # 10s trimmed at 4x speed -> 2.5s output < 5s min
        with pytest.raises(PydanticValidationError):
            EditParams(trim_start=0.0, trim_end=10.0, speed=4.0)

    def test_extend_last_frame_counts_toward_duration(self):
        # 2s raw + 10s extend = 12s output -> valid despite raw < min
        p = EditParams(trim_start=0.0, trim_end=2.0, extend_last_frame=True)
        assert p.has_edits()

    @pytest.mark.parametrize("speed", [EDIT_LIMITS["min_speed"] - 0.1,
                                       EDIT_LIMITS["max_speed"] + 0.1])
    def test_speed_out_of_range_rejected(self, speed):
        with pytest.raises(PydanticValidationError):
            EditParams(speed=speed)

    @pytest.mark.parametrize("kwargs", [
        {"trim_start": 1.0, "trim_end": 30.0},
        {"trim_end": 30.0},
        {"speed": 2.0},
        {"extend_last_frame": True},
    ])
    def test_has_edits_true(self, kwargs):
        assert EditParams(**kwargs).has_edits()
