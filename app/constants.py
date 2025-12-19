"""
Unified constants and configurations for Mock ONVIF Camera Service
Centralizes all quality presets and configuration parameters
"""

# Quality Presets Configuration
# Each preset defines video/audio encoding parameters for transcoding
QUALITY_PRESETS = {
    '480p': {
        'width': 854,
        'height': 480,
        'fps': 24,
        'video_bitrate_mbps': 1.0,      # Video bitrate in Mbps
        'video_maxrate_mbps': 1.5,      # Max video bitrate in Mbps
        'video_bufsize_mbps': 2.0,      # Buffer size in Mbps
        'audio_bitrate_kbps': 128,      # Audio bitrate in Kbps
        'description': '480p'
    },
    '720p': {
        'width': 1280,
        'height': 720,
        'fps': 30,
        'video_bitrate_mbps': 2.5,
        'video_maxrate_mbps': 3.0,
        'video_bufsize_mbps': 5.0,
        'audio_bitrate_kbps': 128,
        'description': '720p HD'
    },
    '1080p': {
        'width': 1920,
        'height': 1080,
        'fps': 30,
        'video_bitrate_mbps': 4.0,
        'video_maxrate_mbps': 5.0,
        'video_bufsize_mbps': 8.0,
        'audio_bitrate_kbps': 128,
        'description': '1080p Full HD'
    },
    '4k': {
        'width': 3840,
        'height': 2160,
        'fps': 30,
        'video_bitrate_mbps': 15.0,
        'video_maxrate_mbps': 18.0,
        'video_bufsize_mbps': 30.0,
        'audio_bitrate_kbps': 128,
        'description': 'Ultra HD 4K'
    },
    '5k': {
        'width': 5120,
        'height': 2880,
        'fps': 24,
        'video_bitrate_mbps': 25.0,
        'video_maxrate_mbps': 30.0,
        'video_bufsize_mbps': 50.0,
        'audio_bitrate_kbps': 128,
        'description': '5K'
    }
}


def get_transcode_preset(preset_name):
    """
    Convert unified preset to FFmpeg transcode parameters

    Args:
        preset_name: Preset name ('480p', '720p', '1080p', '4k', '5k')

    Returns:
        dict: Transcode parameters for camera_manager
    """
    if preset_name not in QUALITY_PRESETS:
        raise ValueError(f"Invalid preset: {preset_name}")

    preset = QUALITY_PRESETS[preset_name]
    return {
        'resolution': f"{preset['width']}x{preset['height']}",
        'fps': preset['fps'],
        'video_bitrate': f"{preset['video_bitrate_mbps']}M",
        'video_maxrate': f"{preset['video_maxrate_mbps']}M",
        'video_bufsize': f"{preset['video_bufsize_mbps']}M",
        'gop': int(round(preset['fps'])),  # GOP = FPS, must be integer
        'audio_bitrate': f"{preset['audio_bitrate_kbps']}k",
        'description': preset['description']
    }


def get_onvif_preset(preset_name):
    """
    Convert unified preset to ONVIF configuration parameters

    Args:
        preset_name: Preset name ('480p', '720p', '1080p', '4k', '5k')

    Returns:
        dict: ONVIF parameters for onvif_server
    """
    if preset_name not in QUALITY_PRESETS:
        raise ValueError(f"Invalid preset: {preset_name}")

    preset = QUALITY_PRESETS[preset_name]
    return {
        'width': preset['width'],
        'height': preset['height'],
        'fps': preset['fps'],
        'bitrate': int(preset['video_bitrate_mbps'] * 1024),  # Convert Mbps to Kbps
        'audio_bitrate': preset['audio_bitrate_kbps'],
    }


def get_all_preset_names():
    """Get list of all available preset names"""
    return list(QUALITY_PRESETS.keys())


def validate_preset(preset_name):
    """Check if preset name is valid"""
    return preset_name in QUALITY_PRESETS or preset_name == 'custom'


# Custom Parameter Validation Ranges
CUSTOM_PARAM_RANGES = {
    'width': {'min': 320, 'max': 7680},
    'height': {'min': 240, 'max': 4320},
    'fps': {'min': 1.0, 'max': 60.0},  # Supports decimal values (e.g., 29.97, 23.976)
    'video_bitrate_mbps': {'min': 0.5, 'max': 50.0},
}

VALID_AUDIO_BITRATES = ['64k', '128k', '192k', '256k']


# Port Configuration
MEDIAMTX_HOST = 'mediamtx'  # Docker service name (use '127.0.0.1' for local)
MEDIAMTX_RTSP_PORT = 8554
ONVIF_PORT_MIN = 12000
ONVIF_PORT_MAX = 13000
