let ws;
let reportPartners = [];
let reportSheetName = '';
let reportSheetUpdating = false;
let selectedPartners = new Set();
let failedLinks = [];
let duplicateLinks = [];
let duplicateRowCount = 0;
let googleOAuthAuthorized = false;

// Everything that differs between platforms lives here. Adding a platform means adding
// one entry (plus its icon in static/platform-icons/, a source row in index.html with the
// ids from `dom`, and backend support); the platform bar, link matching, metric labels,
// status texts and source-control wiring are derived from it.
const PLATFORMS = Object.freeze({
    tiktok: Object.freeze({
        key: 'tiktok',
        label: 'TikTok',
        icon: '/static/platform-icons/tiktok.svg',
        defaultScrapeMode: 'request',
        linkHosts: ['tiktok.com', 'www.tiktok.com', 'm.tiktok.com', 'mobile.tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com'],
        linkPath: /^\/./,
        liveLinkHeader: 'LINK TIKTOK',
        // Fourth metric column: TikTok saves vs Threads reposts.
        savedMetric: Object.freeze({ column: 'LƯỢT LƯU', label: 'Lượt lưu' }),
        previewKeepsTotalRow: true,
        hidesGeneratedSheetTabs: false,
        cookieSession: false,
        partialWithMetricsIsSuccess: false,
        reconcileCountsFromRows: false,
        noStatsTag: 'Ẩn số liệu',
        noStatsReason: () => 'Không đọc được số liệu (TikTok ẩn / không trả lượt xem)',
        completedWithIssues: () => 'Hoàn tất, có link thiếu số',
        dom: Object.freeze({ button: 'platformTikTok', sourceLabel: 'sourceFileTikTok', url: 'googleSheetUrlInput', sync: 'syncSheetBtn', sheet: 'pushSheetSelect', push: 'pushGoogleBtn' }),
    }),
    threads: Object.freeze({
        key: 'threads',
        label: 'Threads',
        icon: '/static/platform-icons/threads.svg',
        defaultScrapeMode: 'hybrid',
        linkHosts: ['threads.com', 'www.threads.com', 'threads.net', 'www.threads.net'],
        linkPath: /^(?:\/@[^/]+\/post\/|\/share\/)[A-Za-z0-9_-]+\/?$/,
        liveLinkHeader: 'LINK THREADS',
        savedMetric: Object.freeze({ column: 'REPOST', label: 'Repost' }),
        previewKeepsTotalRow: false,
        hidesGeneratedSheetTabs: true,
        cookieSession: true,
        partialWithMetricsIsSuccess: true,
        reconcileCountsFromRows: true,
        noStatsTag: 'Thiếu số',
        noStatsReason: item => item.status || 'Không đọc được số liệu Threads',
        completedWithIssues: errorCount => (errorCount > 0 ? 'Hoàn tất, có link lỗi' : 'Hoàn tất, có link không trả số liệu'),
        dom: Object.freeze({ button: 'platformThreads', sourceLabel: 'sourceFileThreads', url: 'threadsGoogleSheetUrlInput', sync: 'threadsSyncSheetBtn', sheet: 'threadsPushSheetSelect', push: 'threadsPushGoogleBtn' }),
    }),
});
const PLATFORM_KEYS = Object.keys(PLATFORMS);

let activePlatform = PLATFORM_KEYS[0];

function isPlatform(platform) {
    return Object.prototype.hasOwnProperty.call(PLATFORMS, platform);
}

function platformConfig(platform = activePlatform) {
    return PLATFORMS[platform] || PLATFORMS[activePlatform] || PLATFORMS[PLATFORM_KEYS[0]];
}

function emptyPlatformSource() {
    return { fileId: '', label: '', displaySheet: '', scanSheet: '', pushSheet: '', sheets: [], url: '', dirty: false };
}

const platformScrapeModes = Object.fromEntries(PLATFORM_KEYS.map(key => [key, PLATFORMS[key].defaultScrapeMode]));
const platformSources = Object.fromEntries(PLATFORM_KEYS.map(key => [key, emptyPlatformSource()]));

function activeSource() {
    return platformSources[activePlatform];
}

// The active platform's source slot is the single source of truth. These long-standing
// names are live views onto it (reads and writes go to platformSources[activePlatform]),
// so switching platform can never leave a stale file or sheet behind.
for (const [name, key] of Object.entries({
    currentFileId: 'fileId',
    currentSheetName: 'displaySheet',
    currentScanSheetName: 'scanSheet',
    currentPushSheetName: 'pushSheet',
    googleSheetUrlDirty: 'dirty',
})) {
    Object.defineProperty(globalThis, name, {
        configurable: true,
        get: () => activeSource()[key],
        set: value => { activeSource()[key] = value; },
    });
}

let sourceBusy = false;
let sourceRevision = 0;
let sourcesInitialized = false;
let sourcePreferencesLoad = null;
let sourcePreferencesReady = false;
let sourcePreferencesGeneration = 0;
let sourcePreferencesPending = null;
let sourcePreferencesWriting = false;
let sourcePreferencesSaveFailed = false;
let sourcePreferencesScheduled = false;
let activeWorkspaceTab = 'sheet';
let pendingLiveResults = 0;
let desktopUpdateCheckInFlight = false;
let desktopUpdateInstalling = false;
let websocketSessionReady = false;
const WS_RECONNECT_BASE_MS = 1000;
const WS_RECONNECT_MAX_MS = 30000;
const WS_PROBE_AFTER_FAILURES = 5;
let wsReconnectFailures = 0;
let wsSessionProbed = false;
let wsConnectionAbandoned = false;
let scanPhase = 'idle';
let currentRunContext = null;
let lastTerminalStatus = '';
const liveResultClasses = new Map();
let lastProgressData = null;
const readRequestSequence = { files: 0, preview: 0, summary: 0, partners: 0 };
const startBtn = document.getElementById('startBtn');
const cancelBtn = document.getElementById('cancelBtn');
const workerCountSelect = document.getElementById('workerCountSelect');
const scrapeModeSelect = document.getElementById('scrapeModeSelect');
const proxyUseCheckbox = document.getElementById('proxyUseCheckbox');
const proxyTextInput = document.getElementById('proxyTextInput');
const proxyTestResult = document.getElementById('proxyTestResult');
const proxyModalSummary = document.getElementById('proxyModalSummary');
const proxyCard = document.getElementById('proxyCard');
const proxyConfigBtn = document.getElementById('proxyConfigBtn');
let proxyListText = '';
let proxySavedCount = 0;
const SERVER_BUILD_KEY = 'serverProxyBuild';
let threadsCookieState = { configured: false, state: 'none', generation: '' };
let threadsCookieBusy = false;
let threadsCookieFocus = null;
let threadsCookieSource = 'file';
let threadsCookieRevision = 0;

const BTN_START_IDLE = '<span class="material-icons-outlined">play_arrow</span><span>Bắt đầu quét</span>';
const BTN_START_BUSY = '<span class="material-icons-outlined">hourglass_top</span><span>Đang quét...</span>';
const BTN_CANCEL_IDLE = '<span class="material-icons-outlined">stop_circle</span><span>Hủy</span>';
const BTN_CANCEL_BUSY = '<span class="material-icons-outlined">hourglass_top</span><span>Đang hủy...</span>';

function setProxyCardState({ ready = false, active = false } = {}) {
    if (!proxyCard) return;
    proxyCard.classList.toggle('ready', ready);
    proxyCard.classList.toggle('active', active);
}

function syncProxyCardActiveState() {
    const ready = proxySavedCount > 0;
    setProxyCardState({ ready, active: ready && Boolean(proxyUseCheckbox && proxyUseCheckbox.checked) });
}
const scanSheetSelect = document.getElementById('scanSheetSelect');

function renderPlatformBar() {
    const bar = document.getElementById('platformBar');
    if (!bar) return;
    bar.innerHTML = PLATFORM_KEYS.map(key => {
        const config = PLATFORMS[key];
        const selected = key === activePlatform;
        return `<button class="platform-option${selected ? ' active' : ''}" id="${escapeHtml(config.dom.button)}" type="button" aria-pressed="${selected}" onclick="setPlatform('${escapeHtml(key)}')"><img class="platform-brand-icon" src="${escapeHtml(config.icon)}" alt="" aria-hidden="true">${escapeHtml(config.label)}</button>`;
    }).join('');
}

function applyPlatformUI(platform) {
    if (!isPlatform(platform)) return;
    platformScrapeModes[activePlatform] = scrapeModeSelect.value;
    activePlatform = platform;
    const config = platformConfig(platform);
    document.body.dataset.platform = platform;
    const sessionControls = document.getElementById('threadsSessionControls');
    if (sessionControls) sessionControls.hidden = !config.cookieSession;
    scrapeModeSelect.value = platformScrapeModes[platform];
    document.querySelectorAll('.platform-option').forEach(button => {
        const selected = button.id === config.dom.button;
        button.classList.toggle('active', selected);
        button.setAttribute('aria-pressed', selected ? 'true' : 'false');
    });
    document.getElementById('liveLinkHeader').textContent = config.liveLinkHeader;
    document.getElementById('liveSavedHeader').textContent = config.savedMetric.column;
    const reportIcon = document.getElementById('reportPlatformIcon');
    const reportLabel = document.getElementById('reportPlatformLabel');
    if (reportIcon) reportIcon.src = config.icon;
    if (reportLabel) reportLabel.textContent = `Đối tác & báo cáo ${config.label}`;
}

function resetProgressDisplay() {
    for (const id of ['processedLinks', 'successLinks', 'hiddenCountBadge', 'failedCountBadge']) {
        document.getElementById(id).textContent = '0';
    }
    document.getElementById('progressBar').style.width = '0%';
    document.getElementById('progressText').textContent = 'Sẵn sàng chờ lệnh...';
    const statusEl = document.getElementById('progressStatus');
    statusEl.textContent = '';
    statusEl.className = 'progress-status';
}

// Drop rows, counters and failure/duplicate lists that belong to another run.
function clearLiveResults({ duplicates = true, emptyText = '' } = {}) {
    liveResultClasses.clear();
    lastProgressData = null;
    document.getElementById('dataFeed').innerHTML = emptyText
        ? `<tr><td colspan="10" class="workspace-empty">${escapeHtml(emptyText)}</td></tr>`
        : '';
    pendingLiveResults = 0;
    updatePendingLiveResults();
    clearFailedLinks(false);
    if (duplicates) clearDuplicateLinks(false);
}

async function setPlatform(platform) {
    if (!isPlatform(platform) || scanIsBusy() || desktopUpdateInstalling || sourceBusy || threadsCookieBusy || activePlatform === platform) return;
    saveActiveSource();
    sourceBusy = true;
    ++sourceRevision;
    syncScanControls();
    try {
        if (platformSources[platform].fileId) await activateSource(platform);
        applyPlatformUI(platform);
        restoreActiveSource();
    } catch (error) {
        notify(error.message || 'Không chuyển được nguồn dữ liệu.', 'error');
        return;
    } finally { sourceBusy = false; syncScanControls(); }
    clearLiveResults({ emptyText: 'Chưa có kết quả mới' });
    resetProgressDisplay();
    if (platformConfig(platform).hidesGeneratedSheetTabs && isSummarySheetName(currentSheetName)) {
        currentSheetName = filterDataSheets(window.lastWorkbookSheets || [])[0] || '';
    }
    setWorkspaceTab('sheet');
    void loadPreview();
}

const WORKSPACE_VIEW_META = Object.freeze({
    sheet: {
        title: 'Dữ liệu sheet hiện tại',
        subtitle: 'Xem và chuyển sheet trong cùng một vùng làm việc',
        panel: 'sheet',
    },
    live: {
        title: 'Kết quả đang quét',
        subtitle: 'Kết quả mới nhất nằm ở đầu danh sách',
        panel: 'live',
    },
    success: {
        title: 'Đọc số liệu thành công',
        subtitle: 'Chỉ hiển thị các link đã đọc được số liệu',
        panel: 'live',
    },
    nostats: {
        title: 'Không đọc được số liệu',
        subtitle: 'Link hoạt động nhưng nền tảng không trả số liệu',
        panel: 'nostats',
    },
    errors: {
        title: 'Lỗi quét cần kiểm tra',
        subtitle: 'Lỗi kết nối, link không hợp lệ hoặc lỗi xử lý',
        panel: 'errors',
    },
    duplicates: {
        title: 'Link bị trùng',
        subtitle: 'Hiển thị đầy đủ sheet và số dòng của từng link',
        panel: 'duplicates',
    },
    logs: {
        title: 'Nhật ký hệ thống',
        subtitle: 'Theo dõi chi tiết quá trình quét và cập nhật dữ liệu',
        panel: 'logs',
    },
});

function syncCompactDrawerState() {
    const sourceOpen = document.body.classList.contains('source-drawer-open');
    const settingsOpen = document.body.classList.contains('scan-settings-open');
    const sourceDrawer = document.getElementById('sourceDrawer');
    const settingsDrawer = document.getElementById('scanSettingsDrawer');
    const backdrop = document.getElementById('compactDrawerBackdrop');
    const sourceButton = document.getElementById('sourceDrawerButton');
    const settingsButton = document.getElementById('scanSettingsButton');
    const settingsAreInline = window.matchMedia('(min-width: 1025px)').matches;

    if (sourceDrawer) sourceDrawer.setAttribute('aria-hidden', sourceOpen ? 'false' : 'true');
    if (settingsDrawer) settingsDrawer.setAttribute('aria-hidden', settingsAreInline || settingsOpen ? 'false' : 'true');
    if (sourceButton) sourceButton.setAttribute('aria-expanded', sourceOpen ? 'true' : 'false');
    if (settingsButton) settingsButton.setAttribute('aria-expanded', settingsOpen ? 'true' : 'false');
    if (backdrop) backdrop.classList.toggle('active', sourceOpen || settingsOpen);
}

function openSourceDrawer() {
    document.body.classList.remove('scan-settings-open');
    document.body.classList.add('source-drawer-open');
    syncCompactDrawerState();
}

function closeSourceDrawer() {
    document.body.classList.remove('source-drawer-open');
    syncCompactDrawerState();
}

function openScanSettingsDrawer() {
    document.body.classList.remove('source-drawer-open');
    document.body.classList.add('scan-settings-open');
    syncCompactDrawerState();
}

function closeCompactDrawers() {
    document.body.classList.remove('source-drawer-open', 'scan-settings-open');
    syncCompactDrawerState();
}

function syncCompactSourceSummary(fileLabel = '', sheetName = '') {
    const fileSelect = document.getElementById('excelFileSelect');
    const selectedFile = fileSelect?.selectedOptions?.[0];
    const resolvedFile = String(fileLabel || (selectedFile?.value ? selectedFile.textContent : '') || currentFileId || '').trim();
    const resolvedSheet = String(sheetName || scanSheetSelect?.value || currentScanSheetName || currentSheetName || '').trim();
    const fileEl = document.getElementById('compactSourceFile');
    const sheetEl = document.getElementById('compactSourceSheet');

    if (fileEl) {
        fileEl.textContent = resolvedFile || 'Chưa chọn file';
        fileEl.title = resolvedFile || 'Chưa chọn file';
    }
    if (sheetEl) {
        sheetEl.textContent = resolvedSheet || 'Chưa chọn sheet';
        sheetEl.title = resolvedSheet || 'Chưa chọn sheet';
    }
}

function showNewestLiveResults() {
    const wrapper = document.querySelector('.live-results-wrap');
    if (wrapper) wrapper.scrollTo({ top: 0, behavior: 'smooth' });
    pendingLiveResults = 0;
    const button = document.getElementById('newResultsButton');
    if (button) button.hidden = true;
}

function updatePendingLiveResults() {
    const button = document.getElementById('newResultsButton');
    const text = document.getElementById('newResultsButtonText');
    if (!button || !text) return;
    text.textContent = `Có ${pendingLiveResults} kết quả mới`;
    button.hidden = pendingLiveResults < 1;
}

