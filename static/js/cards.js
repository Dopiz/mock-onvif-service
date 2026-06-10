// Camera card rendering, the batch-cameras modal, thumbnail tooltips, and
// camera load/delete actions. All card interactions go through ONE delegated
// listener on #camerasGrid (data-action attributes) — no per-card listeners.

import { api } from './api.js';
import { state } from './state.js';
import { escapeHtml, showToast, copyToClipboard, derivePresetLabel } from './utils.js';
import { updateFilters, filterCameras } from './filters.js';

const camerasGrid = document.getElementById('camerasGrid');
const emptyState = document.getElementById('emptyState');
const cameraCount = document.getElementById('cameraCount');
const thumbnailTooltip = document.getElementById('thumbnailTooltip');

// Single source for the copy icon (was pasted 5 times)
const COPY_ICON_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
    </svg>`;

// Shared URL row template (used by all three card builders)
function renderUrlItem(label, url, type, itemClass = 'url-item') {
    const safeUrl = escapeHtml(url);
    return `
        <div class="${itemClass}">
            <div class="url-label">${escapeHtml(label)}</div>
            <div class="url-container">
                <div class="url-text" title="${safeUrl}">${safeUrl}</div>
                <button class="copy-btn" data-action="copy" data-url="${safeUrl}" data-type="${escapeHtml(type)}">
                    ${COPY_ICON_SVG}
                </button>
            </div>
        </div>`;
}

function renderInfoRow(label, value, valueClass = '') {
    const cls = valueClass ? ` ${valueClass}` : '';
    return `
                <div class="info-row">
                    <span class="info-label">${escapeHtml(label)}</span>
                    <span class="info-value${cls}">${escapeHtml(value)}</span>
                </div>`;
}

function resolutionBadgesFor(camera) {
    const preset = derivePresetLabel(camera);
    let badges = `<span class="preset-badge preset-${preset}">${preset.toUpperCase()}</span>`;
    if (camera.sub_profile) {
        badges += ' <span class="preset-badge preset-sub">+ SUB</span>';
    }
    return badges;
}

// ── Card builders ───────────────────────────────────────────────────────────

// Create Batch Camera Card HTML (consistent with single camera)
function createBatchCameraCard(cameras, sharedVideoId) {
    const count = cameras.length;
    const shortBatchId = escapeHtml(sharedVideoId.substring(0, 8));

    // Sort cameras by port to get the first one (lowest port)
    const sortedCameras = [...cameras].sort((a, b) => a.onvif_port - b.onvif_port);
    const firstCamera = sortedCameras[0];

    // macvlan: every camera shares port 80, ports differ only by IP, so a "port
    // range" is meaningless. Show "Click to check" pointing into the modal.
    const isMacvlan = !!firstCamera.camera_ip;
    const minPort = sortedCameras[0].onvif_port;
    const maxPort = sortedCameras[sortedCameras.length - 1].onvif_port;

    const infoRows = isMacvlan
        ? renderInfoRow('NAME', firstCamera.manufacturer || 'MockONVIF')
          + renderInfoRow('ONVIF PORT', '80')
          + renderInfoRow('CAMERA IP', 'Click to check', 'click-to-expand')
        : renderInfoRow('NAME', firstCamera.manufacturer || 'MockONVIF')
          + renderInfoRow('ONVIF PORT', `${minPort} - ${maxPort}`);

    return `
        <div class="camera-card batch-camera-card" data-batch-id="${escapeHtml(sharedVideoId)}">
            <div class="camera-header">
                <div class="camera-id">
                    <div class="camera-label">BATCH CAMERAS SHARED ID</div>
                    <div class="camera-id-value">${shortBatchId} ${resolutionBadgesFor(firstCamera)}</div>
                </div>
                <span class="batch-count-badge">x${count}</span>
            </div>

            <div class="camera-info">${infoRows}</div>

            <div class="camera-urls">
                ${renderUrlItem('RTSP URL (mediamtx) - First Camera', firstCamera.rtsp_url, 'RTSP')}
                ${renderUrlItem('ONVIF URL - First Camera', firstCamera.onvif_url, 'ONVIF')}
            </div>

            <div class="camera-actions">
                <button class="btn btn-delete" data-action="delete-batch" data-batch-id="${escapeHtml(sharedVideoId)}">
                    TERMINATE ALL
                </button>
            </div>
        </div>
    `;
}

// Create Camera Card HTML
function createCameraCard(camera) {
    const shortId = escapeHtml(camera.id.substring(0, 8));
    const cameraIpRow = camera.camera_ip ? renderInfoRow('CAMERA IP', camera.camera_ip) : '';

    return `
        <div class="camera-card" data-camera-id="${escapeHtml(camera.id)}">
            <div class="camera-header">
                <div class="camera-id">
                    <div class="camera-label">CAMERA ID</div>
                    <div class="camera-id-value">${shortId} ${resolutionBadgesFor(camera)}</div>
                </div>
            </div>

            <div class="camera-info">
                ${renderInfoRow('NAME', camera.manufacturer || 'MockONVIF')}
                ${renderInfoRow('ONVIF PORT', camera.onvif_port)}
                ${cameraIpRow}
            </div>

            <div class="camera-urls">
                ${renderUrlItem('RTSP URL (mediamtx)', camera.rtsp_url, 'RTSP')}
                ${renderUrlItem('ONVIF URL', camera.onvif_url, 'ONVIF')}
            </div>

            <div class="camera-actions">
                <button class="btn btn-delete" data-action="delete" data-camera-id="${escapeHtml(camera.id)}">
                    TERMINATE
                </button>
            </div>
        </div>
    `;
}

// Create individual camera item for the batch modal
function createBatchCameraItem(camera, index) {
    const shortId = escapeHtml(camera.id.substring(0, 8));
    // macvlan: show the camera's LAN IP — that's what differentiates cameras
    // when every port is 80. Standard mode still shows port.
    const subtitle = camera.camera_ip
        ? `IP: ${camera.camera_ip}`
        : `Port: ${camera.onvif_port}`;

    return `
        <div class="batch-camera-item" data-camera-id="${escapeHtml(camera.id)}">
            <div class="batch-camera-header" data-camera-id="${escapeHtml(camera.id)}">
                <div class="batch-camera-title">
                    <span class="batch-camera-number">#${index + 1}</span>
                    <span class="batch-camera-id">${shortId}</span>
                    <span class="batch-camera-port">${escapeHtml(subtitle)}</span>
                </div>
                <svg class="batch-camera-toggle" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="6 9 12 15 18 9"></polyline>
                </svg>
            </div>
            <div class="batch-camera-content" style="display: none;">
                ${renderUrlItem('RTSP URL', camera.rtsp_url, 'RTSP', 'batch-camera-url')}
                ${renderUrlItem('ONVIF URL', camera.onvif_url, 'ONVIF', 'batch-camera-url')}
            </div>
        </div>
    `;
}

// ── Rendering ───────────────────────────────────────────────────────────────

export async function loadCameras() {
    try {
        const data = await api.getCameras();
        const cameras = data.cameras || [];

        state.allCameras = cameras;
        state.camerasById.clear();
        cameras.forEach((camera) => state.camerasById.set(camera.id, camera));

        updateFilters(cameras);
        renderCameras(cameras);
    } catch (error) {
        console.error('Failed to load cameras:', error);
    }
}

export function renderCameras(cameras) {
    const filteredCameras = filterCameras(cameras);

    // Sort cameras by created_at (oldest first), then by onvif_port
    filteredCameras.sort((a, b) => {
        const timeA = a.created_at || 0;
        const timeB = b.created_at || 0;
        if (timeA !== timeB) return timeA - timeB;
        return (a.onvif_port || 0) - (b.onvif_port || 0);
    });

    // Update count with filtered results
    cameraCount.textContent = filteredCameras.length;

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

    filteredCameras.forEach((camera) => {
        if (camera.shared_video_id) {
            if (!batchGroups.has(camera.shared_video_id)) {
                batchGroups.set(camera.shared_video_id, []);
            }
            batchGroups.get(camera.shared_video_id).push(camera);
        } else {
            individualCameras.push(camera);
        }
    });

    const cardsHTML = [];

    individualCameras.forEach((camera) => {
        cardsHTML.push(createCameraCard(camera));
    });

    batchGroups.forEach((batchCameras, sharedVideoId) => {
        if (batchCameras.length > 1) {
            cardsHTML.push(createBatchCameraCard(batchCameras, sharedVideoId));
        } else {
            // Only one camera in this "batch", render as individual
            cardsHTML.push(createCameraCard(batchCameras[0]));
        }
    });

    camerasGrid.innerHTML = cardsHTML.join('');
}

// ── Event delegation on the grid ────────────────────────────────────────────

let hoverTimeout = null;

export function initCamerasGrid() {
    camerasGrid.addEventListener('click', onGridClick);
    camerasGrid.addEventListener('mouseover', onGridMouseOver);
    camerasGrid.addEventListener('mouseout', onGridMouseOut);
    camerasGrid.addEventListener('mousemove', onGridMouseMove);
}

function onGridClick(e) {
    const copyBtn = e.target.closest('[data-action="copy"]');
    if (copyBtn) {
        copyToClipboard(copyBtn.dataset.url, copyBtn.dataset.type);
        return;
    }

    const deleteBtn = e.target.closest('[data-action="delete"]');
    if (deleteBtn) {
        deleteCamera(deleteBtn.dataset.cameraId);
        return;
    }

    const deleteBatchBtn = e.target.closest('[data-action="delete-batch"]');
    if (deleteBatchBtn) {
        deleteBatchCameras(deleteBatchBtn.dataset.batchId);
        return;
    }

    // Click anywhere else on a batch card opens the batch modal
    const batchCard = e.target.closest('.batch-camera-card');
    if (batchCard) {
        showBatchCamerasModal(batchCard.dataset.batchId);
    }
}

function batchCamerasFor(batchId) {
    return state.allCameras
        .filter((c) => c.shared_video_id === batchId)
        .sort((a, b) => a.onvif_port - b.onvif_port);
}

function cardHoverInfo(card) {
    if (card.classList.contains('batch-camera-card')) {
        const batchId = card.dataset.batchId;
        // Use first camera's data (lowest port): all batch cameras share params
        const firstCamera = batchCamerasFor(batchId)[0] || null;
        return { id: batchId, label: `BATCH ${batchId.substring(0, 8)}`, camera: firstCamera };
    }
    const id = card.dataset.cameraId;
    return { id, label: `CAMERA ${id.substring(0, 8)}`, camera: state.camerasById.get(id) || null };
}

function onGridMouseOver(e) {
    const card = e.target.closest('.camera-card');
    if (!card) return;
    // Ignore moves between elements inside the same card
    if (e.relatedTarget && card.contains(e.relatedTarget)) return;

    const { id, label, camera } = cardHoverInfo(card);
    // Delay showing tooltip slightly
    hoverTimeout = setTimeout(() => {
        showThumbnail(id, label, e, camera);
    }, 300);
}

function onGridMouseOut(e) {
    const card = e.target.closest('.camera-card');
    if (!card) return;
    if (e.relatedTarget && card.contains(e.relatedTarget)) return;

    clearTimeout(hoverTimeout);
    hideThumbnail();
}

function onGridMouseMove(e) {
    if (thumbnailTooltip.classList.contains('show')) {
        positionThumbnail(e);
    }
}

// ── Delete actions ──────────────────────────────────────────────────────────

async function deleteCamera(cameraId) {
    if (!confirm('Are you sure you want to terminate this camera?')) {
        return;
    }

    try {
        await api.deleteCamera(cameraId);
        showToast('Camera terminated successfully', 'success');

        // Clear thumbnail cache for this camera
        state.thumbnailCache.delete(cameraId);

        // Animate out the card
        const card = document.querySelector(`[data-camera-id="${CSS.escape(cameraId)}"]`);
        if (card) {
            card.style.animation = 'slideOut 0.3s ease-out';
            setTimeout(() => {
                loadCameras();
            }, 300);
        }
    } catch (error) {
        // api client already showed the toast
        console.error('Delete error:', error);
    }
}

async function deleteBatchCameras(batchId) {
    const cameras = batchCamerasFor(batchId);
    if (!confirm(`Are you sure you want to terminate all ${cameras.length} cameras in this batch?`)) {
        return;
    }

    const batchCard = document.querySelector(`.batch-camera-card[data-batch-id="${CSS.escape(batchId)}"]`);
    if (batchCard) {
        batchCard.style.opacity = '0.5';
        batchCard.style.pointerEvents = 'none';
    }

    let successCount = 0;
    let failCount = 0;

    // Delete all cameras in parallel (silent: one summary toast at the end)
    await Promise.all(cameras.map((camera) =>
        api.deleteCamera(camera.id, { silent: true })
            .then(() => successCount++)
            .catch(() => failCount++)
    ));

    // Clear thumbnail cache for this batch
    state.thumbnailCache.delete(batchId);

    showToast(
        `Batch terminated: ${successCount} succeeded, ${failCount} failed`,
        successCount > 0 ? 'success' : 'error'
    );

    setTimeout(() => {
        loadCameras();
    }, 300);
}

// ── Batch cameras modal ─────────────────────────────────────────────────────

function showBatchCamerasModal(batchId) {
    const cameras = batchCamerasFor(batchId);
    if (cameras.length === 0) return;

    const shortBatchId = escapeHtml(batchId.substring(0, 8));
    const preset = derivePresetLabel(cameras[0]);

    const modalHTML = `
        <div id="batchCamerasModal" class="modal batch-modal" style="display: flex;">
            <div class="modal-content batch-modal-content">
                <div class="batch-modal-header">
                    <h2>Batch Cameras - ${shortBatchId}</h2>
                    <p class="batch-modal-subtitle">${cameras.length} cameras · ${preset.toUpperCase()}</p>
                </div>

                <div class="batch-cameras-accordion">
                    ${cameras.map((camera, index) => createBatchCameraItem(camera, index)).join('')}
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

    document.body.insertAdjacentHTML('beforeend', modalHTML);
    document.body.style.overflow = 'hidden';

    const modal = document.getElementById('batchCamerasModal');

    const closeModal = () => {
        modal.remove();
        document.body.style.overflow = '';
    };

    // One delegated listener handles close / copy / accordion toggle
    modal.addEventListener('click', (e) => {
        if (e.target === modal || e.target.closest('#closeBatchModal')) {
            closeModal();
            return;
        }

        const copyBtn = e.target.closest('[data-action="copy"]');
        if (copyBtn) {
            copyToClipboard(copyBtn.dataset.url, copyBtn.dataset.type);
            return;
        }

        const header = e.target.closest('.batch-camera-header');
        if (header) {
            const item = header.closest('.batch-camera-item');
            const content = item.querySelector('.batch-camera-content');
            const toggle = header.querySelector('.batch-camera-toggle');
            const isOpen = content.style.display !== 'none';

            content.style.display = isOpen ? 'none' : 'block';
            toggle.style.transform = isOpen ? 'rotate(0deg)' : 'rotate(180deg)';
        }
    });
}

