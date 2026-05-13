"""Centralised runtime configuration (env-driven)."""
from __future__ import annotations

import os
from pathlib import Path

# ── Server ─────────────────────────────────────────────────────────────────
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "9999"))
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() in ("true", "1", "yes")

# Cap upload size (bytes). Default 500 MB.
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# Cap the transcoded output size. 180s × 50Mbps (worst-case combo allowed by
# the validation ranges) is ~1.1 GB, so 1 GB rejects only that extreme. Any
# legitimate 3-min camera config sits well under this.
MAX_VIDEO_SIZE_MB = int(os.getenv("MAX_VIDEO_SIZE_MB", "1024"))
MAX_VIDEO_SIZE_BYTES = MAX_VIDEO_SIZE_MB * 1024 * 1024

# Orphan scan: detects mp4/jpg in ./data not referenced by SQLite.
DATA_CLEANUP_ENABLED = os.getenv("DATA_CLEANUP_ENABLED", "true").lower() in ("true", "1", "yes")
DATA_CLEANUP_INTERVAL_HOURS = float(os.getenv("DATA_CLEANUP_INTERVAL_HOURS", "1"))
# Files newer than this are skipped (could be a transcode in flight).
DATA_ORPHAN_GRACE_SECONDS = int(os.getenv("DATA_ORPHAN_GRACE_SECONDS", "300"))

# ── MediaMTX ───────────────────────────────────────────────────────────────
MEDIAMTX_HOST = os.getenv("MEDIAMTX_HOST", "127.0.0.1")
MEDIAMTX_RTSP_PORT = int(os.getenv("MEDIAMTX_PORT", "8554"))

# ── ONVIF ──────────────────────────────────────────────────────────────────
ONVIF_PORT_MIN = int(os.getenv("ONVIF_PORT_MIN", "12000"))
ONVIF_PORT_MAX = int(os.getenv("ONVIF_PORT_MAX", "13000"))

# Single-dispatcher mode collapses N onvif_server subprocesses into one in-process
# Flask app that routes by request.host. Opt-in; defaults off for backward compat.
ONVIF_DISPATCHER_ENABLED = os.getenv("ONVIF_DISPATCHER_ENABLED", "false").lower() == "true"

# ── Macvlan ────────────────────────────────────────────────────────────────
MACVLAN_ENABLED = os.getenv("MACVLAN_ENABLED", "false").lower() == "true"
MACVLAN_DHCP = os.getenv("MACVLAN_DHCP", "false").lower() == "true"
MACVLAN_SUBNET = os.getenv("MACVLAN_SUBNET", "192.168.0.0/24")
MACVLAN_GATEWAY = os.getenv("MACVLAN_GATEWAY", "192.168.0.1")
MACVLAN_IP_START = os.getenv("MACVLAN_IP_START", "192.168.0.201")
MACVLAN_IP_END = os.getenv("MACVLAN_IP_END", "192.168.0.250")
MACVLAN_PARENT_IFACE = os.getenv("MACVLAN_PARENT_IFACE", "eth1")

# ── Paths ──────────────────────────────────────────────────────────────────
DATA_DIR = Path("./data")
VIDEOS_DIR = DATA_DIR / "videos"
CAMERAS_DIR = DATA_DIR / "cameras"  # legacy YAML configs (migrated to SQLite)
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
DB_PATH = DATA_DIR / "service.db"

LOGS_DIR = Path("./logs")
FFMPEG_LOGS_DIR = LOGS_DIR / "ffmpeg"
ONVIF_LOGS_DIR = LOGS_DIR / "onvif"

# ── Concurrency / batching ─────────────────────────────────────────────────
BATCH_MAX_WORKERS = int(os.getenv("BATCH_MAX_WORKERS", "20"))
PROCESS_KILL_GRACE_SECONDS = float(os.getenv("PROCESS_KILL_GRACE_SECONDS", "0.5"))

# ── Watchdog ───────────────────────────────────────────────────────────────
WATCHDOG_ENABLED = os.getenv("WATCHDOG_ENABLED", "true").lower() in ("true", "1", "yes")
WATCHDOG_INTERVAL_SECONDS = int(os.getenv("WATCHDOG_INTERVAL_SECONDS", "15"))
WATCHDOG_MAX_RESTARTS = int(os.getenv("WATCHDOG_MAX_RESTARTS", "5"))


def ensure_dirs() -> None:
    """Create every directory the service writes to (idempotent).

    Note: ``CAMERAS_DIR`` is deliberately NOT created here. It only exists for
    legacy YAML migration; once migration runs it gets pruned.
    """
    for d in (VIDEOS_DIR, SNAPSHOTS_DIR, FFMPEG_LOGS_DIR, ONVIF_LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
