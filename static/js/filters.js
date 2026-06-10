// Camera list filters (resolution preset / type). One delegated listener on
// the filters container — rebuilding the buttons never re-attaches listeners.

import { state } from './state.js';
import { derivePresetLabel } from './utils.js';

const PRESET_ORDER = ['5k', '4k', '2k', '1080p', '720p', 'unknown'];
const PRESET_NAMES = {
    '5k': '5K',
    '4k': '4K',
    '2k': '2K',
    '1080p': '1080p',
    '720p': '720p',
    'unknown': 'Unknown',
};

// Set up the single delegated click listener. Call once on startup.
export function initFilters(onChange) {
    const container = document.querySelector('.cameras-filters');
    if (!container) return;

    container.addEventListener('click', (e) => {
        const btn = e.target.closest('.filter-btn');
        if (!btn) return;

        const filterType = btn.dataset.filterType;
        const filterValue = btn.dataset.filterValue;

        // Update active state for buttons of the same type
        container.querySelectorAll(`[data-filter-type="${filterType}"]`).forEach((b) => {
            b.classList.remove('active');
        });
        btn.classList.add('active');

        state.filters[filterType] = filterValue;
        onChange();
    });
}

// Apply the current filter state to a camera list
export function filterCameras(cameras) {
    return cameras.filter((camera) => {
        // Filter by preset (derived from resolution)
        if (state.filters.preset !== 'all') {
            if (derivePresetLabel(camera) !== state.filters.preset) return false;
        }

        // Filter by type
        if (state.filters.type !== 'all') {
            const isBatch = !!camera.shared_video_id;
            const hasSub = !!camera.sub_profile;

            if (state.filters.type === 'single') {
                // Single = 單台（無論有沒有 sub）
                if (isBatch) return false;
            } else if (state.filters.type === 'batch') {
                // Batch = 批次相機
                if (!isBatch) return false;
            } else if (state.filters.type === 'sub') {
                // Sub = 有 sub 的單台
                if (isBatch || !hasSub) return false;
            }
        }

        return true;
    });
}

// Rebuild the filter buttons based on available cameras
export function updateFilters(cameras) {
    const filtersContainer = document.querySelector('.cameras-filters');

    if (cameras.length === 0) {
        // Hide entire filters section if no cameras
        filtersContainer.style.display = 'none';
        return;
    }

    // Analyze available presets and types
    const availablePresets = new Set();
    const availableTypes = new Set();

    cameras.forEach((camera) => {
        availablePresets.add(derivePresetLabel(camera));

        const isBatch = !!camera.shared_video_id;
        const hasSub = !!camera.sub_profile;

        if (isBatch) {
            availableTypes.add('batch');
        } else {
            availableTypes.add('single');
            if (hasSub) availableTypes.add('sub');
        }
    });

    // Update Resolution filter (hidden if only one preset)
    const presetFilterGroup = document
        .querySelector('[data-filter-container="preset"]')
        .closest('.filter-group');
    if (availablePresets.size <= 1) {
        presetFilterGroup.style.display = 'none';
    } else {
        presetFilterGroup.style.display = 'flex';
        updatePresetButtons(availablePresets);
    }

    // Update Type filter (hidden if only one type)
    const typeFilterGroup = document
        .querySelector('[data-filter-container="type"]')
        .closest('.filter-group');
    if (availableTypes.size <= 1) {
        typeFilterGroup.style.display = 'none';
    } else {
        typeFilterGroup.style.display = 'flex';
        updateTypeButtons(availableTypes);
    }

    // Show/hide entire filters container
    const hasVisibleFilters = availablePresets.size > 1 || availableTypes.size > 1;
    filtersContainer.style.display = hasVisibleFilters ? 'flex' : 'none';
}

function filterButton(type, value, label, isActive) {
    const active = isActive ? 'active' : '';
    return `<button class="filter-btn ${active}" data-filter-type="${type}" data-filter-value="${value}">${label}</button>`;
}

function updatePresetButtons(availablePresets) {
    const presetContainer = document.querySelector('[data-filter-container="preset"]');

    // Reset filter if current selection is no longer available
    if (state.filters.preset !== 'all' && !availablePresets.has(state.filters.preset)) {
        state.filters.preset = 'all';
    }
    const current = state.filters.preset;

    let buttonsHTML = filterButton('preset', 'all', 'All', current === 'all');
    PRESET_ORDER.forEach((preset) => {
        if (availablePresets.has(preset)) {
            buttonsHTML += filterButton('preset', preset, PRESET_NAMES[preset], current === preset);
        }
    });

    presetContainer.innerHTML = buttonsHTML;
}

function updateTypeButtons(availableTypes) {
    const typeContainer = document.querySelector('[data-filter-container="type"]');

    // Reset filter if current selection is no longer available
    if (state.filters.type !== 'all' && !availableTypes.has(state.filters.type)) {
        state.filters.type = 'all';
    }
    const current = state.filters.type;

    let buttonsHTML = filterButton('type', 'all', 'All', current === 'all');
    [['single', 'Single'], ['batch', 'Batch'], ['sub', 'Sub']].forEach(([value, label]) => {
        if (availableTypes.has(value)) {
            buttonsHTML += filterButton('type', value, label, current === value);
        }
    });

    typeContainer.innerHTML = buttonsHTML;
}
