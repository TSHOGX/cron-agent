// State
let currentView = 'status';
let config = {};
let prompt = {};
let summaryPrompt = {};
let currentJournalPeriod = 'daily';
let isChecking = false;

// API Helpers
async function apiCall(url, options = {}) {
    try {
        const response = await fetch(url, {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                ...options.headers
            }
        });
        return await response.json();
    } catch (error) {
        console.error('API Error:', error);
        showToast('请求失败: ' + error.message, 'error');
        return null;
    }
}

// Toast Notification
function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = 'toast ' + type;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3000);
}

// Navigation
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
        const view = item.dataset.view;
        switchView(view);
    });
});

function switchView(view) {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.view === view);
    });
    document.querySelectorAll('.view').forEach(v => {
        v.classList.toggle('active', v.id === 'view-' + view);
    });
    currentView = view;

    // Load data for the view
    if (view === 'status') loadStatus();
    if (view === 'config') loadConfig();
    if (view === 'prompt') loadPrompt();
    if (view === 'journal') loadJournalFiles();
    if (view === 'logs') loadLogs();
    if (view === 'messages') loadMessages();
}

// Helper function to calculate next run time from cron expression
function getNextRunTime(cronExpr) {
    const now = new Date();
    const parts = cronExpr.trim().split(/\s+/);
    if (parts.length < 5) return null;

    const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;

    // Calculate next run time
    let nextRun = new Date(now);
    nextRun.setSeconds(0);
    nextRun.setMilliseconds(0);

    // Handle minute
    if (minute.startsWith('*/')) {
        const interval = parseInt(minute.substring(2));
        const currentMinute = now.getMinutes();
        nextRun.setMinutes(Math.ceil((currentMinute + 1) / interval) * interval);
        if (nextRun <= now) {
            nextRun.setMinutes(nextRun.getMinutes() + interval);
        }
    } else {
        const mins = parseInt(minute);
        if (!isNaN(mins)) {
            if (mins <= now.getMinutes()) {
                nextRun.setMinutes(mins);
                nextRun.setHours(now.getHours() + 1);
            } else {
                nextRun.setMinutes(mins);
            }
        }
    }

    // Handle hour
    if (hour && !hour.startsWith('*')) {
        const h = parseInt(hour);
        if (!isNaN(h) && h !== now.getHours()) {
            nextRun.setHours(h);
        }
    }

    return nextRun;
}