function setWorkspaceTab(tabName) {
    const meta = WORKSPACE_VIEW_META[tabName];
    if (!meta) return;

    activeWorkspaceTab = tabName;
    document.body.dataset.workspaceTab = tabName;
    document.querySelectorAll('.workspace-tab').forEach(button => {
        const active = button.dataset.workspaceTab === tabName;
        button.classList.toggle('active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    document.querySelectorAll('.workspace-panel').forEach(panel => {
        panel.classList.toggle('active', panel.dataset.workspacePanel === meta.panel);
    });
    document.querySelectorAll('.workspace-action').forEach(button => {
        button.hidden = button.dataset.actionFor !== tabName;
    });

    const title = document.getElementById('workspaceViewTitle');
    const subtitle = document.getElementById('workspaceViewSubtitle');
    if (title) title.textContent = meta.title;
    if (subtitle) subtitle.textContent = meta.subtitle;

    if (meta.panel === 'live') showNewestLiveResults();
}

function cmdLine(type, message) {
    const prefix = type ? `[${type}] ` : '';
    return `${prefix}${message}`;
}

function detectLogLevel(msg, explicitLevel = '') {
    const level = String(explicitLevel || '').trim().toUpperCase();
    if (level) return level;
    const text = String(msg || '');
    if (
        text.includes('LỖI')
        || text.includes('Lỗi')
        || text.includes('Mất kết nối')
        || text.includes('thất bại')
        || text.includes('Error:')
        || text.includes('[4/10] Lỗi')
    ) {
        return 'ERROR';
    }
    if (text.includes('CẢNH BÁO') || text.includes('Đang chờ') || text.includes('Ẩn số liệu')) return 'WARN';
    if (
        text.includes('OK •')
        || text.includes('] OK •')
        || text.includes('thành công')
        || text.includes('HOÀN THÀNH')
        || text.includes('--- QUÉT HOÀN TẤT ---')
    ) {
        return 'OK';
    }
    return 'INFO';
}

function logLevelColor(level) {
    if (level === 'ERROR') return '#ff6b6b';
    if (level === 'WARN') return '#fbbf24';
    if (level === 'OK') return '#7ddc83';
    return '#d4d4d4';
}

function renderLogMetricChip(label, value, className, title = '') {
    const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
    return `<span class="log-chip ${className}"${titleAttr}><span class="log-chip-label">${escapeHtml(label)}</span><span class="log-chip-value">${escapeHtml(formatNumber(value))}</span></span>`;
}

function renderScrapeLogHtml(details, level) {
    const progress = `<span class="log-badge log-progress">[${details.processed}/${details.total}]</span>`;
    const worker = `<span class="log-meta">Luồng ${escapeHtml(String(details.worker || '?'))}</span>`;
    const elapsed = `<span class="log-meta">${escapeHtml(String(details.elapsed))}s</span>`;
    const rows = details.rows ? `<span class="log-meta log-rows" title="Dòng Excel">📄 ${escapeHtml(details.rows)}</span>` : '';
    const url = details.url
        ? `<a class="log-url" href="${escapeHtml(details.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(details.url)}</a>`
        : '';

    if (details.kind === 'scrape_ok') {
        const m = details.metrics || {};
        const channel = `<span class="log-channel" title="Tên kênh">${escapeHtml(details.channel || '—')}</span>`;
        const metrics = [
            renderLogMetricChip('Lượt xem', m.views, 'log-views', 'Số lần xem video/ảnh'),
            renderLogMetricChip('Tim', m.likes, 'log-likes', 'Lượt thích (tim)'),
            renderLogMetricChip('Bình luận', m.comments, 'log-comments', 'Số bình luận'),
            renderLogMetricChip('Lưu', m.saves, 'log-saves', 'Lượt lưu / bookmark trên TikTok'),
            renderLogMetricChip('Chia sẻ', m.shares, 'log-shares', 'Số lần chia sẻ'),
        ].join('');
        return `<div class="log-rich log-rich-ok">${progress}<span class="log-badge log-ok">OK</span>${channel}<span class="log-metrics">${metrics}</span>${worker}${elapsed}${rows}${url ? `<div class="log-url-row">${url}</div>` : ''}</div>`;
    }

    if (details.kind === 'scrape_hidden') {
        return `<div class="log-rich log-rich-warn">${progress}<span class="log-badge log-warn">Ẩn số liệu</span><span class="log-meta">TikTok không trả lượt xem cho post này</span>${worker}${elapsed}${rows}${url ? `<div class="log-url-row">${url}</div>` : ''}</div>`;
    }

    if (details.kind === 'scrape_error') {
        const reason = `<span class="log-error-text">${escapeHtml(details.status || 'Lỗi không xác định')}</span>`;
        const attempts = details.attempts ? `<span class="log-meta">Thử ${details.attempts} lần</span>` : '';
        return `<div class="log-rich log-rich-error">${progress}<span class="log-badge log-error">Lỗi</span>${reason}${attempts}${worker}${elapsed}${rows}${url ? `<div class="log-url-row">${url}</div>` : ''}</div>`;
    }

    return escapeHtml(cmdLine(level, ''));
}

function renderLogBody(msg, level, details) {
    if (details && details.kind) {
        return renderScrapeLogHtml(details, level);
    }
    return `<span class="log-plain" style="color:${logLevelColor(level)}">${escapeHtml(cmdLine(level, msg))}</span>`;
}

function addLog(msg, options = {}) {
    const logs = document.getElementById('logs');
    const div = document.createElement('div');
    div.className = 'log-line';
    const now = new Date();
    const time = now.toLocaleTimeString();
    const fullTime = now.toLocaleString('vi-VN');
    const level = detectLogLevel(msg, options.level);
    const details = options.details || null;
    const storedLine = `[${fullTime}] [${level}] ${msg}`;
    div.dataset.logText = storedLine;
    div.innerHTML = `<span class="log-time">[${time}]</span> ${renderLogBody(msg, level, details)}`;
    logs.appendChild(div);
    logs.scrollTop = logs.scrollHeight;
    const logCountBadge = document.getElementById('logCountBadge');
    if (logCountBadge) logCountBadge.textContent = logs.querySelectorAll('.log-line').length;
}

const TOAST_ICONS = { success: 'check_circle', error: 'error', warn: 'warning', info: 'info' };

function showToast(message, level = 'info', timeout = 4200) {
    const stack = document.getElementById('toastStack');
    if (!stack) return;
    const toast = document.createElement('div');
    toast.className = `toast ${level}`;
    toast.innerHTML = `<span class="material-icons-outlined">${TOAST_ICONS[level] || 'info'}</span><span>${escapeHtml(message)}</span>`;
    stack.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(12px)';
        setTimeout(() => toast.remove(), 220);
    }, timeout);
}

function notify(message, level = 'info') {
    const logLevel = level === 'warn' ? 'WARN' : level === 'success' ? 'OK' : level === 'error' ? 'ERROR' : '';
    addLog(message, { level: logLevel });
    showToast(message, level);
}

function isNoStatsStatus(status) {
    const text = String(status || '');
    return (
        text.includes('Ẩn số liệu')
        || text.startsWith('Partial:')
        || text.includes('TikTok không trả số liệu')
        || text.includes('TikTok không trả lượt xem')
        || text.includes('không trả số liệu')
        || text.includes('không trả lượt xem')
    );
}

function getLogText() {
    return Array.from(document.querySelectorAll('#logs .log-line'))
        .map(line => line.dataset.logText || line.textContent.trim())
        .join('\n');
}

function logExportFilename() {
    const now = new Date();
    const pad = value => String(value).padStart(2, '0');
    const stamp = `${pad(now.getDate())}-${pad(now.getMonth() + 1)}-${now.getFullYear()}-${pad(now.getHours())}-${pad(now.getMinutes())}`;
    return `log-${stamp}.txt`;
}

function exportLogs() {
    const text = getLogText();
    if (!text) {
        addLog('Không có log để xuất.');
        return;
    }
    try {
        const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = logExportFilename();
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(link.href);
        addLog(`Đã xuất log ra file ${logExportFilename()}.`, { level: 'OK' });
    } catch (error) {
        addLog(`Lỗi xuất log: ${error.message}`);
    }
}

async function copyLogs() {
    const text = getLogText();
    if (!text) {
        addLog('Không có log để copy.');
        return;
    }
    try {
        if (navigator.clipboard) {
            await navigator.clipboard.writeText(text);
        } else {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            textarea.remove();
        }
        addLog('Đã copy toàn bộ log hệ thống.');
    } catch (error) {
        addLog(`Lỗi copy log: ${error.message}`);
    }
}

