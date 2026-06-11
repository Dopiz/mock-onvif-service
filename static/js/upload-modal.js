// Upload zone + transcode preset modal: resolution detection, preset
// calculation, custom parameter form (validated against GET /config ranges),
// and the upload itself.

import { api } from './api.js';
import { state } from './state.js';
import { showToast, getPresetFromPixels, PRESET_PIXELS } from './utils.js';
import { loadCameras } from './cards.js';
import { setupVideoPreview, getEditParams, resetVideoEditing } from './video-editor.js';

// Single source for preset parameters and their default (16:9) descriptions.
// Heights/fps/bitrates drive both the calculation and the modal copy.
const PRESET_DEFAULTS = {
    '720p': { width: 1280, height: 720, fps: 30, videoBitrate: 2.5, audioBitrate: '128k' },
    '1080p': { width: 1920, height: 1080, fps: 30, videoBitrate: 4, audioBitrate: '128k' },
    '2k': { width: 2688, height: 1512, fps: 30, videoBitrate: 8, audioBitrate: '128k' },
    '4k': { width: 3840, height: 2160, fps: 30, videoBitrate: 15, audioBitrate: '128k' },
    '5k': { width: 5120, height: 2880, fps: 24, videoBitrate: 25, audioBitrate: '128k' },
};

const uploadZone = document.getElementById('uploadZone');
const videoFile = document.getElementById('videoFile');
const uploadProgress = document.getElementById('uploadProgress');
const uploadContent = uploadZone.querySelector('.upload-content');
const presetModal = document.getElementById('presetModal');

// Render the default preset descriptions into the modal (single JS source).
// Called on startup and whenever the modal closes.
export function renderDefaultPresetDescriptions() {
    Object.entries(PRESET_DEFAULTS).forEach(([name, p]) => {
        updatePresetDescriptionUI(name, p.width, p.height, p.fps, p.videoBitrate);
    });
}

// Initialize all upload/modal listeners. Call once on startup.
export function initUploadModal() {
    setupUploadZoneListeners();
    setupModalButtons();
    setupCameraCountControls();
    setupPresetChangeListeners();
}

// ── Upload zone ─────────────────────────────────────────────────────────────

function setupUploadZoneListeners() {
    // Click to upload
    uploadZone.addEventListener('click', () => {
        if (!uploadProgress.style.display || uploadProgress.style.display === 'none') {
            videoFile.click();
        }
    });

    // File selection - detect video resolution and show modal
    videoFile.addEventListener('change', async (e) => {
        if (e.target.files.length > 0) {
            state.pendingFile = e.target.files[0];
            await detectVideoResolutionAndShowModal(state.pendingFile);
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
            state.pendingFile = files[0];
            await detectVideoResolutionAndShowModal(state.pendingFile);
        }
    });
}

// ── Modal confirm / cancel ──────────────────────────────────────────────────

function setupModalButtons() {
    const confirmPresetBtn = document.getElementById('confirmPreset');
    const cancelPresetBtn = document.getElementById('cancelPreset');

    confirmPresetBtn.addEventListener('click', () => {
        const selectedPreset = document.querySelector('input[name="preset"]:checked').value;
        const cameraCountOption = document.querySelector('input[name="cameraCount"]:checked').value;
        const subProfile = document.getElementById('subProfile').checked;
        const cameraName = document.getElementById('cameraName').value.trim() || 'MockONVIF';

        let cameraCount;
        if (cameraCountOption === 'batch') {
            // Get the batch camera count from input, clamp between 2-100
            let batchCount = parseInt(document.getElementById('batchCameraCount').value);
            if (isNaN(batchCount) || batchCount < 2) {
                batchCount = 2;
            } else if (batchCount > 100) {
                batchCount = 100;
            }
            document.getElementById('batchCameraCount').value = batchCount;
            cameraCount = batchCount;
        } else {
            cameraCount = parseInt(cameraCountOption);
        }

        // Get video parameters based on selected preset or custom
        let videoParams;
        if (selectedPreset === 'custom') {
            videoParams = {
                width: parseInt(document.getElementById('customWidth').value),
                height: parseInt(document.getElementById('customHeight').value),
                fps: parseFloat(document.getElementById('customFps').value),
                videoBitrate: parseFloat(document.getElementById('customVideoBitrate').value),
                audioBitrate: document.getElementById('customAudioBitrate').value,
            };

            // Validate against the server-provided ranges (GET /config)
            const errors = validateCustomParams(videoParams);
            if (errors.length > 0) {
                showToast(errors[0], 'error');
                return; // keep the modal open so the user can fix the values
            }
        } else {
            // Use pre-calculated parameters for the selected preset
            videoParams = state.calculatedPresets[selectedPreset];
        }

        // Get edit params BEFORE hiding modal (which resets the state)
        const editParams = getEditParams();

        hidePresetModal();
        uploadVideo(state.pendingFile, videoParams, cameraCount, subProfile, cameraName, editParams);
        state.pendingFile = null;
    });

    cancelPresetBtn.addEventListener('click', () => {
        hidePresetModal();
        state.pendingFile = null;
        videoFile.value = ''; // Clear file input
    });
}

