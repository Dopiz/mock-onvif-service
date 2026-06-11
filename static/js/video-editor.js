// Video editing: preview player, integrated timeline / trim handles, speed
// and extend-last-frame controls. Edit limits come from state.config
// (GET /config), with hardcoded defaults as fallback.

import { state } from './state.js';
import { formatDuration } from './utils.js';

// Drag state for trim handles (private to this module)
let isDraggingTrimStart = false;
let isDraggingTrimEnd = false;
let isDraggingTimeline = false;

// Setup video preview with the selected file
export function setupVideoPreview(file) {
    const videoPreview = document.getElementById('videoPreview');
    const totalTimeEl = document.getElementById('totalTime');
    const editor = state.editor;

    if (!videoPreview) {
        return;
    }

    // Revoke previous URL if exists
    if (editor.previewUrl) {
        URL.revokeObjectURL(editor.previewUrl);
    }

    // Create new preview URL
    editor.previewUrl = URL.createObjectURL(file);
    videoPreview.src = editor.previewUrl;

    // Wait for metadata to load
    videoPreview.onloadedmetadata = () => {
        editor.duration = videoPreview.duration;
        if (totalTimeEl) totalTimeEl.textContent = formatDuration(editor.duration);

        // Set default trim values (full video)
        editor.trimStart = 0;
        editor.trimEnd = editor.duration;

        // Apply current playback speed to preview
        videoPreview.playbackRate = editor.speed;

        // Initialize timeline UI and interactions
        updateTimelineUI();
        setupTimelineInteractions();

        // Update output duration
        updateOutputDuration();
    };
}

// Update all timeline UI elements
function updateTimelineUI() {
    const video = document.getElementById('videoPreview');
    const editor = state.editor;
    if (!video || !editor.duration) return;

    const trimRange = document.getElementById('timelineTrimRange');
    const progress = document.getElementById('timelineProgress');
    const startHandle = document.getElementById('trimHandleStart');
    const endHandle = document.getElementById('trimHandleEnd');
    const startTime = document.getElementById('trimStartTime');
    const endTime = document.getElementById('trimEndTime');
    const currentTimeEl = document.getElementById('currentTime');
    const trimStartDisplay = document.getElementById('trimStartDisplay');
    const trimEndDisplay = document.getElementById('trimEndDisplay');

    const startPercent = (editor.trimStart / editor.duration) * 100;
    const endPercent = (editor.trimEnd / editor.duration) * 100;
    const currentPercent = (video.currentTime / editor.duration) * 100;

    // Update trim range highlight
    if (trimRange) {
        trimRange.style.left = startPercent + '%';
        trimRange.style.width = (endPercent - startPercent) + '%';
    }

    // Update progress bar (shows current playback position with color)
    if (progress) {
        const progressPercent = Math.min(currentPercent, endPercent);
        progress.style.left = startPercent + '%';
        progress.style.width = Math.max(0, progressPercent - startPercent) + '%';
    }

    // Update trim handles (both use left positioning with translateX(-50%))
    if (startHandle) startHandle.style.left = startPercent + '%';
    if (endHandle) endHandle.style.left = endPercent + '%';

    // Update time displays
    if (startTime) startTime.textContent = formatDuration(editor.trimStart);
    if (endTime) endTime.textContent = formatDuration(editor.trimEnd);
    if (currentTimeEl) currentTimeEl.textContent = formatDuration(video.currentTime);
    if (trimStartDisplay) trimStartDisplay.textContent = formatDuration(editor.trimStart);
    if (trimEndDisplay) trimEndDisplay.textContent = formatDuration(editor.trimEnd);
}