function clearLogs() {
    document.getElementById('logs').innerHTML = '';
    addLog('Đã xóa log hệ thống.');
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function fileDisplayLabel(file) {
    return typeof file === 'string' ? file : (file?.label || file?.id || '');
}

function isSummarySheetName(value) {
    const key = normalizeVietnameseKey(value);
    return key === 'tong ket' || key.startsWith('tong ket ');
}

function dataSheetNameForSummaryTab(summaryTabName, sheets) {
    const prefix = 'Tổng kết ';
    const text = String(summaryTabName || '').trim();
    if (!text.toLowerCase().startsWith(prefix.toLowerCase())) return '';
    const suffix = text.slice(prefix.length).trim().toLowerCase();
    if (!suffix) return '';
    return (Array.isArray(sheets) ? sheets : []).find(sheet => {
        return String(sheet || '').trim().toLowerCase() === suffix;
    }) || '';
}

function isResultSheetName(value) {
    const key = normalizeVietnameseKey(value);
    return PLATFORM_KEYS.some(platform => key.startsWith(`report seeding ${normalizeVietnameseKey(PLATFORMS[platform].label)}`))
        || /^(?:T\d{1,2}\s+)?\d{2}-\d{2}-\d{4}-\d{2}[:-]?\d{2}(?:-\d+)?$/.test(String(value || '').trim());
}

function filterDataSheets(sheets) {
    return (Array.isArray(sheets) ? sheets : []).filter(sheet => sheet && !isSummarySheetName(sheet) && !isResultSheetName(sheet));
}

function renderSheetSelect(selectEl, sheets, preferredSheet, onUpdate) {
    const validSheets = filterDataSheets(sheets);
    selectEl.innerHTML = '';
    if (validSheets.length === 0) {
        selectEl.innerHTML = '<option value="">Không có sheet</option>';
        onUpdate('');
        selectEl.disabled = true;
        return;
    }
    const currentValue = preferredSheet && validSheets.includes(preferredSheet)
        ? preferredSheet
        : (validSheets.includes(selectEl.value) ? selectEl.value : validSheets[0]);
    validSheets.forEach(sheet => {
        const opt = document.createElement('option');
        opt.value = sheet;
        opt.textContent = sheet;
        if (sheet === currentValue) opt.selected = true;
        selectEl.appendChild(opt);
    });
    if (!validSheets.includes(selectEl.value)) selectEl.value = validSheets[0];
    onUpdate(selectEl.value);
    selectEl.disabled = false;
}

function renderScanSheetOptions(sheets, selectedSheet = '') {
    renderSheetSelect(
        scanSheetSelect,
        sheets,
        selectedSheet || currentScanSheetName,
        value => { currentScanSheetName = value; }
    );
}

function renderPushSheetOptions(sheets, selectedSheet = '') {
    renderSheetSelect(
        sourceControls(activePlatform).sheet,
        sheets,
        selectedSheet || currentPushSheetName || currentScanSheetName,
        value => { currentPushSheetName = value; setGooglePushState(); }
    );
}

// The only rule for the "Tạo sheet" buttons: idle UI, Google login, a target URL,
// and that platform's own file and sheet.
function setGooglePushState() {
    for (const platform of PLATFORM_KEYS) {
        const source = platformSources[platform], controls = sourceControls(platform);
        const enabled = Boolean(!scanIsBusy() && !desktopUpdateInstalling && !sourceBusy && !threadsCookieBusy
            && googleOAuthAuthorized && controls.url?.value.trim() && source.fileId && (controls.sheet?.value || source.pushSheet));
        if (controls.push) { controls.push.disabled = !enabled; controls.push.title = enabled ? '' : 'Cần đăng nhập Google, link đích và file/sheet của nền tảng này.'; }
    }
}

function rememberServerBuild(build) {
    const value = String(build || '').trim();
    if (value) {
        localStorage.setItem(SERVER_BUILD_KEY, value);
    }
}

async function checkServerVersion() {
    const banner = document.getElementById('serverBanner');
    try {
        const res = await fetch('/api/version');
        const data = await res.json();
        const build = String(data.proxyTestBuild || '').trim();
        if (!build) return;
        const stored = localStorage.getItem(SERVER_BUILD_KEY);
        if (stored && stored !== build && banner) {
            banner.hidden = false;
            banner.textContent = 'Server đã cập nhật code (build '
                + build
                + '). Restart Khoidong.bat (Ctrl+C → chạy lại) rồi F5 trang này.';
            rememberServerBuild(build);
        } else if (banner) {
            banner.hidden = true;
            rememberServerBuild(build);
        } else {
            rememberServerBuild(build);
        }
    } catch (error) {
        console.error(error);
    }
}

async function loadProxyList() {
    try {
        const res = await fetch('/proxy-list');
        const data = await res.json();
        proxyListText = String(data.text || '');
        proxySavedCount = Number(data.count || 0);
        if (proxyTextInput && !proxyTextInput.value.trim()) {
            proxyTextInput.value = proxyListText;
        }
        return data;
    } catch (error) {
        notify(error.message || 'Không tải được danh sách proxy', 'error');
        return { text: '', count: 0, samples: [] };
    }
}

function currentProxyText() {
    if (proxyTextInput && proxyTextInput.value.trim()) {
        return proxyTextInput.value;
    }
    return proxyListText || '';
}

function isLegacyProxyTestResponse(data) {
    if (!data || typeof data !== 'object') return true;
    if (String(data.build || '').trim()) return false;
    if (data.uniqueIps != null) return true;
    if (String(data.message || '').includes('IP khác')) return true;
    const total = Number(data.count || 0);
    const rows = Array.isArray(data.results) ? data.results.length : 0;
    if (total > 0 && rows > total) return true;
    return data.okCount == null && rows > 0;
}

function proxyResultName(item, index) {
    if (item?.name) return item.name;
    const label = String(item?.label || '');
    const region = label.match(/region-([A-Za-z0-9_-]+)/i);
    if (region) return region[1].toUpperCase();
    const userMatch = label.match(/^HTTP ([^@]+)@/);
    if (userMatch) {
        const user = userMatch[1];
        return user.length <= 28 ? user : `${user.slice(0, 25)}...`;
    }
    return `Dòng ${item?.line || index + 1}`;
}

function renderProxyTestResult(data, isError = false) {
    if (!proxyTestResult) return;
    if (isError) {
        proxyTestResult.className = 'proxy-test-result error';
        proxyTestResult.textContent = String(data || 'Test thất bại');
        return;
    }
    if (isLegacyProxyTestResponse(data)) {
        proxyTestResult.className = 'proxy-test-result error';
        proxyTestResult.textContent = [
            'Server đang chạy code cũ (chưa restart sau cập nhật).',
            '',
            '1. Vào cửa sổ Khoidong.bat → nhấn Ctrl+C',
            '2. Chạy lại Khoidong.bat',
            '3. Test proxy lại',
        ].join('\n');
        return;
    }
    const results = Array.isArray(data.results) ? data.results : [];
    const total = Number(data.count || results.length || 0);
    const okCount = Number(data.okCount ?? results.filter(item => item && item.ok && item.ip).length);
    const lines = [`${okCount}/${total} proxy OK`];
    results.forEach((item, index) => {
        const name = proxyResultName(item, index);
        if (item && item.ok && item.ip) {
            lines.push(`${name} → ${item.ip}`);
        } else {
            lines.push(`${name} → lỗi`);
        }
    });
    proxyTestResult.className = `proxy-test-result ${okCount > 0 ? 'ok' : 'error'}`;
    proxyTestResult.textContent = lines.join('\n');
    rememberServerBuild(data.build);
}

async function refreshProxyStatus() {
    if (!proxyUseCheckbox) return;
    try {
        const data = await loadProxyList();
        const count = Number(data.count || 0);
        proxySavedCount = count;
        const ready = count > 0;
        proxyUseCheckbox.disabled = false;
        proxyUseCheckbox.title = ready
            ? `${count} proxy đã lưu — bấm Cấu hình để xem/sửa`
            : 'Bấm Cấu hình để dán proxy';
        setProxyCardState({ ready, active: ready && proxyUseCheckbox.checked });
        if (proxyConfigBtn) {
            proxyConfigBtn.title = ready ? `${count} proxy đã lưu` : 'Dán & test proxy';
        }
    } catch (error) {
        setProxyCardState({ ready: false, active: false });
    }
}

function updateProxyModalSummary(count) {
    if (!proxyModalSummary) return;
    if (count > 0) {
        proxyModalSummary.textContent = `Đã lưu ${count} proxy. Mỗi dòng 1 proxy — các tuyến proxy được phân bổ cho luồng quét tùy nền tảng và chế độ.`;
    } else {
        proxyModalSummary.textContent = 'Chưa có proxy. Dán vào ô bên dưới rồi Test hoặc Lưu.';
    }
}

function openProxyModal() {
    const modal = document.getElementById('proxyModal');
    if (!modal) return;
    closeCompactDrawers();
    if (proxyTextInput) {
        proxyTextInput.value = currentProxyText();
    }
    if (proxyTestResult) {
        proxyTestResult.className = 'proxy-test-result';
        proxyTestResult.textContent = 'Chưa test.';
    }
    loadProxyList().then(data => updateProxyModalSummary(Number(data.count || 0)));
    modal.classList.add('active');
    modal.setAttribute('aria-hidden', 'false');
    if (proxyTextInput) proxyTextInput.focus();
}

function closeProxyModal() {
    const modal = document.getElementById('proxyModal');
    if (!modal) return;
    modal.classList.remove('active');
    modal.setAttribute('aria-hidden', 'true');
}

async function saveProxyList() {
    const text = proxyTextInput ? proxyTextInput.value : '';
    const btn = document.getElementById('proxySaveBtn');
    if (btn) btn.disabled = true;
    try {
        const res = await fetch('/proxy-list', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.message || 'Lưu thất bại');
        proxyListText = text;
        updateProxyModalSummary(Number(data.count || 0));
        await refreshProxyStatus();
        notify(data.message || 'Đã lưu proxy', 'success');
    } catch (error) {
        notify(error.message || 'Không lưu được proxy', 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function testProxyList() {
    const text = proxyTextInput ? proxyTextInput.value : '';
    const btn = document.getElementById('proxyTestBtn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="material-icons-outlined">hourglass_top</span> Đang test...';
    }
    if (proxyTestResult) {
        proxyTestResult.className = 'proxy-test-result';
        proxyTestResult.textContent = 'Đang kiểm tra proxy...';
    }
    try {
        const res = await fetch(`/proxy-test?_=${Date.now()}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, save: false }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.message || 'Test thất bại');
        renderProxyTestResult(data);
        notify(data.message || 'Test xong', Number(data.okCount || 0) > 0 ? 'success' : 'warn');
    } catch (error) {
        renderProxyTestResult(error.message || 'Test thất bại', true);
        notify(error.message || 'Test proxy thất bại', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<span class="material-icons-outlined">network_check</span> Test proxy';
        }
    }
}

async function refreshGoogleOauthStatus() {
    try {
        const res = await fetch('/google-oauth-status');
        const data = await res.json();
        const loginBtn = document.getElementById('googleLoginBtn');
        const oauthBtn = document.getElementById('googleOauthBtn');
        googleOAuthAuthorized = Boolean(data.valid ?? data.authorized);
        if (loginBtn) {
            const accountEmail = String(data.accountEmail || '').trim();
            loginBtn.disabled = !data.configured;
            const loginLabel = data.valid
                ? 'Đã đăng nhập'
                : (data.authorized ? 'Token hết hạn' : 'Chưa đăng nhập');
            loginBtn.title = data.configured
                ? (accountEmail ? `Tài khoản: ${accountEmail}` : loginLabel)
                : 'Cần nạp file OAuth trước khi đăng nhập';
            loginBtn.textContent = loginLabel;
        }
        if (oauthBtn) {
            oauthBtn.style.display = data.configured ? 'none' : '';
        }
        setGooglePushState();
    } catch (error) {
        console.error(error);
    }
}

function normalizeVietnameseKey(value) {
    return String(value || '')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .replace(/đ/g, 'd')
        .replace(/Đ/g, 'D')
        .replace(/\s+/g, ' ')
        .trim()
        .toLowerCase();
}

function formatNumber(value) {
    const number = Number(value || 0);
    return Number.isFinite(number) ? number.toLocaleString('vi-VN') : '0';
}

// Unknown metrics stay blank on every platform; only a confirmed number (including 0) is shown.
function formatResultNumber(value) {
    return value === null || value === undefined || value === '' ? '' : formatNumber(value);
}

function summaryDashboardTitle(data, totals) {
    const sheetTitle = String(data.sheet || 'Tổng kết').trim();
    const partnerCount = Number(totals.partners || 0);
    const partnerMeta = partnerCount > 0
        ? `<span class="summary-title-meta">${formatNumber(partnerCount)} đối tác</span>`
        : '';
    return `${escapeHtml(sheetTitle)}${partnerMeta}`;
}

function renderSummaryTableCell(column, value, { footer = false } = {}) {
    const key = normalizeVietnameseKey(column);
    if (key === 'doi tac') {
        return `<td class="partner-cell" title="${escapeHtml(value)}">${escapeHtml(value)}</td>`;
    }
    if (key === 'cap nhat lan cuoi') return `<td>${escapeHtml(value)}</td>`;
    if (key === 'stt') return `<td class="number-cell">${value === '' ? '' : formatNumber(value)}</td>`;
    if (key === 'tong link') return `<td class="number-cell total-link-cell">${formatNumber(value)}</td>`;
    return `<td class="number-cell">${formatResultNumber(value)}</td>`;
}

function setPreviewTableVisible(visible) {
    const wrapper = document.querySelector('#previewTable')?.closest('.table-wrap');
    if (wrapper) wrapper.style.display = visible ? '' : 'none';
    document.getElementById('summaryDashboard').classList.toggle('active', !visible);
}

function sourceControls(platform) {
    const { dom } = platformConfig(platform);
    return {
        url: document.getElementById(dom.url),
        sheet: document.getElementById(dom.sheet),
        sync: document.getElementById(dom.sync),
        push: document.getElementById(dom.push),
        label: document.getElementById(dom.sourceLabel),
    };
}

function validatedSourceItem(item = {}) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) throw new Error('Nguồn đã lưu không hợp lệ.');
    const source = {};
    for (const key of ['fileId', 'displaySheet', 'scanSheet', 'pushSheet', 'url']) {
        const value = item[key] ?? '';
        if (typeof value !== 'string' || /[\x00-\x1f\x7f]/.test(value)) throw new Error('Nguồn đã lưu không hợp lệ.');
        source[key] = key === 'url' ? value.trim() : value;
    }
    if (source.fileId.length > 1024 || /^(?:[\\/]|[A-Za-z]:)/.test(source.fileId)
        || source.fileId.split(/[\\/]/).some(part => ['.', '..'].includes(part))) throw new Error('Tên file đã lưu không hợp lệ.');
    for (const key of ['displaySheet', 'scanSheet', 'pushSheet']) {
        if (source[key].length > 31 || /[\\/?*\[\]:]/.test(source[key])) throw new Error('Tên sheet đã lưu không hợp lệ.');
    }
    if (source.url) {
        const url = new URL(source.url);
        const path = url.pathname.match(/^\/spreadsheets\/d\/([A-Za-z0-9_-]+)(?:\/(?:edit|view|copy))?\/?$/);
        if (source.url.length >= 4096 || url.protocol !== 'https:' || url.hostname !== 'docs.google.com' || url.port || url.username || url.password || !path) throw new Error('URL Google Sheet đã lưu không hợp lệ.');
        const gid = url.searchParams.get('gid') || new URLSearchParams(url.hash.slice(1)).get('gid');
        source.url = `https://docs.google.com/spreadsheets/d/${path[1]}/edit${gid && /^\d+$/.test(gid) ? `#gid=${gid}` : ''}`;
    }
    return source;
}

function validatedSourcePreferences(sources) {
    if (!sources || typeof sources !== 'object' || Array.isArray(sources)) throw new Error('Nguồn đã lưu không hợp lệ.');
    // Like the backend, a platform missing from saved preferences simply has no source yet.
    return Object.fromEntries(PLATFORM_KEYS.map(platform => [platform, validatedSourceItem(sources[platform] ?? {})]));
}

function sourcePreferencesSnapshot() {
    // Do not include cookies, proxy settings, labels or runtime state in persistent preferences.
    const sources = {};
    for (const platform of PLATFORM_KEYS) {
        const { fileId, displaySheet, scanSheet, pushSheet, url } = platformSources[platform];
        const source = { fileId, displaySheet, scanSheet, pushSheet, url };
        // A partially typed/invalid URL is UI state, not a persistable Google target.
        try { source.url = validatedSourceItem(source).url; }
        catch { source.url = ''; }
        sources[platform] = source;
    }
    return validatedSourcePreferences(sources);
}

function storeSourcePreferencesFallback(sources) {
    try { localStorage.setItem('riviuPlatformSourcesV1', JSON.stringify(sources)); } catch {}
}

function loadSourcePreferences() {
    if (sourcePreferencesLoad) return sourcePreferencesLoad;
    const generation = sourcePreferencesGeneration;
    sourcePreferencesLoad = (async () => {
        let sources;
        try {
            const response = await fetch('/source-preferences');
            const data = await response.json();
            if (!response.ok || data.error) throw new Error(data.error || 'Không tải được nguồn đã lưu.');
            sources = validatedSourcePreferences(data.sources);
        } catch {
            sourcePreferencesSaveFailed = true;
            try { sources = validatedSourcePreferences(JSON.parse(localStorage.getItem('riviuPlatformSourcesV1') || 'null')); } catch {}
        }
        if (sources && generation === sourcePreferencesGeneration) {
            for (const platform of PLATFORM_KEYS) Object.assign(platformSources[platform], sources[platform]);
        }
        sourcePreferencesReady = true;
        scheduleSourcePreferencesSave();
    })();
    return sourcePreferencesLoad;
}

function scheduleSourcePreferencesSave() {
    if (!sourcePreferencesReady || !sourcePreferencesPending || sourcePreferencesScheduled || sourcePreferencesWriting
        || sourceBusy || scanIsBusy() || desktopUpdateInstalling) return;
    sourcePreferencesScheduled = true;
    Promise.resolve().then(() => {
        sourcePreferencesScheduled = false;
        void flushSourcePreferences();
    });
}

async function flushSourcePreferences() {
    if (!sourcePreferencesReady || sourcePreferencesWriting || !sourcePreferencesPending
        || sourceBusy || scanIsBusy() || desktopUpdateInstalling) return;
    sourcePreferencesWriting = true;
    try {
        while (sourcePreferencesPending && !sourceBusy && !scanIsBusy() && !desktopUpdateInstalling) {
            const pending = sourcePreferencesPending;
            sourcePreferencesPending = null;
            try {
                const response = await fetch('/source-preferences', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sources: pending.sources }) });
                const data = await response.json();
                if (response.status === 409) {
                    sourcePreferencesPending ||= pending;
                    break; // Retry on the next idle control sync, not while the backend is locked.
                }
                if (!response.ok || !data.success) throw new Error(data.error || 'Không lưu được nguồn dữ liệu.');
                sourcePreferencesSaveFailed = false;
                try { localStorage.removeItem('riviuPlatformSourcesV1'); } catch {}
            } catch {
                sourcePreferencesSaveFailed = true;
                storeSourcePreferencesFallback(sourcePreferencesPending?.sources || pending.sources);
            }
        }
    } finally {
        sourcePreferencesWriting = false;
    }
}

function persistSources() {
    ++sourcePreferencesGeneration;
    try {
        sourcePreferencesPending = { generation: sourcePreferencesGeneration, sources: sourcePreferencesSnapshot() };
        if (sourcePreferencesSaveFailed) storeSourcePreferencesFallback(sourcePreferencesPending.sources);
        scheduleSourcePreferencesSave();
    } catch {}
}

// The slot already holds file/sheet state; only the URL field still needs capturing.
function saveActiveSource() {
    const source = activeSource();
    source.url = sourceControls(activePlatform).url?.value || source.url;
    persistSources();
}

function renderSourceRows() {
    for (const platform of PLATFORM_KEYS) {
        const source = platformSources[platform], controls = sourceControls(platform);
        if (controls.url && controls.url.value !== source.url) controls.url.value = source.url;
        if (controls.sheet) renderSheetSelect(controls.sheet, source.sheets, source.pushSheet || source.scanSheet, value => { source.pushSheet = value; });
        if (controls.label) { controls.label.textContent = source.label || source.fileId || 'Chưa chọn file'; controls.label.title = source.label || source.fileId; }
    }
    setGooglePushState();
}

// Re-render every control from the active platform's slot.
function restoreActiveSource() {
    const source = activeSource();
    renderScanSheetOptions(source.sheets, source.scanSheet);
    renderSourceRows();
    document.getElementById('excelFileSelect').value = source.fileId;
    syncCompactSourceSummary(source.label || source.fileId, source.scanSheet);
}

// A saved sheet name may no longer exist (renamed/deleted tab, re-synced Google Sheet).
// Never let it block the workbook: drop names the known sheet list lacks, and if the server
// still rejects the sheets, retry once without them so it picks its default sheet.
async function activateSource(platform) {
    const source = platformSources[platform];
    if (!source.fileId) return;
    if (!source.sheets?.length) {
        // Saved preferences carry no sheet list. Learn it first so the saved scan sheet can
        // be checked and kept instead of being reset to the workbook's first sheet.
        try {
            const { response, data } = await fetchFileList(platform, source.fileId);
            if (response.ok && data.current === source.fileId && Array.isArray(data.sheets)) {
                source.sheets = data.sheets;
                source.label ||= data.currentLabel || '';
            }
        } catch {}
    }
    const known = source.sheets || [];
    const keep = sheet => (sheet && (!known.length || known.includes(sheet)) ? sheet : '');
    const select = (sheetName, scanSheet) => fetch('/select-file', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: source.fileId, platform, sheet_name: sheetName, scan_sheet: scanSheet }) });
    const sheetName = keep(source.displaySheet), scanSheet = keep(source.scanSheet);
    let response = await select(sheetName, scanSheet);
    if (response.status === 400 && (sheetName || scanSheet)) response = await select('', '');
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error || 'Không chọn được workbook.');
    source.displaySheet = data.sheet || ''; source.scanSheet = data.scanSheet || source.displaySheet;
    source.pushSheet = source.sheets.includes(source.pushSheet) ? source.pushSheet : source.scanSheet;
    persistSources();
}

function sourceQuery(extra = {}, platform = activePlatform, fileId = currentFileId) {
    return new URLSearchParams({ ...extra, file_id: fileId, platform });
}

function setGoogleSheetUrlField(url, { force = false } = {}) {
    const normalized = String(url || '').trim();
    const source = activeSource();
    if (force || !source.dirty) {
        source.url = normalized;
        if (sourceControls(activePlatform).url) sourceControls(activePlatform).url.value = normalized;
        if (force) source.dirty = false;
    }
}

function beginReadRequest(kind, sheetName = undefined) {
    const sequence = ++readRequestSequence[kind];
    const fileId = currentFileId;
    const platform = activePlatform;
    const displaySheet = currentSheetName;
    const revision = sourceRevision;
    return (data = {}) => revision === sourceRevision && sequence === readRequestSequence[kind]
        && fileId === currentFileId && platform === activePlatform
        && (sheetName === undefined || displaySheet === currentSheetName)
        && (!fileId || !(data.file || data.current) || (data.file || data.current) === fileId);
}

