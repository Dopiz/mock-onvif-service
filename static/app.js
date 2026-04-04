// API Base URL
const API_BASE = '';

// Video Edit Limits
const EDIT_LIMITS = {
    MIN_DURATION: 5,       // Minimum output duration in seconds
    MAX_DURATION: 180,     // Maximum output duration in seconds (3 minutes)
    MIN_SPEED: 0.5,        // Minimum playback speed
    MAX_SPEED: 4.0         // Maximum playback speed
};

// Video edit state
let videoPreviewUrl = null;
let originalVideoDuration = 0;
let trimStart = 0;
let trimEnd = 0;
let playbackSpeed = 1.0;
let extendLastFrame = false;

// DOM Elements
const uploadZone = document.getElementById('uploadZone');
const videoFile = document.getElementById('videoFile');
const uploadProgress = document.getElementById('uploadProgress');
const uploadContent = uploadZone.querySelector('.upload-content');
const camerasGrid = document.getElementById('camerasGrid');
const emptyState = document.getElementById('emptyState');
const cameraCount = document.getElementById('cameraCount');
const toast = document.getElementById('toast');
const thumbnailTooltip = document.getElementById('thumbnailTooltip');
const thumbnailImage = document.getElementById('thumbnailImage');
const thumbnailLabel = document.getElementById('thumbnailLabel');
const presetModal = document.getElementById('presetModal');
const confirmPresetBtn = document.getElementById('confirmPreset');
const cancelPresetBtn = document.getElementById('cancelPreset');

// Thumbnail cache
const thumbnailCache = new Map();

// Cameras data cache (for tooltip info)
const camerasDataCache = new Map();

// Pending file for upload
let pendingFile = null;

// Filter state
const filterState = {
    preset: 'all',
    type: 'all'
};

// All cameras data (unfiltered)
let allCamerasData = [];

// Preset pixel thresholds (width * height) - shared constants
const PRESET_PIXELS = {
    '720p': 1280 * 720,      // 921,600
    '1080p': 1920 * 1080,    // 2,073,600
    '2k': 2688 * 1512,       // 4,064,256
    '4k': 3840 * 2160,       // 8,294,400
    '5k': 5120 * 2880        // 14,745,600
};

// Get preset label from pixel count
// Note: anything > 4K is considered 5K
function getPresetFromPixels(pixels) {
    if (pixels > PRESET_PIXELS['4k']) return '5k';
    if (pixels > PRESET_PIXELS['2k']) return '4k';
    if (pixels > PRESET_PIXELS['1080p']) return '2k';
    if (pixels > PRESET_PIXELS['720p']) return '1080p';
    return '720p';
}

// Derive preset label from resolution (for UI display and filtering)
function derivePresetLabel(camera) {
    const width = camera.width;
    const height = camera.height;
    if (!width || !height) return 'unknown';
    return getPresetFromPixels(width * height);
}

// ==================== Video Editing Functions ====================

// Drag state for trim handles
let isDraggingTrimStart = false;
let isDraggingTrimEnd = false;
let isDraggingTimeline = false;

