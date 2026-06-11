"""Pydantic schemas for HTTP request validation."""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.constants import (
    CUSTOM_PARAM_RANGES,
    EDIT_LIMITS,
    EXTEND_FRAME_DURATION,
    VALID_AUDIO_BITRATES,
)

# Whitelist for user-supplied camera names. The value is interpolated into
# ONVIF SOAP XML and rendered in the Web UI, so keep it to safe characters.
_MANUFACTURER_PATTERN = re.compile(r"[\w\s\-.]{1,50}")


class VideoParams(BaseModel):
    width: int = Field(default=1920, ge=CUSTOM_PARAM_RANGES["width"]["min"],
                       le=CUSTOM_PARAM_RANGES["width"]["max"])
    height: int = Field(default=1080, ge=CUSTOM_PARAM_RANGES["height"]["min"],
                        le=CUSTOM_PARAM_RANGES["height"]["max"])
    fps: float = Field(default=30.0, ge=CUSTOM_PARAM_RANGES["fps"]["min"],
                       le=CUSTOM_PARAM_RANGES["fps"]["max"])
    video_bitrate: str = "4M"
    audio_bitrate: str = "128k"

    @field_validator("fps")
    @classmethod
    def _round_fps(cls, v: float) -> float:
        return round(v, 1)

    @field_validator("video_bitrate")
    @classmethod
    def _validate_video_bitrate(cls, v: str) -> str:
        rng = CUSTOM_PARAM_RANGES["video_bitrate_mbps"]
        try:
            value = float(v.rstrip("M"))
        except (ValueError, AttributeError):
            raise ValueError("Invalid video bitrate format. Use format like '2.5M'")
        if not (rng["min"] <= value <= rng["max"]):
            raise ValueError(f"Video bitrate must be between {rng['min']}M and {rng['max']}M")
        return v

    @field_validator("audio_bitrate")
    @classmethod
    def _validate_audio_bitrate(cls, v: str) -> str:
        if v not in VALID_AUDIO_BITRATES:
            raise ValueError(f"Audio bitrate must be one of: {', '.join(VALID_AUDIO_BITRATES)}")
        return v

    def to_dict(self) -> dict:
        return self.model_dump()


class EditParams(BaseModel):
    trim_start: float = 0.0
    trim_end: Optional[float] = None
    speed: float = Field(default=1.0, ge=EDIT_LIMITS["min_speed"], le=EDIT_LIMITS["max_speed"])
    extend_last_frame: bool = False

    @field_validator("trim_end")
    @classmethod
    def _validate_trim_end(cls, v, info):
        if v is None or v <= 0:
            return None
        trim_start = info.data.get("trim_start", 0.0)
        if v <= trim_start:
            raise ValueError("End time must be greater than start time")
        return v

    @model_validator(mode="after")
    def _validate_duration(self) -> "EditParams":
        """Check the resulting output duration is within allowed limits."""
        if self.trim_end is None:
            return self
        raw_duration = self.trim_end - self.trim_start
        extend = EXTEND_FRAME_DURATION if self.extend_last_frame else 0
        output = raw_duration / self.speed + extend
        if output < EDIT_LIMITS["min_duration"]:
            raise ValueError(
                f"Output duration must be at least {EDIT_LIMITS['min_duration']} seconds")
        if output > EDIT_LIMITS["max_duration"]:
            raise ValueError(
                f"Output duration cannot exceed {EDIT_LIMITS['max_duration']} seconds")
        return self

    def has_edits(self) -> bool:
        return (self.trim_start > 0 or self.trim_end is not None or
                self.speed != 1.0 or self.extend_last_frame)


class UploadRequest(BaseModel):
    camera_count: int = Field(default=1, ge=1, le=100)
    sub_profile: bool = False
    # Canonical name is "manufacturer"; the web form posts it as "camera_name"
    # and app.py maps it at the HTTP boundary.
    manufacturer: str = "MockONVIF"
    video_params: VideoParams
    edit_params: Optional[EditParams] = None

    @field_validator("manufacturer")
    @classmethod
    def _validate_manufacturer(cls, v: str) -> str:
        if not _MANUFACTURER_PATTERN.fullmatch(v):
            raise ValueError(
                "Camera name may only contain letters, digits, spaces, "
                "underscores, hyphens and dots (1-50 characters)"
            )
        return v