// Status View
async function loadStatus() {
    const status = await apiCall('/api/status');
    if (!status) return;

    // Load config to get interval
    const config = await apiCall('/api/config');
    const intervalSeconds = config?.capture_interval || 900;
    const intervalMinutes = Math.floor(intervalSeconds / 60);

    // Handle Capture Service (Tmux)
    const captureIndicator = document.getElementById('capture-status-indicator');
    const captureStatusText = document.getElementById('capture-status-text');
    const capture = status.capture || {};

    if (capture.running) {
        captureIndicator.querySelector('span').textContent = '运行中';
        captureIndicator.querySelector('.status-dot').className = 'status-dot active';
        captureStatusText.textContent = `会话 "${capture.session}" 运行中`;
    } else {
        captureIndicator.querySelector('span').textContent = '未运行';
        captureIndicator.querySelector('.status-dot').className = 'status-dot inactive';
        captureStatusText.textContent = '点击"启动/重启截图"开始定时截图';
    }

    // Handle Summarizer Service (Cron)
    const summarizerIndicator = document.getElementById('summarizer-status-indicator');
    const summarizerStatusText = document.getElementById('summarizer-status-text');
    const summarizer = status.summarizer || {};

    if (summarizer.installed) {
        summarizerIndicator.querySelector('span').textContent = '运行中';
        summarizerIndicator.querySelector('.status-dot').className = 'status-dot active';
        summarizerStatusText.textContent = `${summarizer.jobs?.length || 0} 个定时任务`;
    } else {
        summarizerIndicator.querySelector('span').textContent = '未运行';
        summarizerIndicator.querySelector('.status-dot').className = 'status-dot inactive';
        summarizerStatusText.textContent = summarizer.error || '点击"启动/重启汇总"开始定时汇总';
    }

    // Show service jobs with next run time
    const jobsContainer = document.getElementById('service-jobs');
    const allJobs = [];

    // Add capture job info
    if (capture.running) {
        const now = new Date();
        const nextRun = new Date(now.getTime() + intervalSeconds * 1000);
        allJobs.push({
            service: '截图 (Tmux)',
            time: `每 ${intervalMinutes} 分钟`,
            nextRun: nextRun.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
            desc: '自动截图并分析'
        });
    }

    // Add summarizer jobs
    if (summarizer.jobs && summarizer.jobs.length > 0) {
        summarizer.jobs.forEach(job => {
            const parts = job.trim().split(/\s+/);
            if (parts.length >= 6) {
                const cronExpr = parts.slice(0, 5).join(' ');
                const cmd = parts.slice(5).join(' ').replace(/.*scheduler\.py /, '');
                const nextRun = getNextRunTime(cronExpr);

                // Map command to friendly name
                let jobName = '汇总';
                if (cmd.includes('daily')) jobName = '日报';
                else if (cmd.includes('weekly')) jobName = '周报';
                else if (cmd.includes('monthly')) jobName = '月报';
                else if (cmd.includes('morning')) jobName = '上午总结';
                else if (cmd.includes('afternoon')) jobName = '下午总结';
                else if (cmd.includes('evening')) jobName = '晚上总结';

                allJobs.push({
                    service: `${jobName} (Cron)`,
                    time: cronExpr,
                    nextRun: nextRun ? nextRun.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-',
                    desc: cmd
                });
            }
        });
    }

    if (allJobs.length > 0) {
        jobsContainer.innerHTML = allJobs.map(job => `
            <div class="service-job">
                <span class="job-service">${job.service}</span>
                <span class="job-time">${job.time}</span>
                <span class="job-desc">${job.desc}</span>
                <span class="job-next-run">下次: ${job.nextRun}</span>
            </div>
        `).join('');
    } else {
        jobsContainer.innerHTML = '<div class="loading">暂无运行中的服务</div>';
    }

    // Load today's records count
    const records = await apiCall('/api/records?limit=100');
    document.getElementById('today-records').textContent = records ? records.length + ' 条' : '0 条';
}

// Start Capture Service (Tmux)
document.getElementById('btn-start-capture').addEventListener('click', async () => {
    showToast('正在启动截图服务...', 'info');
    const result = await apiCall('/api/capture/start', { method: 'POST' });
    if (result && result.success) {
        showToast('截图服务已启动', 'success');
        loadStatus();
    } else {
        showToast('启动失败: ' + (result?.error || '未知错误'), 'error');
    }
});

// Stop Capture Service (Tmux)
document.getElementById('btn-stop-capture').addEventListener('click', async () => {
    if (!confirm('确定要停止截图服务吗？停止后将不再自动截图。')) return;

    showToast('正在停止截图服务...', 'info');
    const result = await apiCall('/api/capture/stop', { method: 'POST' });
    if (result && result.success) {
        showToast('截图服务已停止', 'success');
        loadStatus();
    } else {
        showToast('停止失败: ' + (result?.error || '未知错误'), 'error');
    }
});

// Start Summarizer Service (Cron)
document.getElementById('btn-start-summarizer').addEventListener('click', async () => {
    showToast('正在启动汇总服务...', 'info');
    const result = await apiCall('/api/summarizer/start', { method: 'POST' });
    if (result && result.success) {
        showToast('汇总服务已启动', 'success');
        loadStatus();
    } else {
        showToast('启动失败: ' + (result?.error || '未知错误'), 'error');
    }
});

// Stop Summarizer Service (Cron)
document.getElementById('btn-stop-summarizer').addEventListener('click', async () => {
    if (!confirm('确定要停止汇总服务吗？停止后将不再自动生成汇总。')) return;

    showToast('正在停止汇总服务...', 'info');
    const result = await apiCall('/api/summarizer/stop', { method: 'POST' });
    if (result && result.success) {
        showToast('汇总服务已停止', 'success');
        loadStatus();
    } else {
        showToast('停止失败: ' + (result?.error || '未知错误'), 'error');
    }
});