// Always pass an explicit file_id (possibly empty): without it the server answers with its
// legacy global selection, which may be another platform's workbook.
async function fetchFileList(platform, fileId) {
    const response = await fetch(`/list-files?${sourceQuery({}, platform, fileId)}`);
    const data = await response.json();
    return { response, data };
}

// First load: reopen only the workbook this platform saved. Anything else (nothing saved,
// file deleted, unreadable) leaves the platform on its empty "choose a source" state.
async function initializeSources({ applyGoogleSheetUrl = false } = {}) {
    const isCurrent = beginReadRequest('files');
    sourcesInitialized = true;
    const platform = activePlatform, saved = platformSources[platform];
    if (saved.fileId) {
        sourceBusy = true; syncScanControls();
        try {
            const { response, data } = await fetchFileList(platform, saved.fileId);
            if (!isCurrent(data)) return;
            if (!response.ok || data.error || data.current !== saved.fileId) throw new Error(data.error || 'Không tải được workbook đã lưu.');
            saved.sheets = data.sheets || [];
            saved.label = data.currentLabel || saved.fileId;
            // The workbook exists: a rejected selection must not forget the saved file.
            try { await activateSource(platform); }
            catch (error) {
                if (!isCurrent()) return;
                addLog(`Không chọn lại được sheet đã lưu: ${error.message}`);
            }
            if (!isCurrent()) return;
            restoreActiveSource();
        } catch (error) {
            if (!isCurrent()) return;
            Object.assign(saved, emptyPlatformSource(), { url: saved.url });
            persistSources();
            addLog(`Không mở lại được nguồn đã lưu: ${error.message}`);
        } finally { sourceBusy = false; syncScanControls(); }
    }
    await updateFileList({ applyGoogleSheetUrl: applyGoogleSheetUrl && !saved.url });
}

function applyFileListState(data, { applyGoogleSheetUrl = false } = {}) {
    if (applyGoogleSheetUrl) setGoogleSheetUrlField(data.googleSheetUrl);
    const source = activeSource();
    const sheets = data.sheets || [];
    source.sheets = sheets;
    source.label = source.fileId ? (data.currentLabel || source.fileId) : '';
    if (!sheets.includes(source.displaySheet)) source.displaySheet = data.currentSheet || data.scanSheet || '';
    if (!sheets.includes(source.scanSheet)) source.scanSheet = data.scanSheet || data.currentSheet || '';
    source.pushSheet ||= source.scanSheet;
    renderScanSheetOptions(sheets, source.scanSheet);
    renderPushSheetOptions(sheets, source.pushSheet);
    googleOAuthAuthorized = Boolean(data.googleOAuthAuthorized);
    setGooglePushState();
}

function renderFileOptions(files, selectedId) {
    const select = document.getElementById('excelFileSelect');
    select.innerHTML = '';
    if (!files.length) {
        select.innerHTML = '<option value="">(Không có file nào)</option>';
        return;
    }
    if (!selectedId) {
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = '— Chọn file cho nền tảng này —';
        placeholder.selected = true;
        select.appendChild(placeholder);
    }
    files.forEach(file => {
        const opt = document.createElement('option');
        opt.value = file.id;
        opt.textContent = fileDisplayLabel(file);
        if (file.id === selectedId) opt.selected = true;
        select.appendChild(opt);
    });
}

async function updateFileList({ applyGoogleSheetUrl = false } = {}) {
    if (!sourcesInitialized) return initializeSources({ applyGoogleSheetUrl });
    const isCurrent = beginReadRequest('files');
    try {
        const { response, data } = await fetchFileList(activePlatform, currentFileId);
        if (!isCurrent(data)) return;
        if (!response.ok || data.error) throw new Error(data.error || 'Không tải được nguồn dữ liệu.');
        const files = data.files || [];
        applyFileListState(data, { applyGoogleSheetUrl });
        renderFileOptions(files, currentFileId);
        syncCompactSourceSummary('', currentScanSheetName);
        if (files.length) await refreshGoogleOauthStatus();
        saveActiveSource();
        renderSourceRows();
    } catch (error) {
        if (!isCurrent()) return;
        console.error(error);
        addLog(`Lỗi tải danh sách file: ${error.message}`);
    } finally {
        if (scanIsBusy()) applyRunContext(currentRunContext || {});
        syncScanControls();
    }
}

async function selectFile(fileId) {
    if (!fileId || sourceBusy || scanIsBusy() || threadsCookieBusy || desktopUpdateInstalling) return;
    const platform = activePlatform;
    sourceBusy = true; ++sourceRevision; syncScanControls();
    const source = platformSources[platform];
    try {
        source.displaySheet = '';
        setGooglePushState();
        const res = await fetch('/select-file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: fileId, platform })
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Không chọn được file');
        const displaySheet = data.sheet || '';
        const scanSheet = data.scanSheet || displaySheet;
        Object.assign(source, { fileId: data.selected, displaySheet, scanSheet, pushSheet: scanSheet, dirty: false });
        await updateFileList({ applyGoogleSheetUrl: true });
        await loadPreview();
        addLog(`Đã chuyển sang sheet: ${fileDisplayLabel({ id: source.fileId, label: source.fileId })}`);
    } catch (error) {
        addLog(`Lỗi: ${error.message}`);
    } finally { sourceBusy = false; syncScanControls(); }
}

async function syncGoogleSheet(platform = activePlatform) {
    if (!isPlatform(platform) || sourceBusy || scanIsBusy() || threadsCookieBusy || desktopUpdateInstalling) return;
    const controls = sourceControls(platform);
    const url = controls.url.value.trim();
    if (!url) {
        addLog('Vui lòng nhập URL Google Sheet.');
        return;
    }

    sourceBusy = true; const revision = ++sourceRevision; syncScanControls();
    const btn = controls.sync;
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="material-icons-outlined">hourglass_top</span> Đang đồng bộ...';
    setGooglePushState();

    try {
        const res = await fetch('/sync-google-sheet', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, platform, activate: false })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Không đồng bộ được Google Sheet');

        if (revision !== sourceRevision) return;
        const source = platformSources[platform];
        Object.assign(source, { fileId: data.file, label: data.label || data.file, displaySheet: data.currentSheet || '', scanSheet: data.scanSheet || data.currentSheet || '', pushSheet: data.scanSheet || data.currentSheet || '', sheets: data.sheets || [], url, dirty: false });
        if (platform === activePlatform) {
            await activateSource(platform);
            restoreActiveSource();
            await updateFileList();
            await loadPreview();
        }
        persistSources(); renderSourceRows();
        notify(`Đã nạp Google Sheet: ${data.label || data.file || ''}`, 'success');
    } catch (error) {
        notify(`Lỗi đồng bộ Google Sheet: ${error.message}`, 'error');
    } finally {
        btn.innerHTML = originalHtml;
        sourceBusy = false;
        syncScanControls();
    }
}

async function uploadFile(input) {
    if (sourceBusy || scanIsBusy() || threadsCookieBusy || desktopUpdateInstalling) return;
    const platform = activePlatform;
    const file = input.files[0];
    if (!file) return;
    sourceBusy = true; ++sourceRevision; syncScanControls();
    addLog(`Đang tải lên: ${file.name}...`);
    setGooglePushState();
    const formData = new FormData();
    formData.append('file', file);
    formData.append('platform', platform);
    formData.append('activate', 'false');
    try {
        const res = await fetch('/upload-excel', { method: 'POST', body: formData });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Tải file thất bại');
        const displaySheet = data.sheet || '';
        const scanSheet = data.scanSheet || displaySheet;
        Object.assign(platformSources[platform], { fileId: data.filename, displaySheet, scanSheet, pushSheet: scanSheet, sheets: data.sheets || [], url: '', dirty: false });
        await activateSource(platform);
        restoreActiveSource();
        await updateFileList({ applyGoogleSheetUrl: true });
        await loadPreview();
        notify(`Tải lên thành công: ${file.name}`, 'success');
    } catch (error) {
        notify(`Lỗi tải file: ${error.message}`, 'error');
    }
    input.value = '';
    sourceBusy = false; syncScanControls();
}

async function uploadGoogleOauthClient(input) {
    const file = input.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    try {
        const res = await fetch('/google-oauth-client', { method: 'POST', body: formData });
        const data = await res.json();
        if (!res.ok || !data.success) throw new Error(data.error || 'Không nạp được OAuth client');
        addLog('Đã cài cấu hình Google cho ứng dụng. Từ giờ trên máy này chỉ cần bấm "Đăng nhập Google".');
        await refreshGoogleOauthStatus();
    } catch (error) {
        addLog(`Lỗi OAuth client: ${error.message}`);
    }
    input.value = '';
}

async function connectGoogleOAuth() {
    const btn = document.getElementById('googleLoginBtn');
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = 'Đang mở đăng nhập...';
    try {
        const res = await fetch('/google-oauth-login', { method: 'POST' });
        const data = await res.json();
        if (!res.ok || !data.success) throw new Error(data.error || 'Đăng nhập Google thất bại');
        notify('Đăng nhập Google thành công.', 'success');
        await refreshGoogleOauthStatus();
        await updateFileList();
    } catch (error) {
        notify(`Lỗi đăng nhập Google: ${error.message}`, 'error');
    } finally {
        btn.innerHTML = originalHtml;
        await refreshGoogleOauthStatus();
        await updateFileList();
    }
}

async function pushCurrentSheetToGoogle(event, platform = activePlatform) {
    if (sourceBusy || scanIsBusy() || threadsCookieBusy || desktopUpdateInstalling) return;
    if (platform === activePlatform) saveActiveSource();
    const source = platformSources[platform], controls = sourceControls(platform);
    const fileId = source.fileId, sourceSheet = controls.sheet.value || source.pushSheet;
    if (!fileId) { notify('Chưa chọn file cho nền tảng này.', 'warn'); return; }
    const button = event?.currentTarget || controls.push;
    if (button && button.disabled) {
        addLog('Nút Tạo sheet cần đăng nhập Google, URL sheet đích, file local và sheet nguồn.');
        return;
    }
    const url = controls.url.value.trim();
    if (!url) {
        addLog('Vui lòng nhập URL Google Sheet đích trước khi tạo sheet.');
        return;
    }
    source.pushSheet = sourceSheet;
    if (!sourceSheet) {
        addLog('Vui lòng chọn sheet nguồn trước khi tạo sheet Google.');
        return;
    }
    sourceBusy = true; syncScanControls();
    const originalHtml = button ? button.innerHTML : '';
    if (button) {
        button.disabled = true;
        button.innerHTML = '<span class="material-icons-outlined">hourglass_top</span> Đang tạo...';
    }
    try {
        const res = await fetch('/push-google-sheet', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, sourceSheet, platform, file_id: fileId })
        });
        const data = await res.json();
        if (!res.ok || !data.success) throw new Error(data.error || 'Không tạo được sheet trên Google');
        addLog(`Đã tạo sheet Google mới từ ${data.sourceSheet || sourceSheet}: ${data.sheetTitle}`);
    } catch (error) {
        addLog(`Lỗi tạo sheet Google: ${error.message}`);
    } finally {
        sourceBusy = false;
        if (button) button.innerHTML = originalHtml;
        syncScanControls();
    }
}

async function downloadCurrentWorkbook() {
    if (!currentFileId || sourceBusy) return;
    const link = document.createElement('a');
    link.href = `/download-excel?${sourceQuery()}`;
    link.click();
}

function renderSheetTabs(sheets, currentSheet) {
    const tabs = document.getElementById('sheetTabs');
    tabs.innerHTML = '';
    const visibleSheets = platformConfig().hidesGeneratedSheetTabs ? filterDataSheets(sheets) : sheets;
    if (!visibleSheets || visibleSheets.length <= 1) return;

    visibleSheets.forEach(sheet => {
        const button = document.createElement('button');
        button.className = `sheet-tab${sheet === currentSheet ? ' active' : ''}`;
        button.textContent = sheet;
        button.onclick = () => switchSheet(sheet);
        tabs.appendChild(button);
    });
}

async function switchSheet(sheetName) {
    if (sourceBusy || scanIsBusy()) return;
    currentSheetName = sheetName;
    saveActiveSource();
    await loadPreview(sheetName);
}

function matchesPlatformLink(value, platform = activePlatform) {
    const text = String(value || '').trim();
    if (!text || /[\x00-\x20\x7f\\]/.test(text)) return false;
    try {
        const url = new URL(text.startsWith('//') ? `https:${text}` : /^[a-z][a-z0-9+.-]*:/i.test(text) ? text : `https://${text}`);
        if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.port) return false;
        const config = platformConfig(platform);
        return config.linkHosts.includes(url.hostname) && config.linkPath.test(url.pathname);
    } catch { return false; }
}

const PREVIEW_LINK_COLUMNS = ['link', 'link air', 'url'];
const PREVIEW_SAVED_COLUMN = 'LƯỢT LƯU';
const PREVIEW_SHARES_COLUMN = 'CHIA SẺ';

// Workbooks share the TikTok layout; a platform whose fourth metric differs shows its
// own column (e.g. Threads REPOST) in place of LƯỢT LƯU, just before CHIA SẺ.
function previewDisplayColumns(columns, platform = activePlatform) {
    const metricColumn = platformConfig(platform).savedMetric.column;
    if (metricColumn === PREVIEW_SAVED_COLUMN || !columns.includes(PREVIEW_SAVED_COLUMN)) return columns;
    return columns.filter(column => column !== PREVIEW_SAVED_COLUMN && column !== metricColumn).reduce((result, column) => {
        if (column === PREVIEW_SHARES_COLUMN) result.push(metricColumn);
        result.push(column);
        return result;
    }, []);
}

function platformPreview(data, platform = activePlatform) {
    const config = platformConfig(platform);
    const linkColumn = data.columns.find(column => PREVIEW_LINK_COLUMNS.includes(normalizeVietnameseKey(column)));
    const isPlatformLink = row => Boolean(linkColumn) && matchesPlatformLink(row[linkColumn], platform);
    const rows = (data.data || []).filter(row => !linkColumn || isPlatformLink(row)
        || (config.previewKeepsTotalRow && normalizeVietnameseKey(row[linkColumn]) === 'tong'));
    return { linkColumn, rows, columns: previewDisplayColumns(data.columns, platform) };
}

function renderPreviewMessage(message, color = '') {
    setPreviewTableVisible(true);
    document.getElementById('previewHeader').innerHTML = '';
    const style = `text-align:center; padding:40px${color ? `; color:${color}` : ''}`;
    document.getElementById('previewBody').innerHTML = `<tr><td colspan="10" style="${style}">${escapeHtml(message)}</td></tr>`;
}

function renderEmptySourcePreview() {
    ++readRequestSequence.preview;
    ++readRequestSequence.summary;
    setPreviewTableVisible(true);
    document.getElementById('summaryDashboard').innerHTML = '';
    document.getElementById('previewHeader').innerHTML = '';
    document.getElementById('previewBody').innerHTML = '<tr><td colspan="10" class="workspace-empty">Chưa chọn nguồn cho nền tảng này.</td></tr>';
    window.lastWorkbookSheets = [];
    for (const id of ['totalLinks', 'processedLinks', 'successLinks', 'hiddenCountBadge', 'failedCountBadge']) {
        document.getElementById(id).textContent = '0';
    }
    renderSheetTabs([], '');
}

function applyPreviewSource(data) {
    const source = activeSource();
    const sheets = data.sheets || [];
    source.displaySheet = data.currentSheet || '';
    syncCompactSourceSummary(data.fileLabel || data.file || '', source.displaySheet);
    renderSheetTabs(sheets, source.displaySheet);
    renderScanSheetOptions(sheets, source.scanSheet);
    source.sheets = sheets;
    source.label = data.fileLabel || source.fileId;
    saveActiveSource(); renderSourceRows();
    window.lastWorkbookSheets = sheets;
}

