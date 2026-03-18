import os
import socket


def get_server_ip():
    """Get the server's external IP address for RTSP/ONVIF URLs

    Priority:
    1. EXTERNAL_IP environment variable (for Docker deployment with host IP)
    2. Auto-detect via socket connection
    3. Fallback to 127.0.0.1
    """
    # Check if EXTERNAL_IP is set (for Docker)
    external_ip = os.getenv('EXTERNAL_IP')
    if external_ip:
        return external_ip

    try:
        # Create a socket to determine the local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def is_port_in_use(port):
    """Check if a port is already in use"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0