// ── Thumbnail tooltip ───────────────────────────────────────────────────────

function showThumbnail(id, label, event, cameraData = null) {
    ensureTooltipStructure();

    // Check if thumbnail is already cached
    if (state.thumbnailCache.has(id)) {
        const cachedData = state.thumbnailCache.get(id);

        if (cachedData.error) {
            thumbnailTooltip.innerHTML = '<div class="thumbnail-error">Snapshot not available</div>';
        } else {
            ensureTooltipStructure();
            const img = document.getElementById('thumbnailImage');
            const lbl = document.getElementById('thumbnailLabel');
            const params = document.getElementById('thumbnailParams');

            if (img && lbl) {
                img.src = cachedData.dataUrl;
                lbl.textContent = label;
                if (params && cameraData) {
                    updateThumbnailParams(params, cameraData);
                }
            }
        }

        positionThumbnail(event);
        thumbnailTooltip.classList.add('show');
        return;
    }

    // Show loading state immediately
    thumbnailTooltip.innerHTML = '<div class="thumbnail-error">Loading...</div>';
    positionThumbnail(event);
    thumbnailTooltip.classList.add('show');

    // Prefer the server-provided snapshot_url; fall back to the canonical path
    const baseUrl = (cameraData && cameraData.snapshot_url) || `/snapshots/${id}.jpg`;
    const snapshotUrl = `${baseUrl}?t=${Date.now()}`;

    const img = new Image();

    img.onload = () => {
        // Convert image to data URL for caching
        const canvas = document.createElement('canvas');
        canvas.width = img.width;
        canvas.height = img.height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0);
        const dataUrl = canvas.toDataURL('image/jpeg', 0.9);

        state.thumbnailCache.set(id, { dataUrl, error: false });

        // Restore structure and display the image
        ensureTooltipStructure();
        const thumbnailImg = document.getElementById('thumbnailImage');
        const thumbnailLbl = document.getElementById('thumbnailLabel');
        const thumbnailParams = document.getElementById('thumbnailParams');

        if (thumbnailImg && thumbnailLbl) {
            thumbnailImg.src = dataUrl;
            thumbnailLbl.textContent = label;
            if (thumbnailParams && cameraData) {
                updateThumbnailParams(thumbnailParams, cameraData);
            }
        }
    };

    img.onerror = () => {
        state.thumbnailCache.set(id, { error: true });
        thumbnailTooltip.innerHTML = '<div class="thumbnail-error">Snapshot not available</div>';
    };

    img.src = snapshotUrl;
}

