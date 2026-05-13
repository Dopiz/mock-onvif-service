"""Thread-safe ONVIF port allocator with TOCTOU mitigation."""
from __future__ import annotations

import socket
from threading import Lock

from app.config import ONVIF_PORT_MAX, ONVIF_PORT_MIN
from app.exceptions import PortAllocationError


def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Probe whether a TCP port is bound right now. Best-effort; subject to TOCTOU."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.1)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False


class PortAllocator:
    def __init__(self, port_min: int = ONVIF_PORT_MIN, port_max: int = ONVIF_PORT_MAX):
        self._port_min = port_min
        self._port_max = port_max
        self._used: set[int] = set()
        self._lock = Lock()

    def prime(self, used_ports: set[int]) -> None:
        """Seed the allocator with already-known-used ports (e.g. on restore)."""
        with self._lock:
            self._used |= set(used_ports)

    def allocate(self) -> int:
        with self._lock:
            for port in range(self._port_min, self._port_max):
                if port in self._used:
                    continue
                if _is_port_in_use(port):
                    self._used.add(port)
                    continue
                self._used.add(port)
                return port
        raise PortAllocationError(
            f"No available ports in range {self._port_min}-{self._port_max}"
        )

    def reserve(self, port: int) -> bool:
        """Reserve a specific port if free. Returns False if already taken."""
        with self._lock:
            if port in self._used or _is_port_in_use(port):
                return False
            self._used.add(port)
            return True

    def release(self, port: int) -> None:
        with self._lock:
            self._used.discard(port)


_default = PortAllocator()


def get_default_allocator() -> PortAllocator:
    return _default
