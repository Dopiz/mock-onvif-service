"""Shared ONVIF HTTP shell.

Both ONVIF front-ends — the per-camera subprocess (:mod:`onvif_server`) and the
in-process dispatcher (:mod:`app.onvif_dispatcher`) — expose the same routes.
This module registers them once, parameterised by a *context resolver* so each
front-end decides how the current request maps to an :class:`OnvifContext`.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from flask import Flask, Response, jsonify, request, send_file

from app.config import SNAPSHOTS_DIR
from app.onvif_handlers import OnvifContext, dispatch_device, dispatch_media

logger = logging.getLogger(__name__)

CtxResolver = Callable[[], Optional[OnvifContext]]


def extract_host_port(default_port: int = 80) -> tuple[str, int]:
    """Split ``request.host`` into (ip, port)."""
    host = request.host
    if ":" in host:
        ip, port_s = host.rsplit(":", 1)
        try:
            return ip, int(port_s)
        except ValueError:
            return ip, default_port
    return host, default_port


def register_onvif_routes(app: Flask, ctx_resolver: CtxResolver) -> None:
    """Register the ONVIF SOAP/snapshot/wsdl/health routes on ``app``."""

    @app.route("/onvif/device_service", methods=["POST"])
    def device_service():
        ctx = ctx_resolver()
        if ctx is None:
            return Response("Unknown camera", status=404)
        xml_data = request.data.decode("utf-8", errors="ignore")
        ip, port = extract_host_port()
        body = dispatch_device(ctx, xml_data, ip, port)
        logger.info("[%s] device: %s", ctx.camera_id[:8], request.remote_addr)
        return Response(body, mimetype="application/soap+xml")

    @app.route("/onvif/media_service", methods=["POST"])
    def media_service():
        ctx = ctx_resolver()
        if ctx is None:
            return Response("Unknown camera", status=404)
        xml_data = request.data.decode("utf-8", errors="ignore")
        ip, port = extract_host_port()
        body = dispatch_media(ctx, xml_data, ip, port)
        logger.info("[%s] media: %s", ctx.camera_id[:8], request.remote_addr)
        return Response(body, mimetype="application/soap+xml")

    @app.route("/snapshot.jpg", methods=["GET"])
    def snapshot():
        ctx = ctx_resolver()
        if ctx is None:
            return Response("Unknown camera", status=404)
        snap_id = ctx.shared_video_id or ctx.camera_id
        path = SNAPSHOTS_DIR / f"{snap_id}.jpg"
        if path.exists():
            return send_file(str(path.resolve()), mimetype="image/jpeg")
        return Response("Snapshot not available", status=404)

    @app.route("/onvif/device_service.wsdl", methods=["GET"])
    @app.route("/onvif/media_service.wsdl", methods=["GET"])
    def wsdl():
        return Response('<?xml version="1.0"?><definitions/>', mimetype="text/xml")

    @app.route("/health", methods=["GET"])
    def health():
        payload: dict = {"status": "ok"}
        ctx = ctx_resolver()
        if ctx is not None:
            payload["camera_id"] = ctx.camera_id
        return jsonify(payload)