function previewRowHtml(row, columns) {
    const isSinglePartner = Boolean(row._singlePartner);
    const isVideoLink = Boolean(row._videoLink);
    const classes = [
        isSinglePartner ? 'single-partner-row' : '',
        isVideoLink ? 'video-link-row' : '',
    ].filter(Boolean).join(' ');
    const rowClass = classes ? ` class="${classes}"` : '';
    let linkColor = '#ff6b00';
    if (isVideoLink) linkColor = '#1d4ed8';
    else if (isSinglePartner) linkColor = '#9a3412';
    return `<tr${rowClass}>${columns.map(column => {
        let val = row[column] ?? '';
        if (typeof val === 'string' && val.startsWith('http')) {
            return `<td title="${escapeHtml(val)}"><a href="${escapeHtml(val)}" target="_blank" style="color: ${linkColor}; text-decoration: none;">${escapeHtml(val)}</a></td>`;
        }
        return `<td title="${escapeHtml(val)}">${escapeHtml(val)}</td>`;
    }).join('')}</tr>`;
}

function renderPreviewTable(preview) {
    const header = document.getElementById('previewHeader');
    const body = document.getElementById('previewBody');
    header.innerHTML = `<tr>${preview.columns.map(column => `<th>${escapeHtml(column)}</th>`).join('')}</tr>`;
    if (preview.rows.length === 0) {
        body.innerHTML = `<tr><td colspan="${preview.columns.length}" style="text-align:center; padding:40px; color:#9ca3af">Sheet này chưa có link ${escapeHtml(platformConfig().label)}.</td></tr>`;
        return;
    }
    body.innerHTML = preview.rows.map(row => previewRowHtml(row, preview.columns)).join('');
}

async function loadPreview(sheetName = '') {
    if (sourcesInitialized && !currentFileId) {
        renderEmptySourcePreview();
        return;
    }
    const isCurrent = beginReadRequest('preview', sheetName || currentSheetName);
    try {
        const query = sourceQuery({ sheet_name: sheetName || currentSheetName });
        const res = await fetch(`/preview-excel?${query}`);
        const data = await res.json();
        if (!isCurrent(data)) return;

        if (data.error || data.message) {
            document.getElementById('summaryDashboard').innerHTML = '';
            renderPreviewMessage(data.message || data.error);
            return;
        }

        applyPreviewSource(data);
        if (isSummarySheetName(currentSheetName)) {
            // Ask for the opened tab itself: a data sheet can have one summary tab per
            // platform, and the server reads that tab with the platform that owns it.
            await renderSummaryDashboard(currentSheetName);
            return;
        }

        setPreviewTableVisible(true);
        if (!data.columns || data.columns.length === 0) {
            renderPreviewMessage('Sheet này chưa có dữ liệu.', '#9ca3af');
            return;
        }

        const { linkColumn, rows: visibleRows, columns } = platformPreview(data);
        const matchingLink = value => matchesPlatformLink(value);
        // While a scan runs, the badge shows the run's own total from progress updates.
        if (!scanIsBusy()) {
            document.getElementById('totalLinks').textContent = visibleRows.filter(row => linkColumn && matchingLink(row[linkColumn])).length;
        }
        renderPreviewTable({ rows: visibleRows, columns });
    } catch (error) {
        if (!isCurrent()) return;
        console.error(error);
        addLog(`Lỗi preview: ${error.message}`);
    } finally {
        syncScanControls();
    }
}

async function renderSummaryDashboard(dataSheetName = '') {
    if (sourcesInitialized && !currentFileId) return;
    const isCurrent = beginReadRequest('summary', currentSheetName);
    const dashboard = document.getElementById('summaryDashboard');
    const header = document.getElementById('previewHeader');
    const body = document.getElementById('previewBody');
    setPreviewTableVisible(false);
    header.innerHTML = '';
    body.innerHTML = '';
    dashboard.innerHTML = '<div class="summary-empty">Đang tải tổng kết đối tác...</div>';

    const resolvedDataSheet = dataSheetName
        || dataSheetNameForSummaryTab(currentSheetName, window.lastWorkbookSheets || [])
        || currentScanSheetName
        || filterDataSheets(window.lastWorkbookSheets || [])[0]
        || '';
    const query = `?${sourceQuery({ sheet_name: resolvedDataSheet || '' })}`;

    try {
        const res = await fetch(`/summary-dashboard${query}`);
        const data = await res.json();
        if (!isCurrent(data)) return;
        if (!res.ok) throw new Error(data.error || 'Không đọc được sheet Tổng kết');

        const rows = data.rows || [];
        const columns = data.columns && data.columns.length
            ? data.columns
            : ['Stt', 'ĐỐI TÁC', 'TỔNG LINK', 'TỔNG LƯỢT XEM', 'TỔNG TIM', 'TỔNG BÌNH LUẬN', 'TỔNG LƯỢT LƯU', 'TỔNG CHIA SẺ', 'Cập nhật lần cuối'];
        const totals = data.totals || {};

        if (rows.length === 0) {
            dashboard.innerHTML = `
                <div class="summary-head">
                    <div class="summary-title">${summaryDashboardTitle(data, totals)}</div>
                </div>
                <div class="summary-empty">Chưa có đối tác nào. Quét sheet dữ liệu để tạo tổng kết.</div>
            `;
            return;
        }

        const tableRows = rows.map(row => `
            <tr>
                ${columns.map(column => renderSummaryTableCell(column, row[column] ?? '')).join('')}
            </tr>
        `).join('');

        dashboard.innerHTML = `
            <div class="summary-head">
                <div class="summary-title">${summaryDashboardTitle(data, totals)}</div>
            </div>
            <div class="summary-table-wrap">
                <table class="summary-table">
                    <thead><tr>${columns.map(column => `<th>${escapeHtml(column)}</th>`).join('')}</tr></thead>
                    <tbody>${tableRows}</tbody>
                </table>
            </div>
        `;
    } catch (error) {
        if (!isCurrent()) return;
        dashboard.innerHTML = `<div class="summary-empty">${escapeHtml(error.message)}</div>`;
        addLog(`Lỗi tổng kết: ${error.message}`);
    }
}

function scanIsBusy() {
    return ['starting', 'running', 'saving', 'cancelling'].includes(scanPhase);
}

function syncScanControls() {
    scheduleSourcePreferencesSave();
    const busy = scanIsBusy() || desktopUpdateInstalling || threadsCookieBusy || sourceBusy;
    for (const id of ['threadsCookieBtn', 'threadsCookieImport', 'threadsCookieVerify', 'threadsCookieDelete', 'threadsCookieDeleteAccept', 'threadsCookieDeleteCancel', 'threadsCookieTabFile', 'threadsCookieTabPaste', 'threadsSessionUse', 'threadsCookieFile', 'threadsCookieText']) {
        const input = document.getElementById(id);
        if (input) input.disabled = busy || (['threadsSessionUse', 'threadsCookieVerify', 'threadsCookieDelete'].includes(id) && !threadsCookieState.configured);
    }
    document.querySelectorAll('.threads-cookie-close').forEach(button => { button.disabled = threadsCookieBusy; });
    startBtn.disabled = busy;
    startBtn.innerHTML = busy ? BTN_START_BUSY : BTN_START_IDLE;
    cancelBtn.disabled = !scanIsBusy() || ['saving', 'cancelling'].includes(scanPhase);
    cancelBtn.innerHTML = scanPhase === 'cancelling' ? BTN_CANCEL_BUSY : BTN_CANCEL_IDLE;
    for (const input of [workerCountSelect, scrapeModeSelect, scanSheetSelect, proxyUseCheckbox, proxyConfigBtn]) {
        if (input) input.disabled = busy;
    }
    for (const id of ['excelFileSelect', 'fileInput']) {
        const input = document.getElementById(id);
        if (input) input.disabled = busy;
    }
    // Push buttons are owned by setGooglePushState below.
    for (const platform of PLATFORM_KEYS) {
        const { url, sync, sheet } = sourceControls(platform);
        for (const input of [url, sync, sheet]) if (input) input.disabled = busy;
    }
    document.querySelectorAll('.platform-option').forEach(button => { button.disabled = busy; });
    document.getElementById('refreshPartnerBtn').disabled = busy || selectedPartners.size === 0;
    setGooglePushState();
}

function applyRunContext(context) {
    if (!context?.runId) return;
    const changedRun = currentRunContext?.runId !== context.runId;
    currentRunContext = Object.fromEntries(
        ['runId', 'platform', 'fileId', 'sheetName'].map(key => [key, context[key] ?? currentRunContext?.[key]])
    );
    if (changedRun) {
        clearLiveResults();
        lastTerminalStatus = '';
    }
    showRunSource(context);
}

// Follow the workbook the server is scanning. Every change goes through that platform's
// own source slot, and a platform/file change reloads the label, sheet list and preview
// so the table never shows one workbook under another platform's headers.
function showRunSource({ platform, fileId, sheetName }) {
    const runPlatform = isPlatform(platform) ? platform : activePlatform;
    const slot = platformSources[runPlatform];
    const platformChanged = runPlatform !== activePlatform;
    const fileChanged = Boolean(fileId) && slot.fileId !== fileId;
    if (platformChanged) {
        saveActiveSource();
        applyPlatformUI(runPlatform);
    }
    if (fileChanged) {
        Object.assign(slot, { fileId, label: fileId, sheets: sheetName ? [sheetName] : [], displaySheet: sheetName || '', pushSheet: sheetName || '' });
    }
    if (sheetName) slot.scanSheet = sheetName;
    if (platformChanged || fileChanged) {
        ++sourceRevision;
        restoreActiveSource();
        persistSources();
        void reloadActiveSource();
    } else if (sheetName) {
        scanSheetSelect.value = sheetName;
    }
}

async function reloadActiveSource() {
    await updateFileList();
    await loadPreview();
}

// A snapshot replays the server's last run whenever this page (re)connects. A run that
// had already finished was saved and loaded before this page saw it, so its terminal
// side effects (log line, reloads) only run for a run this page was tracking as active.
function handleSessionSnapshot(session) {
    websocketSessionReady = true;
    const trackedActiveRun = scanIsBusy() && Boolean(session.runId) && currentRunContext?.runId === session.runId;
    scanPhase = session.running ? (session.status?.phase || 'running') : 'idle';
    if (session.running || session.platform === activePlatform && session.fileId === currentFileId && currentFileId) {
        applyRunContext(session);
        liveResultClasses.clear(); lastProgressData = null;
        document.getElementById('dataFeed').innerHTML = '';
        clearFailedLinks(false);
        for (const row of session.results || []) appendData(row, session);
        if (session.status?.phase || session.status?.done) {
            updateProgress(session.status, { replay: !session.running && !trackedActiveRun });
        }
    }
    if (session.running) {
        scanPhase = session.status?.phase || 'running';
        setWorkspaceTab('live');
    }
    syncScanControls();
}

function connectWS() {
    if (wsConnectionAbandoned) return;
    websocketSessionReady = false;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
    ws.onmessage = handleSocketMessage;
    ws.onopen = () => {
        wsReconnectFailures = 0;
        wsSessionProbed = false;
        addLog('Hệ thống đã kết nối trực tiếp.');
    };
    ws.onclose = handleSocketClose;
}

function handleSocketMessage(event) {
    const message = JSON.parse(event.data);
    if (message.type === 'session') {
        handleSessionSnapshot(message.data || {});
        return;
    }
    if (message.data?.done && message.platform && (message.platform !== activePlatform || message.fileId !== currentFileId)) return;
    if (message.runId && message.type !== 'log') applyRunContext(message);
    if (message.type === 'log') {
        addLog(message.message, { level: message.level || '', details: message.details || null });
    }
    else if (message.type === 'status') {
        updateProgress(message.data);
        // The cookie vault is Threads-only (not a per-platform setting); a Threads scan may mark it expired.
        if (message.data?.done && activePlatform === 'threads') void refreshThreadsCookieStatus();
    }
    else if (message.type === 'data') appendData(message.row, message);
    else if (message.type === 'duplicates') setDuplicateLinks(message.data);
}

function reconnectDelay(failures) {
    return Math.min(WS_RECONNECT_BASE_MS * 2 ** Math.max(failures - 1, 0), WS_RECONNECT_MAX_MS);
}

// After a server restart the old session cookie is rejected and /ws closes before the
// handshake forever. Back off, log the outage once, and after a few failures check once
// whether the page itself is still served; if not, ask for a reload instead of retrying.
function handleSocketClose() {
    websocketSessionReady = false;
    if (wsConnectionAbandoned) return;
    wsReconnectFailures += 1;
    if (wsReconnectFailures === 1) addLog('Mất kết nối. Đang tự động kết nối lại...');
    if (wsReconnectFailures >= WS_PROBE_AFTER_FAILURES && !wsSessionProbed) {
        wsSessionProbed = true;
        void probeLocalSession();
        return;
    }
    setTimeout(connectWS, reconnectDelay(wsReconnectFailures));
}

async function probeLocalSession() {
    let reachable = false;
    try {
        const response = await fetch('/', { cache: 'no-store', credentials: 'same-origin' });
        reachable = Boolean(response.ok);
    } catch {}
    if (reachable) {
        setTimeout(connectWS, reconnectDelay(wsReconnectFailures));
        return;
    }
    abandonConnection();
}

function abandonConnection() {
    wsConnectionAbandoned = true;
    websocketSessionReady = false;
    const banner = document.getElementById('connectionBanner');
    if (banner) banner.hidden = false;
    // The scan state is unknown now; do not keep every control locked behind it.
    scanPhase = 'idle';
    syncScanControls();
}