// Config View
async function loadConfig() {
    config = await apiCall('/api/config');
    if (!config) return;

    document.getElementById('config-capture-interval').value = Math.floor((config.capture_interval || 300) / 60);
    document.getElementById('config-daily-time').value = config.daily_summary_time || '12:00';
    document.getElementById('config-weekly-day').value = config.weekly_summary_day || 'sunday';
    document.getElementById('config-monthly-day').value = config.monthly_summary_day || 1;
    document.getElementById('config-model').value = config.model || 'kimi-k2.5';
    document.getElementById('config-quality').value = config.screenshot_quality || 80;
    document.getElementById('config-api-key').value = '';

    // Load time periods
    const timePeriods = config.time_periods || {
        morning: { start: '06:00', end: '12:00' },
        afternoon: { start: '12:00', end: '18:00' },
        evening: { start: '18:00', end: '24:00' }
    };

    document.getElementById('config-morning-start').value = timePeriods.morning?.start || '06:00';
    document.getElementById('config-morning-end').value = timePeriods.morning?.end || '12:00';
    document.getElementById('config-afternoon-start').value = timePeriods.afternoon?.start || '12:00';
    document.getElementById('config-afternoon-end').value = timePeriods.afternoon?.end || '18:00';
    document.getElementById('config-evening-start').value = timePeriods.evening?.start || '18:00';
    document.getElementById('config-evening-end').value = timePeriods.evening?.end || '24:00';
}

// Save Config
document.getElementById('btn-save-config').addEventListener('click', async () => {
    const newConfig = {
        capture_interval: parseInt(document.getElementById('config-capture-interval').value) * 60,
        daily_summary_time: document.getElementById('config-daily-time').value,
        weekly_summary_day: document.getElementById('config-weekly-day').value,
        monthly_summary_day: parseInt(document.getElementById('config-monthly-day').value),
        model: document.getElementById('config-model').value,
        screenshot_quality: parseInt(document.getElementById('config-quality').value),
        time_periods: {
            morning: {
                start: document.getElementById('config-morning-start').value,
                end: document.getElementById('config-morning-end').value
            },
            afternoon: {
                start: document.getElementById('config-afternoon-start').value,
                end: document.getElementById('config-afternoon-end').value
            },
            evening: {
                start: document.getElementById('config-evening-start').value,
                end: document.getElementById('config-evening-end').value
            }
        }
    };

    const apiKey = document.getElementById('config-api-key').value;
    if (apiKey) {
        newConfig.api = { auth_token: apiKey };
    }

    const result = await apiCall('/api/config', {
        method: 'POST',
        body: JSON.stringify(newConfig)
    });

    if (result && result.success) {
        showToast('配置已保存，正在重启服务...', 'info');

        // Restart both services
        const restartResult = await apiCall('/api/services/restart', {
            method: 'POST'
        });

        if (restartResult && restartResult.success) {
            showToast('配置已保存，服务已重启', 'success');
        } else {
            const errorMsg = restartResult?.errors?.join(', ') || '未知错误';
            showToast('配置已保存，但服务重启失败: ' + errorMsg, 'error');
        }
        config = { ...config, ...newConfig };
    } else {
        showToast('保存失败: ' + (result?.error || '未知错误'), 'error');
    }
});

// Prompt View
async function loadPrompt() {
    // Load recorder prompt
    prompt = await apiCall('/api/record_prompt');
    if (prompt) {
        document.getElementById('prompt-system').value = prompt.system || '';
        document.getElementById('prompt-user').value = prompt.user || '';
        updatePromptPreview();
    }

    // Load summarizer prompt
    summaryPrompt = await apiCall('/api/summary_prompt');
    if (summaryPrompt) {
        document.getElementById('summary-prompt-system').value = summaryPrompt.system || '';
        document.getElementById('summary-prompt-daily').value = summaryPrompt.daily || '';
        document.getElementById('summary-prompt-weekly').value = summaryPrompt.weekly || '';
        document.getElementById('summary-prompt-monthly').value = summaryPrompt.monthly || '';
        document.getElementById('summary-prompt-time-of-day').value = summaryPrompt.time_of_day || '';
    }
}

// Prompt tab switching
document.querySelectorAll('.prompt-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.prompt-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        document.querySelectorAll('.prompt-tab-content').forEach(c => c.classList.remove('active'));
        document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
    });
});

// Update Prompt Preview
document.getElementById('prompt-system').addEventListener('input', updatePromptPreview);
document.getElementById('prompt-user').addEventListener('input', updatePromptPreview);

