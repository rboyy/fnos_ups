/* === UPS Watchdog Dashboard JS === */

const API = {
    status: '/api/status',
    config: '/api/config',
    targets: '/api/targets',
    logs: '/api/logs',
    shutdown: '/api/shutdown',
};

// --- Toast ---
function createToastContainer() {
    let c = document.querySelector('.toast-container');
    if (!c) {
        c = document.createElement('div');
        c.className = 'toast-container';
        document.body.appendChild(c);
    }
    return c;
}

function toast(msg, type = 'success', duration = 3000) {
    const container = createToastContainer();
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => {
        el.style.opacity = '0';
        el.style.transform = 'translateX(20px)';
        el.style.transition = 'all 0.3s';
        setTimeout(() => el.remove(), 300);
    }, duration);
}

// --- API helpers ---
async function apiGet(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

async function apiPost(url, body = {}) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
}

async function apiPut(url, body = {}) {
    const res = await fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
}

async function apiDelete(url) {
    const res = await fetch(url, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
}

// --- Status ---
async function refreshStatus() {
    try {
        const status = await apiGet(API.status);
        updateStatusBar(status);
        updateTargetStatuses(status);
    } catch (e) {
        console.error('获取状态失败:', e);
    }
}

function updateStatusBar(status) {
    const indicator = document.getElementById('powerIndicator');
    const dot = indicator.querySelector('.dot');
    const label = indicator.querySelector('.label');

    indicator.className = 'status-indicator';

    if (!status.running) {
        indicator.classList.add('warn');
        label.textContent = '看门狗未运行';
    } else if (status.power_ok === false) {
        indicator.classList.add('danger');
        label.textContent = status.message || '市电中断！';
    } else if (status.consecutive_fails > 0) {
        indicator.classList.add('warn');
        label.textContent = status.message || '网络异常';
    } else {
        indicator.classList.add('ok');
        label.textContent = '市电正常';
    }

    document.getElementById('statChecks').textContent = `检测: ${status.total_checks || 0}`;
    document.getElementById('statFails').textContent = `失败: ${status.total_fails || 0}`;
    document.getElementById('statConsec').textContent = `连续: ${status.consecutive_fails || 0}`;
    document.getElementById('statLastCheck').textContent = `最后: ${status.last_check || '-'}`;

    if (status.version) {
        document.getElementById('version').textContent = `v${status.version}`;
    }
}

// --- Targets ---
let currentConfig = {};

async function refreshConfig() {
    try {
        currentConfig = await apiGet(API.config);
        renderTargets(currentConfig.targets || []);
        populateConfigForm(currentConfig);
    } catch (e) {
        console.error('获取配置失败:', e);
    }
}

function updateTargetStatuses(status) {
    const items = document.querySelectorAll('.target-item');
    const targetMap = {};
    (status.targets || []).forEach(t => { targetMap[t.ip] = t; });

    items.forEach(item => {
        const ip = item.dataset.ip;
        const info = targetMap[ip];
        const statusDot = item.querySelector('.target-status');
        const rttEl = item.querySelector('.target-rtt');

        if (!info) {
            statusDot.className = 'target-status unknown';
            if (rttEl) rttEl.textContent = '';
        } else if (info.success) {
            statusDot.className = 'target-status online';
            if (rttEl) rttEl.textContent = `${info.rtt}ms`;
        } else {
            statusDot.className = 'target-status offline';
            if (rttEl) rttEl.textContent = '离线';
        }
    });
}

function renderTargets(targets) {
    const container = document.getElementById('targetList');

    if (!targets || targets.length === 0) {
        container.innerHTML = '<div class="empty-state">暂无监控目标，点击上方「添加」按钮</div>';
        return;
    }

    container.innerHTML = targets.map(t => `
        <div class="target-item ${t.enabled ? '' : 'disabled'}" data-ip="${escHtml(t.ip)}">
            <span class="target-status unknown"></span>
            <div class="target-info">
                <div class="target-name">${escHtml(t.name || t.ip)}</div>
                <div class="target-ip">${escHtml(t.ip)}</div>
            </div>
            <span class="target-rtt"></span>
            <div class="target-actions">
                <button class="btn btn-sm" onclick="pingTest('${escHtml(t.ip)}')" title="Ping 测试">Ping</button>
                <button class="btn btn-sm" onclick="toggleTarget('${escHtml(t.ip)}')" title="${t.enabled ? '禁用' : '启用'}">
                    ${t.enabled ? '禁用' : '启用'}
                </button>
                <button class="btn btn-sm" onclick="deleteTarget('${escHtml(t.ip)}')" title="删除" style="color:var(--danger)">删除</button>
            </div>
        </div>
    `).join('');
}

function escHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// --- Config Form ---
function populateConfigForm(cfg) {
    document.getElementById('pingInterval').value = cfg.ping_interval || 10;
    document.getElementById('pingTimeout').value = cfg.ping_timeout || 3;
    document.getElementById('failThreshold').value = cfg.failure_threshold || 6;
    document.getElementById('shutdownDelay').value = cfg.shutdown_delay || 30;
    document.getElementById('allMustFail').checked = cfg.all_must_fail !== false;
}

// --- Logs ---
async function refreshLogs() {
    try {
        const data = await apiGet(`${API.logs}?lines=500`);
        const viewer = document.getElementById('logViewer');
        const autoScroll = document.getElementById('autoScroll').checked;
        const lines = data.lines || [];
        viewer.textContent = lines.join('\n') || '暂无日志';

        // 必须在 DOM 更新后再设置滚动位置
        if (autoScroll) {
            requestAnimationFrame(() => {
                viewer.scrollTop = viewer.scrollHeight;
            });
        }
    } catch (e) {
        console.error('获取日志失败:', e);
    }
}

// --- Actions ---
async function addTarget(ip, name) {
    try {
        await apiPost(API.targets, { ip, name });
        toast(`已添加 ${ip}`);
        await refreshConfig();
        closeModal();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function deleteTarget(ip) {
    if (!confirm(`确定要删除监控目标 ${ip} 吗？`)) return;
    try {
        await apiDelete(`${API.targets}/${ip}`);
        toast(`已删除 ${ip}`);
        await refreshConfig();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function toggleTarget(ip) {
    try {
        const data = await apiPost(`${API.targets}/${ip}/toggle`);
        toast(data.message);
        await refreshConfig();
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function pingTest(ip) {
    toast(`正在 Ping ${ip}...`, 'warning', 2000);
    try {
        const data = await apiPost(`${API.targets}/${ip}/ping`);
        if (data.ok) {
            toast(`Ping ${ip} 成功`, 'success');
        } else {
            toast(`Ping ${ip} 失败: ${data.error || '不可达'}`, 'error');
        }
    } catch (e) {
        toast(`Ping 测试异常: ${e.message}`, 'error');
    }
}

async function saveConfig() {
    const cfg = {
        targets: currentConfig.targets || [],
        ping_interval: parseInt(document.getElementById('pingInterval').value),
        ping_timeout: parseInt(document.getElementById('pingTimeout').value),
        failure_threshold: parseInt(document.getElementById('failThreshold').value),
        shutdown_delay: parseInt(document.getElementById('shutdownDelay').value),
        all_must_fail: document.getElementById('allMustFail').checked,
    };
    try {
        await apiPut(API.config, cfg);
        toast('设置已保存');
        currentConfig = cfg;
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function clearLogs() {
    if (!confirm('确定要清空所有日志吗？')) return;
    try {
        await apiPost(`${API.logs}/clear`);
        toast('日志已清空');
        await refreshLogs();
    } catch (e) {
        toast(e.message, 'error');
    }
}

// --- Modal ---
function openModal() {
    document.getElementById('modalAddTarget').classList.add('active');
    document.getElementById('targetIp').focus();
}

function closeModal() {
    document.getElementById('modalAddTarget').classList.remove('active');
    document.getElementById('addTargetForm').reset();
}

// --- Init ---
document.addEventListener('DOMContentLoaded', () => {
    // 按钮事件
    document.getElementById('btnAddTarget').addEventListener('click', openModal);
    document.getElementById('btnRefresh').addEventListener('click', () => {
        refreshStatus();
        refreshConfig();
        refreshLogs();
        toast('已刷新');
    });
    document.getElementById('btnClearLog').addEventListener('click', clearLogs);

    // Modal 事件
    document.querySelector('.modal-overlay').addEventListener('click', closeModal);
    document.querySelector('.modal-close').addEventListener('click', closeModal);
    document.querySelector('.modal-cancel-btn').addEventListener('click', closeModal);

    // 表单提交
    document.getElementById('addTargetForm').addEventListener('submit', (e) => {
        e.preventDefault();
        const ip = document.getElementById('targetIp').value.trim();
        const name = document.getElementById('targetName').value.trim() || ip;
        if (ip) addTarget(ip, name);
    });

    document.getElementById('configForm').addEventListener('submit', (e) => {
        e.preventDefault();
        saveConfig();
    });

    // 键盘事件
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeModal();
    });

    // 初始加载
    refreshConfig();
    refreshStatus();
    refreshLogs();

    // 定时刷新
    setInterval(refreshStatus, 5000);
    setInterval(refreshLogs, 8000);
});
