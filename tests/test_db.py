"""CameraRepository roundtrips + YAML migration against a tmp_path SQLite db."""
import dataclasses

import pytest
import yaml

from app.db import CameraRecord, CameraRepository, migrate_yaml_configs
from app.schemas import VideoParams

PARAMS = {"width": 1920, "height": 1080, "fps": 30.0,
          "video_bitrate": "4M", "audio_bitrate": "128k"}


@pytest.fixture
def repo(tmp_path):
    r = CameraRepository(db_path=tmp_path / "t.db")
    yield r
    r.close()


def _record(camera_id="11111111-2222-4333-8444-555555555555", **overrides):
    kwargs = dict(
        camera_id=camera_id,
        video_path=f"data/videos/{camera_id}.mp4",
        onvif_port=12001,
        video_params=dict(PARAMS),
        created_at=1700000000,
        sub_profile=True,
        manufacturer="TestCam",
        shared_video_id=None,
        camera_ip=None,
    )
    kwargs.update(overrides)
    return CameraRecord(**kwargs)


class TestCrudRoundtrip:
    def test_upsert_get_roundtrip_all_fields(self, repo):
        rec = _record(shared_video_id="sharedX", camera_ip="192.168.0.201")
        repo.upsert(rec)
        got = repo.get(rec.camera_id)
        # from_row must populate every dataclass field
        assert dataclasses.asdict(got) == dataclasses.asdict(rec)

    def test_get_missing_returns_none(self, repo):
        assert repo.get("nope") is None

    def test_upsert_twice_updates_in_place(self, repo):
        rec = _record()
        repo.upsert(rec)
        rec.onvif_port = 12099
        rec.manufacturer = "Updated"
        repo.upsert(rec)
        assert len(repo.all()) == 1
        got = repo.get(rec.camera_id)
        assert got.onvif_port == 12099
        assert got.manufacturer == "Updated"

    def test_all_ordered_by_created_at(self, repo):
        repo.upsert(_record("b" * 36, created_at=200))
        repo.upsert(_record("a" * 36, created_at=100))
        assert [r.created_at for r in repo.all()] == [100, 200]

    def test_delete(self, repo):
        rec = _record()
        repo.upsert(rec)
        repo.delete(rec.camera_id)
        assert repo.get(rec.camera_id) is None
        assert repo.all() == []

    def test_parsed_params_returns_videoparams(self, repo):
        repo.upsert(_record())
        got = repo.get(_record().camera_id)
        vp = got.parsed_params()
        assert isinstance(vp, VideoParams)
        assert (vp.width, vp.height, vp.fps) == (1920, 1080, 30.0)
        assert vp.video_bitrate == "4M"

    def test_update_port_and_ip(self, repo):
        rec = _record()
        repo.upsert(rec)
        repo.update_onvif_port(rec.camera_id, 12555)
        repo.update_camera_ip(rec.camera_id, "10.1.1.5")
        got = repo.get(rec.camera_id)
        assert got.onvif_port == 12555
        assert got.camera_ip == "10.1.1.5"

    def test_count_using_shared_video(self, repo):
        repo.upsert(_record("a" * 36, shared_video_id="vidX"))
        repo.upsert(_record("b" * 36, shared_video_id="vidX"))
        repo.upsert(_record("c" * 36, shared_video_id="other"))
        assert repo.count_using_shared_video("vidX") == 2
        assert repo.count_using_shared_video("vidX", exclude_id="a" * 36) == 1
        assert repo.count_using_shared_video("missing") == 0


class TestYamlMigration:
    CAM_ID = "99999999-8888-4777-8666-555555555555"

    def _write_legacy_yaml(self, configs_dir):
        configs_dir.mkdir(parents=True, exist_ok=True)
        cfg = {
            "camera_id": self.CAM_ID,
            "onvif_port": 12042,
            "created_at": 1690000000,
            "sub_profile": True,
            "manufacturer": "LegacyCam",
            "camera_ip": "192.168.0.210",
            "video_params": dict(PARAMS),
        }
        path = configs_dir / f"config_{self.CAM_ID}.yaml"
        with open(path, "w") as f:
            yaml.safe_dump(cfg, f)
        return path

    def test_migration_imports_and_deletes_yaml(self, repo, tmp_path):
        configs_dir = tmp_path / "cameras"
        yaml_path = self._write_legacy_yaml(configs_dir)

        assert migrate_yaml_configs(repo, configs_dir=configs_dir) == 1
        assert not yaml_path.exists()
        # empty legacy dir removed entirely
        assert not configs_dir.exists()

        rec = repo.get(self.CAM_ID)
        assert rec is not None
        assert rec.onvif_port == 12042
        assert rec.manufacturer == "LegacyCam"
        assert rec.sub_profile is True
        assert rec.camera_ip == "192.168.0.210"
        assert rec.video_params == PARAMS

    def test_migration_is_idempotent(self, repo, tmp_path):
        configs_dir = tmp_path / "cameras"
        self._write_legacy_yaml(configs_dir)
        assert migrate_yaml_configs(repo, configs_dir=configs_dir) == 1
        # second run: nothing left to import, no error, record intact
        assert migrate_yaml_configs(repo, configs_dir=configs_dir) == 0
        assert len(repo.all()) == 1

    def test_existing_record_not_overwritten(self, repo, tmp_path):
        repo.upsert(_record(self.CAM_ID, manufacturer="AlreadyHere"))
        configs_dir = tmp_path / "cameras"
        self._write_legacy_yaml(configs_dir)
        assert migrate_yaml_configs(repo, configs_dir=configs_dir) == 0
        assert repo.get(self.CAM_ID).manufacturer == "AlreadyHere"

    def test_yaml_without_camera_id_skipped(self, repo, tmp_path):
        configs_dir = tmp_path / "cameras"
        configs_dir.mkdir()
        with open(configs_dir / "config_bad.yaml", "w") as f:
            yaml.safe_dump({"video_params": dict(PARAMS)}, f)
        assert migrate_yaml_configs(repo, configs_dir=configs_dir) == 0
        assert repo.all() == []

    def test_missing_dir_is_noop(self, repo, tmp_path):
        assert migrate_yaml_configs(repo, configs_dir=tmp_path / "nope") == 0
