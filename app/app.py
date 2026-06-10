"""Flask HTTP routes for the Mock ONVIF Camera Service."""
from __future__ import annotations

import logging
from typing import Optional

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from pydantic import ValidationError as PydanticValidationError
from werkzeug.exceptions import HTTPException

from app.camera_lifecycle import create_camera, create_cameras_batch, delete_camera
from app.config import (
    MACVLAN_ENABLED,
    MAX_UPLOAD_BYTES,
    MEDIAMTX_RTSP_PORT,
    ONVIF_PASSWORD,
    ONVIF_USERNAME,
    SNAPSHOTS_DIR,
)
from app.constants import (
    CUSTOM_PARAM_RANGES,
    EDIT_LIMITS,
    EXTEND_FRAME_DURATION,
    VALID_AUDIO_BITRATES,
)
from app.exceptions import CameraNotFoundError, CameraServiceError, ValidationError
from app.runtime_registry import RuntimeState, get_registry
from app.schemas import EditParams, UploadRequest, VideoParams
from app.utils import get_server_ip

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


@app.errorhandler(404)
def _handle_not_found(_e):
    return jsonify({"error": "not found", "type": "NotFound"}), 404


@app.errorhandler(405)
def _handle_method_not_allowed(_e):
    return jsonify({"error": "method not allowed", "type": "MethodNotAllowed"}), 405


@app.errorhandler(413)
def _handle_too_large(_e):
    return jsonify({
        "error": f"upload exceeds limit ({MAX_UPLOAD_BYTES // (1024 * 1024)} MB)"
    }), 413


@app.errorhandler(Exception)
def _handle_unexpected_error(e: Exception):
    if isinstance(e, HTTPException):
        # Keep proper status codes for HTTP errors without a dedicated handler
        return jsonify({"error": e.description, "type": e.name.replace(" ", "")}), e.code
    logger.exception("Unhandled error: %s", e)
    return jsonify({"error": "internal server error", "type": "InternalError"}), 500


# ── Presentation ───────────────────────────────────────────────────────────
def _camera_to_dict(state: RuntimeState) -> dict:
    """HTTP response shape for one camera (presentation layer)."""
    rec = state.record
    server_ip = get_server_ip()
    onvif_url = (
        f"{rec.camera_ip}:80"
        if MACVLAN_ENABLED and rec.camera_ip
        else f"{server_ip}:{rec.onvif_port}"
    )
    snap_id = rec.shared_video_id or rec.camera_id
    info = {
        "id": rec.camera_id,
        "video_path": rec.video_path,
        "rtsp_port": MEDIAMTX_RTSP_PORT,
        "onvif_port": rec.onvif_port,
        "ffmpeg_pid": state.ffmpeg_pid,
        "onvif_pid": state.onvif_pid,
        "rtsp_url": f"rtsp://{server_ip}:{MEDIAMTX_RTSP_PORT}/{rec.camera_id}",
        "onvif_url": onvif_url,
        "snapshot_url": f"/snapshots/{snap_id}.jpg",
        "username": ONVIF_USERNAME,
        "password": ONVIF_PASSWORD,
        "width": state.width,
        "height": state.height,
        "fps": state.fps,
        "video_bitrate_mbps": round(state.video_bitrate_kbps / 1000, 2),
        "sub_profile": rec.sub_profile,
        "manufacturer": rec.manufacturer,
        "created_at": rec.created_at,
    }
    if rec.shared_video_id:
        info["shared_video_id"] = rec.shared_video_id
    if MACVLAN_ENABLED and rec.camera_ip:
        info["camera_ip"] = rec.camera_ip
    if rec.sub_profile and state.ffmpeg_pid_sub:
        info["ffmpeg_pid_sub"] = state.ffmpeg_pid_sub
        info["rtsp_url_sub"] = f"rtsp://{server_ip}:{MEDIAMTX_RTSP_PORT}/{rec.camera_id}_sub"
    return info


# ── Routes ─────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


def _parse_upload_form() -> UploadRequest:
    """Build an UploadRequest from multipart form fields."""
    f = request.form

    def _opt(name: str) -> Optional[str]:
        v = f.get(name)
        return None if v is None or v == "" else v

    # Only pass fields that are present, so Pydantic defaults stay the single
    # source of truth for default video parameters.
    video_kwargs: dict = {}
    try:
        for field, conv in (("width", int), ("height", int), ("fps", float),
                            ("video_bitrate", str), ("audio_bitrate", str)):
            raw = _opt(field)
            if raw is not None:
                video_kwargs[field] = conv(raw)
        video_params = VideoParams(**video_kwargs)
    except (TypeError, ValueError) as e:
        raise ValidationError(f"Invalid video parameters: {e}") from e

    try:
        candidate = EditParams(
            trim_start=float(_opt("trim_start") or 0),
            trim_end=float(_opt("trim_end") or 0) or None,
            speed=float(_opt("speed") or 1.0),
            extend_last_frame=str(_opt("extend_last_frame") or "false").lower() == "true",
        )
    except (TypeError, ValueError) as e:
        raise ValidationError(f"Invalid edit parameters: {e}") from e
    edit_params = candidate if candidate.has_edits() else None

    # The web form posts "camera_name"; internally the canonical field is
    # "manufacturer" (it feeds the ONVIF GetDeviceInformation response).
    manufacturer = (_opt("camera_name") or "MockONVIF").strip() or "MockONVIF"

    return UploadRequest(
        camera_count=int(_opt("camera_count") or 1),
        sub_profile=str(_opt("sub_profile") or "false").lower() == "true",
        manufacturer=manufacturer,
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
        states = [create_camera(
            video_file,
            req.video_params,
            sub_profile=req.sub_profile,
            manufacturer=req.manufacturer,
            edit_params=req.edit_params,
        )]
    else:
        states = create_cameras_batch(
            video_file,
            req.video_params,
            count=req.camera_count,
            sub_profile=req.sub_profile,
            manufacturer=req.manufacturer,
            edit_params=req.edit_params,
        )
    cameras = [_camera_to_dict(s) for s in states]
    return jsonify({"cameras": cameras, "count": len(cameras)}), 201


@app.route("/cameras", methods=["GET"])
def list_cameras():
    states = get_registry().all()
    return jsonify({"cameras": [_camera_to_dict(s) for s in states]}), 200


@app.route("/cameras/<camera_id>", methods=["GET"])
def get_camera(camera_id: str):
    state = get_registry().get(camera_id)
    if state is None:
        raise CameraNotFoundError(f"Camera {camera_id} not found")
    return jsonify(_camera_to_dict(state)), 200


@app.route("/cameras/<camera_id>", methods=["DELETE"])
def remove_camera(camera_id: str):
    result = delete_camera(camera_id)
    return jsonify(result), 200


@app.route("/snapshots/<path:filename>")
def serve_snapshot(filename: str):
    # send_from_directory rejects path traversal outside SNAPSHOTS_DIR.
    return send_from_directory(str(SNAPSHOTS_DIR.resolve()), filename)


@app.route("/config", methods=["GET"])
def get_config():
    """Validation ranges for the frontend (single source: app/constants.py)."""
    return jsonify({
        "param_ranges": CUSTOM_PARAM_RANGES,
        "valid_audio_bitrates": VALID_AUDIO_BITRATES,
        "edit_limits": EDIT_LIMITS,
        "extend_frame_duration": EXTEND_FRAME_DURATION,
    }), 200


@app.route("/health", methods=["GET"])
def health():
    states = get_registry().all()
    return jsonify({"status": "ok", "cameras": len(states)}), 200
