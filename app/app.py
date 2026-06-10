"""Flask HTTP routes for the Mock ONVIF Camera Service."""
from __future__ import annotations

import logging
import os
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from pydantic import ValidationError as PydanticValidationError

from app.camera_lifecycle import (
    create_camera,
    create_cameras_batch,
    delete_camera,
    get_registry,
)
from app.config import MAX_UPLOAD_BYTES
from app.exceptions import CameraServiceError, ValidationError
from app.schemas import EditParams, UploadRequest, VideoParams

logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="../static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
CORS(app)


# ── Error mapping ──────────────────────────────────────────────────────────
@app.errorhandler(CameraServiceError)
def _handle_service_error(e: CameraServiceError):
    logger.warning("Service error: %s", e)
    return jsonify({"error": str(e), "type": type(e).__name__}), e.http_status


@app.errorhandler(PydanticValidationError)
def _handle_pydantic_error(e: PydanticValidationError):
    return jsonify({"error": "validation failed", "details": e.errors()}), 400


@app.errorhandler(413)
def _handle_too_large(_e):
    return jsonify({
        "error": f"upload exceeds limit ({MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"
    }), 413


# ── Routes ─────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


def _parse_upload_form() -> UploadRequest:
    """Build an UploadRequest from multipart form fields."""
    f = request.form

    def _get(name: str, default: Any = None) -> Any:
        v = f.get(name)
        return default if v is None or v == "" else v

    try:
        video_params = VideoParams(
            width=int(_get("width", 1920)),
            height=int(_get("height", 1080)),
            fps=float(_get("fps", 30)),
            video_bitrate=_get("video_bitrate", "4M"),
            audio_bitrate=_get("audio_bitrate", "128k"),
        )
    except (TypeError, ValueError) as e:
        raise ValidationError(f"Invalid video parameters: {e}") from e

    edit_params = None
    try:
        trim_start = float(_get("trim_start", 0))
        trim_end_raw = _get("trim_end", "0")
        trim_end_val = float(trim_end_raw) if trim_end_raw else 0.0
        speed = float(_get("speed", 1.0))
        extend = str(_get("extend_last_frame", "false")).lower() == "true"
        if trim_start > 0 or trim_end_val > 0 or speed != 1.0 or extend:
            edit_params = EditParams(
                trim_start=trim_start,
                trim_end=trim_end_val if trim_end_val > 0 else None,
                speed=speed,
                extend_last_frame=extend,
            )
            edit_params.validate_duration()
    except (TypeError, ValueError) as e:
        raise ValidationError(f"Invalid edit parameters: {e}") from e

    return UploadRequest(
        camera_count=int(_get("camera_count", 1)),
        sub_profile=str(_get("sub_profile", "false")).lower() == "true",
        camera_name=str(_get("camera_name", "MockONVIF")).strip() or "MockONVIF",
        video_params=video_params,
        edit_params=edit_params,
    )


@app.route("/upload", methods=["POST"])
def upload_video():
    if "file" not in request.files:
        raise ValidationError("no file")
    video_file = request.files["file"]
    if not video_file.filename:
        raise ValidationError("no file")

    req = _parse_upload_form()

    if req.camera_count == 1:
        info = create_camera(
            video_file,
            req.video_params,
            sub_profile=req.sub_profile,
            camera_name=req.camera_name,
            edit_params=req.edit_params,
        )
        return jsonify(info), 201

    infos = create_cameras_batch(
        video_file,
        req.video_params,
        count=req.camera_count,
        sub_profile=req.sub_profile,
        camera_name=req.camera_name,
        edit_params=req.edit_params,
    )
    return jsonify({"cameras": infos, "count": len(infos)}), 201


@app.route("/cameras", methods=["GET"])
def list_cameras():
    states = get_registry().all()
    return jsonify([s.to_info_dict() for s in states]), 200


@app.route("/cameras/<camera_id>", methods=["DELETE"])
def remove_camera(camera_id: str):
    result = delete_camera(camera_id)
    return jsonify(result), 200


@app.route("/data/<path:filename>")
def serve_data_file(filename: str):
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    return send_from_directory(data_dir, filename)


@app.route("/health", methods=["GET"])
def health():
    states = get_registry().all()
    return jsonify({"status": "ok", "cameras": len(states)}), 200