// Setup timeline interactions (drag, click, play/pause)
function setupTimelineInteractions() {
    const video = document.getElementById('videoPreview');
    const timeline = document.getElementById('timelineContainer');
    const startHandle = document.getElementById('trimHandleStart');
    const endHandle = document.getElementById('trimHandleEnd');
    const playPauseBtn = document.getElementById('playPauseBtn');
    const editor = state.editor;

    if (!video || !timeline) return;

    // Play/Pause button
    if (playPauseBtn) {
        playPauseBtn.onclick = () => {
            if (video.paused) {
                // Start from trim start if outside range
                if (video.currentTime < editor.trimStart || video.currentTime >= editor.trimEnd) {
                    video.currentTime = editor.trimStart;
                }
                video.play();
            } else {
                video.pause();
            }
        };
    }

    // Update play/pause icon
    video.onplay = () => updatePlayPauseIcon(false);
    video.onpause = () => updatePlayPauseIcon(true);

    // Update timeline during playback
    video.ontimeupdate = () => {
        updateTimelineUI();
        // Stop at trim end
        if (video.currentTime >= editor.trimEnd) {
            video.pause();
            video.currentTime = editor.trimStart;
        }
    };

    // Click on timeline to seek
    timeline.addEventListener('mousedown', (e) => {
        if (e.target.closest('.trim-handle')) return; // Don't seek if clicking handle
        isDraggingTimeline = true;
        seekToPosition(e);
    });

    // Trim handle dragging
    if (startHandle) {
        startHandle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            e.stopPropagation();
            isDraggingTrimStart = true;
        });
    }

    if (endHandle) {
        endHandle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            e.stopPropagation();
            isDraggingTrimEnd = true;
        });
    }

    // Global mouse move/up for dragging
    document.addEventListener('mousemove', onTimelineMouseMove);
    document.addEventListener('mouseup', onTimelineMouseUp);

    // Touch support
    timeline.addEventListener('touchstart', (e) => {
        if (e.target.closest('.trim-handle-start')) {
            isDraggingTrimStart = true;
        } else if (e.target.closest('.trim-handle-end')) {
            isDraggingTrimEnd = true;
        } else {
            isDraggingTimeline = true;
            seekToPosition(e.touches[0]);
        }
    });

    document.addEventListener('touchmove', (e) => {
        if (isDraggingTrimStart || isDraggingTrimEnd || isDraggingTimeline) {
            onTimelineMouseMove(e.touches[0]);
        }
    });

    document.addEventListener('touchend', onTimelineMouseUp);
}

function onTimelineMouseMove(e) {
    if (!isDraggingTrimStart && !isDraggingTrimEnd && !isDraggingTimeline) return;

    const timeline = document.getElementById('timelineContainer');
    const video = document.getElementById('videoPreview');
    const editor = state.editor;
    if (!timeline || !video) return;

    const rect = timeline.getBoundingClientRect();
    const percent = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const time = percent * editor.duration;

    if (isDraggingTrimStart) {
        editor.trimStart = Math.max(0, Math.min(time, editor.trimEnd - 0.1));
        updateTimelineUI();
        updateOutputDuration();
    } else if (isDraggingTrimEnd) {
        editor.trimEnd = Math.min(editor.duration, Math.max(time, editor.trimStart + 0.1));
        updateTimelineUI();
        updateOutputDuration();
    } else if (isDraggingTimeline) {
        seekToPosition(e);
    }
}

function onTimelineMouseUp() {
    isDraggingTrimStart = false;
    isDraggingTrimEnd = false;
    isDraggingTimeline = false;
}

function seekToPosition(e) {
    const timeline = document.getElementById('timelineContainer');
    const video = document.getElementById('videoPreview');
    const editor = state.editor;
    if (!timeline || !video) return;

    const rect = timeline.getBoundingClientRect();
    const percent = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    let time = percent * editor.duration;

    // Clamp to trim range
    time = Math.max(editor.trimStart, Math.min(editor.trimEnd, time));
    video.currentTime = time;
    updateTimelineUI();
}

function updatePlayPauseIcon(showPlay) {
    const playIcon = document.querySelector('.play-icon');
    const pauseIcon = document.querySelector('.pause-icon');
    if (playIcon) playIcon.style.display = showPlay ? 'block' : 'none';
    if (pauseIcon) pauseIcon.style.display = showPlay ? 'none' : 'block';
}

function computeOutputDuration() {
    const editor = state.editor;
    const rawDuration = editor.trimEnd - editor.trimStart;
    const extendSeconds = editor.extendLastFrame ? state.config.extend_frame_duration : 0;
    return rawDuration / editor.speed + extendSeconds;
}

// Calculate and update output duration display
function updateOutputDuration() {
    const editor = state.editor;
    const outputDurationEl = document.getElementById('outputDuration');
    const outputDuration = computeOutputDuration();

    if (outputDurationEl) outputDurationEl.textContent = formatDuration(outputDuration);

    // Update trim displays
    const trimStartDisplay = document.getElementById('trimStartDisplay');
    const trimEndDisplay = document.getElementById('trimEndDisplay');
    if (trimStartDisplay) trimStartDisplay.textContent = formatDuration(editor.trimStart);
    if (trimEndDisplay) trimEndDisplay.textContent = formatDuration(editor.trimEnd);

    // Validate and show/hide warning
    validateEditParams();
}

