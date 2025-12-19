// API Base URL
const API_BASE = '';

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

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    setupUploadZone();
    setupRefreshButton();
    setupFilters();
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
        const preset = (camera.preset || 'unknown').toLowerCase();
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
        '480p': '480p',
        '720p': '720p',
        '1080p': '1080p',
        '4k': '4K',
        '5k': '5K',
        'custom': 'Custom',
        'unknown': 'Unknown'
    };

    // Build buttons HTML
    const allIsActive = currentActive === 'all' ? 'active' : '';
    let buttonsHTML = `<button class="filter-btn ${allIsActive}" data-filter-type="preset" data-filter-value="all">All</button>`;

    ['480p', '720p', '1080p', '4k', '5k', 'custom', 'unknown'].forEach(preset => {
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

// Setup Filters (Initial setup - now just calls setupFilterButtons)
function setupFilters() {
    setupFilterButtons();
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
        const preset = document.querySelector('input[name="preset"]:checked').value;
        const cameraCountOption = document.querySelector('input[name="cameraCount"]:checked').value;
        const subProfile = document.getElementById('subProfile').checked;

        let cameraCount;
        if (cameraCountOption === 'batch') {
            // Get the batch camera count from input and validate
            let batchCount = parseInt(document.getElementById('batchCameraCount').value);

            // Validate and clamp between 2-50
            if (isNaN(batchCount) || batchCount < 2) {
                batchCount = 2;
            } else if (batchCount > 50) {
                batchCount = 50;
            }

            // Update the input field with the clamped value
            document.getElementById('batchCameraCount').value = batchCount;
            cameraCount = batchCount;
        } else {
            cameraCount = parseInt(cameraCountOption);
        }

        hidePresetModal();
        uploadVideo(pendingFile, preset, cameraCount, subProfile);
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

                    // Also re-enable 480p option if it was disabled
                    const p480Option = document.querySelector('input[name="preset"][value="480p"]');
                    const p480Label = p480Option?.closest('.preset-option');
                    if (p480Option) {
                        p480Option.disabled = false;
                        if (p480Label) {
                            p480Label.style.opacity = '1';
                            p480Label.style.cursor = 'pointer';
                        }
                    }
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

            // Validate and clamp between 2-50
            if (isNaN(value) || value < 2) {
                value = 2;
            } else if (value > 50) {
                value = 50;
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

    // Sub-profile checkbox handler
    if (subProfileCheckbox) {
        subProfileCheckbox.addEventListener('change', (e) => {
            const isChecked = e.target.checked;
            const p480Option = document.querySelector('input[name="preset"][value="480p"]');
            const p480Label = p480Option?.closest('.preset-option');

            if (isChecked) {
                // Disable 480p option
                if (p480Option) {
                    p480Option.disabled = true;
                    if (p480Label) {
                        p480Label.style.opacity = '0.5';
                        p480Label.style.cursor = 'not-allowed';
                    }

                    // If 480p is currently selected, auto-switch to 720p
                    if (p480Option.checked) {
                        const p720Option = document.querySelector('input[name="preset"][value="720p"]');
                        if (p720Option) {
                            p720Option.checked = true;
                            updateUpscaleWarning();
                            toggleCustomParamsSection();
                        }
                    }
                }
            } else {
                // Re-enable 480p option
                if (p480Option) {
                    p480Option.disabled = false;
                    if (p480Label) {
                        p480Label.style.opacity = '1';
                        p480Label.style.cursor = 'pointer';
                    }
                }
            }
        });
    }
}

// Detect video resolution and show modal with appropriate options
async function detectVideoResolutionAndShowModal(file) {
    try {
        const resolution = await getVideoResolution(file);
        const maxHeight = resolution.height;

        // Store resolution for custom params
        window.originalVideoResolution = resolution;

        // Update modal with detected resolution
        const originalResolution = document.getElementById('originalResolution');
        if (originalResolution) {
            originalResolution.textContent = `${resolution.width}x${resolution.height}`;
        }

        // Preset heights mapping
        const presets = {
            '480p': 480,
            '720p': 720,
            '1080p': 1080,
            '4k': 2160,
            '5k': 2880
        };

        // Determine recommended preset (closest match to original)
        let selectedPreset = '480p';
        if (maxHeight >= 2880) selectedPreset = '5k';
        else if (maxHeight >= 2160) selectedPreset = '4k';
        else if (maxHeight >= 1080) selectedPreset = '1080p';
        else if (maxHeight >= 720) selectedPreset = '720p';

        // Update all preset options with visual markers
        document.querySelectorAll('.preset-option').forEach(option => {
            const input = option.querySelector('input[type="radio"]');
            const card = option.querySelector('.preset-card');
            const preset = input.value;
            const targetHeight = presets[preset];

            // Clear all previous markers
            card.classList.remove('upscale-option', 'recommended-option');
            card.removeAttribute('data-upscale');
            const recommendedBadge = card.querySelector('.recommended-badge');
            const upscaleBadge = card.querySelector('.upscale-badge');
            if (recommendedBadge) recommendedBadge.style.display = 'none';
            if (upscaleBadge) upscaleBadge.style.display = 'none';

            // Skip custom preset for markers
            if (preset === 'custom') {
                return;
            }

            // Apply new markers
            if (targetHeight > maxHeight) {
                // Upscale option - show warning badge
                card.classList.add('upscale-option');
                card.setAttribute('data-upscale', 'true');
                if (upscaleBadge) upscaleBadge.style.display = 'inline-block';
            } else if (preset === selectedPreset) {
                // Recommended option - show recommended badge
                card.classList.add('recommended-option');
                if (recommendedBadge) recommendedBadge.style.display = 'inline-block';
            }

            // Keep all options enabled
            input.disabled = false;
            option.style.opacity = '1';
            option.style.cursor = 'pointer';
        });

        // Auto-select the recommended preset
        document.querySelector(`input[value="${selectedPreset}"]`).checked = true;

        // Populate custom defaults with original resolution
        populateCustomDefaults(resolution);

        // Setup radio change listeners for warning message and custom params
        setupPresetChangeListeners();

        // Check initial selection and show/hide warning
        updateUpscaleWarning();

        // Toggle custom params section based on initial selection
        toggleCustomParamsSection();

        // Check sub-profile checkbox state and update 480p option accordingly
        checkAndApplySubProfileState();

        showPresetModal();
    } catch (error) {
        console.error('Failed to detect video resolution:', error);
        // If detection fails, show modal with all options enabled
        const originalResolution = document.getElementById('originalResolution');
        if (originalResolution) {
            originalResolution.textContent = 'Unknown';
        }
        // Set default resolution for custom params
        populateCustomDefaults({ width: 1920, height: 1080 });
        // Setup listeners and hide custom params by default
        setupPresetChangeListeners();
        toggleCustomParamsSection();

        // Check sub-profile checkbox state and update 480p option accordingly
        checkAndApplySubProfileState();

        showPresetModal();
    }
}

// Check and apply sub-profile checkbox state to 480p option
function checkAndApplySubProfileState() {
    const subProfileCheckbox = document.getElementById('subProfile');
    const p480Option = document.querySelector('input[name="preset"][value="480p"]');
    const p480Label = p480Option?.closest('.preset-option');

    if (subProfileCheckbox && subProfileCheckbox.checked && p480Option) {
        // Disable 480p if sub-profile is checked
        p480Option.disabled = true;
        if (p480Label) {
            p480Label.style.opacity = '0.5';
            p480Label.style.cursor = 'not-allowed';
        }

        // If 480p is currently selected, auto-switch to 720p
        if (p480Option.checked) {
            const p720Option = document.querySelector('input[name="preset"][value="720p"]');
            if (p720Option) {
                p720Option.checked = true;
                updateUpscaleWarning();
                toggleCustomParamsSection();
            }
        }
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
    if (fpsInput) fpsInput.value = 30;
    if (fpsSlider) fpsSlider.value = 30;

    // Calculate and set suggested bitrate
    const suggestedBitrate = calculateSuggestedBitrate(resolution.width, resolution.height, 30);
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
    const fps = parseInt(document.getElementById('customFps').value) || 30;

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
            const value = parseInt(e.target.value) || 30;
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

// Check if custom resolution is upscaling
function checkCustomUpscale() {
    if (!window.originalVideoResolution) return;

    const width = parseInt(document.getElementById('customWidth').value) || 1920;
    const height = parseInt(document.getElementById('customHeight').value) || 1080;
    const original = window.originalVideoResolution;

    const warningDiv = document.getElementById('upscaleWarning');
    const selectedPreset = document.querySelector('input[name="preset"]:checked');

    if (warningDiv && selectedPreset && selectedPreset.value === 'custom') {
        if (width > original.width || height > original.height) {
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

        video.onloadedmetadata = function () {
            window.URL.revokeObjectURL(video.src);
            resolve({
                width: video.videoWidth,
                height: video.videoHeight
            });
        };

        video.onerror = function () {
            reject(new Error('Failed to load video metadata'));
        };

        video.src = URL.createObjectURL(file);
    });
}

// Show preset modal
function showPresetModal() {
    presetModal.style.display = 'flex';

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
    // Reset resolution text
    const originalResolution = document.getElementById('originalResolution');
    if (originalResolution) {
        originalResolution.textContent = 'Detecting...';
    }
}

// Validate custom parameters
function validateCustomParams() {
    const width = parseInt(document.getElementById('customWidth').value);
    const height = parseInt(document.getElementById('customHeight').value);
    const fps = parseInt(document.getElementById('customFps').value);
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
async function uploadVideo(file, preset = '1080p', cameraCount = 1, subProfile = false) {
    // Validate custom parameters if preset is custom
    if (preset === 'custom') {
        const errors = validateCustomParams();
        if (errors.length > 0) {
            showToast(`Validation error: ${errors[0]}`, 'error');
            return;
        }
    }

    // Show progress
    uploadContent.style.display = 'none';
    uploadProgress.style.display = 'flex';

    const formData = new FormData();
    formData.append('file', file);
    formData.append('preset', preset);
    formData.append('camera_count', cameraCount);
    formData.append('sub_profile', subProfile);

    // Add custom parameters if preset is custom
    if (preset === 'custom') {
        const width = document.getElementById('customWidth').value;
        const height = document.getElementById('customHeight').value;
        const fps = document.getElementById('customFps').value;
        const videoBitrate = document.getElementById('customVideoBitrate').value + 'M';
        const audioBitrate = document.getElementById('customAudioBitrate').value;

        formData.append('width', width);
        formData.append('height', height);
        formData.append('fps', fps);
        formData.append('video_bitrate', videoBitrate);
        formData.append('audio_bitrate', audioBitrate);
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
        // Filter by preset
        if (filterState.preset !== 'all') {
            const cameraPreset = (camera.preset || 'unknown').toLowerCase();
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

    // Get preset from first camera
    const preset = firstCamera.preset || 'unknown';
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
    const preset = camera.preset || 'unknown';
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
                    <span class="info-label">ONVIF PORT</span>
                    <span class="info-value">${camera.onvif_port}</span>
                </div>
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
    const preset = cameras[0].preset || 'unknown';

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

    // Setup event listeners
    setupBatchModalEventListeners(cameras);
}

// Create individual camera item for batch modal
function createBatchCameraItem(camera, index) {
    const shortId = camera.id.substring(0, 8);

    // Build resolution badges
    const preset = camera.preset || 'unknown';

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
    });

    // Click outside to close
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.remove();
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