function updatePromptPreview() {
    const system = document.getElementById('prompt-system').value;
    const user = document.getElementById('prompt-user').value;
    document.getElementById('preview-system').textContent = system || '(未设置)';
    document.getElementById('preview-user').textContent = user || '(未设置)';
}

// Save All Prompts
document.getElementById('btn-save-all-prompts').addEventListener('click', async () => {
    // Save recorder prompt
    const newPrompt = {
        system: document.getElementById('prompt-system').value,
        user: document.getElementById('prompt-user').value
    };

    const promptResult = await apiCall('/api/record_prompt', {
        method: 'POST',
        body: JSON.stringify(newPrompt)
    });

    // Save summarizer prompt
    const newSummaryPrompt = {
        system: document.getElementById('summary-prompt-system').value,
        daily: document.getElementById('summary-prompt-daily').value,
        weekly: document.getElementById('summary-prompt-weekly').value,
        monthly: document.getElementById('summary-prompt-monthly').value,
        time_of_day: document.getElementById('summary-prompt-time-of-day').value
    };

    const summaryResult = await apiCall('/api/summary_prompt', {
        method: 'POST',
        body: JSON.stringify(newSummaryPrompt)
    });

    if (promptResult && promptResult.success && summaryResult && summaryResult.success) {
        showToast('所有提示词已保存', 'success');
        prompt = newPrompt;
        summaryPrompt = newSummaryPrompt;
    } else {
        showToast('保存失败: ' + ((promptResult?.error || summaryResult?.error) || '未知错误'), 'error');
    }
});

// Journal View
document.querySelectorAll('.journal-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.journal-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        currentJournalPeriod = tab.dataset.period;
        loadJournalFiles();
    });
});

async function loadJournalFiles() {
    const filesContainer = document.getElementById('journal-files');
    const detailContainer = document.getElementById('journal-detail');

    filesContainer.innerHTML = '<div class="loading">加载中...</div>';
    detailContainer.innerHTML = '<div class="journal-detail-placeholder"><p>选择一个文件查看内容</p></div>';

    const files = await apiCall(`/api/journal/${currentJournalPeriod}`);

    if (!files || files.length === 0) {
        filesContainer.innerHTML = '<div class="loading">暂无文件</div>';
        return;
    }

    filesContainer.innerHTML = files.map(file => `
        <div class="journal-file" data-filename="${file.name}">
            <span class="journal-file-name">${file.name}</span>
        </div>
    `).join('');

    // Add click handlers
    document.querySelectorAll('.journal-file').forEach(fileEl => {
        fileEl.addEventListener('click', () => {
            document.querySelectorAll('.journal-file').forEach(f => f.classList.remove('active'));
            fileEl.classList.add('active');
            loadJournalContent(fileEl.dataset.filename);
        });
    });
}

async function loadJournalContent(filename) {
    const detailContainer = document.getElementById('journal-detail');

    const result = await apiCall(`/api/journal/${currentJournalPeriod}/${filename}`);

    if (!result || !result.content) {
        detailContainer.innerHTML = '<div class="journal-detail-placeholder"><p>无法加载内容</p></div>';
        return;
    }

    const markdownHtml = renderMarkdown(result.content);
    detailContainer.innerHTML = `<div class="journal-content-markdown">${markdownHtml}</div>`;
}

// Logs View
async function loadLogs() {
    // Load available dates
    const dates = await apiCall('/api/records/dates');
    const dateSelect = document.getElementById('log-date');

    if (dates && dates.length > 0) {
        dateSelect.innerHTML = dates.map(d =>
            `<option value="${d}">${d}</option>`
        ).join('');
    } else {
        dateSelect.innerHTML = '<option value="">暂无数据</option>';
    }

    // Load logs for selected date
    loadLogsForDate(dateSelect.value);
}

