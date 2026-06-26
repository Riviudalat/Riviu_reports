let ws;
let reportPartners = [];
let reportSheetName = '';
let reportSheetUpdating = false;
let selectedPartners = new Set();
let failedLinks = [];
let currentSheetName = '';
let currentFileId = '';
let currentScanSheetName = '';
let currentPushSheetName = '';
let googleSheetUrlDirty = false;
let googlePushReady = false;
let scanCompletedForCurrentFile = false;
let googleOAuthAuthorized = false;
const startBtn = document.getElementById('startBtn');
const cancelBtn = document.getElementById('cancelBtn');
const workerCountSelect = document.getElementById('workerCountSelect');
const scrapeModeSelect = document.getElementById('scrapeModeSelect');
const proxyUseCheckbox = document.getElementById('proxyUseCheckbox');
const proxyUseLabel = document.getElementById('proxyUseLabel');
const scanSheetSelect = document.getElementById('scanSheetSelect');
const pushSheetSelect = document.getElementById('pushSheetSelect');
const googleSheetUrlInput = document.getElementById('googleSheetUrlInput');

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
    return String(status || '').includes('TikTok không trả số liệu');
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
    return normalizeVietnameseKey(value).startsWith('report seeding tiktok');
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
        pushSheetSelect,
        sheets,
        selectedSheet || currentPushSheetName || currentScanSheetName,
        value => { currentPushSheetName = value; setGooglePushState(); }
    );
}

function hasGoogleTargetUrl() {
    return Boolean(googleSheetUrlInput.value.trim());
}


function setGooglePushState() {
    const button = document.getElementById('pushGoogleBtn');
    if (!button) return;
    const enabled = Boolean(googleOAuthAuthorized && hasGoogleTargetUrl() && currentFileId && (pushSheetSelect.value || currentPushSheetName));
    button.disabled = !enabled;
    button.title = enabled
        ? ''
        : 'Chỉ bật sau khi đã có file local, URL sheet đích, đăng nhập Google và chọn sheet nguồn.';
}

async function refreshProxyStatus() {
    if (!proxyUseCheckbox || !proxyUseLabel) return;
    try {
        const res = await fetch('/proxy-status');
        const data = await res.json();
        if (data.configured && data.enabled) {
            proxyUseLabel.textContent = `Proxy xoay (${data.host}:${data.port || ''})`;
            proxyUseCheckbox.title = `HTTP proxy ${data.host}:${data.port || ''} — IP đổi theo từng kết nối`;
        } else if (data.configured) {
            proxyUseLabel.textContent = 'Proxy (tắt trong config)';
            proxyUseCheckbox.disabled = true;
            proxyUseCheckbox.title = 'Mở data/proxy_config.json và đặt enabled: true';
        } else {
            proxyUseLabel.textContent = 'Proxy xoay (chưa cấu hình)';
            proxyUseCheckbox.title = 'Tạo data/proxy_config.json từ proxy_config.example.json';
        }
    } catch (error) {
        proxyUseLabel.textContent = 'Proxy xoay';
    }
}

