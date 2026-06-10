// API client. All fetch calls go through request(): unified JSON envelope
// parsing ({"error", "type"} on failure) and a toast on every failure unless
// the caller opts out with { silent: true }.

import { showToast } from './utils.js';

const API_BASE = '';

async function request(path, { silent = false, ...options } = {}) {
    let response;
    try {
        response = await fetch(`${API_BASE}${path}`, options);
    } catch (error) {
        if (!silent) showToast('Network error occurred', 'error');
        throw error;
    }

    let data = null;
    try {
        data = await response.json();
    } catch {
        // Backend always returns JSON envelopes; tolerate empty bodies anyway.
    }

    if (!response.ok) {
        const message = data && data.error
            ? `Error: ${data.error}`
            : `Error: HTTP ${response.status}`;
        if (!silent) showToast(message, 'error');
        const err = new Error(message);
        err.status = response.status;
        err.data = data;
        throw err;
    }
    return data;
}

export const api = {
    // {"param_ranges", "valid_audio_bitrates", "edit_limits", "extend_frame_duration"}
    getConfig: () => request('/config', { silent: true }),

    // {"cameras": [...]}
    getCameras: () => request('/cameras'),

    // Always {"cameras": [...], "count": n} (single and batch)
    upload: (formData) => request('/upload', { method: 'POST', body: formData }),

    deleteCamera: (id, { silent = false } = {}) =>
        request(`/cameras/${encodeURIComponent(id)}`, { method: 'DELETE', silent }),
};