// Load logs for specific date
async function loadLogsForDate(date) {
    const logsList = document.getElementById('logs-list');

    if (!date) {
        logsList.innerHTML = '<div class="log-empty">请选择日期</div>';
        return;
    }

    const records = await apiCall(`/api/records?date=${date}&limit=100`);
    if (!records) return;

    if (records.length === 0) {
        logsList.innerHTML = '<div class="log-empty">当日无记录</div>';
        return;
    }

    logsList.innerHTML = records.map(record => {
        const time = new Date(record.timestamp).toLocaleString('zh-CN', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
        return `
            <div class="log-item">
                <span class="log-time">${time}</span>
                <span class="log-content">${escapeHtml(record.description)}</span>
            </div>
        `;
    }).join('');
}

// Date change handler
document.getElementById('log-date').addEventListener('change', (e) => {
    loadLogsForDate(e.target.value);
});

// Refresh logs
document.getElementById('btn-refresh-logs').addEventListener('click', () => {
    const date = document.getElementById('log-date').value;
    loadLogsForDate(date);
});

// Messages View
async function loadMessages() {
    const messagesList = document.getElementById('messages-list');

    messagesList.innerHTML = '<div class="loading">加载中...</div>';

    const messages = await apiCall('/api/messages?limit=100');

    if (!messages || messages.length === 0) {
        messagesList.innerHTML = '<div class="messages-empty">暂无消息</div>';
        return;
    }

    messagesList.innerHTML = messages.map(msg => {
        const time = new Date(msg.timestamp).toLocaleString('zh-CN', {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });

        let typeLabel = '';
        let typeClass = '';
        if (msg.type === 'daily') {
            typeLabel = '日报';
            typeClass = 'msg-daily';
        } else if (msg.type === 'weekly') {
            typeLabel = '周报';
            typeClass = 'msg-weekly';
        } else if (msg.type === 'monthly') {
            typeLabel = '月报';
            typeClass = 'msg-monthly';
        } else {
            typeLabel = msg.type;
            typeClass = 'msg-info';
        }

        const filled = msg.filled ? '<span class="msg-filled">(已补发)</span>' : '';

        return `
            <div class="message-item ${typeClass}">
                <span class="msg-time">${time}</span>
                <span class="msg-type">${typeLabel}</span>
                <span class="msg-period">${msg.period || '-'}</span>
                ${filled}
            </div>
        `;
    }).join('');
}

// Check and fill missing summaries
document.getElementById('btn-check-messages').addEventListener('click', async () => {
    if (isChecking) return;

    isChecking = true;
    const btn = document.getElementById('btn-check-messages');
    btn.disabled = true;
    btn.textContent = '检查中...';

    showToast('正在检查并补发遗漏的汇总...', 'info');

    const result = await apiCall('/api/messages/check', { method: 'POST' });

    if (result && result.success) {
        if (result.count > 0) {
            showToast(`已完成补发，共 ${result.count} 条`, 'success');
        } else {
            showToast('没有遗漏的汇总', 'info');
        }
        loadMessages();
    } else {
        showToast('检查失败: ' + (result?.error || '未知错误'), 'error');
    }

    isChecking = false;
    btn.disabled = false;
    btn.textContent = '检查并补发';
});

// Refresh messages
document.getElementById('btn-refresh-messages').addEventListener('click', () => {
    loadMessages();
    showToast('消息已刷新', 'info');
});

// Utility: Escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function renderInlineMarkdown(text) {
    return escapeHtml(text)
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/`(.+?)`/g, '<code>$1</code>');
}

function renderMarkdown(content) {
    if (!content) return '<p>暂无内容</p>';

    const lines = content.replace(/\r\n/g, '\n').trim().split('\n');
    const html = [];
    const paragraph = [];

    const flushParagraph = () => {
        if (!paragraph.length) return;
        html.push(`<p>${paragraph.map(renderInlineMarkdown).join('<br>')}</p>`);
        paragraph.length = 0;
    };

    lines.forEach(line => {
        const trimmed = line.trim();
        if (!trimmed) {
            flushParagraph();
            return;
        }

        const headingMatch = trimmed.match(/^(#{1,6})\s+(.+)$/);
        if (headingMatch) {
            flushParagraph();
            const level = headingMatch[1].length;
            html.push(`<h${level}>${renderInlineMarkdown(headingMatch[2])}</h${level}>`);
            return;
        }

        if (/^-{3,}$/.test(trimmed)) {
            flushParagraph();
            html.push('<hr>');
            return;
        }

        paragraph.push(trimmed);
    });

    flushParagraph();
    return html.join('');
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    loadStatus();
});