async function refreshGoogleOauthStatus() {
    try {
        const res = await fetch('/google-oauth-status');
        const data = await res.json();
        const loginBtn = document.getElementById('googleLoginBtn');
        const oauthBtn = document.getElementById('googleOauthBtn');
        googleOAuthAuthorized = Boolean(data.authorized);
        if (loginBtn) {
            const accountEmail = String(data.accountEmail || '').trim();
            loginBtn.disabled = !data.configured;
            const loginLabel = data.authorized ? 'Đã đăng nhập' : 'Chưa đăng nhập';
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
    return `<td class="number-cell">${formatNumber(value)}</td>`;
}

function setPreviewTableVisible(visible) {
    document.querySelector('#previewTable').closest('.table-wrap').style.display = visible ? '' : 'none';
    document.getElementById('summaryDashboard').classList.toggle('active', !visible);
}

function setGoogleSheetUrlField(url, { force = false } = {}) {
    const normalized = String(url || '').trim();
    if (!normalized) return;
    if (force || !googleSheetUrlDirty) {
        googleSheetUrlInput.value = normalized;
        if (force) googleSheetUrlDirty = false;
    }
}

function markGoogleSheetUrlDirty() {
    googleSheetUrlDirty = true;
    setGooglePushState();
}

async function updateFileList({ applyGoogleSheetUrl = false } = {}) {
    try {
        const res = await fetch('/list-files');
        const data = await res.json();
        const select = document.getElementById('excelFileSelect');
        select.innerHTML = '';

        if (applyGoogleSheetUrl) {
            setGoogleSheetUrlField(data.googleSheetUrl);
        }
        currentFileId = data.current || '';
        currentScanSheetName = data.scanSheet || data.currentSheet || currentScanSheetName;
        currentPushSheetName = currentPushSheetName || currentScanSheetName;
        renderScanSheetOptions(data.sheets || [], currentScanSheetName);
        renderPushSheetOptions(data.sheets || [], currentPushSheetName);
        googleOAuthAuthorized = Boolean(data.googleOAuthAuthorized);
        await refreshGoogleOauthStatus();
        setGooglePushState();

        if (!data.files || data.files.length === 0) {
            select.innerHTML = '<option value="">(Không có file nào)</option>';
            renderScanSheetOptions([], '');
            renderPushSheetOptions([], '');
            return;
        }

        data.files.forEach(file => {
            const opt = document.createElement('option');
            opt.value = file.id;
            opt.textContent = fileDisplayLabel(file);
            if (file.id === data.current) opt.selected = true;
            select.appendChild(opt);
        });

    } catch (error) {
        console.error(error);
        addLog(`Lỗi tải danh sách file: ${error.message}`);
    }
}

async function selectFile(fileId) {
    if (!fileId) return;
    try {
        scanCompletedForCurrentFile = false;
        currentSheetName = '';
        setGooglePushState();
        const res = await fetch('/select-file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: fileId })
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Không chọn được file');
        currentFileId = data.selected;
        currentSheetName = data.sheet || '';
        currentScanSheetName = data.scanSheet || currentSheetName;
        currentPushSheetName = currentScanSheetName;
        await updateFileList({ applyGoogleSheetUrl: !googleSheetUrlDirty });
        await loadPreview();
        addLog(`Đã chuyển sang sheet: ${fileDisplayLabel({ id: currentFileId, label: currentFileId })}`);
    } catch (error) {
        addLog(`Lỗi: ${error.message}`);
    }
}

async function syncGoogleSheet() {
    const url = googleSheetUrlInput.value.trim();
    if (!url) {
        addLog('Vui lòng nhập URL Google Sheet.');
        return;
    }

    const btn = document.getElementById('syncSheetBtn');
    const originalHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="material-icons-outlined">hourglass_top</span> Đang đồng bộ...';
    scanCompletedForCurrentFile = false;
    setGooglePushState();

    try {
        const res = await fetch('/sync-google-sheet', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Không đồng bộ được Google Sheet');

        currentFileId = data.file;
        currentSheetName = data.currentSheet || '';
        currentScanSheetName = data.scanSheet || currentSheetName;
        currentPushSheetName = currentScanSheetName;
        setGoogleSheetUrlField(url, { force: true });
        await updateFileList();
        await loadPreview();
        notify(`Đã nạp Google Sheet: ${data.label || data.file || ''}`, 'success');
    } catch (error) {
        notify(`Lỗi đồng bộ Google Sheet: ${error.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = originalHtml;
    }
}

async function uploadFile(input) {
    const file = input.files[0];
    if (!file) return;
    addLog(`Đang tải lên: ${file.name}...`);
    scanCompletedForCurrentFile = false;
    setGooglePushState();
    const formData = new FormData();
    formData.append('file', file);
    try {
        const res = await fetch('/upload-excel', { method: 'POST', body: formData });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Tải file thất bại');
        currentFileId = data.filename;
        currentSheetName = data.sheet || '';
        currentScanSheetName = data.scanSheet || currentSheetName;
        currentPushSheetName = currentScanSheetName;
        await updateFileList();
        await loadPreview();
        notify(`Tải lên thành công: ${file.name}`, 'success');
    } catch (error) {
        notify(`Lỗi tải file: ${error.message}`, 'error');
    }
    input.value = '';
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
    } catch (error) {
        notify(`Lỗi đăng nhập Google: ${error.message}`, 'error');
    } finally {
        btn.innerHTML = originalHtml;
        await refreshGoogleOauthStatus();
    }
}

async function pushCurrentSheetToGoogle(event) {
        const button = event?.currentTarget;
    if (button && button.disabled) {
        addLog('Nút Tạo sheet chỉ bật sau khi nạp đúng Google Sheet, đăng nhập Google và quét dữ liệu xong.');
        return;
    }
    const url = googleSheetUrlInput.value.trim();
    if (!url) {
        addLog('Vui lòng nhập URL Google Sheet đích trước khi tạo sheet.');
        return;
    }
    currentPushSheetName = pushSheetSelect.value || currentPushSheetName;
    if (!currentPushSheetName) {
        addLog('Vui lòng chọn sheet nguồn trước khi tạo sheet Google.');
        return;
    }
    const originalHtml = button ? button.innerHTML : '';
    if (button) {
        button.disabled = true;
        button.innerHTML = '<span class="material-icons-outlined">hourglass_top</span> Đang tạo...';
    }
    try {
        const res = await fetch('/push-google-sheet', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, sourceSheet: currentPushSheetName })
        });
        const data = await res.json();
        if (!res.ok || !data.success) throw new Error(data.error || 'Không tạo được sheet trên Google');
        addLog(`Đã tạo sheet Google mới từ ${data.sourceSheet || currentPushSheetName}: ${data.sheetTitle}`);
    } catch (error) {
        addLog(`Lỗi tạo sheet Google: ${error.message}`);
    } finally {
        if (button) {
            button.innerHTML = originalHtml;
            setGooglePushState();
        }
    }
}

async function downloadCurrentWorkbook() {
    const link = document.createElement('a');
    link.href = '/download-excel';
    link.click();
}

function renderSheetTabs(sheets, currentSheet) {
    const tabs = document.getElementById('sheetTabs');
    tabs.innerHTML = '';
    if (!sheets || sheets.length <= 1) return;

    sheets.forEach(sheet => {
        const button = document.createElement('button');
        button.className = `sheet-tab${sheet === currentSheet ? ' active' : ''}`;
        button.textContent = sheet;
        button.onclick = () => switchSheet(sheet);
        tabs.appendChild(button);
    });
}

async function switchSheet(sheetName) {
    currentSheetName = sheetName;
    await loadPreview(sheetName);
}

async function loadPreview(sheetName = '') {
    try {
        const query = sheetName || currentSheetName ? `?sheet_name=${encodeURIComponent(sheetName || currentSheetName)}` : '';
        const res = await fetch(`/preview-excel${query}`);
        const data = await res.json();
        const header = document.getElementById('previewHeader');
        const body = document.getElementById('previewBody');

        if (data.error || data.message) {
            body.innerHTML = `<tr><td colspan="10" style="text-align:center; padding:40px">${escapeHtml(data.message || data.error)}</td></tr>`;
            header.innerHTML = '';
            return;
        }

        currentSheetName = data.currentSheet || '';
        renderSheetTabs(data.sheets || [], currentSheetName);
        renderScanSheetOptions(data.sheets || [], currentScanSheetName);
        renderPushSheetOptions(data.sheets || [], currentPushSheetName);

        window.lastWorkbookSheets = data.sheets || [];
        if (isSummarySheetName(currentSheetName)) {
            await renderSummaryDashboard(
                dataSheetNameForSummaryTab(currentSheetName, data.sheets || [])
            );
            return;
        }

        setPreviewTableVisible(true);

        if (!data.columns || data.columns.length === 0) {
            header.innerHTML = '';
            body.innerHTML = '<tr><td colspan="10" style="text-align:center; padding:40px; color:#9ca3af">Sheet này chưa có dữ liệu.</td></tr>';
            return;
        }

        header.innerHTML = `<tr>${data.columns.map(column => `<th>${escapeHtml(column)}</th>`).join('')}</tr>`;
        if (!data.data || data.data.length === 0) {
            body.innerHTML = `<tr><td colspan="${data.columns.length}" style="text-align:center; padding:40px; color:#9ca3af">Sheet này chưa có dòng dữ liệu.</td></tr>`;
            return;
        }

        body.innerHTML = data.data.map(row => `<tr>${data.columns.map(column => {
            let val = row[column] || '';
            if (typeof val === 'string' && val.startsWith('http')) {
                return `<td title="${escapeHtml(val)}"><a href="${escapeHtml(val)}" target="_blank" style="color: #ff6b00; text-decoration: none;">${escapeHtml(val)}</a></td>`;
            }
            return `<td title="${escapeHtml(val)}">${escapeHtml(val)}</td>`;
        }).join('')}</tr>`).join('');
    } catch (error) {
        console.error(error);
        addLog(`Lỗi preview: ${error.message}`);
    }
}

async function renderSummaryDashboard(dataSheetName = '') {
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
    const query = resolvedDataSheet ? `?sheet_name=${encodeURIComponent(resolvedDataSheet)}` : '';

    try {
        const res = await fetch(`/summary-dashboard${query}`);
        const data = await res.json();
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
        dashboard.innerHTML = `<div class="summary-empty">${escapeHtml(error.message)}</div>`;
        addLog(`Lỗi tổng kết: ${error.message}`);
    }
}

function connectWS() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname === 'localhost' ? '127.0.0.1' : window.location.hostname;
    const port = window.location.port ? `:${window.location.port}` : '';
    ws = new WebSocket(`${protocol}//${host}${port}/ws`);

    ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        if (message.type === 'log') {
            addLog(message.message, { level: message.level || '', details: message.details || null });
        }
        else if (message.type === 'status') updateProgress(message.data);
        else if (message.type === 'data') appendData(message.row);
    };
    ws.onopen = () => addLog('Hệ thống đã kết nối trực tiếp.');
    ws.onclose = () => {
        addLog('Mất kết nối. Đang tự động kết nối lại...');
        setTimeout(connectWS, 2000);
    };
}

function startScraping(partners = []) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        notify('Lỗi: Chưa kết nối được server. Vui lòng kiểm tra CMD.', 'error');
        return;
    }
    scanCompletedForCurrentFile = false;
    setGooglePushState();
    const selected = Array.isArray(partners) ? partners : (partners ? [partners] : []);
    const workers = Number(workerCountSelect.value || 20);
    const scrapeMode = scrapeModeSelect.value === 'browser' ? 'browser' : 'request';
    const useProxy = Boolean(proxyUseCheckbox && proxyUseCheckbox.checked);
    currentScanSheetName = scanSheetSelect.value || currentScanSheetName || currentSheetName;
    if (!currentScanSheetName) {
        notify('Vui lòng chọn sheet để quét.', 'warn');
        return;
    }
    const modeLabel = scrapeMode === 'request' ? 'Request (HTTP)' : 'trình duyệt';
    const proxyLabel = useProxy ? ' • proxy bật' : '';
    addLog(`Bắt đầu quét sheet "${currentScanSheetName}" • file: ${currentFileId || 'chưa rõ'} • ${modeLabel}${proxyLabel} • luồng: ${workers} • đối tác: ${selected.length ? selected.join(', ') : 'tất cả'}.`);
    ws.send(JSON.stringify({
        action: 'start',
        workers,
        scrape_mode: scrapeMode,
        use_proxy: useProxy,
        sheet_name: currentScanSheetName,
        partners: selected,
        partner: selected.length === 1 ? selected[0] : ''
    }));
    startBtn.disabled = true;
    cancelBtn.disabled = false;
    cancelBtn.innerHTML = '<span class="material-icons-outlined">stop_circle</span> HỦY QUÉT';
    workerCountSelect.disabled = true;
    scrapeModeSelect.disabled = true;
    if (proxyUseCheckbox) proxyUseCheckbox.disabled = true;
    scanSheetSelect.disabled = true;
    document.getElementById('refreshPartnerBtn').disabled = true;
    startBtn.innerHTML = '<span class="material-icons-outlined">hourglass_top</span> ĐANG QUÉT...';
    document.getElementById('dataFeed').innerHTML = '';
    clearFailedLinks(false);
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
    cancelBtn.disabled = true;
    cancelBtn.innerHTML = '<span class="material-icons-outlined">hourglass_top</span> ĐANG HỦY...';
    addLog('Đã gửi lệnh hủy quét.');
}

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
    closeReportModal();
    startScraping(partners);
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

function updateProgress(data) {
    document.getElementById('totalLinks').textContent = data.total;
    document.getElementById('processedLinks').textContent = data.processed;
    document.getElementById('successLinks').textContent = data.success;
    document.getElementById('errorLinks').textContent = data.error;
    const pct = data.total > 0 ? (data.processed / data.total) * 100 : 0;
    document.getElementById('progressBar').style.width = `${pct}%`;
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
    document.getElementById('progressText').textContent = parts.join(' • ');

    const statusEl = document.getElementById('progressStatus');
    if (data.done || (data.processed === data.total && data.total > 0)) {
        if (data.cancelled) {
            statusEl.textContent = 'Đã huỷ';
            statusEl.className = 'progress-status cancelled';
        } else {
            statusEl.textContent = 'Thành công';
            statusEl.className = 'progress-status success';
        }
    } else {
        statusEl.textContent = '';
        statusEl.className = 'progress-status';
    }

    if (data.done || (data.processed === data.total && data.total > 0)) {
        startBtn.disabled = false;
        cancelBtn.disabled = true;
        cancelBtn.innerHTML = '<span class="material-icons-outlined">stop_circle</span> HỦY QUÉT';
        workerCountSelect.disabled = false;
        scrapeModeSelect.disabled = false;
        if (proxyUseCheckbox) proxyUseCheckbox.disabled = false;
        scanSheetSelect.disabled = false;
        startBtn.innerHTML = '<span class="material-icons-outlined">play_circle</span> BẮT ĐẦU QUÉT';
        addLog(data.cancelled ? '--- ĐÃ HỦY QUÉT ---' : '--- QUÉT HOÀN TẤT ---');
        scanCompletedForCurrentFile = true;
        setGooglePushState();
        updateFileList();
        loadPreview();
    }
}

function appendData(row) {
    const tbody = document.getElementById('dataFeed');
    if (tbody.innerText.includes('Chưa có kết quả')) tbody.innerHTML = '';
    const tr = document.createElement('tr');
    const views = formatNumber(row.views);
    const likes = formatNumber(row.likes);
    const comments = formatNumber(row.comments);
    const saves = formatNumber(row.saves);
    const shares = formatNumber(row.shares);

    tr.innerHTML = `
        <td>${row.id}</td>
        <td>${escapeHtml(row.sheetName || '')}</td>
        <td title="${escapeHtml(row.channelName || '')}">${escapeHtml(row.channelName || '')}</td>
        <td class="col-url" title="${escapeHtml(row.url)}"><a href="${escapeHtml(row.url)}" target="_blank" style="color: inherit; text-decoration: none;">${escapeHtml(row.url)}</a></td>
        <td style="text-align:right; font-weight:bold">${views}</td>
        <td style="text-align:right; font-weight:bold">${likes}</td>
        <td style="text-align:right; font-weight:bold">${comments}</td>
        <td style="text-align:right; font-weight:bold">${saves}</td>
        <td style="text-align:right; font-weight:bold">${shares}</td>
        <td><span class="col-status" title="${escapeHtml(row.status || '')}" style="color:${row.status === 'Success' ? '#16a34a' : '#dc2626'}">${row.status === 'Success' ? 'OK' : 'LỖI'}</span></td>
    `;
    tbody.prepend(tr);
    if (row.status !== 'Success') appendFailedLink(row);
}

function renderFailedLinks() {
    const tbody = document.getElementById('failedLinksBody');
    const badge = document.getElementById('failedCountBadge');
    const note = document.getElementById('failedNote');
    badge.textContent = failedLinks.length;

    if (failedLinks.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:32px; color:#9ca3af">Chưa có link lỗi</td></tr>';
        if (note) note.textContent = '';
        return;
    }

    const noStatsCount = failedLinks.filter(item => isNoStatsStatus(item.status)).length;
    const realErrorCount = failedLinks.length - noStatsCount;
    if (note) {
        const parts = [];
        if (realErrorCount > 0) parts.push(`${realErrorCount} lỗi quét`);
        if (noStatsCount > 0) parts.push(`${noStatsCount} post TikTok ẩn số liệu`);
        note.textContent = parts.length ? `(${parts.join(' • ')})` : '';
    }

    tbody.innerHTML = failedLinks.map(item => {
        const noStats = isNoStatsStatus(item.status);
        const reasonClass = noStats ? 'failed-reason soft-warn' : 'failed-reason';
        const reasonText = noStats ? 'TikTok không trả số liệu (post có thể ẩn lượt xem)' : (item.status || 'Lỗi không xác định');
        const tag = noStats ? '<span class="reason-tag">TikTok ẩn</span>' : '';
        return `
        <tr>
            <td>${item.id}</td>
            <td>${escapeHtml(item.sheetName || '')}</td>
            <td class="col-url" title="${escapeHtml(item.url)}"><a href="${escapeHtml(item.url)}" target="_blank" style="color: inherit; text-decoration: none;">${escapeHtml(item.url)}</a></td>
            <td class="${reasonClass}" title="${escapeHtml(item.status || '')}">${escapeHtml(reasonText)}${tag}</td>
            <td>${item.worker ? `Luồng ${item.worker}` : ''}</td>
        </tr>
    `;
    }).join('');
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

async function copyFailedLinks() {
    if (failedLinks.length === 0) {
        notify('Không có link lỗi để copy.', 'warn');
        return;
    }
    const text = failedLinks.map(item => item.url).join('\n');
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
        notify(`Đã copy ${failedLinks.length} link lỗi.`, 'success');
    } catch (error) {
        notify(`Lỗi copy link: ${error.message}`, 'error');
    }
}

function clearFailedLinks(showLog = true) {
    failedLinks = [];
    renderFailedLinks();
    if (showLog) addLog('Đã xóa danh sách link lỗi.');
}

async function openReportModal() {
    const modal = document.getElementById('reportModal');
    const list = document.getElementById('partnerList');
    modal.classList.add('active');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    document.getElementById('partnerSearch').value = '';
    reportPartners = [];
    reportSheetName = '';
    selectedPartners = new Set();
    list.innerHTML = '<div class="empty-state">Đang tải danh sách đối tác...</div>';
    updateReportSummary();
    await loadReportPartners();
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
    const list = document.getElementById('partnerList');
    const requestedSheet = sheetName || reportSheetName || document.getElementById('reportSheetSelect')?.value || '';
    const { applyMinViews, minViews } = reportFilterParams();
    const params = new URLSearchParams();
    if (requestedSheet) params.set('sheet_name', requestedSheet);
    params.set('apply_min_views', applyMinViews ? 'true' : 'false');
    params.set('min_views', String(minViews));
    const query = params.toString() ? `?${params.toString()}` : '';
    list.innerHTML = '<div class="empty-state">Đang tải danh sách đối tác...</div>';
    selectedPartners = new Set();
    updateReportSummary();
    try {
        const res = await fetch(`/report-partners${query}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Không tải được danh sách đối tác');
        const sheetList = resolveReportSheets(data);
        const activeSheet = data.currentSheet || data.dataSheet || requestedSheet || sheetList[0] || '';
        renderReportSheetOptions(sheetList, activeSheet);
        reportPartners = normalizeReportPartners(data.partners);
        reportSheetName = activeSheet || reportSheetName;
        document.getElementById('reportModalSubtitle').textContent = `${data.fileLabel || data.file} • ${reportSheetName || '—'} • ${reportPartners.length} đối tác`;
        renderPartnerList();
    } catch (error) {
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
    const modal = document.getElementById('reportModal');
    modal.classList.remove('active');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
}

async function openHistoryModal() {
    const modal = document.getElementById('historyModal');
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
    updateReportSummary();
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
    refreshBtn.disabled = count === 0;
}

function filenameFromDisposition(disposition, fallback) {
    const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match) return decodeURIComponent(utf8Match[1]);
    const plainMatch = disposition.match(/filename="?([^";]+)"?/i);
    return plainMatch ? plainMatch[1] : fallback;
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
        const applyMinViews = document.getElementById('minViewToggle').checked;
        const minViewsRaw = parseInt(document.getElementById('minViewInput').value, 10);
        const minViews = Number.isFinite(minViewsRaw) && minViewsRaw >= 0 ? minViewsRaw : 100;
        const res = await fetch('/export-report', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ partners, applyMinViews, minViews, sheetName: reportSheetName || document.getElementById('reportSheetSelect')?.value || '' })
        });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.error || 'Không xuất được báo cáo');
        }

        const blob = await res.blob();
        const fallback = partners.length === 1 ? `${partners[0]}.xlsx` : 'bao_cao_doi_tac.zip';
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

document.getElementById('reportModal').addEventListener('click', (event) => {
    if (event.target.id === 'reportModal') closeReportModal();
});

document.getElementById('historyModal').addEventListener('click', (event) => {
    if (event.target.id === 'historyModal') closeHistoryModal();
});

document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (document.getElementById('reportModal').classList.contains('active')) {
        closeReportModal();
    } else if (document.getElementById('historyModal').classList.contains('active')) {
        closeHistoryModal();
    }
});

scanSheetSelect.addEventListener('change', () => {
    currentScanSheetName = scanSheetSelect.value;
});

pushSheetSelect.addEventListener('change', () => {
    currentPushSheetName = pushSheetSelect.value;
    setGooglePushState();
});

['input', 'change', 'paste', 'keydown'].forEach(eventName => {
    googleSheetUrlInput.addEventListener(eventName, markGoogleSheetUrlDirty);
});

window.onload = async () => {
    await updateFileList({ applyGoogleSheetUrl: true });
    await loadPreview();
    await refreshProxyStatus();
    connectWS();
};
