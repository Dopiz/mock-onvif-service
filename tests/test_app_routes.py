"""Flask route contract tests via test_client, with lifecycle/registry mocked.

`app.app` is import-safe (singletons are lazy), but every route dependency
that would touch disk, subprocesses or the network is monkeypatched here.
"""
import io

import pytest

import app.app as app_module
from app.db import CameraRecord
from app.exceptions import CameraNotFoundError
from app.runtime_registry import RuntimeState

CAM_ID = "8c058bc4-0000-4000-8000-000000000001"


def _state(camera_id=CAM_ID) -> RuntimeState:
    rec = CameraRecord(
        camera_id=camera_id,
        video_path=f"data/videos/{camera_id}.mp4",
        onvif_port=12001,
        video_params={"width": 1920, "height": 1080, "fps": 30.0,
                      "video_bitrate": "4M", "audio_bitrate": "128k"},
        created_at=1700000000,
        manufacturer="TestCam",
    )
    return RuntimeState(record=rec, ffmpeg_pid=111, onvif_pid=222,
                        width=1920, height=1080, fps=30.0,
                        video_bitrate_kbps=4000)


class FakeRegistry:
    def __init__(self, states=()):
        self._states = list(states)

    def all(self):
        return self._states

    def get(self, camera_id):
        return next((s for s in self._states
                     if s.record.camera_id == camera_id), None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "get_server_ip", lambda: "127.0.0.1")
    return app_module.app.test_client()


def _set_registry(monkeypatch, states=()):
    registry = FakeRegistry(states)
    monkeypatch.setattr(app_module, "get_registry", lambda: registry)


# ── Health / config ─────────────────────────────────────────────────────────
def test_health(client, monkeypatch):
    _set_registry(monkeypatch, [_state()])
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "cameras": 1}


def test_config_shape(client):
    from app.constants import (
        CUSTOM_PARAM_RANGES,
        EDIT_LIMITS,
        EXTEND_FRAME_DURATION,
        VALID_AUDIO_BITRATES,
    )
    resp = client.get("/config")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["param_ranges"] == CUSTOM_PARAM_RANGES
    assert body["valid_audio_bitrates"] == VALID_AUDIO_BITRATES
    assert body["edit_limits"] == EDIT_LIMITS
    assert body["extend_frame_duration"] == EXTEND_FRAME_DURATION


# ── Cameras listing ─────────────────────────────────────────────────────────
def test_list_cameras_shape(client, monkeypatch):
    _set_registry(monkeypatch, [_state()])
    resp = client.get("/cameras")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body.keys()) == {"cameras"}
    assert len(body["cameras"]) == 1
    cam = body["cameras"][0]
    assert cam["id"] == CAM_ID
    assert cam["onvif_port"] == 12001
    assert cam["ffmpeg_pid"] == 111
    assert cam["manufacturer"] == "TestCam"
    assert cam["snapshot_url"] == f"/snapshots/{CAM_ID}.jpg"
    assert cam["rtsp_url"].startswith("rtsp://127.0.0.1:")


def test_list_cameras_empty(client, monkeypatch):
    _set_registry(monkeypatch, [])
    assert client.get("/cameras").get_json() == {"cameras": []}


def test_get_single_camera(client, monkeypatch):
    _set_registry(monkeypatch, [_state()])
    resp = client.get(f"/cameras/{CAM_ID}")
    assert resp.status_code == 200
    assert resp.get_json()["id"] == CAM_ID


def test_get_single_camera_unknown_is_404_json(client, monkeypatch):
    _set_registry(monkeypatch, [])
    resp = client.get("/cameras/nope")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["type"] == "CameraNotFoundError"
    assert "error" in body


# ── Error envelopes ─────────────────────────────────────────────────────────
def test_404_json_envelope(client):
    resp = client.get("/no-such-route")
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not found", "type": "NotFound"}


def test_405_json_envelope(client):
    # POST on a GET-only route. (GET /upload would fall through to the
    # static-file catch-all and 404 instead, so probe with POST /config.)
    resp = client.post("/config")
    assert resp.status_code == 405
    assert resp.get_json() == {
        "error": "method not allowed", "type": "MethodNotAllowed",
    }


# ── Upload ──────────────────────────────────────────────────────────────────
def test_upload_missing_file_is_400_json(client):
    resp = client.post("/upload", data={})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["type"] == "ValidationError"
    assert body["error"] == "no file"


def test_upload_single_returns_cameras_envelope(client, monkeypatch):
    monkeypatch.setattr(app_module, "create_camera",
                        lambda *a, **kw: _state())
    data = {"file": (io.BytesIO(b"fake-video"), "test.mp4")}
    resp = client.post("/upload", data=data,
                       content_type="multipart/form-data")
    assert resp.status_code == 201
    body = resp.get_json()
    # contract: always {"cameras": [...], "count": n}, even for one camera
    assert set(body.keys()) == {"cameras", "count"}
    assert body["count"] == 1
    assert body["cameras"][0]["id"] == CAM_ID


def test_upload_batch_returns_count(client, monkeypatch):
    states = [_state("a" * 36), _state("b" * 36)]
    monkeypatch.setattr(app_module, "create_cameras_batch",
                        lambda *a, **kw: states)
    data = {
        "file": (io.BytesIO(b"fake-video"), "test.mp4"),
        "camera_count": "2",
    }
    resp = client.post("/upload", data=data,
                       content_type="multipart/form-data")
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["count"] == 2
    assert [c["id"] for c in body["cameras"]] == ["a" * 36, "b" * 36]


def test_upload_invalid_manufacturer_rejected(client, monkeypatch):
    monkeypatch.setattr(app_module, "create_camera",
                        lambda *a, **kw: _state())
    data = {
        "file": (io.BytesIO(b"fake-video"), "test.mp4"),
        "camera_name": "<script>alert(1)</script>",
    }
    resp = client.post("/upload", data=data,
                       content_type="multipart/form-data")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "validation failed"


# ── Delete ──────────────────────────────────────────────────────────────────
def test_delete_unknown_camera_is_404_json(client, monkeypatch):
    def _raise(camera_id):
        raise CameraNotFoundError(f"Camera {camera_id} not found")

    monkeypatch.setattr(app_module, "delete_camera", _raise)
    resp = client.delete("/cameras/nope")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["type"] == "CameraNotFoundError"
    assert "nope" in body["error"]


def test_delete_known_camera(client, monkeypatch):
    monkeypatch.setattr(app_module, "delete_camera",
                        lambda camera_id: {"deleted": camera_id})
    resp = client.delete(f"/cameras/{CAM_ID}")
    assert resp.status_code == 200
    assert resp.get_json() == {"deleted": CAM_ID}
