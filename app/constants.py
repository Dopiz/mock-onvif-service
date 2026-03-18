"""
Constants for Mock ONVIF Camera Service
Parameter validation ranges and allowed values
"""

# Custom Parameter Validation Ranges
CUSTOM_PARAM_RANGES = {
    'width': {'min': 320, 'max': 7680},
    'height': {'min': 240, 'max': 4320},
    'fps': {'min': 1.0, 'max': 60.0},  # Supports decimal values (e.g., 29.97, 23.976)
    'video_bitrate_mbps': {'min': 0.5, 'max': 50.0},
}

VALID_AUDIO_BITRATES = ['64k', '128k', '192k', '256k']

# Video Edit Limits
EDIT_LIMITS = {
    'min_duration': 5,       # Minimum output duration in seconds
    'max_duration': 180,     # Maximum output duration in seconds (3 minutes)
    'min_speed': 0.5,        # Minimum playback speed (slow-mo)
    'max_speed': 4.0         # Maximum playback speed (fast)
}
EXTEND_FRAME_DURATION = 10   # Duration to extend last frame in seconds
