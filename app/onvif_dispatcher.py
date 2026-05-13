"""In-process ONVIF dispatcher.

Replaces N onvif_server.py subprocesses with a single Flask app shared across
camera-specific werkzeug servers (one per port). The huge memory win is that
we no longer pay for one Python interpreter per camera.

This is OPT-IN via ``ONVIF_DISPATCHER_ENABLED=true`` so existing deployments
that depend on the per-camera subprocess model are not affected.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from flask import Flask, Response, request, send_file
from werkzeug.serving import make_server

from app.config import SNAPSHOTS_DIR
from app.onvif_handlers import OnvifContext, dispatch_device, dispatch_media

logger = logging.getLogger(__name__)


# ── Per-port server wrapper ────────────────────────────────────────────────
class _PortServer:
    def __init__(self, app: Flask, host: str, port: int):
        # threaded=True so concurrent ONVIF requests do not serialise
        self._server = make_server(host, port, app, threaded=True)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name=f"onvif-port-{port}",
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=3)


# ── Dispatcher ─────────────────────────────────────────────────────────────
class OnvifDispatcher:
    """Single Flask app served from many ports/IPs.

    Routes per request: camera is identified by ``request.host`` (IP and port).
    """

    def __init__(self) -> None:
        self.app = Flask(__name__)
        self._port_to_ctx: dict[int, OnvifContext] = {}
        self._ip_to_ctx: dict[str, OnvifContext] = {}
        self._lock = threading.Lock()
        self._servers: dict[tuple[str, int], _PortServer] = {}
        self._register_routes()

    # ── Context lookup ────────────────────────────────────────────────────
    def _lookup_ctx(self) -> Optional[OnvifContext]:
        host = request.host
        if ":" in host:
            ip, port_s = host.rsplit(":", 1)
            try:
                port = int(port_s)
            except ValueError:
                port = 80
        else:
            ip, port = host, 80
        with self._lock:
            # Macvlan: lookup by IP. Standard: lookup by port.
            ctx = self._ip_to_ctx.get(ip)
            if ctx is None:
                ctx = self._port_to_ctx.get(port)
        return ctx

    # ── Routes ────────────────────────────────────────────────────────────
    def _register_routes(self) -> None:
        @self.app.route("/onvif/device_service", methods=["POST"])
        def device_service():
            ctx = self._lookup_ctx()
            if ctx is None:
                return Response("Unknown camera", status=404)
            xml_data = request.data.decode("utf-8", errors="ignore")
            ip, port = self._extract_host_port()
            resp = dispatch_device(ctx, xml_data, ip, port)
            logger.info("[%s] device: %s", ctx.camera_id[:8], request.remote_addr)
            return Response(resp, mimetype="application/soap+xml")

        @self.app.route("/onvif/media_service", methods=["POST"])
        def media_service():
            ctx = self._lookup_ctx()
            if ctx is None:
                return Response("Unknown camera", status=404)
            xml_data = request.data.decode("utf-8", errors="ignore")
            ip, port = self._extract_host_port()
            resp = dispatch_media(ctx, xml_data, ip, port)
            logger.info("[%s] media: %s", ctx.camera_id[:8], request.remote_addr)
            return Response(resp, mimetype="application/soap+xml")

        @self.app.route("/snapshot.jpg", methods=["GET"])
        def snapshot():
            ctx = self._lookup_ctx()
            if ctx is None:
                return Response("Unknown camera", status=404)
            snap_id = ctx.shared_video_id or ctx.camera_id
            path = SNAPSHOTS_DIR / f"{snap_id}.jpg"
            if path.exists():
                return send_file(str(path), mimetype="image/jpeg")
            return Response("Snapshot not available", status=404)

        @self.app.route(
            "/onvif/device_service.wsdl", methods=["GET"]
        )
        @self.app.route("/onvif/media_service.wsdl", methods=["GET"])
        def wsdl():
            return Response('<?xml version="1.0"?><definitions/>', mimetype="text/xml")

    @staticmethod
    def _extract_host_port() -> tuple[str, int]:
        host = request.host
        if ":" in host:
            ip, port_s = host.rsplit(":", 1)
            try:
                return ip, int(port_s)
            except ValueError:
                return ip, 80
        return host, 80

    # ── Camera registration ───────────────────────────────────────────────
    def add_camera(self, ctx: OnvifContext, bind_ip: Optional[str] = None,
                   bind_host: str = "0.0.0.0") -> None:
        """Register a camera and start a port-server for it.

        Args:
            ctx: ONVIF context for the camera.
            bind_ip: Macvlan IP to bind to (port 80). When set, port-based
                routing is bypassed and lookup is by IP.
            bind_host: Host to bind when not in macvlan mode.
        """
        with self._lock:
            if bind_ip:
                self._ip_to_ctx[bind_ip] = ctx
                key = (bind_ip, 80)
                if key in self._servers:
                    return
                srv = _PortServer(self.app, bind_ip, 80)
            else:
                self._port_to_ctx[ctx.server_port] = ctx
                key = (bind_host, ctx.server_port)
                if key in self._servers:
                    return
                srv = _PortServer(self.app, bind_host, ctx.server_port)
            self._servers[key] = srv
        srv.start()
        logger.info("Dispatcher serving camera %s on %s:%s",
                    ctx.camera_id[:8], key[0], key[1])

    def remove_camera(self, camera_id: str) -> None:
        keys_to_stop: list[tuple[str, int]] = []
        with self._lock:
            for port, ctx in list(self._port_to_ctx.items()):
                if ctx.camera_id == camera_id:
                    del self._port_to_ctx[port]
                    keys_to_stop.append(("0.0.0.0", port))
            for ip, ctx in list(self._ip_to_ctx.items()):
                if ctx.camera_id == camera_id:
                    del self._ip_to_ctx[ip]
                    keys_to_stop.append((ip, 80))
            servers = [self._servers.pop(k, None) for k in keys_to_stop]
        for srv in servers:
            if srv is not None:
                srv.stop()
        if keys_to_stop:
            logger.info("Dispatcher removed camera %s", camera_id[:8])

    def stop_all(self) -> None:
        with self._lock:
            servers = list(self._servers.values())
            self._servers.clear()
            self._port_to_ctx.clear()
            self._ip_to_ctx.clear()
        for srv in servers:
            srv.stop()


# Singleton
_dispatcher: Optional[OnvifDispatcher] = None
_dispatcher_lock = threading.Lock()


def get_dispatcher() -> OnvifDispatcher:
    global _dispatcher
    if _dispatcher is None:
        with _dispatcher_lock:
            if _dispatcher is None:
                _dispatcher = OnvifDispatcher()
    return _dispatcher
