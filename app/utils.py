"""Small shared helpers."""
from __future__ import annotations

import socket
from functools import lru_cache

from app.config import EXTERNAL_IP


@lru_cache(maxsize=1)
def get_server_ip() -> str:
    """Return the IP advertised to NVRs/Web UI for RTSP/ONVIF URLs.

    Cached after the first call so high-frequency callers (e.g. ``/cameras``
    with many cameras) don't open a UDP socket per camera.
    """
    if EXTERNAL_IP:
        return EXTERNAL_IP
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