// Format seconds to MM:SS or M:SS
function formatDuration(seconds) {
    if (isNaN(seconds) || seconds < 0) return '0:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

// Setup video preview with the selected file
function setupVideoPreview(file) {
    const videoPreview = document.getElementById('videoPreview');
    const totalTimeEl = document.getElementById('totalTime');

    if (!videoPreview) {
        return;
    }

    // Revoke previous URL if exists
    if (videoPreviewUrl) {
        URL.revokeObjectURL(videoPreviewUrl);
    }

    // Create new preview URL
    videoPreviewUrl = URL.createObjectURL(file);
    videoPreview.src = videoPreviewUrl;

    // Wait for metadata to load
    videoPreview.onloadedmetadata = () => {
        originalVideoDuration = videoPreview.duration;
        if (totalTimeEl) totalTimeEl.textContent = formatDuration(originalVideoDuration);

        // Set default trim values (full video)
        trimStart = 0;
        trimEnd = originalVideoDuration;

        // Apply current playback speed to preview
        videoPreview.playbackRate = playbackSpeed;

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
    if (!video || !originalVideoDuration) return;

    const trimRange = document.getElementById('timelineTrimRange');
    const progress = document.getElementById('timelineProgress');
    const startHandle = document.getElementById('trimHandleStart');
    const endHandle = document.getElementById('trimHandleEnd');
    const startTime = document.getElementById('trimStartTime');
    const endTime = document.getElementById('trimEndTime');
    const currentTimeEl = document.getElementById('currentTime');
    const trimStartDisplay = document.getElementById('trimStartDisplay');
    const trimEndDisplay = document.getElementById('trimEndDisplay');

    const startPercent = (trimStart / originalVideoDuration) * 100;
    const endPercent = (trimEnd / originalVideoDuration) * 100;
    const currentPercent = (video.currentTime / originalVideoDuration) * 100;

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
    if (startTime) startTime.textContent = formatDuration(trimStart);
    if (endTime) endTime.textContent = formatDuration(trimEnd);
    if (currentTimeEl) currentTimeEl.textContent = formatDuration(video.currentTime);
    if (trimStartDisplay) trimStartDisplay.textContent = formatDuration(trimStart);
    if (trimEndDisplay) trimEndDisplay.textContent = formatDuration(trimEnd);
}

// Setup timeline interactions (drag, click, play/pause)
function setupTimelineInteractions() {
    const video = document.getElementById('videoPreview');
    const timeline = document.getElementById('timelineContainer');
    const startHandle = document.getElementById('trimHandleStart');
    const endHandle = document.getElementById('trimHandleEnd');
    const playPauseBtn = document.getElementById('playPauseBtn');

    if (!video || !timeline) return;

    // Play/Pause button
    if (playPauseBtn) {
        playPauseBtn.onclick = () => {
            if (video.paused) {
                // Start from trim start if outside range
                if (video.currentTime < trimStart || video.currentTime >= trimEnd) {
                    video.currentTime = trimStart;
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
        if (video.currentTime >= trimEnd) {
            video.pause();
            video.currentTime = trimStart;
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
    if (!timeline || !video) return;

    const rect = timeline.getBoundingClientRect();
    const percent = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const time = percent * originalVideoDuration;

    if (isDraggingTrimStart) {
        trimStart = Math.min(time, trimEnd - 0.1);
        trimStart = Math.max(0, trimStart);
        updateTimelineUI();
        updateOutputDuration();
    } else if (isDraggingTrimEnd) {
        trimEnd = Math.max(time, trimStart + 0.1);
        trimEnd = Math.min(originalVideoDuration, trimEnd);
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
    if (!timeline || !video) return;

    const rect = timeline.getBoundingClientRect();
    const percent = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    let time = percent * originalVideoDuration;

    // Clamp to trim range
    time = Math.max(trimStart, Math.min(trimEnd, time));
    video.currentTime = time;
    updateTimelineUI();
}

function updatePlayPauseIcon(showPlay) {
    const playIcon = document.querySelector('.play-icon');
    const pauseIcon = document.querySelector('.pause-icon');
    if (playIcon) playIcon.style.display = showPlay ? 'block' : 'none';
    if (pauseIcon) pauseIcon.style.display = showPlay ? 'none' : 'block';
}

// Calculate and update output duration display
function updateOutputDuration() {
    const outputDurationEl = document.getElementById('outputDuration');
    const rawDuration = trimEnd - trimStart;
    const outputDuration = rawDuration / playbackSpeed + (extendLastFrame ? 10 : 0);

    if (outputDurationEl) outputDurationEl.textContent = formatDuration(outputDuration);

    // Update trim displays
    const trimStartDisplay = document.getElementById('trimStartDisplay');
    const trimEndDisplay = document.getElementById('trimEndDisplay');
    if (trimStartDisplay) trimStartDisplay.textContent = formatDuration(trimStart);
    if (trimEndDisplay) trimEndDisplay.textContent = formatDuration(trimEnd);

    // Validate and show/hide warning
    validateEditParams();
}

// Validate edit parameters and show warnings
function validateEditParams() {
    const rawDuration = trimEnd - trimStart;
    const outputDuration = rawDuration / playbackSpeed + (extendLastFrame ? 10 : 0);

    const editWarning = document.getElementById('editWarning');
    const editWarningText = document.getElementById('editWarningText');
    const confirmBtn = document.getElementById('confirmPreset');

    let error = null;

    if (trimEnd <= trimStart) {
        error = 'End time must be greater than start time';
    } else if (outputDuration < EDIT_LIMITS.MIN_DURATION) {
        error = `Output duration must be at least ${EDIT_LIMITS.MIN_DURATION} seconds (currently ${formatDuration(outputDuration)})`;
    } else if (outputDuration > EDIT_LIMITS.MAX_DURATION) {
        error = `Output duration cannot exceed ${EDIT_LIMITS.MAX_DURATION} seconds / 3 minutes (currently ${formatDuration(outputDuration)})`;
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
function getEditParams() {
    // Always return edit params if video is loaded
    if (originalVideoDuration <= 0) {
        return null;
    }

    return {
        trimStart: trimStart,
        trimEnd: trimEnd,
        speed: playbackSpeed,
        extendLastFrame: extendLastFrame
    };
}

// Setup video editing event listeners
function setupVideoEditingListeners() {
    // Speed dropdown - also applies to video preview playback
    const speedSelect = document.getElementById('speedSelect');
    if (speedSelect) {
        speedSelect.addEventListener('change', (e) => {
            playbackSpeed = parseFloat(e.target.value);
            // Apply speed to video preview in real-time
            const video = document.getElementById('videoPreview');
            if (video) video.playbackRate = playbackSpeed;
            updateOutputDuration();
        });
    }

    // Extend last frame checkbox
    const extendCheckbox = document.getElementById('extendLastFrame');
    if (extendCheckbox) {
        extendCheckbox.addEventListener('change', (e) => {
            extendLastFrame = e.target.checked;
            updateOutputDuration();
        });
    }
}

// Reset video editing state and UI
function resetVideoEditing() {
    // Reset state
    if (videoPreviewUrl) {
        URL.revokeObjectURL(videoPreviewUrl);
        videoPreviewUrl = null;
    }
    originalVideoDuration = 0;
    trimStart = 0;
    trimEnd = 0;
    playbackSpeed = 1.0;
    extendLastFrame = false;
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
    const currentTime = document.getElementById('currentTime');
    const totalTime = document.getElementById('totalTime');
    const outputDuration = document.getElementById('outputDuration');
    const trimStartDisplay = document.getElementById('trimStartDisplay');
    const trimEndDisplay = document.getElementById('trimEndDisplay');
    const trimStartTime = document.getElementById('trimStartTime');
    const trimEndTime = document.getElementById('trimEndTime');

    if (currentTime) currentTime.textContent = '0:00';
    if (totalTime) totalTime.textContent = '0:00';
    if (outputDuration) outputDuration.textContent = '0:00';
    if (trimStartDisplay) trimStartDisplay.textContent = '0:00';
    if (trimEndDisplay) trimEndDisplay.textContent = '0:00';
    if (trimStartTime) trimStartTime.textContent = '0:00';
    if (trimEndTime) trimEndTime.textContent = '0:00';

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

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    setupUploadZone();
    setupRefreshButton();
    setupFilterButtons();
    setupVideoEditingListeners();
    loadCameras();
});

// Update Filters based on available cameras
function updateFilters(cameras) {
    if (cameras.length === 0) {
        // Hide entire filters section if no cameras
        document.querySelector('.cameras-filters').style.display = 'none';
        return;
    }

    // Analyze available presets and types
    const availablePresets = new Set();
    const availableTypes = new Set();

    cameras.forEach(camera => {
        const preset = derivePresetLabel(camera);
        availablePresets.add(preset);

        const isBatch = !!camera.shared_video_id;
        const hasSub = !!camera.sub_profile;

        if (isBatch) {
            availableTypes.add('batch');
        } else {
            availableTypes.add('single');
            if (hasSub) {
                availableTypes.add('sub');
            }
        }
    });

    // Update Resolution filter
    const presetFilterGroup = document.querySelector('[data-filter-type="preset"]').closest('.filter-group');
    if (availablePresets.size <= 1) {
        // Hide resolution filter if only one preset
        presetFilterGroup.style.display = 'none';
    } else {
        presetFilterGroup.style.display = 'flex';
        updatePresetButtons(availablePresets);
    }

    // Update Type filter
    const typeFilterGroup = document.querySelector('[data-filter-type="type"]').closest('.filter-group');
    if (availableTypes.size <= 1) {
        // Hide type filter if only one type
        typeFilterGroup.style.display = 'none';
    } else {
        typeFilterGroup.style.display = 'flex';
        updateTypeButtons(availableTypes);
    }

    // Show/hide entire filters container
    const hasVisibleFilters = (availablePresets.size > 1) || (availableTypes.size > 1);
    document.querySelector('.cameras-filters').style.display = hasVisibleFilters ? 'flex' : 'none';

    // Re-setup filter button listeners
    setupFilterButtons();
}

// Update preset filter buttons
function updatePresetButtons(availablePresets) {
    const presetContainer = document.querySelector('[data-filter-container="preset"]');
    const currentActive = filterState.preset;

    // Preset display names
    const presetNames = {
        '5k': '5K',
        '4k': '4K',
        '2k': '2K',
        '1080p': '1080p',
        '720p': '720p',
        'unknown': 'Unknown'
    };

    // Build buttons HTML
    const allIsActive = currentActive === 'all' ? 'active' : '';
    let buttonsHTML = `<button class="filter-btn ${allIsActive}" data-filter-type="preset" data-filter-value="all">All</button>`;

    ['5k', '4k', '2k', '1080p', '720p', 'unknown'].forEach(preset => {
        if (availablePresets.has(preset)) {
            const isActive = currentActive === preset ? 'active' : '';
            buttonsHTML += `<button class="filter-btn ${isActive}" data-filter-type="preset" data-filter-value="${preset}">${presetNames[preset]}</button>`;
        }
    });

    presetContainer.innerHTML = buttonsHTML;

    // Reset filter if current filter is not available
    if (currentActive !== 'all' && !availablePresets.has(currentActive)) {
        filterState.preset = 'all';
        // Re-render with reset filter
        const allButton = presetContainer.querySelector('[data-filter-value="all"]');
        if (allButton) {
            allButton.classList.add('active');
        }
    }
}

// Update type filter buttons
function updateTypeButtons(availableTypes) {
    const typeContainer = document.querySelector('[data-filter-container="type"]');
    const currentActive = filterState.type;

    // Build buttons HTML
    const allIsActive = currentActive === 'all' ? 'active' : '';
    let buttonsHTML = `<button class="filter-btn ${allIsActive}" data-filter-type="type" data-filter-value="all">All</button>`;

    if (availableTypes.has('single')) {
        const isActive = currentActive === 'single' ? 'active' : '';
        buttonsHTML += `<button class="filter-btn ${isActive}" data-filter-type="type" data-filter-value="single">Single</button>`;
    }

    if (availableTypes.has('batch')) {
        const isActive = currentActive === 'batch' ? 'active' : '';
        buttonsHTML += `<button class="filter-btn ${isActive}" data-filter-type="type" data-filter-value="batch">Batch</button>`;
    }

    if (availableTypes.has('sub')) {
        const isActive = currentActive === 'sub' ? 'active' : '';
        buttonsHTML += `<button class="filter-btn ${isActive}" data-filter-type="type" data-filter-value="sub">Sub</button>`;
    }

    typeContainer.innerHTML = buttonsHTML;

    // Reset filter if current filter is not available
    if (currentActive !== 'all' && !availableTypes.has(currentActive)) {
        filterState.type = 'all';
        // Re-render with reset filter
        const allButton = typeContainer.querySelector('[data-filter-value="all"]');
        if (allButton) {
            allButton.classList.add('active');
        }
    }
}

// Setup filter button event listeners
function setupFilterButtons() {
    const filterButtons = document.querySelectorAll('.filter-btn');

    filterButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const filterType = btn.getAttribute('data-filter-type');
            const filterValue = btn.getAttribute('data-filter-value');

            // Update active state for buttons of the same type
            document.querySelectorAll(`[data-filter-type="${filterType}"]`).forEach(b => {
                b.classList.remove('active');
            });
            btn.classList.add('active');

            // Update filter state
            filterState[filterType] = filterValue;

            // Re-render cameras with filters
            renderCameras(allCamerasData);
        });
    });
}

// Refresh Button Setup
function setupRefreshButton() {
    const refreshBtn = document.getElementById('refreshBtn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', async () => {
            // Add spinning animation
            refreshBtn.classList.add('spinning');

            // Refresh cameras
            await loadCameras();

            // Remove spinning animation after a short delay
            setTimeout(() => {
                refreshBtn.classList.remove('spinning');
            }, 500);
        });
    }
}

// Upload Zone Setup
function setupUploadZone() {
    // Click to upload
    uploadZone.addEventListener('click', () => {
        if (!uploadProgress.style.display || uploadProgress.style.display === 'none') {
            videoFile.click();
        }
    });

    // File selection - detect video resolution and show modal
    videoFile.addEventListener('change', async (e) => {
        if (e.target.files.length > 0) {
            pendingFile = e.target.files[0];
            await detectVideoResolutionAndShowModal(pendingFile);
        }
    });

    // Drag and drop - detect video resolution and show modal
    uploadZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadZone.classList.add('drag-over');
    });

    uploadZone.addEventListener('dragleave', () => {
        uploadZone.classList.remove('drag-over');
    });

    uploadZone.addEventListener('drop', async (e) => {
        e.preventDefault();
        uploadZone.classList.remove('drag-over');

        const files = e.dataTransfer.files;
        if (files.length > 0) {
            pendingFile = files[0];
            await detectVideoResolutionAndShowModal(pendingFile);
        }
    });

    // Modal confirm button
    confirmPresetBtn.addEventListener('click', () => {
        const selectedPreset = document.querySelector('input[name="preset"]:checked').value;
        const cameraCountOption = document.querySelector('input[name="cameraCount"]:checked').value;
        const subProfile = document.getElementById('subProfile').checked;
        const cameraName = document.getElementById('cameraName').value.trim() || 'MockONVIF';

        let cameraCount;
        if (cameraCountOption === 'batch') {
            // Get the batch camera count from input and validate
            let batchCount = parseInt(document.getElementById('batchCameraCount').value);

            // Validate and clamp between 2-100
            if (isNaN(batchCount) || batchCount < 2) {
                batchCount = 2;
            } else if (batchCount > 100) {
                batchCount = 100;
            }

            // Update the input field with the clamped value
            document.getElementById('batchCameraCount').value = batchCount;
            cameraCount = batchCount;
        } else {
            cameraCount = parseInt(cameraCountOption);
        }

        // Get video parameters based on selected preset or custom
        let videoParams;
        if (selectedPreset === 'custom') {
            // Use custom parameters from form
            videoParams = {
                width: parseInt(document.getElementById('customWidth').value),
                height: parseInt(document.getElementById('customHeight').value),
                fps: parseFloat(document.getElementById('customFps').value),
                videoBitrate: parseFloat(document.getElementById('customVideoBitrate').value),
                audioBitrate: document.getElementById('customAudioBitrate').value
            };
        } else {
            // Use pre-calculated parameters for the selected preset
            videoParams = window.calculatedPresets[selectedPreset];
        }

        // Get edit params BEFORE hiding modal (which resets the state)
        const editParams = getEditParams();

        hidePresetModal();
        uploadVideo(pendingFile, videoParams, cameraCount, subProfile, cameraName, editParams);
        pendingFile = null;
    });

    // Modal cancel button
    cancelPresetBtn.addEventListener('click', () => {
        hidePresetModal();
        pendingFile = null;
        videoFile.value = ''; // Clear file input
    });

    // Get sub-profile checkbox reference (used in multiple places)
    const subProfileCheckbox = document.getElementById('subProfile');

    // Camera count radio buttons - handle sub-profile checkbox
    const cameraCountRadios = document.querySelectorAll('input[name="cameraCount"]');

    cameraCountRadios.forEach(radio => {
        radio.addEventListener('change', () => {
            if (radio.value === 'batch' && radio.checked) {
                // Disable and uncheck sub-profile for multi-cameras
                if (subProfileCheckbox) {
                    subProfileCheckbox.checked = false;
                    subProfileCheckbox.disabled = true;
                }
            } else if (radio.value === '1' && radio.checked) {
                // Re-enable sub-profile for single camera
                if (subProfileCheckbox) {
                    subProfileCheckbox.disabled = false;
                }
            }
        });
    });

    // Batch camera count input validation
    const batchCameraCountInput = document.getElementById('batchCameraCount');
    if (batchCameraCountInput) {
        // Auto-select multi-camera option when input is focused or clicked
        const autoSelectBatchOption = () => {
            const batchRadio = document.querySelector('input[name="cameraCount"][value="batch"]');
            if (batchRadio && !batchRadio.checked) {
                batchRadio.checked = true;
                // Trigger change event to update sub-profile checkbox
                batchRadio.dispatchEvent(new Event('change'));
            }
        };

        batchCameraCountInput.addEventListener('focus', autoSelectBatchOption);
        batchCameraCountInput.addEventListener('click', autoSelectBatchOption);

        // Validate on blur (when user leaves the input)
        batchCameraCountInput.addEventListener('blur', (e) => {
            let value = parseInt(e.target.value);

            // Validate and clamp between 2-100
            if (isNaN(value) || value < 2) {
                value = 2;
            } else if (value > 100) {
                value = 100;
            }

            e.target.value = value;
        });

        // Prevent negative values on input
        batchCameraCountInput.addEventListener('input', (e) => {
            const value = e.target.value;
            // Allow empty or valid numbers
            if (value !== '' && (isNaN(parseInt(value)) || parseInt(value) < 0)) {
                e.target.value = '';
            }
        });
    }

    // Sub-profile checkbox handler (360p sub-profile doesn't conflict with any main preset)
}


// Calculate dimensions for a preset based on aspect ratio and original video
// Returns dimensions that maintain aspect ratio while targeting preset resolution
function calculatePresetDimensions(presetName, originalPreset, targetHeight, aspectRatio, originalPixels, resolution) {
    // Keep original dimensions for the matching preset level
    if (presetName === originalPreset) {
        return { width: resolution.width, height: resolution.height };
    }

    // Calculate scaled dimensions (ensure even numbers for FFmpeg compatibility)
    const scaledWidth = Math.round(targetHeight * aspectRatio / 2) * 2;
    const scaledHeight = targetHeight;
    const scaledPixels = scaledWidth * scaledHeight;

    // For upscale presets, calculate from preset's pixel threshold to maintain quality
    if (scaledPixels <= originalPixels) {
        const targetPixels = PRESET_PIXELS[presetName];
        const height = Math.round(Math.sqrt(targetPixels / aspectRatio) / 2) * 2;
        const width = Math.round(height * aspectRatio / 2) * 2;
        return { width, height };
    }

    return { width: scaledWidth, height: scaledHeight };
}

// Update the preset description text in the modal UI
function updatePresetDescriptionUI(presetName, width, height, fps, videoBitrate) {
    const presetOption = document.querySelector(`input[value="${presetName}"]`);
    if (!presetOption) return;

    const card = presetOption.closest('.preset-option').querySelector('.preset-card');
    const desc = card?.querySelector('.preset-desc');
    if (desc) {
        desc.textContent = `${width}x${height} @ ${fps}fps \u00B7 ${videoBitrate} Mbps`;
    }
}

// Update visual markers on preset options (upscale warning, original badge)
function updatePresetOptionMarkers(originalPixels, selectedPreset) {
    document.querySelectorAll('.preset-option').forEach(option => {
        const input = option.querySelector('input[type="radio"]');
        const card = option.querySelector('.preset-card');
        const preset = input.value;

        // Clear previous markers
        card.classList.remove('upscale-option', 'original-option');
        card.removeAttribute('data-upscale');
        const originalBadge = card.querySelector('.original-badge');
        if (originalBadge) originalBadge.style.display = 'none';

        // Skip custom preset
        if (preset === 'custom') return;

        // Apply markers based on pixel comparison
        const targetPixels = PRESET_PIXELS[preset];
        if (targetPixels > originalPixels) {
            card.classList.add('upscale-option');
            card.setAttribute('data-upscale', 'true');
        } else if (preset === selectedPreset) {
            card.classList.add('original-option');
            if (originalBadge) originalBadge.style.display = 'inline-block';
        }

        // Ensure all options are enabled
        input.disabled = false;
        option.style.opacity = '1';
        option.style.cursor = 'pointer';
    });
}

// Detect video resolution and show modal with appropriate options
async function detectVideoResolutionAndShowModal(file) {
    try {
        const resolution = await getVideoResolution(file);
        const originalPixels = resolution.width * resolution.height;

        // Calculate aspect ratio from original video
        const aspectRatio = resolution.width / resolution.height;

        // Store resolution and aspect ratio for later use
        window.originalVideoResolution = resolution;
        window.videoAspectRatio = aspectRatio;

        // Update modal with detected resolution (and FPS if available)
        const originalResolution = document.getElementById('originalResolution');
        if (originalResolution) {
            let resolutionText = `${resolution.width}x${resolution.height}`;
            if (resolution.fps) {
                resolutionText += ` @ ${resolution.fps}fps`;
            }
            originalResolution.textContent = resolutionText;
        }

        // Determine the original video's preset level first
        const originalPreset = getPresetFromPixels(originalPixels);

        // Preset configuration with base heights and other parameters
        const presetConfigs = {
            '720p': { height: 720, fps: 30, videoBitrate: 2.5, audioBitrate: '128k' },
            '1080p': { height: 1080, fps: 30, videoBitrate: 4.0, audioBitrate: '128k' },
            '2k': { height: 1512, fps: 30, videoBitrate: 8.0, audioBitrate: '128k' },
            '4k': { height: 2160, fps: 30, videoBitrate: 15.0, audioBitrate: '128k' },
            '5k': { height: 2880, fps: 24, videoBitrate: 25.0, audioBitrate: '128k' }
        };

        // Calculate dynamic parameters for each preset based on aspect ratio
        window.calculatedPresets = {};
        Object.keys(presetConfigs).forEach(presetName => {
            const config = presetConfigs[presetName];
            const { width: finalWidth, height: finalHeight } = calculatePresetDimensions(
                presetName, originalPreset, config.height, aspectRatio, originalPixels, resolution
            );

            window.calculatedPresets[presetName] = {
                width: finalWidth,
                height: finalHeight,
                fps: config.fps,
                videoBitrate: config.videoBitrate,
                audioBitrate: config.audioBitrate
            };

            updatePresetDescriptionUI(presetName, finalWidth, finalHeight, config.fps, config.videoBitrate);
        });

        // Update visual markers and auto-select the recommended preset
        updatePresetOptionMarkers(originalPixels, originalPreset);
        document.querySelector(`input[value="${originalPreset}"]`).checked = true;

        populateCustomDefaults(resolution);
        setupPresetChangeListeners();
        updateUpscaleWarning();
        toggleCustomParamsSection();

        // Setup video preview for editing
        setupVideoPreview(file);

        showPresetModal();
    } catch (error) {
        console.error('Failed to detect video resolution:', error);
        const originalResolution = document.getElementById('originalResolution');
        if (originalResolution) {
            originalResolution.textContent = 'Unknown';
        }
        populateCustomDefaults({ width: 1920, height: 1080 });
        setupPresetChangeListeners();
        toggleCustomParamsSection();

        // Setup video preview even on error
        setupVideoPreview(file);

        showPresetModal();
    }
}

// Populate custom defaults with original video resolution
function populateCustomDefaults(resolution) {
    const widthInput = document.getElementById('customWidth');
    const heightInput = document.getElementById('customHeight');
    const fpsInput = document.getElementById('customFps');
    const fpsSlider = document.getElementById('customFpsSlider');
    const videoBitrateInput = document.getElementById('customVideoBitrate');

    if (widthInput) widthInput.value = resolution.width;
    if (heightInput) heightInput.value = resolution.height;

    // Use detected FPS if available, otherwise default to 30
    const fps = resolution.fps || 30;
    if (fpsInput) fpsInput.value = fps;
    if (fpsSlider) fpsSlider.value = fps;

    // Calculate and set suggested bitrate based on actual fps
    const suggestedBitrate = calculateSuggestedBitrate(resolution.width, resolution.height, fps);
    if (videoBitrateInput) videoBitrateInput.value = suggestedBitrate.toFixed(1);
    updateSuggestedBitrate();
}

// Calculate suggested bitrate based on resolution and fps
function calculateSuggestedBitrate(width, height, fps) {
    // Formula: bitrate = (width * height * fps * 0.07) / 1000000 Mbps
    const bitrate = (width * height * fps * 0.07) / 1000000;
    return Math.max(0.5, Math.min(50, bitrate)); // Clamp between 0.5 and 50
}

// Update suggested bitrate display
function updateSuggestedBitrate() {
    const width = parseInt(document.getElementById('customWidth').value) || 1920;
    const height = parseInt(document.getElementById('customHeight').value) || 1080;
    const fps = parseFloat(document.getElementById('customFps').value) || 30;

    const suggested = calculateSuggestedBitrate(width, height, fps);
    const suggestedElement = document.getElementById('suggestedBitrate');
    if (suggestedElement) {
        suggestedElement.textContent = suggested.toFixed(1);
    }
}

// Setup preset change listeners
function setupPresetChangeListeners() {
    document.querySelectorAll('input[name="preset"]').forEach(radio => {
        // Remove any existing listeners by cloning
        const newRadio = radio.cloneNode(true);
        radio.parentNode.replaceChild(newRadio, radio);

        newRadio.addEventListener('change', () => {
            updateUpscaleWarning();
            toggleCustomParamsSection();
        });
    });

    // Setup custom parameter input listeners
    const widthInput = document.getElementById('customWidth');
    const heightInput = document.getElementById('customHeight');
    const fpsInput = document.getElementById('customFps');
    const fpsSlider = document.getElementById('customFpsSlider');

    if (widthInput) {
        widthInput.addEventListener('input', () => {
            updateSuggestedBitrate();
            checkCustomUpscale();
        });
    }

    if (heightInput) {
        heightInput.addEventListener('input', () => {
            updateSuggestedBitrate();
            checkCustomUpscale();
        });
    }

    if (fpsInput) {
        fpsInput.addEventListener('input', (e) => {
            const value = parseFloat(e.target.value) || 30;
            if (fpsSlider) fpsSlider.value = value;
            updateSuggestedBitrate();
        });
    }

    if (fpsSlider) {
        fpsSlider.addEventListener('input', (e) => {
            const value = e.target.value;
            if (fpsInput) fpsInput.value = value;
            updateSuggestedBitrate();
        });
    }
}

// Toggle custom parameters section visibility
function toggleCustomParamsSection() {
    const selectedPreset = document.querySelector('input[name="preset"]:checked');
    const customSection = document.getElementById('customParamsSection');

    if (customSection) {
        if (selectedPreset && selectedPreset.value === 'custom') {
            customSection.style.display = 'block';
            updateSuggestedBitrate();
        } else {
            customSection.style.display = 'none';
        }
    }
}

// Check if custom resolution is upscaling (based on total pixels)
function checkCustomUpscale() {
    if (!window.originalVideoResolution) return;

    const width = parseInt(document.getElementById('customWidth').value) || 1920;
    const height = parseInt(document.getElementById('customHeight').value) || 1080;
    const original = window.originalVideoResolution;

    const customPixels = width * height;
    const originalPixels = original.width * original.height;

    const warningDiv = document.getElementById('upscaleWarning');
    const selectedPreset = document.querySelector('input[name="preset"]:checked');

    if (warningDiv && selectedPreset && selectedPreset.value === 'custom') {
        if (customPixels > originalPixels) {
            warningDiv.style.display = 'flex';
        } else {
            warningDiv.style.display = 'none';
        }
    }
}

// Update upscale warning visibility based on selected preset
function updateUpscaleWarning() {
    const selectedRadio = document.querySelector('input[name="preset"]:checked');
    if (!selectedRadio) return;

    const selectedCard = selectedRadio.closest('.preset-option').querySelector('.preset-card');
    const isUpscale = selectedCard.getAttribute('data-upscale') === 'true';

    const warningDiv = document.getElementById('upscaleWarning');
    if (warningDiv) {
        warningDiv.style.display = isUpscale ? 'flex' : 'none';
    }
}

// Get video resolution from file
function getVideoResolution(file) {
    return new Promise((resolve, reject) => {
        const video = document.createElement('video');
        video.preload = 'metadata';
        video.muted = true;

        let resolved = false;

        const resolveOnce = (result) => {
            if (!resolved) {
                resolved = true;
                window.URL.revokeObjectURL(video.src);
                video.pause();
                video.remove();
                resolve(result);
            }
        };

        video.onloadedmetadata = function () {
            const result = {
                width: video.videoWidth,
                height: video.videoHeight,
                duration: video.duration || null
            };

            // Try to estimate FPS using requestVideoFrameCallback (modern browsers)
            if ('requestVideoFrameCallback' in HTMLVideoElement.prototype) {
                let frameCount = 0;
                let startTime = null;
                const maxSampleTime = 0.3; // Sample for 0.3 seconds

                // Set timeout to prevent hanging
                const timeout = setTimeout(() => {
                    resolveOnce(result);
                }, 2000); // 2 second timeout

                video.playbackRate = 2; // Speed up for faster sampling

                const countFrame = (now, metadata) => {
                    if (resolved) return;

                    if (startTime === null) {
                        startTime = metadata.mediaTime;
                    }
                    frameCount++;

                    const elapsed = metadata.mediaTime - startTime;
                    if (elapsed < maxSampleTime && video.currentTime < video.duration - 0.1) {
                        video.requestVideoFrameCallback(countFrame);
                    } else {
                        clearTimeout(timeout);
                        if (elapsed > 0 && frameCount > 1) {
                            const estimatedFps = Math.round(frameCount / elapsed);
                            // Validate FPS is reasonable (1-120)
                            if (estimatedFps >= 1 && estimatedFps <= 120) {
                                result.fps = estimatedFps;
                            }
                        }
                        resolveOnce(result);
                    }
                };

                video.requestVideoFrameCallback(countFrame);
                video.play().catch(() => {
                    clearTimeout(timeout);
                    resolveOnce(result);
                });
            } else {
                // Fallback for browsers without requestVideoFrameCallback
                resolveOnce(result);
            }
        };

        video.onerror = function () {
            if (!resolved) {
                resolved = true;
                window.URL.revokeObjectURL(video.src);
                reject(new Error('Failed to load video metadata'));
            }
        };

        video.src = URL.createObjectURL(file);
    });
}

// Show preset modal
function showPresetModal() {
    presetModal.style.display = 'flex';
    document.body.style.overflow = 'hidden';

    // Check camera count and apply sub-profile checkbox state
    const selectedCameraCount = document.querySelector('input[name="cameraCount"]:checked');
    if (selectedCameraCount && selectedCameraCount.value === 'batch') {
        // If multi-cameras is selected, ensure sub-profile is disabled
        const subProfileCheckbox = document.getElementById('subProfile');
        if (subProfileCheckbox) {
            subProfileCheckbox.checked = false;
            subProfileCheckbox.disabled = true;
        }
    }
}

// Hide preset modal
function hidePresetModal() {
    presetModal.style.display = 'none';
    document.body.style.overflow = '';
    // Reset resolution text
    const originalResolution = document.getElementById('originalResolution');
    if (originalResolution) {
        originalResolution.textContent = 'Detecting...';
    }
    // Reset custom parameters to default values
    const customWidth = document.getElementById('customWidth');
    const customHeight = document.getElementById('customHeight');
    const customFps = document.getElementById('customFps');
    const customFpsSlider = document.getElementById('customFpsSlider');
    const customVideoBitrate = document.getElementById('customVideoBitrate');
    const cameraNameInput = document.getElementById('cameraName');

    if (customWidth) customWidth.value = 1920;
    if (customHeight) customHeight.value = 1080;
    if (customFps) customFps.value = 30;
    if (customFpsSlider) customFpsSlider.value = 30;
    if (customVideoBitrate) customVideoBitrate.value = '4.0';
    if (cameraNameInput) cameraNameInput.value = '';

    // Reset preset descriptions to default 16:9 values
    const defaultDescriptions = {
        '720p': '1280x720 @ 30fps · 2.5 Mbps',
        '1080p': '1920x1080 @ 30fps · 4 Mbps',
        '2k': '2688x1512 @ 30fps · 8 Mbps',
        '4k': '3840x2160 @ 30fps · 15 Mbps',
        '5k': '5120x2880 @ 24fps · 25 Mbps'
    };
    Object.keys(defaultDescriptions).forEach(presetKey => {
        const presetOption = document.querySelector(`input[value="${presetKey}"]`);
        if (presetOption) {
            const card = presetOption.closest('.preset-option').querySelector('.preset-card');
            const desc = card?.querySelector('.preset-desc');
            if (desc) {
                desc.textContent = defaultDescriptions[presetKey];
            }
        }
    });

    // Reset video editing state
    resetVideoEditing();
}

// Validate custom parameters
function validateCustomParams() {
    const width = parseInt(document.getElementById('customWidth').value);
    const height = parseInt(document.getElementById('customHeight').value);
    const fps = parseFloat(document.getElementById('customFps').value);
    const videoBitrate = parseFloat(document.getElementById('customVideoBitrate').value);

    const errors = [];

    if (isNaN(width) || width < 320 || width > 7680) {
        errors.push('Width must be between 320 and 7680');
    }
    if (isNaN(height) || height < 240 || height > 4320) {
        errors.push('Height must be between 240 and 4320');
    }
    if (isNaN(fps) || fps < 1 || fps > 60) {
        errors.push('FPS must be between 1 and 60');
    }
    if (isNaN(videoBitrate) || videoBitrate < 0.5 || videoBitrate > 50) {
        errors.push('Video bitrate must be between 0.5 and 50 Mbps');
    }

    return errors;
}

// Upload Video
async function uploadVideo(file, videoParams, cameraCount = 1, subProfile = false, cameraName = 'MockONVIF', editParams = null) {
    // Validate parameters
    if (!videoParams || !videoParams.width || !videoParams.height) {
        showToast('Invalid video parameters', 'error');
        return;
    }

    // Show progress
    uploadContent.style.display = 'none';
    uploadProgress.style.display = 'flex';

    const formData = new FormData();
    formData.append('file', file);
    formData.append('camera_count', cameraCount);
    formData.append('sub_profile', subProfile);
    formData.append('camera_name', cameraName);

    // Send specific video parameters (no more preset concept for backend)
    formData.append('width', videoParams.width);
    formData.append('height', videoParams.height);
    formData.append('fps', videoParams.fps);
    formData.append('video_bitrate', videoParams.videoBitrate + 'M');
    formData.append('audio_bitrate', videoParams.audioBitrate);

    // Add video edit parameters (passed from caller before modal reset)
    if (editParams) {
        formData.append('trim_start', Math.floor(editParams.trimStart));
        formData.append('trim_end', Math.floor(editParams.trimEnd));
        formData.append('speed', editParams.speed);
        formData.append('extend_last_frame', editParams.extendLastFrame);
    }

    try {
        const response = await fetch(`${API_BASE}/upload`, {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (response.ok) {
            const message = cameraCount > 1 ?
                `${cameraCount} cameras deployed successfully!` :
                'Camera deployed successfully!';
            showToast(message, 'success');
            await loadCameras();
        } else {
            showToast(`Error: ${data.error}`, 'error');
        }
    } catch (error) {
        showToast('Network error occurred', 'error');
        console.error('Upload error:', error);
    } finally {
        // Reset upload zone
        uploadContent.style.display = 'flex';
        uploadProgress.style.display = 'none';
        videoFile.value = '';
    }
}

// Load Cameras
async function loadCameras() {
    try {
        const response = await fetch(`${API_BASE}/cameras`);
        const cameras = await response.json();

        // Store all cameras data
        allCamerasData = cameras;

        // Update filters based on available cameras
        updateFilters(cameras);

        renderCameras(cameras);
    } catch (error) {
        console.error('Failed to load cameras:', error);
    }
}

// Render Cameras
function renderCameras(cameras) {
    // Apply filters
    let filteredCameras = cameras.filter(camera => {
        // Filter by preset (derived from resolution)
        if (filterState.preset !== 'all') {
            const cameraPreset = derivePresetLabel(camera);
            if (cameraPreset !== filterState.preset) {
                return false;
            }
        }

        // Filter by type
        if (filterState.type !== 'all') {
            const isBatch = !!camera.shared_video_id;
            const hasSub = !!camera.sub_profile;

            if (filterState.type === 'single') {
                // Single = 單台（無論有沒有 sub）
                if (isBatch) {
                    return false;
                }
            } else if (filterState.type === 'batch') {
                // Batch = 批次相機
                if (!isBatch) {
                    return false;
                }
            } else if (filterState.type === 'sub') {
                // Sub = 有 sub 的單台
                if (isBatch || !hasSub) {
                    return false;
                }
            }
        }

        return true;
    });

    // Sort cameras by created_at (oldest first), then by onvif_port
    filteredCameras.sort((a, b) => {
        const timeA = a.created_at || 0;
        const timeB = b.created_at || 0;
        if (timeA !== timeB) {
            return timeA - timeB;  // Oldest first
        }
        return (a.onvif_port || 0) - (b.onvif_port || 0);  // Then by port
    });

    // Update count with filtered results
    cameraCount.textContent = filteredCameras.length;

    // Clear and update cameras data cache
    camerasDataCache.clear();
    cameras.forEach(camera => {
        camerasDataCache.set(camera.id, camera);
    });

    // Show/hide empty state
    if (filteredCameras.length === 0) {
        emptyState.style.display = 'block';
        camerasGrid.style.display = 'none';
        return;
    }

    emptyState.style.display = 'none';
    camerasGrid.style.display = 'grid';

    // Group cameras by shared_video_id
    const batchGroups = new Map();
    const individualCameras = [];

    filteredCameras.forEach(camera => {
        if (camera.shared_video_id) {
            if (!batchGroups.has(camera.shared_video_id)) {
                batchGroups.set(camera.shared_video_id, []);
            }
            batchGroups.get(camera.shared_video_id).push(camera);
        } else {
            individualCameras.push(camera);
        }
    });

    // Render cards
    const cardsHTML = [];

    // Render individual cameras
    individualCameras.forEach(camera => {
        cardsHTML.push(createCameraCard(camera));
    });

    // Render batch groups
    batchGroups.forEach((batchCameras, sharedVideoId) => {
        if (batchCameras.length > 1) {
            cardsHTML.push(createBatchCameraCard(batchCameras, sharedVideoId));
        } else {
            // Only one camera in this "batch", render as individual
            cardsHTML.push(createCameraCard(batchCameras[0]));
        }
    });

    camerasGrid.innerHTML = cardsHTML.join('');

    // Attach event listeners
    individualCameras.forEach(camera => {
        attachCameraEventListeners(camera.id);
    });

    batchGroups.forEach((batchCameras, sharedVideoId) => {
        if (batchCameras.length > 1) {
            attachBatchCameraEventListeners(sharedVideoId, batchCameras);
        } else {
            attachCameraEventListeners(batchCameras[0].id);
        }
    });
}

// Create Batch Camera Card HTML (consistent with single camera)
function createBatchCameraCard(cameras, sharedVideoId) {
    const count = cameras.length;
    const shortBatchId = sharedVideoId.substring(0, 8);

    // Sort cameras by port to get the first one (lowest port)
    const sortedCameras = [...cameras].sort((a, b) => a.onvif_port - b.onvif_port);
    const firstCamera = sortedCameras[0];

    // Get port range
    const minPort = sortedCameras[0].onvif_port;
    const maxPort = sortedCameras[sortedCameras.length - 1].onvif_port;

    // Derive preset label from resolution
    const preset = derivePresetLabel(firstCamera);
    let resolutionBadges = `<span class="preset-badge preset-${preset}">${preset.toUpperCase()}</span>`;

    // Add "+ Sub" badge if sub_profile enabled
    if (firstCamera.sub_profile) {
        resolutionBadges += ' <span class="preset-badge preset-sub">+ SUB</span>';
    }

    return `
        <div class="camera-card batch-camera-card" data-batch-id="${sharedVideoId}">
            <div class="camera-header">
                <div class="camera-id">
                    <div class="camera-label">BATCH CAMERAS SHARED ID</div>
                    <div class="camera-id-value">${shortBatchId} ${resolutionBadges}</div>
                </div>
                <span class="batch-count-badge">x${count}</span>
            </div>

            <div class="camera-info">
                <div class="info-row">
                    <span class="info-label">NAME</span>
                    <span class="info-value">${firstCamera.manufacturer || 'MockONVIF'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">ONVIF PORT</span>
                    <span class="info-value">${minPort} - ${maxPort}</span>
                </div>
            </div>

            <div class="camera-urls">
                <div class="url-item">
                    <div class="url-label">RTSP URL (mediamtx) - First Camera</div>
                    <div class="url-container">
                        <div class="url-text" title="${firstCamera.rtsp_url}">${firstCamera.rtsp_url}</div>
                        <button class="copy-btn" data-url="${firstCamera.rtsp_url}" data-type="RTSP">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                            </svg>
                        </button>
                    </div>
                </div>
                <div class="url-item">
                    <div class="url-label">ONVIF URL - First Camera</div>
                    <div class="url-container">
                        <div class="url-text" title="${firstCamera.onvif_url}">${firstCamera.onvif_url}</div>
                        <button class="copy-btn" data-url="${firstCamera.onvif_url}" data-type="ONVIF">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>

            <div class="camera-actions">
                <button class="btn btn-delete batch-delete-btn" data-batch-id="${sharedVideoId}">
                    TERMINATE ALL
                </button>
            </div>
        </div>
    `;
}

// Create Camera Card HTML
function createCameraCard(camera) {
    const shortId = camera.id.substring(0, 8);
    const preset = derivePresetLabel(camera);
    const presetBadge = `<span class="preset-badge preset-${preset}">${preset.toUpperCase()}</span>`;

    // Build resolution display - include "+ Sub" if sub_profile enabled
    let resolutionBadges = presetBadge;
    if (camera.sub_profile) {
        resolutionBadges += ' <span class="preset-badge preset-sub">+ SUB</span>';
    }

    return `
        <div class="camera-card" data-camera-id="${camera.id}">
            <div class="camera-header">
                <div class="camera-id">
                    <div class="camera-label">CAMERA ID</div>
                    <div class="camera-id-value">${shortId} ${resolutionBadges}</div>
                </div>
            </div>

            <div class="camera-info">
                <div class="info-row">
                    <span class="info-label">NAME</span>
                    <span class="info-value">${camera.manufacturer || 'MockONVIF'}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">ONVIF PORT</span>
                    <span class="info-value">${camera.onvif_port}</span>
                </div>
                ${camera.camera_ip ? `<div class="info-row">
                    <span class="info-label">CAMERA IP</span>
                    <span class="info-value">${camera.camera_ip}</span>
                </div>` : ''}
            </div>

            <div class="camera-urls">
                <div class="url-item">
                    <div class="url-label">RTSP URL (mediamtx)</div>
                    <div class="url-container">
                        <div class="url-text" title="${camera.rtsp_url}">${camera.rtsp_url}</div>
                        <button class="copy-btn" data-url="${camera.rtsp_url}" data-type="RTSP">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                            </svg>
                        </button>
                    </div>
                </div>
                <div class="url-item">
                    <div class="url-label">ONVIF URL</div>
                    <div class="url-container">
                        <div class="url-text" title="${camera.onvif_url}">${camera.onvif_url}</div>
                        <button class="copy-btn" data-url="${camera.onvif_url}" data-type="ONVIF">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>

            <div class="camera-actions">
                <button class="btn btn-delete" data-camera-id="${camera.id}">
                    TERMINATE
                </button>
            </div>
        </div>
    `;
}

// Attach Event Listeners to Batch Camera Card
function attachBatchCameraEventListeners(batchId, cameras) {
    const batchCard = document.querySelector(`.batch-camera-card[data-batch-id="${batchId}"]`);

    // Click card to open modal (except when clicking buttons)
    batchCard.addEventListener('click', (e) => {
        // Don't open modal if clicking on buttons or copy icons
        if (e.target.closest('.btn') || e.target.closest('.copy-btn')) {
            return;
        }
        showBatchCamerasModal(batchId, cameras);
    });

    // Add cursor pointer style
    batchCard.style.cursor = 'pointer';

    // Copy buttons in the card
    const copyButtons = batchCard.querySelectorAll('.copy-btn');
    copyButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const url = btn.getAttribute('data-url');
            const type = btn.getAttribute('data-type');
            copyToClipboard(url, type);
        });
    });

    // Delete all button
    const deleteAllBtn = batchCard.querySelector('.batch-delete-btn');
    deleteAllBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        deleteBatchCameras(batchId, cameras);
    });

    // Thumbnail hover for batch card (using shared snapshot)
    // Use first camera's data (lowest port) as all batch cameras share same parameters
    const sortedCameras = [...cameras].sort((a, b) => a.onvif_port - b.onvif_port);
    const firstCamera = sortedCameras[0];
    setupThumbnailHover(batchCard, batchId, true, firstCamera);
}

