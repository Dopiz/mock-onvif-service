// Entry point: wires modules together on DOMContentLoaded.
// All logic lives in ./js/*.js (ES modules, no build step).

import { api } from './js/api.js';
import { state } from './js/state.js';
import { initFilters } from './js/filters.js';
import { initCamerasGrid, loadCameras, renderCameras } from './js/cards.js';
import { initUploadModal, renderDefaultPresetDescriptions } from './js/upload-modal.js';
import { setupVideoEditingListeners } from './js/video-editor.js';

// Fetch validation ranges / edit limits once. Falls back to the hardcoded
// DEFAULT_CONFIG (state.js) when the request fails.
async function loadConfig() {
    try {
        const config = await api.getConfig();
        if (config) {
            state.config = { ...state.config, ...config };
        }
    } catch (error) {
        console.warn('GET /config failed, using built-in defaults:', error);
    }
}

// Reflect the (possibly server-provided) limits in static UI copy
function applyConfigToUi() {
    const limits = state.config.edit_limits;

    const durationHint = document.querySelector('.duration-hint');
    if (durationHint) {
        durationHint.textContent = `(Min: ${limits.min_duration}s, Max: ${limits.max_duration}s)`;
    }

    const extendLabel = document.getElementById('extendFrameLabel');
    if (extendLabel) {
        extendLabel.textContent =
            `Extend last frame by ${state.config.extend_frame_duration} seconds (freeze frame)`;
    }
}

function setupRefreshButton() {
    const refreshBtn = document.getElementById('refreshBtn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', async () => {
            refreshBtn.classList.add('spinning');
            await loadCameras();
            setTimeout(() => {
                refreshBtn.classList.remove('spinning');
            }, 500);
        });
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    initCamerasGrid();
    initFilters(() => renderCameras(state.allCameras));
    initUploadModal();
    setupVideoEditingListeners();
    setupRefreshButton();
    renderDefaultPresetDescriptions();

    await loadConfig();
    applyConfigToUi();
    loadCameras();
});
