"""Backward-compat shim. New code should import from :mod:`app.camera_lifecycle`."""
from __future__ import annotations

from app.camera_lifecycle import (
    cleanup_all,
    create_camera,
    create_cameras_batch,
    delete_camera,
    get_registry,
    restore_cameras,
)
from app.config import MACVLAN_ENABLED  # re-export


class CameraManager:
    """Legacy facade. Static methods proxy to :mod:`camera_lifecycle`."""

    @staticmethod
    def list_cameras():
        return [s.to_info_dict() for s in get_registry().all()]

    @staticmethod
    def create_camera(*args, **kwargs):
        return create_camera(*args, **kwargs)

    @staticmethod
    def create_cameras_batch(*args, **kwargs):
        return create_cameras_batch(*args, **kwargs)

    @staticmethod
    def delete_camera(camera_id):
        return delete_camera(camera_id)

    @staticmethod
    def restore_cameras():
        return restore_cameras()


# Legacy callers still poke at this dict directly. Provide a read-only adapter.
class _CamerasView:
    def __iter__(self):
        return iter(get_registry().ids())

    def __contains__(self, key):
        return get_registry().get(key) is not None

    def __getitem__(self, key):
        st = get_registry().get(key)
        if st is None:
            raise KeyError(key)
        return st.to_info_dict()

    def __len__(self):
        return len(get_registry().ids())

    def keys(self):
        return list(get_registry().ids())

    def values(self):
        return [s.to_info_dict() for s in get_registry().all()]

    def items(self):
        return [(s.record.camera_id, s.to_info_dict()) for s in get_registry().all()]


CAMERAS = _CamerasView()


def _get_macvlan_manager():
    from app.camera_lifecycle import _macvlan_manager
    return _macvlan_manager()


__all__ = [
    "CAMERAS",
    "CameraManager",
    "MACVLAN_ENABLED",
    "_get_macvlan_manager",
    "cleanup_all",
]
