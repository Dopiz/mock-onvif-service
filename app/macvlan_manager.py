"""Macvlan sub-interface management (Linux only, requires NET_ADMIN)."""
from __future__ import annotations

import ipaddress
import logging
import subprocess
import threading
from typing import Optional

from app.exceptions import MacvlanError

logger = logging.getLogger(__name__)


class _IPAllocator:
    def __init__(self, ip_start: str, ip_end: str) -> None:
        start = ipaddress.ip_address(ip_start)
        end = ipaddress.ip_address(ip_end)
        self.pool = [str(ipaddress.ip_address(int(start) + i))
                     for i in range(int(end) - int(start) + 1)]
        self.allocated: set[str] = set()
        self.lock = threading.Lock()

    def allocate(self) -> str:
        with self.lock:
            for ip in self.pool:
                if ip not in self.allocated:
                    self.allocated.add(ip)
                    return ip
        raise MacvlanError("No available IPs in macvlan range")

    def release(self, ip: str) -> None:
        with self.lock:
            self.allocated.discard(ip)

    def mark_used(self, ip: str) -> None:
        with self.lock:
            self.allocated.add(ip)


class MacvlanManager:
    """Manages macvlan sub-interfaces inside the container.

    Each camera gets its own macvlan interface with a unique IP and MAC,
    allowing NVR platforms to discover each camera as an independent device.

    Requires NET_ADMIN capability in the container.

    Two IP assignment modes:
    - DHCP mode (use_dhcp=True): each interface requests an IP from the router
      via dhclient. No manual IP range needed.
    - Static mode (use_dhcp=False): IPs are allocated from a configured pool.
    """

    def __init__(self, subnet: str, gateway: str, ip_start: str, ip_end: str,
                 parent_iface: str, use_dhcp: bool = False) -> None:
        self.use_dhcp = use_dhcp
        # Resolve the actual interface name (auto-detect if configured name missing)
        self.parent_iface = self._resolve_parent_iface(parent_iface)

        if not use_dhcp:
            self.subnet = ipaddress.ip_network(subnet, strict=False)
            self.prefix_len = self.subnet.prefixlen
            self._allocator = _IPAllocator(ip_start, ip_end)
            logger.info(
                "MacvlanManager initialized (static): parent=%s, range=%s-%s, prefix=/%s",
                self.parent_iface, ip_start, ip_end, self.prefix_len,
            )
        else:
            logger.info("MacvlanManager initialized (DHCP): parent=%s", self.parent_iface)

    def create_interface(self, camera_id: str) -> str:
        """Create a macvlan sub-interface and return the assigned IP.

        Args:
            camera_id: Camera UUID (first 8 chars used for interface name)

        Returns:
            Assigned IP address string

        Raises:
            MacvlanError: If interface creation fails
        """
        iface = self._iface_name(camera_id)

        # Remove stale interface if it exists (e.g. from a previous crash)
        subprocess.run(["ip", "link", "del", iface], capture_output=True, check=False)

        try:
            self._run(["ip", "link", "add", iface, "link", self.parent_iface,
                       "type", "macvlan", "mode", "bridge"])
            self._run(["ip", "link", "set", iface, "up"])

            if self.use_dhcp:
                ip = self._dhcp_request(iface)
            else:
                ip = self._allocator.allocate()
                try:
                    self._run(["ip", "addr", "add", f"{ip}/{self.prefix_len}", "dev", iface])
                except Exception:
                    self._allocator.release(ip)
                    raise

            logger.info("Created macvlan interface %s with IP %s", iface, ip)
            return ip

        except Exception as e:
            # Clean up partially created interface
            subprocess.run(["ip", "link", "del", iface], capture_output=True, check=False)
            raise MacvlanError(f"Failed to create macvlan interface {iface}: {e}") from e

    def delete_interface(self, camera_id: str, camera_ip: Optional[str] = None) -> None:
        """Delete a macvlan sub-interface and release its IP.

        Args:
            camera_id: Camera UUID
            camera_ip: IP to release back to pool (static mode only)
        """
        iface = self._iface_name(camera_id)

        if self.use_dhcp:
            # Release DHCP lease gracefully before removing the interface
            subprocess.run(["dhclient", "-r", iface], capture_output=True, check=False)

        try:
            self._run(["ip", "link", "del", iface])
            logger.info("Deleted macvlan interface %s", iface)
        except Exception as e:
            logger.warning("Failed to delete macvlan interface %s: %s", iface, e)

        if not self.use_dhcp and camera_ip:
            self._allocator.release(camera_ip)
            logger.debug("Released IP %s back to pool", camera_ip)

    def restore_interface(self, camera_id: str, camera_ip: str) -> str:
        """Recreate a macvlan interface from persisted config (on startup).

        In DHCP mode the router may assign a different IP than last time
        (unless it has a static lease by MAC). The returned IP is the
        newly assigned one and should be persisted by the caller.

        Args:
            camera_id: Camera UUID
            camera_ip: Previously assigned IP (used for static mode)

        Returns:
            The IP address on the restored interface
        """
        iface = self._iface_name(camera_id)

        # Remove stale interface if it exists
        subprocess.run(["ip", "link", "del", iface], capture_output=True, check=False)

        try:
            self._run(["ip", "link", "add", iface, "link", self.parent_iface,
                       "type", "macvlan", "mode", "bridge"])
            self._run(["ip", "link", "set", iface, "up"])

            if self.use_dhcp:
                new_ip = self._dhcp_request(iface)
                logger.info("Restored macvlan interface %s via DHCP, IP=%s", iface, new_ip)
                return new_ip
            else:
                self._allocator.mark_used(camera_ip)
                self._run(["ip", "addr", "add", f"{camera_ip}/{self.prefix_len}", "dev", iface])
                logger.info("Restored macvlan interface %s with static IP %s", iface, camera_ip)
                return camera_ip

        except Exception as e:
            subprocess.run(["ip", "link", "del", iface], capture_output=True, check=False)
            if not self.use_dhcp:
                self._allocator.release(camera_ip)
            raise MacvlanError(f"Failed to restore macvlan interface {iface}: {e}") from e

    def cleanup_all(self) -> None:
        """Delete all cam_* macvlan interfaces (called on shutdown)."""
        result = subprocess.run(
            ["ip", "link", "show"],
            capture_output=True, text=True, check=False
        )
        ifaces = [
            line.split(":")[1].strip().split("@")[0]
            for line in result.stdout.splitlines()
            if ": cam_" in line
        ]
        for iface in ifaces:
            if self.use_dhcp:
                subprocess.run(["dhclient", "-r", iface], capture_output=True, check=False)
            subprocess.run(["ip", "link", "del", iface], capture_output=True, check=False)
            logger.info("Cleaned up macvlan interface %s", iface)

    def _dhcp_request(self, iface: str, timeout: int = 15) -> str:
        """Request an IP via DHCP and return the assigned address.

        Args:
            iface: Interface name to run dhclient on
            timeout: Max seconds to wait for DHCP response

        Returns:
            Assigned IP address string
        """
        result = subprocess.run(
            ["dhclient", "-1", "-v", iface],
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            raise MacvlanError(
                f"dhclient failed on {iface}: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return self._get_interface_ip(iface)

    def _get_interface_ip(self, iface: str) -> str:
        """Read the current IPv4 address assigned to the interface."""
        result = subprocess.run(
            ["ip", "-4", "addr", "show", iface],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                return line.split()[1].split("/")[0]
        raise MacvlanError(f"No IPv4 address found on interface {iface}")

    @staticmethod
    def _resolve_parent_iface(configured: str) -> str:
        """Return the configured interface if it exists, otherwise auto-detect.

        Auto-detection strategy:
        - The bridge network interface carries the default route (eth0 typically).
        - The macvlan network interface is any other ethernet interface.
        - We return the first non-default-route ethernet interface found.
        """
        # Check if configured interface exists
        result = subprocess.run(["ip", "link", "show", configured],
                                capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("Using configured macvlan parent interface: %s", configured)
            return configured

        logger.warning(
            "Configured MACVLAN_PARENT_IFACE '%s' not found, "
            "auto-detecting macvlan interface...", configured,
        )

        # Find the default route interface (bridge network)
        route_result = subprocess.run(["ip", "route", "show", "default"],
                                      capture_output=True, text=True)
        default_iface = None
        for line in route_result.stdout.splitlines():
            parts = line.split()
            if "dev" in parts:
                default_iface = parts[parts.index("dev") + 1]
                break

        # List all UP ethernet interfaces
        link_result = subprocess.run(["ip", "link", "show", "up"],
                                     capture_output=True, text=True)
        candidates = []
        for line in link_result.stdout.splitlines():
            # Lines look like: "2: eth0: <FLAGS> ..."
            if ": <" not in line:
                continue
            iface = line.split(":")[1].strip()
            # Skip loopback, docker bridges, veth pairs, and the default route iface
            if iface in ("lo", default_iface):
                continue
            if any(iface.startswith(p) for p in ("lo", "docker", "br-", "veth", "virbr")):
                continue
            candidates.append(iface)

        if candidates:
            detected = candidates[0]
            logger.info(
                "Auto-detected macvlan parent interface: %s (default route is on %s)",
                detected, default_iface,
            )
            return detected

        # Last resort: if only one interface exists (e.g. single-NIC macvlan),
        # use eth0 as the parent (DHCP mode — no static IP on the macvlan iface)
        if default_iface:
            logger.warning(
                "No secondary interface found, falling back to default route "
                "interface: %s", default_iface,
            )
            return default_iface

        raise MacvlanError(
            f"Cannot find macvlan parent interface. "
            f"Configured '{configured}' missing and auto-detection failed. "
            f"Set MACVLAN_PARENT_IFACE to the correct interface name."
        )

    @staticmethod
    def _iface_name(camera_id: str) -> str:
        return f"cam_{camera_id[:8]}"

    @staticmethod
    def _run(cmd: list[str]) -> None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise MacvlanError(result.stderr.strip() or result.stdout.strip())