// Attach Event Listeners to Camera Card
function attachCameraEventListeners(cameraId) {
    // Copy buttons
    const card = document.querySelector(`[data-camera-id="${cameraId}"]`);
    if (!card) return; // Card might not exist yet if in collapsed batch

    const copyButtons = card.querySelectorAll('.copy-btn');

    copyButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const url = btn.getAttribute('data-url');
            const type = btn.getAttribute('data-type');
            copyToClipboard(url, type);
        });
    });

    // Delete button
    const deleteBtn = card.querySelector('.btn-delete');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', () => deleteCamera(cameraId));
    }

    // Thumbnail hover - get camera data from cache
    const cameraData = camerasDataCache.get(cameraId);
    setupThumbnailHover(card, cameraId, false, cameraData);
}

// Copy to Clipboard
async function copyToClipboard(text, type) {
    try {
        // Try modern Clipboard API first
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
            showToast(`${type} URL copied to clipboard`, 'success');
        } else {
            // Fallback to legacy method
            fallbackCopyToClipboard(text, type);
        }
    } catch (error) {
        // If modern API fails, try fallback
        try {
            fallbackCopyToClipboard(text, type);
        } catch (fallbackError) {
            showToast('Failed to copy to clipboard', 'error');
            console.error('Copy error:', error, fallbackError);
        }
    }
}

