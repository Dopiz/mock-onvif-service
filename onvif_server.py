#!/usr/bin/env python3
"""Per-camera ONVIF subprocess entry point.

In the default deployment each camera spawns one of these. Set
``ONVIF_DISPATCHER_ENABLED=true`` to switch to the in-process dispatcher
(:mod:`app.onvif_dispatcher`) and skip this subprocess entirely.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from flask import Flask, Response, request, send_file

from app.onvif_handlers import OnvifContext, dispatch_device, dispatch_media

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Context from env vars ──────────────────────────────────────────────────
CAMERA_ID = os.environ.get("ONVIF_CAMERA_ID", "default")
RTSP_URL = os.environ.get("ONVIF_RTSP_URL", "rtsp://127.0.0.1:8554/default")
SERVER_PORT = int(os.environ.get("ONVIF_PORT", "12999"))
SHARED_VIDEO_ID = os.environ.get("ONVIF_SHARED_VIDEO_ID")

ctx = OnvifContext(
    camera_id=CAMERA_ID,
    rtsp_url=RTSP_URL,
    width=int(os.environ.get("ONVIF_WIDTH", "1920")),
    height=int(os.environ.get("ONVIF_HEIGHT", "1080")),
    fps=float(os.environ.get("ONVIF_FPS", "30")),
    video_bitrate_kbps=int(os.environ.get("ONVIF_VIDEO_BITRATE_KBPS", "4096")),
    audio_bitrate_kbps=int(os.environ.get("ONVIF_AUDIO_BITRATE_KBPS", "128")),
    sub_profile=os.environ.get("ONVIF_SUB_PROFILE", "false").lower() == "true",
    manufacturer=os.environ.get("ONVIF_MANUFACTURER", "MockONVIF"),
    shared_video_id=SHARED_VIDEO_ID,
    server_port=SERVER_PORT,
)

app = Flask(__name__)


def _extract_host_port() -> tuple[str, int]:
    host = request.host
    if ":" in host:
        ip, port_s = host.rsplit(":", 1)
        try:
            return ip, int(port_s)
        except ValueError:
            return ip, 80
    return host, 80


@app.route("/onvif/device_service", methods=["POST"])
def device_service():
    xml_data = request.data.decode("utf-8", errors="ignore")
    ip, port = _extract_host_port()
    body = dispatch_device(ctx, xml_data, ip, port)
    logger.info("[Device] %s from %s", request.path, request.remote_addr)
    return Response(body, mimetype="application/soap+xml")


@app.route("/onvif/media_service", methods=["POST"])
def media_service():
    xml_data = request.data.decode("utf-8", errors="ignore")
    ip, port = _extract_host_port()
    body = dispatch_media(ctx, xml_data, ip, port)
    logger.info("[Media] %s from %s", request.path, request.remote_addr)
    return Response(body, mimetype="application/soap+xml")


@app.route("/onvif/device_service.wsdl", methods=["GET"])
@app.route("/onvif/media_service.wsdl", methods=["GET"])
def wsdl():
    return Response('<?xml version="1.0"?><definitions/>', mimetype="text/xml")


@app.route("/snapshot.jpg", methods=["GET"])
def snapshot():
    snap_id = SHARED_VIDEO_ID or CAMERA_ID
    path = Path(f"./data/snapshots/{snap_id}.jpg")
    if path.exists():
        return send_file(str(path), mimetype="image/jpeg")
    return Response("Snapshot not available", status=404)


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "camera_id": CAMERA_ID}


def main() -> None:
    bind_host = os.getenv("ONVIF_SERVER_IP", "0.0.0.0")
    logger.info("ONVIF subprocess started: camera=%s port=%d rtsp=%s",
                CAMERA_ID[:8], SERVER_PORT, RTSP_URL)
    try:
        from waitress import serve
        serve(app, host=bind_host, port=SERVER_PORT, threads=4)
    except ImportError:
        app.run(host=bind_host, port=SERVER_PORT, debug=False,
                use_reloader=False, threaded=True)
    except Exception as e:
        logger.error("ONVIF subprocess crashed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
