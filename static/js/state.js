// Shared mutable state. Single data bus for all modules (replaces the old
// module-level globals and window.* usage).

// Fallback values used when GET /config is unreachable. Keep in sync with
// app/constants.py — but the server response is the source of truth.
export const DEFAULT_CONFIG = {
    param_ranges: {
        width: { min: 320, max: 7680 },
        height: { min: 240, max: 4320 },
        fps: { min: 1, max: 60 },
        video_bitrate_mbps: { min: 0.5, max: 50 },
    },
    valid_audio_bitrates: ['64k', '128k', '192k', '256k'],
    edit_limits: {
        min_duration: 5,
        max_duration: 180,
        min_speed: 0.5,
        max_speed: 4.0,
    },
    extend_frame_duration: 10,
};

export const state = {
    // Validation ranges / edit limits (refreshed from GET /config on startup)
    config: structuredClone(DEFAULT_CONFIG),

    // Cameras
    allCameras: [],                 // unfiltered list from GET /cameras
    camerasById: new Map(),         // id -> camera (for tooltip info)
    thumbnailCache: new Map(),      // id -> { dataUrl, error }

    // Filters
    filters: { preset: 'all', type: 'all' },

    // Upload modal
    pendingFile: null,
    originalVideoResolution: null,
    calculatedPresets: {},

    // Video editor
    editor: {
        previewUrl: null,
        duration: 0,
        trimStart: 0,
        trimEnd: 0,
        speed: 1.0,
        extendLastFrame: false,
    },
};
