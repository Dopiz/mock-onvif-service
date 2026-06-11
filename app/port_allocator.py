"""Thread-safe ONVIF port allocator with TOCTOU mitigation."""
from __future__ import annotations

import logging
import socket
from threading import Lock

from app.config import ONVIF_PORT_MAX, ONVIF_PORT_MIN, PORT_PROBE_TIMEOUT_SECONDS
from app.exceptions import PortAllocationError

logger = logging.getLogger(__name__)


def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Probe whether a TCP port is bound right now. Best-effort; subject to TOCTOU."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(PORT_PROBE_TIMEOUT_SECONDS)
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

    def allocate_block(self, count: int) -> list[int]:
        """Allocate ``count`` ports, preferring the first contiguous run.

        Batch-created cameras read better in the UI when their ports are
        consecutive, so holes left by deleted cameras are skipped when a large
        enough contiguous run exists further up the range. Falls back to
        scattered first-fit allocation when the range is too fragmented, and
        raises :class:`PortAllocationError` only when fewer than ``count``
        ports are free in total.
        """
        if count <= 0:
            return []
        with self._lock:
            free: list[int] = []
            run: list[int] = []
            for port in range(self._port_min, self._port_max):
                if port in self._used:
                    run = []
                    continue
                if _is_port_in_use(port):
                    self._used.add(port)
                    run = []
                    continue
                free.append(port)
                run.append(port)
                if len(run) == count:
                    self._used.update(run)
                    return run
            if len(free) >= count:
                scattered = free[:count]
                self._used.update(scattered)
                logger.warning(
                    "No contiguous run of %d ports in %d-%d; "
                    "falling back to scattered allocation",
                    count, self._port_min, self._port_max,
                )
                return scattered
        raise PortAllocationError(
            f"Need {count} ports but only {len(free)} free "
            f"in range {self._port_min}-{self._port_max}"
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


# ── Singleton accessor (lazy, double-checked locking) ──────────────────────
_default: PortAllocator | None = None
_default_lock = Lock()


def get_default_allocator() -> PortAllocator:
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = PortAllocator()
    return _default