function formatCookieCheckedAt(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function renderThreadsCookieStatus(status) {
    const previousGeneration = threadsCookieState.generation;
    threadsCookieState = status || { configured: false, state: 'unknown', generation: '' };
    if (previousGeneration !== threadsCookieState.generation) {
        const use = document.getElementById('threadsSessionUse');
        if (use) use.checked = false;
    }
    const labels = { none: 'Chưa import', unchecked: 'Chưa kiểm tra', valid: 'Hợp lệ', expired: 'Hết hạn', invalid: 'Không hợp lệ', checkpoint: 'Cần xác minh', unknown: 'Chưa xác định', storage_unavailable: 'Kho bảo mật không khả dụng' };
    const text = labels[threadsCookieState.state] || 'Chưa xác định';
    const badge = document.getElementById('threadsCookieStatus');
    if (badge) { badge.textContent = text; badge.dataset.state = threadsCookieState.state; }
    const card = document.getElementById('threadsCookieCard');
    if (card) card.dataset.state = threadsCookieState.state;
    const savedBadge = document.getElementById('threadsCookieSavedBadge');
    if (savedBadge) { savedBadge.textContent = text; savedBadge.dataset.state = threadsCookieState.state; }
    const meta = document.getElementById('threadsCookieSavedMeta');
    if (meta) meta.textContent = threadsCookieState.configured
        ? `${threadsCookieState.count || 0} cookie${threadsCookieState.checkedAt ? ` • ${formatCookieCheckedAt(threadsCookieState.checkedAt)}` : ' • Chưa kiểm tra'}`
        : 'Chưa có cookie để sử dụng.';
    const savedMessage = document.getElementById('threadsCookieSavedMessage');
    if (savedMessage) {
        savedMessage.hidden = ['valid', 'none', 'unchecked'].includes(threadsCookieState.state);
        savedMessage.textContent = savedMessage.hidden ? '' : threadsCookieState.message || '';
    }
    if (badge) badge.title = threadsCookieState.message || text;
    const use = document.getElementById('threadsSessionUse');
    if (use && !threadsCookieState.configured) use.checked = false;
    if (!threadsCookieState.configured) cancelThreadsCookieDelete();
    syncScanControls();
}

async function refreshThreadsCookieStatus() {
    const revision = ++threadsCookieRevision;
    try {
        const response = await fetch('/threads-session/status', { cache: 'no-store' });
        const status = await response.json();
        if (revision === threadsCookieRevision && !threadsCookieBusy) renderThreadsCookieStatus(status);
    } catch {
        if (revision === threadsCookieRevision && !threadsCookieBusy) renderThreadsCookieStatus({ configured: false, state: 'unknown', message: 'Không tải được trạng thái cookie.' });
    }
}

function clearThreadsCookieInputs() {
    for (const id of ['threadsCookieText', 'threadsCookieFile']) {
        const input = document.getElementById(id);
        if (input) input.value = '';
    }
    const meta = document.getElementById('threadsCookieFileMeta');
    if (meta) meta.textContent = '';
}

function setThreadsCookieSource(source) {
    if (threadsCookieBusy || !['file', 'paste'].includes(source)) return;
    if (threadsCookieSource !== source) clearThreadsCookieInputs();
    threadsCookieSource = source;
    for (const [value, suffix] of [['file', 'File'], ['paste', 'Paste']]) {
        const tab = document.getElementById(`threadsCookieTab${suffix}`);
        const panel = document.getElementById(`threadsCookiePanel${suffix}`);
        if (tab) { tab.setAttribute('aria-selected', String(value === source)); tab.tabIndex = value === source ? 0 : -1; }
        if (panel) panel.hidden = value !== source;
    }
}

function cancelThreadsCookieDelete() {
    if (threadsCookieBusy) return;
    const confirm = document.getElementById('threadsCookieDeleteConfirm');
    if (confirm) confirm.hidden = true;
}

function openThreadsCookieModal() {
    if (scanIsBusy() || desktopUpdateInstalling || threadsCookieBusy || sourceBusy) return;
    threadsCookieFocus = document.activeElement;
    const modal = document.getElementById('threadsCookieModal');
    modal.classList.add('active');
    modal.setAttribute('aria-hidden', 'false');
    cancelThreadsCookieDelete();
    document.getElementById('threadsCookieModalStatus').textContent = '';
    setThreadsCookieSource(threadsCookieSource);
    document.getElementById(threadsCookieSource === 'file' ? 'threadsCookieFile' : 'threadsCookieText').focus();
    void refreshThreadsCookieStatus();
}

function closeThreadsCookieModal() {
    if (threadsCookieBusy) return;
    const modal = document.getElementById('threadsCookieModal');
    modal.classList.remove('active');
    modal.setAttribute('aria-hidden', 'true');
    clearThreadsCookieInputs();
    cancelThreadsCookieDelete();
    if (threadsCookieFocus?.focus) threadsCookieFocus.focus();
}

async function threadsCookieAction(action) {
    if (scanIsBusy() || desktopUpdateInstalling || threadsCookieBusy || sourceBusy) return;
    const source = threadsCookieSource;
    const file = document.getElementById('threadsCookieFile').files?.[0];
    let body = '';
    threadsCookieBusy = true;
    ++threadsCookieRevision;
    syncScanControls();
    document.querySelector('#threadsCookieModal [role="dialog"]')?.focus();
    const detail = document.getElementById('threadsCookieModalStatus');
    detail.textContent = action === 'import' ? 'Đang đọc và kiểm tra cookie...' : 'Đang xử lý phiên Threads...';
    try {
        if (action === 'import') {
            if (source === 'file') {
                if (!file) throw new Error('Chọn file JSON trước khi import.');
                if (file.size > 262144) throw new Error('File cookie quá lớn, tối đa 256 KiB.');
                body = await file.text();
            } else {
                body = document.getElementById('threadsCookieText').value;
            }
            if (!body.trim() || new TextEncoder().encode(body).length > 262144) throw new Error('Nhập JSON cookie hợp lệ, tối đa 256 KiB.');
        }
        const response = await fetch(action === 'delete' ? '/threads-session' : `/threads-session/${action}`, {
            method: action === 'delete' ? 'DELETE' : 'POST', headers: { 'Content-Type': 'application/json' },
            body: action === 'import' ? body : undefined, cache: 'no-store'
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Không xử lý được phiên Threads.');
        renderThreadsCookieStatus(data.status || data);
        detail.textContent = action === 'delete' ? 'Đã xóa phiên khỏi kho bảo mật.' : action === 'verify' ? 'Đã kiểm tra lại phiên.' : 'Đã import và lưu phiên an toàn.';
        if (data.check && data.check.state !== 'valid') detail.textContent = `${data.check.message || 'Cookie chưa xác minh hợp lệ.'}${action === 'import' ? ' Phiên đã lưu trước đó được giữ nguyên.' : ''}`;
    } catch (error) {
        detail.textContent = error.message || 'Không xử lý được phiên Threads.';
    } finally {
        body = '';
        clearThreadsCookieInputs();
        threadsCookieBusy = false;
        cancelThreadsCookieDelete();
        syncScanControls();
    }
}

function importThreadsCookie() { return threadsCookieAction('import'); }
function verifyThreadsCookie() { return threadsCookieAction('verify'); }
function deleteThreadsCookie() {
    if (threadsCookieBusy || sourceBusy || scanIsBusy() || desktopUpdateInstalling || !threadsCookieState.configured) return;
    document.getElementById('threadsCookieDeleteConfirm').hidden = false;
    document.getElementById('threadsCookieDeleteCancel').focus();
}
function confirmThreadsCookieDelete() {
    if (document.getElementById('threadsCookieDeleteConfirm').hidden) return;
    return threadsCookieAction('delete');
}

const SCRAPE_MODE_LABELS = Object.freeze({
    browser: 'trình duyệt',
    hybrid: 'Hybrid (Request + trình duyệt)',
    request: 'Request (HTTP)',
});

// Collects the scan settings, or explains (and returns null) when the scan cannot start.
function buildScanRequest(partners, sheetName) {
    const config = platformConfig();
    const selected = Array.isArray(partners) ? partners : (partners ? [partners] : []);
    const useProxy = Boolean(proxyUseCheckbox && proxyUseCheckbox.checked);
    const workers = Number(workerCountSelect.value || 10);
    const scrapeMode = scrapeModeSelect.value || 'request';
    const proxyText = useProxy ? currentProxyText() : '';
    const useThreadsSession = config.cookieSession && Boolean(document.getElementById('threadsSessionUse')?.checked);
    if (useThreadsSession && (!threadsCookieState.configured || !threadsCookieState.generation)) {
        notify('Import và kiểm tra cookie Threads trước khi dùng phiên.', 'warn');
        openThreadsCookieModal();
        return null;
    }
    if (useProxy && !proxyText.trim()) {
        notify('Bật proxy nhưng chưa có danh sách. Bấm Cấu hình để dán proxy.', 'warn');
        openProxyModal();
        return null;
    }
    const scanSheet = sheetName || scanSheetSelect.value || currentScanSheetName || currentSheetName;
    if (!scanSheet) {
        notify('Vui lòng chọn sheet để quét.', 'warn');
        return null;
    }
    currentScanSheetName = scanSheet;
    const modeLabel = SCRAPE_MODE_LABELS[scrapeMode] || SCRAPE_MODE_LABELS.request;
    const proxyLabel = useProxy ? ' • proxy bật' : '';
    return {
        // Never include proxy text or cookies in the log line.
        logLine: `Bắt đầu quét ${config.label} • sheet "${scanSheet}" • file: ${currentFileId || 'chưa rõ'} • ${modeLabel}${proxyLabel} • luồng: ${workers} • đối tác: ${selected.length ? selected.join(', ') : 'tất cả'}.`,
        payload: {
            action: 'start',
            file_id: currentFileId,
            platform: activePlatform,
            workers,
            scrape_mode: scrapeMode,
            use_proxy: useProxy,
            proxy_text: proxyText,
            ...(useThreadsSession ? { use_threads_session: true, threads_session_generation: threadsCookieState.generation } : {}),
            sheet_name: scanSheet,
            partners: selected,
            partner: selected.length === 1 ? selected[0] : '',
        },
    };
}

function startScraping(partners = [], sheetName = '') {
    if (scanIsBusy() || desktopUpdateInstalling || threadsCookieBusy || sourceBusy) return;
    if (sourcesInitialized && !currentFileId) { notify('Chưa chọn nguồn cho nền tảng này.', 'warn'); return; }
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        notify('Lỗi: Chưa kết nối được server. Vui lòng kiểm tra CMD.', 'error');
        return;
    }
    const request = buildScanRequest(partners, sheetName);
    if (!request) return;
    addLog(request.logLine);
    ws.send(JSON.stringify(request.payload));
    setWorkspaceTab('live');
    closeCompactDrawers();
    scanPhase = 'starting';
    lastTerminalStatus = '';
    syncScanControls();
    clearLiveResults();
    const statusEl = document.getElementById('progressStatus');
    statusEl.textContent = '';
    statusEl.className = 'progress-status';
}

function cancelScraping() {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        notify('Lỗi: Chưa kết nối được server. Vui lòng kiểm tra CMD.', 'error');
        return;
    }
    ws.send(JSON.stringify({ action: 'cancel' }));
    scanPhase = 'cancelling';
    syncScanControls();
    addLog('Đã gửi lệnh hủy quét.');
}

// Every platform's partner report uses the same min-views threshold (TikTok's rules).
function reportFilterParams() {
    const applyMinViews = document.getElementById('minViewToggle')?.checked ?? true;
    const minViewsRaw = parseInt(document.getElementById('minViewInput')?.value, 10);
    const minViews = Number.isFinite(minViewsRaw) && minViewsRaw >= 0 ? minViewsRaw : 100;
    return { applyMinViews, minViews };
}

function normalizeReportPartners(raw) {
    return (raw || []).map(item => {
        if (typeof item === 'string') return { name: item, linkCount: null, rawLinkCount: null };
        const linkCount = Number(item.linkCount);
        const rawLinkCount = Number(item.rawLinkCount);
        return {
            name: String(item.name || '').trim(),
            linkCount: Number.isFinite(linkCount) ? linkCount : 0,
            rawLinkCount: Number.isFinite(rawLinkCount) ? rawLinkCount : 0,
        };
    }).filter(item => item.name);
}

function partnerShowsNoLinkStatus(item) {
    if (item.linkCount !== null && item.linkCount !== undefined) {
        return item.linkCount <= 0;
    }
    if (item.rawLinkCount !== null && item.rawLinkCount !== undefined) {
        return item.rawLinkCount <= 0;
    }
    return false;
}

function partnerNamesFromSelection() {
    return reportPartners.filter(item => selectedPartners.has(item.name)).map(item => item.name);
}

async function refreshPartnerLinks() {
    const partners = partnerNamesFromSelection();
    if (partners.length === 0) {
        addLog('Hãy chọn ít nhất 1 đối tác để cập nhật lại link.');
        return;
    }
    const sheetName = reportSheetName || document.getElementById('reportSheetSelect')?.value || '';
    closeReportModal();
    startScraping(partners, sheetName);
}

function formatDuration(seconds) {
    if (seconds === null || seconds === undefined || seconds === '') return '';
    const sec = Math.max(parseInt(seconds, 10) || 0, 0);
    if (sec < 60) return `${sec}s`;
    const minutes = Math.floor(sec / 60);
    const remain = sec % 60;
    if (minutes < 60) return `${minutes}m ${remain}s`;
    const hours = Math.floor(minutes / 60);
    const remainMin = minutes % 60;
    return `${hours}h ${remainMin}m`;
}

function classifyLiveResult(row, platform) {
    const status = String(row.status || '');
    const confirmed = [row.views, row.likes, row.comments, row.saves, row.shares].some(value => value !== null && value !== undefined && value !== '');
    if (status === 'Success' || platformConfig(platform).partialWithMetricsIsSuccess && status.startsWith('Partial:') && confirmed) return 'success';
    return isNoStatsStatus(status) ? 'nostats' : 'error';
}

function progressPlatform(data = {}) {
    return data.platform || currentRunContext?.platform || activePlatform;
}

function progressResultCounts(data) {
    const platform = progressPlatform(data);
    const counts = { success: Number(data.success || 0), hidden: Number(data.hidden || 0), error: Number(data.error || 0) };
    // An older runner may count Partial as hidden while this UI correctly renders
    // those same rows OK. Reconcile only when the complete processed batch is known.
    if (platformConfig(platform).reconcileCountsFromRows && Number(data.processed || 0) > 0 && liveResultClasses.size === Number(data.processed)) {
        counts.success = counts.hidden = counts.error = 0;
        for (const kind of liveResultClasses.values()) counts[kind === 'nostats' ? 'hidden' : kind] += 1;
    }
    return counts;
}

const TERMINAL_SCAN_PHASES = ['completed', 'failed', 'cancelled'];

function renderProgressCounts(data, counts) {
    document.getElementById('totalLinks').textContent = data.total;
    document.getElementById('processedLinks').textContent = data.processed;
    document.getElementById('successLinks').textContent = counts.success;
    const hiddenBadge = document.getElementById('hiddenCountBadge');
    const failedBadge = document.getElementById('failedCountBadge');
    if (hiddenBadge) hiddenBadge.textContent = counts.hidden;
    if (failedBadge) failedBadge.textContent = counts.error;
}

function progressSummaryText(data, pct) {
    const parts = [];
    if (data.phase === 'starting') {
        parts.push(`Đang khởi tạo: 0/${data.total} (${Math.round(pct)}%)`);
    } else {
        parts.push(`Tiến độ: ${data.processed}/${data.total} (${Math.round(pct)}%)`);
    }
    if (data.mode === 'partner' && data.partner) parts.push(`đối tác ${data.partner}`);
    if (data.workers) parts.push(`${data.workers} luồng`);
    if (data.phase === 'starting') {
        parts.push(data.usesBrowser ? 'đang mở trình duyệt' : 'đang khởi tạo luồng Request');
    } else if (data.rate) {
        parts.push(`${data.rate} link/phút`);
    }
    if (data.etaSeconds !== null && data.etaSeconds !== undefined && !data.done) {
        parts.push(`còn ~${formatDuration(data.etaSeconds)}`);
    }
    return parts.join(' • ');
}

function progressStatusView(phase, counts, platform) {
    if (phase === 'failed') return ['Quét hoặc lưu thất bại', 'warn'];
    if (phase === 'cancelled') return ['Đã huỷ', 'cancelled'];
    if (phase === 'completed') {
        if (counts.hidden > 0 || counts.error > 0) return [platformConfig(platform).completedWithIssues(counts.error), 'warn'];
        return ['Thành công', 'success'];
    }
    if (phase === 'saving') return ['Đang lưu kết quả...', ''];
    return ['', ''];
}

function renderProgressStatus(phase, counts, platform) {
    const [text, tone] = progressStatusView(phase, counts, platform);
    const statusEl = document.getElementById('progressStatus');
    statusEl.textContent = text;
    statusEl.className = tone ? `progress-status ${tone}` : 'progress-status';
}

// Runs once per run and terminal phase. A replayed snapshot only marks the phase as seen.
function finishScanRun(phase, { replay = false } = {}) {
    syncProxyCardActiveState();
    const terminalKey = `${currentRunContext?.runId || ''}:${phase}`;
    if (lastTerminalStatus === terminalKey) return;
    lastTerminalStatus = terminalKey;
    if (replay) return;
    addLog(phase === 'cancelled' ? '--- ĐÃ HỦY QUÉT ---' : phase === 'failed' ? '--- QUÉT/LƯU THẤT BẠI ---' : '--- QUÉT VÀ LƯU HOÀN TẤT ---');
    updateFileList();
    loadPreview();
}

function updateProgress(data, { replay = false } = {}) {
    lastProgressData = data;
    const counts = progressResultCounts(data);
    renderProgressCounts(data, counts);
    const pct = data.total > 0 ? (data.processed / data.total) * 100 : 0;
    document.getElementById('progressBar').style.width = `${pct}%`;
    document.getElementById('progressText').textContent = progressSummaryText(data, pct);

    const phase = data.phase || (data.cancelled ? 'cancelled' : data.done ? 'failed' : 'running');
    scanPhase = phase;
    syncScanControls();
    renderProgressStatus(phase, counts, progressPlatform(data));
    if (TERMINAL_SCAN_PHASES.includes(phase)) finishScanRun(phase, { replay });
}

function appendData(row, context = {}) {
    const platform = row.platform || context.platform || currentRunContext?.platform || activePlatform;
    const tbody = document.getElementById('dataFeed');
    if (tbody.innerText.includes('Chưa có kết quả')) tbody.innerHTML = '';
    const wrapper = document.querySelector('.live-results-wrap');
    const resultStatus = classifyLiveResult(row, platform);
    const isSuccess = resultStatus === 'success';
    const missingMetricDetail = Array.isArray(row.missingMetrics) && row.missingMetrics.length
        ? ` • Nguồn chưa trả: ${row.missingMetrics.join(', ')}` : '';
    const statusDetail = `${row.status || ''}${missingMetricDetail}`;
    const noStats = resultStatus === 'nostats';
    liveResultClasses.set(String(row.id ?? row.url), resultStatus);
    const visibleInCurrentTab = activeWorkspaceTab === 'live'
        || (activeWorkspaceTab === 'success' && isSuccess);
    const preserveScroll = Boolean(wrapper && visibleInCurrentTab && wrapper.scrollTop > 32);
    const previousScrollHeight = wrapper?.scrollHeight || 0;
    const tr = document.createElement('tr');
    tr.dataset.resultStatus = resultStatus;
    if (row.videoLink) tr.classList.add('video-link-row');
    if (row.singlePartner) tr.classList.add('single-partner-row');
    tr.dataset.platform = platform;
    const views = formatResultNumber(row.views);
    const likes = formatResultNumber(row.likes);
    const comments = formatResultNumber(row.comments);
    const saves = formatResultNumber(row.saves);
    const shares = formatResultNumber(row.shares);
    const statusLabel = isSuccess ? 'OK' : (noStats ? (String(row.status || '').startsWith('Partial:') ? 'THIẾU SỐ' : 'KHÔNG SỐ LIỆU') : 'LỖI');
    const statusColor = isSuccess ? '#15803d' : (noStats ? '#b45309' : '#dc2626');

    tr.innerHTML = `
        <td data-label="ID">${escapeHtml(row.id)}</td>
        <td data-label="Sheet">${escapeHtml(row.sheetName || '')}</td>
        <td data-label="Kênh" title="${escapeHtml(row.channelName || '')}">${escapeHtml(row.channelName || '')}</td>
        <td data-label="Link" class="col-url" title="${escapeHtml(row.url)}"><a href="${escapeHtml(row.url)}" target="_blank" rel="noopener noreferrer" style="color: ${row.videoLink ? '#1d4ed8' : (row.singlePartner ? '#9a3412' : 'inherit')}; text-decoration: none;">${escapeHtml(row.url)}</a></td>
        <td data-label="Lượt xem" style="text-align:right; font-weight:bold">${escapeHtml(views)}</td>
        <td data-label="Tim" style="text-align:right; font-weight:bold">${escapeHtml(likes)}</td>
        <td data-label="Bình luận" style="text-align:right; font-weight:bold">${escapeHtml(comments)}</td>
        <td data-label="${escapeHtml(platformConfig(platform).savedMetric.label)}" style="text-align:right; font-weight:bold">${escapeHtml(saves)}</td>
        <td data-label="Chia sẻ" style="text-align:right; font-weight:bold">${escapeHtml(shares)}</td>
        <td data-label="Trạng thái"><span class="col-status" title="${escapeHtml(statusDetail)}" style="color:${statusColor}">${statusLabel}</span></td>
    `;
    tbody.prepend(tr);
    if (preserveScroll && wrapper) {
        wrapper.scrollTop += Math.max(wrapper.scrollHeight - previousScrollHeight, 0);
        pendingLiveResults += 1;
        updatePendingLiveResults();
    }
    if (!isSuccess) appendFailedLink(row);
    if (lastProgressData) {
        const counts = progressResultCounts(lastProgressData);
        document.getElementById('successLinks').textContent = counts.success;
        document.getElementById('hiddenCountBadge').textContent = counts.hidden;
        document.getElementById('failedCountBadge').textContent = counts.error;
    }
}

function desktopUpdaterInvoke() {
    return window.__TAURI__?.core?.invoke || null;
}

async function checkDesktopUpdate() {
    const invoke = desktopUpdaterInvoke();
    if (!invoke || desktopUpdateCheckInFlight || !websocketSessionReady || scanIsBusy()) return;
    desktopUpdateCheckInFlight = true;
    try {
        const version = await invoke('check_for_update');
        if (!version || !websocketSessionReady || scanIsBusy()) return;
        desktopUpdateInstalling = true;
        syncScanControls();
        showToast(`Đang cài đặt Riviu Reports ${version}...`, 'info');
        await invoke('install_update');
    } catch (error) {
        console.warn('Desktop updater check failed:', error);
        addLog(`Chưa cập nhật được ứng dụng: ${String(error)}`);
    } finally {
        desktopUpdateInstalling = false;
        desktopUpdateCheckInFlight = false;
        syncScanControls();
    }
}

function scheduleDesktopUpdates() {
    if (!desktopUpdaterInvoke()) return;
    void checkDesktopUpdate();
    window.setInterval(() => void checkDesktopUpdate(), 5 * 60 * 1000);
}

function groupDuplicateLocations(locations) {
    const grouped = new Map();
    (Array.isArray(locations) ? locations : []).forEach(location => {
        const sheetName = String(location?.sheetName || 'Không rõ sheet');
        const row = location?.row;
        if (!grouped.has(sheetName)) grouped.set(sheetName, []);
        grouped.get(sheetName).push(row);
    });
    return grouped;
}

function renderDuplicateLinks() {
    const tbody = document.getElementById('duplicateLinksBody');
    const badge = document.getElementById('duplicateCountBadge');
    const note = document.getElementById('duplicateNote');
    if (!tbody || !badge) return;

    badge.textContent = duplicateLinks.length;
    if (note) {
        note.textContent = duplicateRowCount > 0 ? `(${duplicateRowCount} dòng được gộp)` : '';
    }

    if (duplicateLinks.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="duplicate-empty">Chưa có link trùng</td></tr>';
        return;
    }

    tbody.innerHTML = duplicateLinks.map((item, index) => {
        const locations = Array.isArray(item.locations) ? item.locations : [];
        const locationHtml = Array.from(groupDuplicateLocations(locations).entries())
            .map(([sheetName, rows]) => `
                <div class="duplicate-location">
                    <span class="duplicate-sheet">${escapeHtml(sheetName)}</span>
                    <span>dòng ${rows.map(row => escapeHtml(String(row ?? ''))).join(', ')}</span>
                </div>
            `).join('');
        const occurrenceCount = locations.length;
        return `
            <tr>
                <td data-label="ID">${escapeHtml(String(item.id || index + 1))}</td>
                <td data-label="Link" class="col-url duplicate-url-cell" title="${escapeHtml(item.url || '')}">
                    <a href="${escapeHtml(item.url || '')}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.url || '')}</a>
                </td>
                <td data-label="Số lần" class="duplicate-occurrence">${occurrenceCount}</td>
                <td data-label="Sheet / số dòng" class="duplicate-locations">${locationHtml}</td>
            </tr>
        `;
    }).join('');
}

function setDuplicateLinks(data = {}) {
    duplicateLinks = Array.isArray(data?.items) ? data.items : [];
    const fallbackCount = duplicateLinks.reduce((total, item) => {
        const count = Array.isArray(item.locations) ? item.locations.length : 0;
        return total + Math.max(count - 1, 0);
    }, 0);
    const reportedCount = Number(data?.duplicateRowCount);
    duplicateRowCount = Number.isFinite(reportedCount) && reportedCount >= 0
        ? reportedCount
        : fallbackCount;
    renderDuplicateLinks();
}

async function copyDuplicateLinks() {
    if (duplicateLinks.length === 0) {
        notify('Không có link trùng để copy.', 'warn');
        return;
    }

    const text = duplicateLinks.map(item => {
        const locationText = Array.from(groupDuplicateLocations(item.locations).entries())
            .map(([sheetName, rows]) => `${sheetName}: dòng ${rows.join(', ')}`)
            .join(' • ');
        return `${item.url || ''}\n${locationText}`;
    }).join('\n\n');

    try {
        if (navigator.clipboard) {
            await navigator.clipboard.writeText(text);
        } else {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            textarea.remove();
        }
        notify(`Đã copy ${duplicateLinks.length} link trùng kèm số dòng.`, 'success');
    } catch (error) {
        notify(`Lỗi copy link: ${error.message}`, 'error');
    }
}

function clearDuplicateLinks(showLog = true) {
    duplicateLinks = [];
    duplicateRowCount = 0;
    renderDuplicateLinks();
    if (showLog) addLog('Đã xóa danh sách link trùng.');
}

function failedLinksForKind(kind = 'all') {
    if (kind === 'nostats') return failedLinks.filter(item => isNoStatsStatus(item.status));
    if (kind === 'errors') return failedLinks.filter(item => !isNoStatsStatus(item.status));
    return failedLinks;
}

function renderFailureRows(items, emptyMessage) {
    if (items.length === 0) {
        return `<tr><td colspan="5" class="workspace-empty">${escapeHtml(emptyMessage)}</td></tr>`;
    }

    return items.map(item => {
        const noStats = isNoStatsStatus(item.status);
        const reasonClass = noStats ? 'failed-reason soft-warn' : 'failed-reason';
        const config = platformConfig();
        const reasonText = noStats ? config.noStatsReason(item) : (item.status || 'Lỗi không xác định');
        const tag = noStats ? `<span class="reason-tag">${escapeHtml(config.noStatsTag)}</span>` : '';
        return `
        <tr>
            <td data-label="ID">${escapeHtml(item.id)}</td>
            <td data-label="Sheet">${escapeHtml(item.sheetName || '')}</td>
            <td data-label="Link" class="col-url" title="${escapeHtml(item.url)}"><a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer" style="color: inherit; text-decoration: none;">${escapeHtml(item.url)}</a></td>
            <td data-label="Lý do" class="${reasonClass}" title="${escapeHtml(item.status || '')}">${escapeHtml(reasonText)}${tag}</td>
            <td data-label="Luồng">${item.worker ? `Luồng ${escapeHtml(item.worker)}` : ''}</td>
        </tr>
    `;
    }).join('');
}

function renderFailedLinks() {
    const errorBody = document.getElementById('failedLinksBody');
    const noStatsBody = document.getElementById('noStatsLinksBody');
    const errorBadge = document.getElementById('failedCountBadge');
    const noStatsBadge = document.getElementById('hiddenCountBadge');
    const note = document.getElementById('failedNote');
    const noStatsItems = failedLinksForKind('nostats');
    const errorItems = failedLinksForKind('errors');

    if (errorBadge) errorBadge.textContent = errorItems.length;
    if (noStatsBadge) noStatsBadge.textContent = noStatsItems.length;
    if (errorBody) errorBody.innerHTML = renderFailureRows(errorItems, 'Chưa có link lỗi');
    if (noStatsBody) noStatsBody.innerHTML = renderFailureRows(noStatsItems, 'Chưa có link không đọc được số liệu');

    if (note) {
        const parts = [];
        if (errorItems.length > 0) parts.push(`${errorItems.length} lỗi quét`);
        if (noStatsItems.length > 0) parts.push(`${noStatsItems.length} không đọc được số liệu`);
        note.textContent = parts.length ? `(${parts.join(' • ')})` : '';
    }
}

function appendFailedLink(row) {
    failedLinks.unshift({
        id: row.id,
        sheetName: row.sheetName,
        url: row.url,
        status: row.status,
        worker: row.worker
    });
    renderFailedLinks();
}

async function copyFailedLinks(kind = 'all') {
    const items = failedLinksForKind(kind);
    const label = kind === 'nostats' ? 'link không số liệu' : (kind === 'errors' ? 'link lỗi' : 'link cần kiểm tra');
    if (items.length === 0) {
        notify(`Không có ${label} để copy.`, 'warn');
        return;
    }
    const text = items.map(item => item.url).join('\n');
    try {
        if (navigator.clipboard) {
            await navigator.clipboard.writeText(text);
        } else {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            textarea.remove();
        }
        notify(`Đã copy ${items.length} ${label}.`, 'success');
    } catch (error) {
        notify(`Lỗi copy link: ${error.message}`, 'error');
    }
}

function clearFailedLinks(showLog = true, kind = 'all') {
    if (typeof showLog === 'string') {
        kind = showLog;
        showLog = true;
    }
    if (kind === 'nostats') {
        failedLinks = failedLinks.filter(item => !isNoStatsStatus(item.status));
    } else if (kind === 'errors') {
        failedLinks = failedLinks.filter(item => isNoStatsStatus(item.status));
    } else {
        failedLinks = [];
    }
    renderFailedLinks();
    if (showLog) {
        const label = kind === 'nostats' ? 'không số liệu' : (kind === 'errors' ? 'lỗi quét' : 'cần kiểm tra');
        addLog(`Đã xóa danh sách link ${label}.`);
    }
}

async function openReportModal() {
    const modal = document.getElementById('reportModal');
    const list = document.getElementById('partnerList');
    closeCompactDrawers();
    modal.classList.add('active');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    document.getElementById('partnerSearch').value = '';
    reportPartners = [];
    reportSheetName = '';
    const sheetSelect = document.getElementById('reportSheetSelect');
    sheetSelect.innerHTML = '';
    sheetSelect.disabled = true;
    selectedPartners = new Set();
    list.innerHTML = '<div class="empty-state">Đang tải danh sách đối tác...</div>';
    updateReportSummary();
    await loadReportPartners(defaultReportSheet());
}

// Open the report on the sheet the user is scanning/viewing in this source, never on a
// sheet remembered from another workbook; '' lets the server pick its first data sheet.
function defaultReportSheet() {
    const source = activeSource();
    const known = source.sheets || [];
    return filterDataSheets([source.scanSheet, source.displaySheet])
        .find(sheet => !known.length || known.includes(sheet)) || '';
}

function resolveReportSheets(data = {}) {
    if (Array.isArray(data.sheets) && data.sheets.length) {
        return filterDataSheets(data.sheets);
    }
    if (Array.isArray(data.allSheets) && data.allSheets.length) {
        return filterDataSheets(data.allSheets);
    }
    return sheetsFromScanSelect();
}

function sheetsFromScanSelect() {
    return Array.from(scanSheetSelect.options).map(option => option.value).filter(Boolean);
}

function renderReportSheetOptions(sheets, selectedSheet = '') {
    const select = document.getElementById('reportSheetSelect');
    if (!select) return;
    const validSheets = filterDataSheets(sheets);
    reportSheetUpdating = true;
    select.innerHTML = '';
    if (validSheets.length === 0) {
        select.innerHTML = '<option value="">Không có sheet</option>';
        reportSheetName = '';
        select.disabled = true;
        reportSheetUpdating = false;
        return;
    }
    const currentValue = selectedSheet && validSheets.includes(selectedSheet)
        ? selectedSheet
        : validSheets[0];
    validSheets.forEach(sheet => {
        const opt = document.createElement('option');
        opt.value = sheet;
        opt.textContent = sheet;
        if (sheet === currentValue) opt.selected = true;
        select.appendChild(opt);
    });
    reportSheetName = select.value;
    select.disabled = false;
    reportSheetUpdating = false;
}

async function loadReportPartners(sheetName = '') {
    if (sourcesInitialized && !currentFileId) return;
    const isCurrent = beginReadRequest('partners');
    const list = document.getElementById('partnerList');
    const requestedSheet = sheetName || reportSheetName || document.getElementById('reportSheetSelect')?.value || '';
    const { applyMinViews, minViews } = reportFilterParams();
    const params = new URLSearchParams();
    if (requestedSheet) params.set('sheet_name', requestedSheet);
    params.set('apply_min_views', applyMinViews ? 'true' : 'false');
    params.set('min_views', String(minViews));
    params.set('platform', activePlatform);
    params.set('file_id', currentFileId);
    const query = params.toString() ? `?${params.toString()}` : '';
    list.innerHTML = '<div class="empty-state">Đang tải danh sách đối tác...</div>';
    selectedPartners = new Set();
    updateReportSummary();
    try {
        const res = await fetch(`/report-partners${query}`);
        const data = await res.json();
        if (!isCurrent(data)) return;
        if (!res.ok) throw new Error(data.error || 'Không tải được danh sách đối tác');
        const sheetList = resolveReportSheets(data);
        const activeSheet = data.currentSheet || data.dataSheet || requestedSheet || sheetList[0] || '';
        renderReportSheetOptions(sheetList, activeSheet);
        reportPartners = normalizeReportPartners(data.partners);
        reportSheetName = activeSheet || reportSheetName;
        document.getElementById('reportModalSubtitle').textContent = `${data.fileLabel || data.file} • ${reportSheetName || '—'} • ${reportPartners.length} đối tác`;
        renderPartnerList();
    } catch (error) {
        if (!isCurrent()) return;
        const fallbackSheets = sheetsFromScanSelect();
        if (fallbackSheets.length) {
            renderReportSheetOptions(fallbackSheets, requestedSheet || fallbackSheets[0]);
            reportSheetName = document.getElementById('reportSheetSelect')?.value || reportSheetName;
        }
        list.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
        addLog(`Lỗi: ${error.message}`);
    }
}

function closeReportModal() {
    ++readRequestSequence.partners;
    const modal = document.getElementById('reportModal');
    modal.classList.remove('active');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
}

async function openHistoryModal() {
    const modal = document.getElementById('historyModal');
    closeCompactDrawers();
    modal.classList.add('active');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    await loadScrapeHistory();
}

function closeHistoryModal() {
    const modal = document.getElementById('historyModal');
    modal.classList.remove('active');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
}

async function loadScrapeHistory() {
    const body = document.getElementById('historyBody');
    const subtitle = document.getElementById('historyModalSubtitle');
    body.innerHTML = '<tr><td colspan="11" style="text-align:center; padding:32px; color:#9ca3af">Đang tải lịch sử...</td></tr>';
    try {
        const res = await fetch('/scrape-history?limit=50');
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Không tải được lịch sử');
        const history = Array.isArray(data.history) ? data.history : [];
        if (history.length === 0) {
            body.innerHTML = '<tr><td colspan="11" style="text-align:center; padding:32px; color:#9ca3af">Chưa có phiên quét nào.</td></tr>';
            subtitle.textContent = 'Chưa có phiên quét nào.';
            return;
        }
        subtitle.textContent = `${history.length} phiên gần nhất`;
        body.innerHTML = history.map(entry => {
            const linksScanned = entry.scrapedUrls ?? entry.scrapedRows ?? entry.totalLinks ?? 0;
            const rowsScanned = entry.scrapedRows ?? linksScanned;
            return `
                <tr>
                    <td>${escapeHtml(entry.timestamp || '')}</td>
                    <td>${escapeHtml(entry.fileLabel || '')}</td>
                    <td>${escapeHtml(entry.scanSheet || '—')}</td>
                    <td style="text-align:right">${formatNumber(linksScanned)}</td>
                    <td style="text-align:right">${formatNumber(rowsScanned)}</td>
                    <td style="text-align:right">${formatNumber(entry.totalViews || 0)}</td>
                    <td style="text-align:right">${formatNumber(entry.totalLikes || 0)}</td>
                    <td style="text-align:right">${formatNumber(entry.totalComments || 0)}</td>
                    <td style="text-align:right">${formatNumber(entry.totalSaves || 0)}</td>
                    <td style="text-align:right">${formatNumber(entry.totalShares || 0)}</td>
                    <td style="text-align:right">${escapeHtml(formatDuration(entry.durationSeconds || 0))}</td>
                </tr>
            `;
        }).join('');
    } catch (error) {
        body.innerHTML = `<tr><td colspan="11" style="text-align:center; padding:32px; color:#dc2626">${escapeHtml(error.message)}</td></tr>`;
        subtitle.textContent = 'Không tải được lịch sử.';
    }
}

function renderPartnerList() {
    const list = document.getElementById('partnerList');
    const searchValue = document.getElementById('partnerSearch').value.trim().toLowerCase();
    const visiblePartners = reportPartners
        .map((partner, index) => ({ ...partner, index }))
        .filter(item => String(item.name || '').toLowerCase().includes(searchValue));

    if (visiblePartners.length === 0) {
        list.innerHTML = '<div class="empty-state">Không tìm thấy đối tác phù hợp.</div>';
        renderSelectedPartnerList();
        updateReportSummary();
        return;
    }

    list.innerHTML = visiblePartners.map(item => `
        <label class="partner-item">
            <input class="partner-checkbox" type="checkbox" data-index="${item.index}" ${selectedPartners.has(item.name) ? 'checked' : ''}>
            <span class="partner-name">${escapeHtml(item.name)}</span>
            ${partnerShowsNoLinkStatus(item) ? '<span class="partner-link-status">Chưa có link</span>' : ''}
        </label>
    `).join('');
    renderSelectedPartnerList();
    updateReportSummary();
}

function renderSelectedPartnerList() {
    const list = document.getElementById('selectedPartnerList');
    const countEl = document.getElementById('selectedPartnerCount');
    const clearBtn = document.getElementById('clearSelectedPartnersBtn');
    if (!list) return;

    const selected = reportPartners.filter(item => selectedPartners.has(item.name));
    if (countEl) countEl.textContent = String(selected.length);
    if (clearBtn) clearBtn.disabled = selected.length === 0;

    if (selected.length === 0) {
        list.innerHTML = '<div class="empty-state">Chưa chọn đối tác nào.</div>';
        return;
    }

    list.innerHTML = selected.map(item => `
        <div class="selected-partner-item">
            <span class="partner-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
            <button type="button" class="selected-partner-remove" data-partner="${escapeHtml(item.name)}" aria-label="Bỏ chọn ${escapeHtml(item.name)}" title="Bỏ chọn">
                <span class="material-icons-outlined">close</span>
            </button>
        </div>
    `).join('');
}

function removeSelectedPartner(name) {
    const partnerName = String(name || '').trim();
    if (!partnerName || !selectedPartners.has(partnerName)) return;
    selectedPartners.delete(partnerName);
    renderPartnerList();
}

function selectAllPartners() {
    selectedPartners = new Set(reportPartners.map(item => item.name));
    renderPartnerList();
}

function clearAllPartners() {
    selectedPartners = new Set();
    renderPartnerList();
}

function updateReportSummary() {
    const count = selectedPartners.size;
    const total = reportPartners.length;
    const summary = document.getElementById('reportSummary');
    const exportBtn = document.getElementById('exportReportBtn');
    const refreshBtn = document.getElementById('refreshPartnerBtn');

    summary.textContent = count === 0
        ? 'Chưa chọn đối tác nào.'
        : `Đã chọn ${count}/${total} đối tác. Cập nhật lại và xuất báo cáo đều hỗ trợ một hoặc nhiều đối tác.`;
    exportBtn.disabled = count === 0;
    refreshBtn.disabled = count === 0 || scanIsBusy() || desktopUpdateInstalling;
    renderSelectedPartnerList();
}

function filenameFromDisposition(disposition, fallback) {
    const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match) return decodeURIComponent(utf8Match[1]);
    const plainMatch = disposition.match(/filename="?([^";]+)"?/i);
    return plainMatch ? plainMatch[1] : fallback;
}