// Fallback copy method for browsers that don't support Clipboard API
function fallbackCopyToClipboard(text, type) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    textarea.style.pointerEvents = 'none';
    document.body.appendChild(textarea);

    textarea.select();
    textarea.setSelectionRange(0, 99999); // For mobile devices

    try {
        const successful = document.execCommand('copy');
        if (successful) {
            showToast(`${type} URL copied to clipboard`, 'success');
        } else {
            throw new Error('execCommand failed');
        }
    } finally {
        document.body.removeChild(textarea);
    }
}

// Show Batch Cameras Modal
function showBatchCamerasModal(batchId, cameras) {
    const shortBatchId = batchId.substring(0, 8);
    const preset = derivePresetLabel(cameras[0]);

    // Sort cameras by onvif_port
    const sortedCameras = [...cameras].sort((a, b) => a.onvif_port - b.onvif_port);

    // Create modal HTML
    const modalHTML = `
        <div id="batchCamerasModal" class="modal batch-modal" style="display: flex;">
            <div class="modal-content batch-modal-content">
                <div class="batch-modal-header">
                    <h2>Batch Cameras - ${shortBatchId}</h2>
                    <p class="batch-modal-subtitle">${cameras.length} cameras · ${preset.toUpperCase()}</p>
                </div>
                
                <div class="batch-cameras-accordion">
                    ${sortedCameras.map((camera, index) => createBatchCameraItem(camera, index)).join('')}
                </div>
                
                <div class="modal-actions">
                    <div class="modal-buttons">
                        <button id="closeBatchModal" class="btn btn-secondary">CLOSE</button>
                    </div>
                </div>
            </div>
        </div>
    `;

    // Remove existing modal if any
    const existingModal = document.getElementById('batchCamerasModal');
    if (existingModal) {
        existingModal.remove();
    }

    // Add to body
    document.body.insertAdjacentHTML('beforeend', modalHTML);
    document.body.style.overflow = 'hidden';

    // Setup event listeners
    setupBatchModalEventListeners(cameras);
}

