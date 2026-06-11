// Small shared helpers: HTML escaping, formatting, toast, clipboard, presets.

// Escape a value for safe interpolation into innerHTML template literals
// (both text and attribute contexts).
export function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

// Format seconds to M:SS
export function formatDuration(seconds) {
    if (isNaN(seconds) || seconds < 0) return '0:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

// ── Toast ───────────────────────────────────────────────────────────────────

export function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    const toastContent = toast.querySelector('.toast-content');
    toastContent.textContent = message;

    toast.className = 'toast';
    toast.classList.add(type, 'show');

    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}

// ── Clipboard ───────────────────────────────────────────────────────────────

export async function copyToClipboard(text, type) {
    try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
            showToast(`${type} URL copied to clipboard`, 'success');
        } else {
            fallbackCopyToClipboard(text, type);
        }
    } catch (error) {
        try {
            fallbackCopyToClipboard(text, type);
        } catch (fallbackError) {
            showToast('Failed to copy to clipboard', 'error');
            console.error('Copy error:', error, fallbackError);
        }
    }
}

// Fallback copy method for browsers that don't support the Clipboard API
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

// ── Preset labels (derived from resolution) ─────────────────────────────────

// Preset pixel thresholds (width * height)
export const PRESET_PIXELS = {
    '720p': 1280 * 720,      // 921,600
    '1080p': 1920 * 1080,    // 2,073,600
    '2k': 2688 * 1512,       // 4,064,256
    '4k': 3840 * 2160,       // 8,294,400
    '5k': 5120 * 2880,       // 14,745,600
};

// Get preset label from pixel count. Note: anything > 4K is considered 5K.
export function getPresetFromPixels(pixels) {
    if (pixels > PRESET_PIXELS['4k']) return '5k';
    if (pixels > PRESET_PIXELS['2k']) return '4k';
    if (pixels > PRESET_PIXELS['1080p']) return '2k';
    if (pixels > PRESET_PIXELS['720p']) return '1080p';
    return '720p';
}

// Derive preset label from a camera's resolution (for UI display / filtering)
export function derivePresetLabel(camera) {
    const { width, height } = camera;
    if (!width || !height) return 'unknown';
    return getPresetFromPixels(width * height);
}