function buildExportFallbackFilename(partners, sheetName) {
    const now = new Date();
    const pad = (value) => String(value).padStart(2, '0');
    const stamp = `${pad(now.getDate())}-${pad(now.getMonth() + 1)}-${now.getFullYear()}-${pad(now.getHours())}-${pad(now.getMinutes())}`;
    const sheet = String(sheetName || reportSheetName || '').trim();
    if (partners.length === 1) {
        const parts = [partners[0]];
        if (sheet) parts.push(sheet);
        parts.push(stamp);
        return `${parts.join(' ')}.xlsx`;
    }
    return sheet ? `bao_cao_doi_tac ${sheet} ${stamp}.zip` : `bao_cao_doi_tac ${stamp}.zip`;
}

function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

async function exportPartnerReport() {
    if (!currentFileId || sourceBusy) return;
    const partners = partnerNamesFromSelection();
    if (partners.length === 0) {
        addLog('Vui lòng chọn ít nhất một đối tác để xuất báo cáo.');
        return;
    }

    const btn = document.getElementById('exportReportBtn');
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="material-icons-outlined">hourglass_top</span> Đang xuất...';

    try {
        const { applyMinViews, minViews } = reportFilterParams();
        const res = await fetch('/export-report', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ partners, applyMinViews, minViews, sheetName: reportSheetName || document.getElementById('reportSheetSelect')?.value || '', platform: activePlatform, file_id: currentFileId })
        });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.error || 'Không xuất được báo cáo');
        }

        const blob = await res.blob();
        const sheetName = reportSheetName || document.getElementById('reportSheetSelect')?.value || '';
        const fallback = buildExportFallbackFilename(partners, sheetName);
        const filename = filenameFromDisposition(res.headers.get('Content-Disposition') || '', fallback);
        downloadBlob(blob, filename);
        notify(`Đã xuất báo cáo: ${filename}`, 'success');
        closeReportModal();
    } catch (error) {
        notify(`Lỗi xuất báo cáo: ${error.message}`, 'error');
    } finally {
        btn.disabled = selectedPartners.size === 0;
        btn.innerHTML = originalHtml;
        updateReportSummary();
    }
}