// Ensure tooltip has the correct HTML structure
function ensureTooltipStructure() {
    if (!thumbnailTooltip.querySelector('img')) {
        thumbnailTooltip.innerHTML = `
            <img id="thumbnailImage" alt="Camera Snapshot">
            <div class="thumbnail-label" id="thumbnailLabel"></div>
            <div class="thumbnail-params" id="thumbnailParams"></div>
        `;
    }
}

function updateThumbnailParams(paramsElement, cameraData) {
    if (!cameraData) {
        paramsElement.innerHTML = '';
        return;
    }

    const { width, height, fps, video_bitrate_mbps: bitrate } = cameraData;

    if (width && height && fps && bitrate) {
        paramsElement.innerHTML = `
            <div class="param-item">${escapeHtml(width)}×${escapeHtml(height)}</div>
            <div class="param-item">${escapeHtml(fps)} fps</div>
            <div class="param-item">${escapeHtml(bitrate)} Mbps</div>
        `;
    } else {
        paramsElement.innerHTML = '';
    }
}

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

    // Keep the tooltip inside the viewport
    if (x + tooltipWidth > window.innerWidth - padding) {
        x = event.clientX - tooltipWidth - offset; // Show on left side of cursor
    }
    if (y + tooltipHeight > window.innerHeight - padding) {
        y = window.innerHeight - tooltipHeight - padding;
    }
    x = Math.max(padding, x);
    y = Math.max(padding, y);

    tooltip.style.left = `${x}px`;
    tooltip.style.top = `${y}px`;
}

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
