#!/bin/bash
# Setup host ARP isolation for macvlan networking
# Run this ONCE on the Docker host (requires root/sudo).
#
# Without this, the host's physical NIC answers ARP requests for macvlan
# container IPs, causing all cameras to appear with the same MAC address
# to NVR platforms like UniFi Protect.
#
# Usage: sudo bash scripts/setup-macvlan-host.sh [INTERFACE]
#   INTERFACE defaults to eth0

set -e

PARENT_IFACE="${1:-eth0}"

echo "Configuring ARP isolation on interface: $PARENT_IFACE"
echo ""

# arp_ignore=1: Only reply to ARP if the target IP is configured on the
#               interface that received the ARP request.
sudo sysctl -w "net.ipv4.conf.${PARENT_IFACE}.arp_ignore=1"
sudo sysctl -w "net.ipv4.conf.all.arp_ignore=1"

# arp_announce=2: Always use the best local address as the ARP source,
#                 preventing cross-interface ARP pollution.
sudo sysctl -w "net.ipv4.conf.${PARENT_IFACE}.arp_announce=2"
sudo sysctl -w "net.ipv4.conf.all.arp_announce=2"

echo ""
echo "ARP isolation configured for $PARENT_IFACE"
echo ""
echo "To make persistent across reboots, add to /etc/sysctl.conf:"
echo "  net.ipv4.conf.${PARENT_IFACE}.arp_ignore=1"
echo "  net.ipv4.conf.all.arp_ignore=1"
echo "  net.ipv4.conf.${PARENT_IFACE}.arp_announce=2"
echo "  net.ipv4.conf.all.arp_announce=2"