// Create individual camera item for batch modal
function createBatchCameraItem(camera, index) {
    const shortId = camera.id.substring(0, 8);

    return `
        <div class="batch-camera-item" data-camera-id="${camera.id}">
            <div class="batch-camera-header" data-camera-id="${camera.id}">
                <div class="batch-camera-title">
                    <span class="batch-camera-number">#${index + 1}</span>
                    <span class="batch-camera-id">${shortId}</span>
                    <span class="batch-camera-port">Port: ${camera.onvif_port}</span>
                </div>
                <svg class="batch-camera-toggle" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="6 9 12 15 18 9"></polyline>
                </svg>
            </div>
            <div class="batch-camera-content" style="display: none;">
                <div class="batch-camera-url">
                    <div class="url-label">RTSP URL</div>
                    <div class="url-container">
                        <div class="url-text" title="${camera.rtsp_url}">${camera.rtsp_url}</div>
                        <button class="copy-btn" data-url="${camera.rtsp_url}" data-type="RTSP">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                            </svg>
                        </button>
                    </div>
                </div>
                <div class="batch-camera-url">
                    <div class="url-label">ONVIF URL</div>
                    <div class="url-container">
                        <div class="url-text" title="${camera.onvif_url}">${camera.onvif_url}</div>
                        <button class="copy-btn" data-url="${camera.onvif_url}" data-type="ONVIF">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;
}

// Setup event listeners for batch modal
function setupBatchModalEventListeners(cameras) {
    const modal = document.getElementById('batchCamerasModal');

    // Close button
    const closeBtn = document.getElementById('closeBatchModal');
    closeBtn.addEventListener('click', () => {
        modal.remove();
        document.body.style.overflow = '';
    });

    // Click outside to close
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.remove();
            document.body.style.overflow = '';
        }
    });

    // Toggle accordion items
    const headers = modal.querySelectorAll('.batch-camera-header');
    headers.forEach(header => {
        header.addEventListener('click', () => {
            const cameraId = header.getAttribute('data-camera-id');
            const item = modal.querySelector(`.batch-camera-item[data-camera-id="${cameraId}"]`);
            const content = item.querySelector('.batch-camera-content');
            const toggle = header.querySelector('.batch-camera-toggle');

            const isOpen = content.style.display !== 'none';

            if (isOpen) {
                content.style.display = 'none';
                toggle.style.transform = 'rotate(0deg)';
            } else {
                content.style.display = 'block';
                toggle.style.transform = 'rotate(180deg)';
            }
        });
    });

    // Copy buttons
    const copyButtons = modal.querySelectorAll('.copy-btn');
    copyButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const url = btn.getAttribute('data-url');
            const type = btn.getAttribute('data-type');
            copyToClipboard(url, type);
        });
    });
}

// Delete Batch Cameras
async function deleteBatchCameras(batchId, cameras) {
    if (!confirm(`Are you sure you want to terminate all ${cameras.length} cameras in this batch?`)) {
        return;
    }

    const batchCard = document.querySelector(`.batch-camera-card[data-batch-id="${batchId}"]`);
    if (batchCard) {
        batchCard.style.opacity = '0.5';
        batchCard.style.pointerEvents = 'none';
    }

    let successCount = 0;
    let failCount = 0;

    // Delete all cameras in parallel
    const deletePromises = cameras.map(camera =>
        fetch(`${API_BASE}/cameras/${camera.id}`, { method: 'DELETE' })
            .then(res => res.ok ? successCount++ : failCount++)
            .catch(() => failCount++)
    );

    try {
        await Promise.all(deletePromises);

        // Clear thumbnail cache for this batch
        thumbnailCache.delete(batchId);

        showToast(`Batch terminated: ${successCount} succeeded, ${failCount} failed`, successCount > 0 ? 'success' : 'error');

        setTimeout(() => {
            loadCameras();
        }, 300);
    } catch (error) {
        showToast('Network error occurred', 'error');
        console.error('Batch delete error:', error);
        if (batchCard) {
            batchCard.style.opacity = '1';
            batchCard.style.pointerEvents = 'auto';
        }
    }
}

// Delete Camera
async function deleteCamera(cameraId) {
    if (!confirm('Are you sure you want to terminate this camera?')) {
        return;
    }

    try {
        const response = await fetch(`${API_BASE}/cameras/${cameraId}`, {
            method: 'DELETE'
        });

        const data = await response.json();

        if (response.ok) {
            showToast('Camera terminated successfully', 'success');

            // Clear thumbnail cache for this camera
            thumbnailCache.delete(cameraId);

            // Animate out the card
            const card = document.querySelector(`[data-camera-id="${cameraId}"]`);
            if (card) {
                card.style.animation = 'slideOut 0.3s ease-out';
                setTimeout(() => {
                    loadCameras();
                }, 300);
            }
        } else {
            showToast(`Error: ${data.error}`, 'error');
        }
    } catch (error) {
        showToast('Network error occurred', 'error');
        console.error('Delete error:', error);
    }
}

// Show Toast Notification
function showToast(message, type = 'success') {
    const toastContent = toast.querySelector('.toast-content');
    toastContent.textContent = message;

    toast.className = 'toast';
    toast.classList.add(type, 'show');

    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

// Setup Thumbnail Hover
function setupThumbnailHover(card, id, isBatch = false, cameraData = null) {
    const shortId = id.substring(0, 8);
    const label = isBatch ? `BATCH ${shortId}` : `CAMERA ${shortId}`;
    let hoverTimeout;

    card.addEventListener('mouseenter', (e) => {
        // Delay showing tooltip slightly
        hoverTimeout = setTimeout(() => {
            showThumbnail(id, label, e, cameraData);
        }, 300);
    });

    card.addEventListener('mousemove', (e) => {
        if (thumbnailTooltip.classList.contains('show')) {
            positionThumbnail(e);
        }
    });

    card.addEventListener('mouseleave', () => {
        clearTimeout(hoverTimeout);
        hideThumbnail();
    });
}

// Show Thumbnail
function showThumbnail(id, label, event, cameraData = null) {
    // Ensure tooltip has correct structure
    ensureTooltipStructure();

    // Check if thumbnail is already cached
    if (thumbnailCache.has(id)) {
        const cachedData = thumbnailCache.get(id);

        if (cachedData.error) {
            // Show cached error state
            thumbnailTooltip.innerHTML = `
                <div class="thumbnail-error">
                    Snapshot not available
                </div>
            `;
        } else {
            // Ensure structure exists before using cached image
            ensureTooltipStructure();
            const img = document.getElementById('thumbnailImage');
            const lbl = document.getElementById('thumbnailLabel');
            const params = document.getElementById('thumbnailParams');

            if (img && lbl) {
                img.src = cachedData.dataUrl;
                lbl.textContent = label;

                // Update parameters if available
                if (params && cameraData) {
                    updateThumbnailParams(params, cameraData);
                }
            }
        }

        // Position and show tooltip
        positionThumbnail(event);
        thumbnailTooltip.classList.add('show');
        return;
    }

    // Show loading state immediately
    thumbnailTooltip.innerHTML = `
        <div class="thumbnail-error">
            Loading...
        </div>
    `;
    positionThumbnail(event);
    thumbnailTooltip.classList.add('show');

    // Load and cache thumbnail
    const snapshotUrl = `${API_BASE}/data/snapshots/${id}.jpg?t=${Date.now()}`;

    // Create a new image to load and cache
    const img = new Image();

    img.onload = () => {
        // Convert image to data URL for caching
        const canvas = document.createElement('canvas');
        canvas.width = img.width;
        canvas.height = img.height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);
        const dataUrl = canvas.toDataURL('image/jpeg', 0.9);

        // Cache the data URL
        thumbnailCache.set(id, { dataUrl, error: false });

        // Restore structure and display the image
        ensureTooltipStructure();
        const thumbnailImg = document.getElementById('thumbnailImage');
        const thumbnailLbl = document.getElementById('thumbnailLabel');
        const thumbnailParams = document.getElementById('thumbnailParams');

        if (thumbnailImg && thumbnailLbl) {
            thumbnailImg.src = dataUrl;
            thumbnailLbl.textContent = label;

            // Update parameters if available
            if (thumbnailParams && cameraData) {
                updateThumbnailParams(thumbnailParams, cameraData);
            }
        }
    };

    img.onerror = () => {
        // Cache the error state
        thumbnailCache.set(id, { error: true });

        thumbnailTooltip.innerHTML = `
            <div class="thumbnail-error">
                Snapshot not available
            </div>
        `;
    };

    // Start loading
    img.src = snapshotUrl;
}

// Ensure tooltip has correct HTML structure
function ensureTooltipStructure() {
    if (!thumbnailTooltip.querySelector('img')) {
        thumbnailTooltip.innerHTML = `
            <img id="thumbnailImage" alt="Camera Snapshot">
            <div class="thumbnail-label" id="thumbnailLabel"></div>
            <div class="thumbnail-params" id="thumbnailParams"></div>
        `;
    }
}

// Update thumbnail parameters
function updateThumbnailParams(paramsElement, cameraData) {
    if (!cameraData) {
        paramsElement.innerHTML = '';
        return;
    }

    const width = cameraData.width;
    const height = cameraData.height;
    const fps = cameraData.fps;
    const bitrate = cameraData.video_bitrate_mbps;

    if (width && height && fps && bitrate) {
        paramsElement.innerHTML = `
            <div class="param-item">${width}×${height}</div>
            <div class="param-item">${fps} fps</div>
            <div class="param-item">${bitrate} Mbps</div>
        `;
    } else {
        paramsElement.innerHTML = '';
    }
}

// Position Thumbnail
function positionThumbnail(event) {
    const tooltip = thumbnailTooltip;
    const offset = 20; // Distance from cursor
    const padding = 10; // Padding from viewport edges

    let x = event.clientX + offset;
    let y = event.clientY + offset;

    // Get tooltip dimensions (even if not visible yet)
    const tooltipRect = tooltip.getBoundingClientRect();
    const tooltipWidth = tooltipRect.width || 400; // fallback to max-width
    const tooltipHeight = tooltipRect.height || 300;

    // Check if tooltip would go off-screen horizontally
    if (x + tooltipWidth > window.innerWidth - padding) {
        x = event.clientX - tooltipWidth - offset; // Show on left side of cursor
    }

    // Check if tooltip would go off-screen vertically
    if (y + tooltipHeight > window.innerHeight - padding) {
        y = window.innerHeight - tooltipHeight - padding;
    }

    // Ensure tooltip doesn't go off-screen on top or left
    x = Math.max(padding, x);
    y = Math.max(padding, y);

    tooltip.style.left = `${x}px`;
    tooltip.style.top = `${y}px`;
}

// Hide Thumbnail
function hideThumbnail() {
    thumbnailTooltip.classList.remove('show');
    // Reset content after fade out
    setTimeout(() => {
        const img = document.getElementById('thumbnailImage');
        const label = document.getElementById('thumbnailLabel');
        if (img) img.src = '';
        if (label) label.textContent = '';
    }, 200);
}

// Add slide out animation
const style = document.createElement('style');
style.textContent = `
    @keyframes slideOut {
        to {
            opacity: 0;
            transform: translateX(-20px);
        }
    }
`;
document.head.appendChild(style);
