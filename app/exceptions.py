"""Typed exceptions so HTTP routes can map cleanly to status codes."""


class CameraServiceError(Exception):
    """Base class for all expected service errors."""
    http_status = 500


class ValidationError(CameraServiceError):
    http_status = 400


class CameraNotFoundError(CameraServiceError):
    http_status = 404


class VideoSaveError(CameraServiceError):
    http_status = 500


class TranscodeError(CameraServiceError):
    http_status = 500


class SnapshotError(CameraServiceError):
    http_status = 500


class FFmpegStartError(CameraServiceError):
    http_status = 500


class OnvifStartError(CameraServiceError):
    http_status = 500


class PortAllocationError(CameraServiceError):
    http_status = 503


class MacvlanError(CameraServiceError):
    http_status = 500


class PersistenceError(CameraServiceError):
    http_status = 500
