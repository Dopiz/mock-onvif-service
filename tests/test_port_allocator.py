"""PortAllocator with an injected small range and real localhost sockets."""
import socket

import pytest

from app.exceptions import PortAllocationError
from app.port_allocator import PortAllocator, _is_port_in_use


def _find_free_port_block(count: int, start: int = 41000, end: int = 60000) -> int:
    """Find `count` consecutive bindable ports; return the first one."""
    for base in range(start, end, count):
        socks = []
        try:
            for offset in range(count):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(("127.0.0.1", base + offset))
                socks.append(s)
            return base
        except OSError:
            continue
        finally:
            for s in socks:
                s.close()
    raise RuntimeError("no free port block found")


class TestAllocateRelease:
    def test_allocates_distinct_ports_in_range(self):
        base = _find_free_port_block(3)
        alloc = PortAllocator(base, base + 3)
        ports = {alloc.allocate(), alloc.allocate(), alloc.allocate()}
        assert ports == {base, base + 1, base + 2}

    def test_release_makes_port_reusable(self):
        base = _find_free_port_block(1)
        alloc = PortAllocator(base, base + 1)
        p = alloc.allocate()
        with pytest.raises(PortAllocationError):
            alloc.allocate()
        alloc.release(p)
        assert alloc.allocate() == p

    def test_exhaustion_raises(self):
        base = _find_free_port_block(2)
        alloc = PortAllocator(base, base + 2)
        alloc.allocate()
        alloc.allocate()
        with pytest.raises(PortAllocationError):
            alloc.allocate()

    def test_empty_range_raises_immediately(self):
        alloc = PortAllocator(50000, 50000)
        with pytest.raises(PortAllocationError):
            alloc.allocate()


class TestBoundPortSkipped:
    def test_pre_bound_socket_is_skipped(self):
        base = _find_free_port_block(2)
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            blocker.bind(("127.0.0.1", base))
            blocker.listen(1)
            alloc = PortAllocator(base, base + 2)
            # TOCTOU mitigation: connect-probe sees the bound port and skips it
            assert alloc.allocate() == base + 1
        finally:
            blocker.close()

    def test_reserve_refuses_bound_port(self):
        base = _find_free_port_block(2)
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            blocker.bind(("127.0.0.1", base))
            blocker.listen(1)
            alloc = PortAllocator(base, base + 2)
            assert alloc.reserve(base) is False
            assert alloc.reserve(base + 1) is True
            assert alloc.reserve(base + 1) is False  # already taken in-memory
        finally:
            blocker.close()


class TestPrime:
    def test_primed_ports_not_allocated(self):
        base = _find_free_port_block(2)
        alloc = PortAllocator(base, base + 2)
        alloc.prime({base})
        assert alloc.allocate() == base + 1


def test_is_port_in_use_probe():
    base = _find_free_port_block(1)
    assert _is_port_in_use(base) is False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", base))
        s.listen(1)
        assert _is_port_in_use(base) is True
    finally:
        s.close()
