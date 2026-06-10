import os
import socket


def get_server_ip() -> str:
    """Return the IP advertised to NVRs/Web UI for RTSP/ONVIF URLs."""
    external_ip = os.getenv("EXTERNAL_IP")
    if external_ip:
        return external_ip
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def is_port_in_use(port: int) -> bool:
    """Kept for backward-compat. Prefer app.port_allocator._is_port_in_use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.1)
        return s.connect_ex(("127.0.0.1", port)) == 0
