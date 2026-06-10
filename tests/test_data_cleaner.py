"""Orphan scanner with tmp data dirs and a monkeypatched repository."""
import os
import time

import pytest

import app.data_cleaner as data_cleaner
from app.db import CameraRecord

OLD = time.time() - 3600  # well past any grace window we use here


class FakeRepo:
    def __init__(self, records):
        self._records = records

    def all(self):
        return self._records


def _record(camera_id, video_name, *, sub_profile=False, shared_video_id=None):
    return CameraRecord(
        camera_id=camera_id,
        video_path=f"data/videos/{video_name}",
        onvif_port=12001,
        video_params={},
        created_at=0,
        sub_profile=sub_profile,
        shared_video_id=shared_video_id,
    )


def _touch(path, mtime=OLD):
    path.write_bytes(b"x" * 10)
    os.utime(path, (mtime, mtime))


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    snapshots = tmp_path / "snapshots"
    videos.mkdir()
    snapshots.mkdir()
    monkeypatch.setattr(data_cleaner, "VIDEOS_DIR", videos)
    monkeypatch.setattr(data_cleaner, "SNAPSHOTS_DIR", snapshots)
    return videos, snapshots


def _set_repo(monkeypatch, records):
    monkeypatch.setattr(data_cleaner, "get_repository", lambda: FakeRepo(records))


class TestOrphanDetection:
    def test_orphans_deleted_owned_kept(self, dirs, monkeypatch):
        videos, snapshots = dirs
        _set_repo(monkeypatch, [_record("cam1", "cam1.mp4")])

        _touch(videos / "cam1.mp4")        # owned
        _touch(videos / "ghost.mp4")       # orphan
        _touch(snapshots / "cam1.jpg")     # owned
        _touch(snapshots / "ghost.jpg")    # orphan

        stats = data_cleaner.scan_orphans(grace_seconds=60)

        assert stats.videos_deleted == 1
        assert stats.snapshots_deleted == 1
        assert stats.bytes_freed == 20
        assert (videos / "cam1.mp4").exists()
        assert not (videos / "ghost.mp4").exists()
        assert (snapshots / "cam1.jpg").exists()
        assert not (snapshots / "ghost.jpg").exists()

    def test_sub_profile_video_is_owned(self, dirs, monkeypatch):
        videos, _ = dirs
        _set_repo(monkeypatch, [_record("cam1", "cam1.mp4", sub_profile=True)])
        _touch(videos / "cam1.mp4")
        _touch(videos / "cam1_sub.mp4")

        stats = data_cleaner.scan_orphans(grace_seconds=60)

        assert stats.videos_deleted == 0
        assert (videos / "cam1_sub.mp4").exists()

    def test_shared_video_snapshot_is_owned(self, dirs, monkeypatch):
        _, snapshots = dirs
        _set_repo(monkeypatch, [
            _record("cam1", "vidX_shared.mp4", shared_video_id="vidX"),
            _record("cam2", "vidX_shared.mp4", shared_video_id="vidX"),
        ])
        _touch(snapshots / "vidX.jpg")

        stats = data_cleaner.scan_orphans(grace_seconds=60)

        assert stats.snapshots_deleted == 0
        assert (snapshots / "vidX.jpg").exists()

    def test_in_grace_files_skipped(self, dirs, monkeypatch):
        videos, _ = dirs
        _set_repo(monkeypatch, [])
        _touch(videos / "fresh.mp4", mtime=time.time())  # just written

        stats = data_cleaner.scan_orphans(grace_seconds=300)

        assert stats.videos_deleted == 0
        assert (videos / "fresh.mp4").exists()

    def test_dry_run_deletes_nothing(self, dirs, monkeypatch):
        videos, snapshots = dirs
        _set_repo(monkeypatch, [])
        _touch(videos / "ghost.mp4")
        _touch(snapshots / "ghost.jpg")

        stats = data_cleaner.scan_orphans(grace_seconds=0, dry_run=True)

        assert stats.videos_deleted == 0
        assert stats.snapshots_deleted == 0
        assert stats.bytes_freed == 0
        assert (videos / "ghost.mp4").exists()
        assert (snapshots / "ghost.jpg").exists()

    def test_missing_dirs_are_noop(self, tmp_path, monkeypatch):
        monkeypatch.setattr(data_cleaner, "VIDEOS_DIR", tmp_path / "nope_v")
        monkeypatch.setattr(data_cleaner, "SNAPSHOTS_DIR", tmp_path / "nope_s")
        _set_repo(monkeypatch, [])
        stats = data_cleaner.scan_orphans(grace_seconds=0)
        assert stats.errors == 0

    def test_non_matching_extensions_untouched(self, dirs, monkeypatch):
        videos, _ = dirs
        _set_repo(monkeypatch, [])
        _touch(videos / "notes.txt")
        data_cleaner.scan_orphans(grace_seconds=0)
        assert (videos / "notes.txt").exists()
