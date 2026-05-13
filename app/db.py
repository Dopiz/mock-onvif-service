"""SQLite persistence for camera metadata. Replaces per-camera YAML configs.

PIDs and other ephemeral state stay in-memory; only durable metadata is stored.
A migration step on startup imports any pre-existing ``data/cameras/*.yaml``.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import yaml

from app.config import CAMERAS_DIR, DB_PATH
from app.exceptions import PersistenceError

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cameras (
    camera_id        TEXT PRIMARY KEY,
    video_path       TEXT NOT NULL,
    onvif_port       INTEGER NOT NULL,
    camera_ip        TEXT,
    shared_video_id  TEXT,
    sub_profile      INTEGER NOT NULL DEFAULT 0,
    manufacturer     TEXT NOT NULL DEFAULT 'MockONVIF',
    created_at       INTEGER NOT NULL,
    video_params     TEXT NOT NULL              -- JSON blob
);

CREATE INDEX IF NOT EXISTS idx_cameras_shared_video_id
    ON cameras(shared_video_id);
"""


@dataclass
class CameraRecord:
    """Durable camera metadata. PIDs and runtime info live elsewhere."""
    camera_id: str
    video_path: str
    onvif_port: int
    video_params: dict
    created_at: int
    sub_profile: bool = False
    manufacturer: str = "MockONVIF"
    shared_video_id: Optional[str] = None
    camera_ip: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "CameraRecord":
        return cls(
            camera_id=row["camera_id"],
            video_path=row["video_path"],
            onvif_port=row["onvif_port"],
            video_params=json.loads(row["video_params"]),
            created_at=row["created_at"],
            sub_profile=bool(row["sub_profile"]),
            manufacturer=row["manufacturer"],
            shared_video_id=row["shared_video_id"],
            camera_ip=row["camera_ip"],
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


class CameraRepository:
    """Thread-safe SQLite repository for CameraRecord."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # ``check_same_thread=False`` so worker threads can reuse the connection.
        # We serialise writes with a lock, sqlite itself handles read concurrency.
        self._conn = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)

    # ── CRUD ────────────────────────────────────────────────────────────────
    def upsert(self, rec: CameraRecord) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    """
                    INSERT INTO cameras (camera_id, video_path, onvif_port, camera_ip,
                                         shared_video_id, sub_profile, manufacturer,
                                         created_at, video_params)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(camera_id) DO UPDATE SET
                        video_path     = excluded.video_path,
                        onvif_port     = excluded.onvif_port,
                        camera_ip      = excluded.camera_ip,
                        shared_video_id= excluded.shared_video_id,
                        sub_profile    = excluded.sub_profile,
                        manufacturer   = excluded.manufacturer,
                        created_at     = excluded.created_at,
                        video_params   = excluded.video_params
                    """,
                    (
                        rec.camera_id,
                        rec.video_path,
                        rec.onvif_port,
                        rec.camera_ip,
                        rec.shared_video_id,
                        int(rec.sub_profile),
                        rec.manufacturer,
                        rec.created_at,
                        json.dumps(rec.video_params),
                    ),
                )
        except sqlite3.DatabaseError as e:
            raise PersistenceError(f"DB upsert failed: {e}") from e

    def delete(self, camera_id: str) -> None:
        try:
            with self._lock:
                self._conn.execute("DELETE FROM cameras WHERE camera_id = ?", (camera_id,))
        except sqlite3.DatabaseError as e:
            raise PersistenceError(f"DB delete failed: {e}") from e

    def get(self, camera_id: str) -> Optional[CameraRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM cameras WHERE camera_id = ?", (camera_id,)
            ).fetchone()
        return CameraRecord.from_row(row) if row else None

    def all(self) -> list[CameraRecord]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM cameras ORDER BY created_at ASC").fetchall()
        return [CameraRecord.from_row(r) for r in rows]

    def update_onvif_port(self, camera_id: str, port: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE cameras SET onvif_port = ? WHERE camera_id = ?", (port, camera_id)
            )

    def update_camera_ip(self, camera_id: str, ip: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE cameras SET camera_ip = ? WHERE camera_id = ?", (ip, camera_id)
            )

    def count_using_shared_video(self, shared_video_id: str, exclude_id: str | None = None) -> int:
        with self._lock:
            sql = "SELECT COUNT(*) FROM cameras WHERE shared_video_id = ?"
            params: list = [shared_video_id]
            if exclude_id is not None:
                sql += " AND camera_id != ?"
                params.append(exclude_id)
            return self._conn.execute(sql, params).fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ── One-time YAML → SQLite migration ───────────────────────────────────────
def migrate_yaml_configs(repo: CameraRepository, configs_dir: Path = CAMERAS_DIR) -> int:
    """Import legacy ``config_*.yaml`` files into the SQLite repo.

    Returns the number of records imported. YAML files are kept in place but
    are ignored on subsequent boots once the camera_id exists in the DB.
    """
    if not configs_dir.exists():
        return 0
    imported = 0
    for path in configs_dir.glob("config_*.yaml"):
        try:
            with open(path, "r") as f:
                cfg = yaml.safe_load(f) or {}
            camera_id = cfg.get("camera_id")
            if not camera_id:
                logger.warning("Skipping %s: no camera_id", path.name)
                continue
            if repo.get(camera_id) is not None:
                continue  # already migrated

            video_params = cfg.get("video_params")
            if not video_params:
                logger.warning("Skipping %s: missing video_params", path.name)
                continue

            shared = cfg.get("shared_video_id")
            from app.config import VIDEOS_DIR  # local import to avoid cycle at module load
            video_file = (VIDEOS_DIR / f"{shared}_shared.mp4") if shared else (VIDEOS_DIR / f"{camera_id}.mp4")

            rec = CameraRecord(
                camera_id=camera_id,
                video_path=str(video_file),
                onvif_port=int(cfg.get("onvif_port", 12000)),
                video_params=video_params,
                created_at=int(cfg.get("created_at", 0)),
                sub_profile=bool(cfg.get("sub_profile", False)),
                manufacturer=cfg.get("manufacturer", "MockONVIF"),
                shared_video_id=shared,
                camera_ip=cfg.get("camera_ip"),
            )
            repo.upsert(rec)
            imported += 1
            logger.info("Migrated camera %s from %s", camera_id[:8], path.name)
            # YAML is now redundant — SQLite owns this record. Remove the file so
            # data/cameras eventually empties itself out and stops being a source
            # of confusion.
            try:
                path.unlink()
            except OSError as e:
                logger.warning("Migrated but could not delete %s: %s", path.name, e)
        except Exception as e:
            logger.warning("Failed to migrate %s: %s", path.name, e)

    # Once empty, drop the legacy directory entirely (cosmetic).
    if configs_dir.exists():
        try:
            remaining = any(configs_dir.iterdir())
            if not remaining:
                configs_dir.rmdir()
                logger.info("Removed empty legacy directory: %s", configs_dir)
        except OSError:
            pass
    return imported


# ── Singleton accessor ─────────────────────────────────────────────────────
_repo: CameraRepository | None = None
_repo_lock = threading.Lock()


def get_repository() -> CameraRepository:
    global _repo
    if _repo is None:
        with _repo_lock:
            if _repo is None:
                _repo = CameraRepository()
    return _repo