const area = document.getElementById('uploadArea');
area.addEventListener('dragover', (event) => { event.preventDefault(); area.classList.add('dragover'); });
area.addEventListener('dragleave', () => area.classList.remove('dragover'));
area.addEventListener('drop', (event) => {
    event.preventDefault();
    area.classList.remove('dragover');
    const file = event.dataTransfer.files[0];
    if (file) {
        const dt = new DataTransfer();
        dt.items.add(file);
        document.getElementById('fileInput').files = dt.files;
        uploadFile(document.getElementById('fileInput'));
    }
});

document.getElementById('minViewToggle').addEventListener('change', (event) => {
    const input = document.getElementById('minViewInput');
    input.disabled = !event.target.checked;
    input.style.opacity = event.target.checked ? '1' : '0.5';
    if (document.getElementById('reportModal').classList.contains('active')) {
        loadReportPartners();
    }
});

document.getElementById('minViewInput').addEventListener('change', () => {
    if (document.getElementById('reportModal').classList.contains('active')) {
        loadReportPartners();
    }
});

document.getElementById('reportSheetSelect').addEventListener('change', (event) => {
    if (reportSheetUpdating) return;
    const sheetName = event.target.value;
    if (!sheetName) return;
    loadReportPartners(sheetName);
});

document.getElementById('partnerList').addEventListener('change', (event) => {
    if (!event.target.classList.contains('partner-checkbox')) return;
    const partner = reportPartners[Number(event.target.dataset.index)];
    if (!partner) return;
    if (event.target.checked) selectedPartners.add(partner.name);
    else selectedPartners.delete(partner.name);
    updateReportSummary();
});

document.getElementById('selectedPartnerList').addEventListener('click', (event) => {
    const button = event.target.closest('.selected-partner-remove');
    if (!button) return;
    event.preventDefault();
    removeSelectedPartner(button.getAttribute('data-partner') || '');
});

document.getElementById('reportModal').addEventListener('click', (event) => {
    if (event.target.id === 'reportModal') closeReportModal();
});

document.getElementById('historyModal').addEventListener('click', (event) => {
    if (event.target.id === 'historyModal') closeHistoryModal();
});

document.getElementById('proxyModal').addEventListener('click', (event) => {
    if (event.target.id === 'proxyModal') closeProxyModal();
});

document.getElementById('threadsCookieModal')?.addEventListener('click', (event) => {
    if (event.target.id === 'threadsCookieModal') closeThreadsCookieModal();
});
document.getElementById('threadsCookieModal')?.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') return;
    const nodes = [...event.currentTarget.querySelectorAll('button:not(:disabled),input:not(:disabled),textarea:not(:disabled),summary')]
        .filter(node => node.tabIndex >= 0 && node.getClientRects().length && !node.closest('[hidden]'));
    if (!nodes.length) { event.preventDefault(); return; }
    if (event.shiftKey && (document.activeElement === nodes[0] || !nodes.includes(document.activeElement))) { event.preventDefault(); nodes[nodes.length - 1].focus(); }
    else if (!event.shiftKey && (document.activeElement === nodes[nodes.length - 1] || !nodes.includes(document.activeElement))) { event.preventDefault(); nodes[0].focus(); }
});
for (const [source, suffix] of [['file', 'File'], ['paste', 'Paste']]) {
    document.getElementById(`threadsCookieTab${suffix}`)?.addEventListener('keydown', event => {
        if (threadsCookieBusy || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 'file' : event.key === 'End' ? 'paste' : source === 'file' ? 'paste' : 'file';
        setThreadsCookieSource(next);
        document.getElementById(next === 'file' ? 'threadsCookieTabFile' : 'threadsCookieTabPaste').focus();
    });
}
document.getElementById('threadsCookieFile')?.addEventListener('change', event => {
    const file = event.target.files?.[0];
    document.getElementById('threadsCookieFileMeta').textContent = file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KiB` : '';
});

document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (document.getElementById('threadsCookieModal')?.classList.contains('active')) { closeThreadsCookieModal(); return; }
    if (document.getElementById('reportModal').classList.contains('active')) {
        closeReportModal();
    } else if (document.getElementById('historyModal').classList.contains('active')) {
        closeHistoryModal();
    } else if (document.getElementById('proxyModal').classList.contains('active')) {
        closeProxyModal();
    } else {
        closeCompactDrawers();
    }
});

// The platform bar is generated from PLATFORMS; source rows live in index.html.
renderPlatformBar();

scanSheetSelect.addEventListener('change', () => {
    currentScanSheetName = scanSheetSelect.value;
    syncCompactSourceSummary('', currentScanSheetName);
    saveActiveSource();
});

for (const platform of PLATFORM_KEYS) {
    const controls = sourceControls(platform);
    controls.sheet?.addEventListener('change', () => {
        const sheet = controls.sheet.value;
        Object.assign(platformSources[platform], { pushSheet: sheet, scanSheet: sheet, displaySheet: sheet });
        if (platform === activePlatform) {
            renderScanSheetOptions(platformSources[platform].sheets, sheet);
            void loadPreview(sheet);
        }
        persistSources(); setGooglePushState();
    });
    ['input', 'change'].forEach(eventName => controls.url?.addEventListener(eventName, () => {
        platformSources[platform].url = controls.url.value;
        platformSources[platform].dirty = true;
        persistSources(); setGooglePushState();
    }));
}

if (proxyUseCheckbox) {
    proxyUseCheckbox.addEventListener('change', syncProxyCardActiveState);
}

window.addEventListener('resize', syncCompactDrawerState);

window.onload = async () => {
    syncCompactDrawerState();
    setWorkspaceTab('sheet');
    await checkServerVersion();
    await loadSourcePreferences();
    await updateFileList({ applyGoogleSheetUrl: true });
    // Inactive platforms: load each saved workbook's label and sheets for its source row.
    for (const platform of PLATFORM_KEYS) {
        const source = platformSources[platform];
        if (platform === activePlatform || !source.fileId) continue;
        try {
            const { response, data } = await fetchFileList(platform, source.fileId);
            if (!response.ok) { source.fileId = ''; continue; }
            Object.assign(source, { sheets: data.sheets || [], label: data.currentLabel || data.current });
        } catch {}
    }
    renderSourceRows();
    await loadPreview();
    await refreshProxyStatus();
    await refreshThreadsCookieStatus();
    connectWS();
    scheduleDesktopUpdates();
};