// Validate edit parameters and show warnings
export function validateEditParams() {
    const editor = state.editor;
    const limits = state.config.edit_limits;
    const outputDuration = computeOutputDuration();

    const editWarning = document.getElementById('editWarning');
    const editWarningText = document.getElementById('editWarningText');
    const confirmBtn = document.getElementById('confirmPreset');

    let error = null;

    if (editor.trimEnd <= editor.trimStart) {
        error = 'End time must be greater than start time';
    } else if (outputDuration < limits.min_duration) {
        error = `Output duration must be at least ${limits.min_duration} seconds (currently ${formatDuration(outputDuration)})`;
    } else if (outputDuration > limits.max_duration) {
        error = `Output duration cannot exceed ${limits.max_duration} seconds (currently ${formatDuration(outputDuration)})`;
    }

    if (error) {
        editWarning.style.display = 'flex';
        editWarningText.textContent = error;
        confirmBtn.disabled = true;
        confirmBtn.style.opacity = '0.5';
        return { valid: false, error };
    } else {
        editWarning.style.display = 'none';
        confirmBtn.disabled = false;
        confirmBtn.style.opacity = '1';
        return { valid: true };
    }
}

// Get edit parameters for upload
export function getEditParams() {
    const editor = state.editor;
    if (editor.duration <= 0) {
        return null;
    }

    return {
        trimStart: editor.trimStart,
        trimEnd: editor.trimEnd,
        speed: editor.speed,
        extendLastFrame: editor.extendLastFrame,
    };
}

// Setup video editing event listeners (once, on startup)
export function setupVideoEditingListeners() {
    // Speed dropdown - also applies to video preview playback
    const speedSelect = document.getElementById('speedSelect');
    if (speedSelect) {
        speedSelect.addEventListener('change', (e) => {
            state.editor.speed = parseFloat(e.target.value);
            // Apply speed to video preview in real-time
            const video = document.getElementById('videoPreview');
            if (video) video.playbackRate = state.editor.speed;
            updateOutputDuration();
        });
    }

    // Extend last frame checkbox
    const extendCheckbox = document.getElementById('extendLastFrame');
    if (extendCheckbox) {
        extendCheckbox.addEventListener('change', (e) => {
            state.editor.extendLastFrame = e.target.checked;
            updateOutputDuration();
        });
    }
}

// Reset video editing state and UI
export function resetVideoEditing() {
    const editor = state.editor;

    // Reset state
    if (editor.previewUrl) {
        URL.revokeObjectURL(editor.previewUrl);
        editor.previewUrl = null;
    }
    editor.duration = 0;
    editor.trimStart = 0;
    editor.trimEnd = 0;
    editor.speed = 1.0;
    editor.extendLastFrame = false;
    isDraggingTrimStart = false;
    isDraggingTrimEnd = false;
    isDraggingTimeline = false;

    // Reset video element
    const videoPreview = document.getElementById('videoPreview');
    if (videoPreview) {
        videoPreview.src = '';
        videoPreview.playbackRate = 1.0;
        videoPreview.onplay = null;
        videoPreview.onpause = null;
        videoPreview.ontimeupdate = null;
    }

    // Reset time displays
    ['currentTime', 'totalTime', 'outputDuration', 'trimStartDisplay',
     'trimEndDisplay', 'trimStartTime', 'trimEndTime'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.textContent = '0:00';
    });

    // Reset timeline elements
    const trimRange = document.getElementById('timelineTrimRange');
    const progress = document.getElementById('timelineProgress');
    const startHandle = document.getElementById('trimHandleStart');
    const endHandle = document.getElementById('trimHandleEnd');

    if (trimRange) {
        trimRange.style.left = '0%';
        trimRange.style.width = '100%';
    }
    if (progress) progress.style.width = '0';
    if (startHandle) startHandle.style.left = '0';
    if (endHandle) endHandle.style.left = '100%';

    // Reset play/pause icon
    updatePlayPauseIcon(true);

    // Reset speed dropdown
    const speedSelect = document.getElementById('speedSelect');
    if (speedSelect) speedSelect.value = '1';

    // Reset extend checkbox
    const extendCheckbox = document.getElementById('extendLastFrame');
    if (extendCheckbox) extendCheckbox.checked = false;

    // Hide warning
    const editWarning = document.getElementById('editWarning');
    if (editWarning) editWarning.style.display = 'none';

    // Re-enable confirm button
    const confirmBtn = document.getElementById('confirmPreset');
    if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.style.opacity = '1';
    }
}
