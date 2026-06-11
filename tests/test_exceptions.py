"""Every CameraServiceError subclass must map to its expected HTTP status."""
import pytest

from app import exceptions
from app.exceptions import (
    CameraNotFoundError,
    CameraServiceError,
    FFmpegStartError,
    MacvlanError,
    OnvifStartError,
    PersistenceError,
    PortAllocationError,
    SnapshotError,
    TranscodeError,
    ValidationError,
    VideoSaveError,
)

EXPECTED = {
    CameraServiceError: 500,
    ValidationError: 400,
    CameraNotFoundError: 404,
    VideoSaveError: 500,
    TranscodeError: 500,
    SnapshotError: 500,
    FFmpegStartError: 500,
    OnvifStartError: 500,
    PortAllocationError: 503,
    MacvlanError: 500,
    PersistenceError: 500,
}


@pytest.mark.parametrize("exc_cls,status", EXPECTED.items(),
                         ids=[c.__name__ for c in EXPECTED])
def test_http_status_mapping(exc_cls, status):
    assert exc_cls.http_status == status
    assert issubclass(exc_cls, CameraServiceError)


def test_no_unmapped_subclasses():
    """Catch new exception types added without updating this mapping."""
    declared = {
        obj for obj in vars(exceptions).values()
        if isinstance(obj, type) and issubclass(obj, CameraServiceError)
    }
    assert declared == set(EXPECTED)