// Validate custom parameters against state.config.param_ranges
function validateCustomParams({ width, height, fps, videoBitrate, audioBitrate }) {
    const ranges = state.config.param_ranges;
    const errors = [];

    if (isNaN(width) || width < ranges.width.min || width > ranges.width.max) {
        errors.push(`Width must be between ${ranges.width.min} and ${ranges.width.max}`);
    }
    if (isNaN(height) || height < ranges.height.min || height > ranges.height.max) {
        errors.push(`Height must be between ${ranges.height.min} and ${ranges.height.max}`);
    }
    if (isNaN(fps) || fps < ranges.fps.min || fps > ranges.fps.max) {
        errors.push(`FPS must be between ${ranges.fps.min} and ${ranges.fps.max}`);
    }
    const br = ranges.video_bitrate_mbps;
    if (isNaN(videoBitrate) || videoBitrate < br.min || videoBitrate > br.max) {
        errors.push(`Video bitrate must be between ${br.min} and ${br.max} Mbps`);
    }
    if (!state.config.valid_audio_bitrates.includes(audioBitrate)) {
        errors.push(`Audio bitrate must be one of: ${state.config.valid_audio_bitrates.join(', ')}`);
    }

    return errors;
}

// ── Camera count controls ───────────────────────────────────────────────────

function setupCameraCountControls() {
    // Sub-profile checkbox reference (used in multiple places)
    const subProfileCheckbox = document.getElementById('subProfile');

    // Camera count radio buttons - handle sub-profile checkbox
    document.querySelectorAll('input[name="cameraCount"]').forEach((radio) => {
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
    if (!batchCameraCountInput) return;

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
        if (value !== '' && (isNaN(parseInt(value)) || parseInt(value) < 0)) {
            e.target.value = '';
        }
    });
}

// ── Preset selection ────────────────────────────────────────────────────────

// Setup preset / custom-input listeners once (radios are static in the HTML,
// so no cloneNode reset hack is needed).
function setupPresetChangeListeners() {
    document.querySelectorAll('input[name="preset"]').forEach((radio) => {
        radio.addEventListener('change', () => {
            updateUpscaleWarning();
            toggleCustomParamsSection();
        });
    });

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

// Calculate dimensions for a preset based on aspect ratio and original video.
// Returns dimensions that maintain aspect ratio while targeting the preset.
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
        desc.textContent = `${width}x${height} @ ${fps}fps · ${videoBitrate} Mbps`;
    }
}

// Update visual markers on preset options (upscale warning, original badge)
function updatePresetOptionMarkers(originalPixels, selectedPreset) {
    document.querySelectorAll('.preset-option').forEach((option) => {
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
        const aspectRatio = resolution.width / resolution.height;

        // Store resolution for later use (custom upscale check)
        state.originalVideoResolution = resolution;

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

        // Calculate dynamic parameters for each preset based on aspect ratio
        state.calculatedPresets = {};
        Object.keys(PRESET_DEFAULTS).forEach((presetName) => {
            const config = PRESET_DEFAULTS[presetName];
            const { width: finalWidth, height: finalHeight } = calculatePresetDimensions(
                presetName, originalPreset, config.height, aspectRatio, originalPixels, resolution
            );

            state.calculatedPresets[presetName] = {
                width: finalWidth,
                height: finalHeight,
                fps: config.fps,
                videoBitrate: config.videoBitrate,
                audioBitrate: config.audioBitrate,
            };

            updatePresetDescriptionUI(presetName, finalWidth, finalHeight, config.fps, config.videoBitrate);
        });

        // Update visual markers and auto-select the recommended preset
        updatePresetOptionMarkers(originalPixels, originalPreset);
        document.querySelector(`input[value="${originalPreset}"]`).checked = true;

        populateCustomDefaults(resolution);
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
    const { min, max } = state.config.param_ranges.video_bitrate_mbps;
    return Math.max(min, Math.min(max, bitrate));
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
    if (!state.originalVideoResolution) return;

    const width = parseInt(document.getElementById('customWidth').value) || 1920;
    const height = parseInt(document.getElementById('customHeight').value) || 1080;
    const original = state.originalVideoResolution;

    const customPixels = width * height;
    const originalPixels = original.width * original.height;

    const warningDiv = document.getElementById('upscaleWarning');
    const selectedPreset = document.querySelector('input[name="preset"]:checked');

    if (warningDiv && selectedPreset && selectedPreset.value === 'custom') {
        warningDiv.style.display = customPixels > originalPixels ? 'flex' : 'none';
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
                URL.revokeObjectURL(video.src);
                video.pause();
                video.remove();
                resolve(result);
            }
        };

        video.onloadedmetadata = function () {
            const result = {
                width: video.videoWidth,
                height: video.videoHeight,
                duration: video.duration || null,
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
                URL.revokeObjectURL(video.src);
                reject(new Error('Failed to load video metadata'));
            }
        };

        video.src = URL.createObjectURL(file);
    });
}

// ── Modal show / hide ───────────────────────────────────────────────────────

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
    renderDefaultPresetDescriptions();

    // Reset video editing state
    resetVideoEditing();
}

// ── Upload ──────────────────────────────────────────────────────────────────

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

    // Send specific video parameters (no preset concept on the backend)
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
        // /upload always returns {"cameras": [...], "count": n}
        const data = await api.upload(formData);
        const count = data.count || cameraCount;
        const message = count > 1
            ? `${count} cameras deployed successfully!`
            : 'Camera deployed successfully!';
        showToast(message, 'success');
        await loadCameras();
    } catch (error) {
        // api client already showed the toast
        console.error('Upload error:', error);
    } finally {
        // Reset upload zone
        uploadContent.style.display = 'flex';
        uploadProgress.style.display = 'none';
        videoFile.value = '';
    }
}
