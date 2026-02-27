let currentView = "status";
let currentJournalPeriod = "daily";
let taskList = [];

async function apiCall(url, options = {}) {
    try {
        const response = await fetch(url, {
            ...options,
            headers: {
                "Content-Type": "application/json",
                ...(options.headers || {}),
            },
        });
        return await response.json();
    } catch (error) {
        console.error("API Error:", error);
        showToast("请求失败: " + error.message, "error");
        return null;
    }
}

function showToast(message, type = "info") {
    const toast = document.getElementById("toast");
    toast.textContent = message;
    toast.className = "toast " + type;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 3000);
}

function switchView(view) {
    document.querySelectorAll(".nav-item").forEach((item) => {
        item.classList.toggle("active", item.dataset.view === view);
    });
    document.querySelectorAll(".view").forEach((v) => {
        v.classList.toggle("active", v.id === "view-" + view);
    });

    currentView = view;
    if (view === "status") loadStatus();
    if (view === "task-settings") loadTaskSettingsView();
    if (view === "journal") loadJournalFiles();
    if (view === "logs") loadLogs();
    if (view === "messages") loadMessages();
}

function getNextRunTime(cronExpr) {
    const now = new Date();
    const parts = cronExpr.trim().split(/\s+/);
    if (parts.length < 5) return null;

    const minute = parts[0];
    let nextRun = new Date(now);
    nextRun.setSeconds(0);
    nextRun.setMilliseconds(0);

    if (minute.startsWith("*/")) {
        const interval = parseInt(minute.substring(2), 10);
        const m = now.getMinutes();
        nextRun.setMinutes(Math.ceil((m + 1) / interval) * interval);
        if (nextRun <= now) nextRun.setMinutes(nextRun.getMinutes() + interval);
    } else {
        const m = parseInt(minute, 10);
        if (!Number.isNaN(m)) {
            if (m <= now.getMinutes()) {
                nextRun.setMinutes(m);
                nextRun.setHours(now.getHours() + 1);
            } else {
                nextRun.setMinutes(m);
            }
        }
    }

    return nextRun;
}

async function loadTasks() {
    const tasks = await apiCall("/api/tasks");
    if (!tasks || !Array.isArray(tasks)) {
        taskList = [];
        return [];
    }
    taskList = tasks;
    return tasks;
}

function getTaskById(taskId) {
    return taskList.find((t) => t.metadata && t.metadata.id === taskId);
}

async function loadStatus() {
    const [status, records, tasks] = await Promise.all([
        apiCall("/api/status"),
        apiCall("/api/records?limit=100"),
        loadTasks(),
    ]);
    if (!status) return;

    const backends = status?.cron_manager?.backends || {};
    const tmux = backends.tmux || {};
    const cron = backends.cron || {};

    const captureTask = (tasks || []).find((t) => t.metadata?.id === "capture-analyze");
    const intervalSeconds = captureTask?.spec?.schedule?.intervalSeconds || 900;
    const intervalMinutes = Math.floor(intervalSeconds / 60);

    const captureIndicator = document.getElementById("capture-status-indicator");
    const captureStatusText = document.getElementById("capture-status-text");
    if (tmux.running) {
        captureIndicator.querySelector("span").textContent = "运行中";
        captureIndicator.querySelector(".status-dot").className = "status-dot active";
        const session = (tmux.sessions && tmux.sessions[0]) || "";
        captureStatusText.textContent = `会话 "${session}" 运行中`;
    } else {
        captureIndicator.querySelector("span").textContent = "未运行";
        captureIndicator.querySelector(".status-dot").className = "status-dot inactive";
        captureStatusText.textContent = '点击"启动/重启截图"恢复 capture-analyze 任务';
    }

    const summarizerIndicator = document.getElementById("summarizer-status-indicator");
    const summarizerStatusText = document.getElementById("summarizer-status-text");
    if (cron.installed) {
        summarizerIndicator.querySelector("span").textContent = "运行中";
        summarizerIndicator.querySelector(".status-dot").className = "status-dot active";
        summarizerStatusText.textContent = `${(cron.jobs || []).length} 个定时任务`;
    } else {
        summarizerIndicator.querySelector("span").textContent = "未运行";
        summarizerIndicator.querySelector(".status-dot").className = "status-dot inactive";
        summarizerStatusText.textContent = '点击"启动/重启汇总"恢复 summary-* 任务';
    }

    const jobsContainer = document.getElementById("service-jobs");
    const allJobs = [];

    if (tmux.running) {
        const nextRun = new Date(Date.now() + intervalSeconds * 1000);
        allJobs.push({
            service: "截图 (Tmux)",
            time: `每 ${intervalMinutes} 分钟`,
            nextRun: nextRun.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
            desc: "capture-analyze",
        });
    }

    (cron.jobs || []).forEach((job) => {
        const parts = job.trim().split(/\s+/);
        if (parts.length < 6) return;
        const cronExpr = parts.slice(0, 5).join(" ");
        const cmd = parts.slice(5).join(" ");
        const nextRun = getNextRunTime(cronExpr);
        allJobs.push({
            service: "Cron",
            time: cronExpr,
            nextRun: nextRun
                ? nextRun.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
                : "-",
            desc: cmd,
        });
    });

    if (allJobs.length === 0) {
        jobsContainer.innerHTML = '<div class="loading">暂无运行中的服务</div>';
    } else {
        jobsContainer.innerHTML = allJobs
            .map(
                (j) => `
            <div class="service-job">
                <span class="job-service">${j.service}</span>
                <span class="job-time">${j.time}</span>
                <span class="job-desc">${j.desc}</span>
                <span class="job-next-run">下次: ${j.nextRun}</span>
            </div>`
            )
            .join("");
    }

    document.getElementById("today-records").textContent = Array.isArray(records) ? `${records.length} 条` : "0 条";
}

async function loadTaskSettingsView() {
    const tasks = await loadTasks();
    const select = document.getElementById("settings-task-id");
    const current = select.value;

    if (!tasks.length) {
        select.innerHTML = "<option value=''>暂无任务</option>";
        return;
    }

    select.innerHTML = tasks
        .map((t) => `<option value="${t.metadata.id}">${t.metadata.id}</option>`)
        .join("");

    const preferred = tasks.some((t) => t.metadata.id === current) ? current : tasks[0].metadata.id;
    select.value = preferred;
    await loadTaskSettings(preferred);
}

function setInputValue(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = value == null ? "" : String(value);
}

function setCheckboxValue(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    el.checked = !!value;
}

async function loadTaskSettings(taskId) {
    const res = await apiCall(`/api/tasks/${taskId}/settings`);
    if (!res || !res.success) {
        showToast(res?.error || "加载任务配置失败", "error");
        return;
    }

    const s = res.settings || {};
    const schedule = s.schedule || {};
    const input = s.input || {};
    const execution = s.execution || {};
    const modeConfig = s.modeConfig || {};
    const agent = modeConfig.agent || {};
    const llm = modeConfig.llm || {};
    const output = s.output || {};
    const logging = s.logging || {};

    setInputValue("settings-mode", s.mode || "agent");
    setInputValue("settings-run-backend", s.runBackend || "cron");

    setInputValue("settings-cron", schedule.cron || "");
    setInputValue("settings-interval", schedule.intervalSeconds || "");
    setInputValue("settings-timezone", schedule.timezone || "");
    setInputValue("settings-max-concurrency", schedule.maxConcurrency || 1);
    setInputValue("settings-misfire", schedule.misfirePolicy || "run_once");

    setInputValue("settings-prompt", input.prompt || "");
    setInputValue("settings-timeout", execution.timeoutSeconds || 600);
    setInputValue("settings-workdir", execution.workingDirectory || ".");

    setInputValue("settings-agent-provider", agent.provider || "codex_cli");
    setInputValue("settings-agent-model", agent.model || "gpt-5-codex");
    setInputValue("settings-agent-sandbox", agent.sandboxMode || "workspace-write");
    setInputValue("settings-agent-system", agent.systemPrompt || "");

    setInputValue("settings-llm-provider", llm.provider || "kimi_openai_compat");
    setInputValue("settings-llm-model", llm.model || "kimi-k2.5");
    setInputValue("settings-llm-base", llm.apiBase || "");
    setInputValue("settings-llm-auth", llm.authRef || "");
    setInputValue("settings-llm-temp", llm.temperature == null ? "" : llm.temperature);
    setInputValue("settings-llm-max-tokens", llm.maxTokens || "");

    setInputValue("settings-output-sink", output.sink || "file");
    setInputValue("settings-output-format", output.format || "text");
    setInputValue("settings-output-path", output.pathTemplate || "");

    setInputValue("settings-log-path", logging.eventJsonlPath || "");
    setCheckboxValue("settings-log-prompt", logging.savePrompt);
    setCheckboxValue("settings-log-toolcalls", logging.saveToolCalls);
    setCheckboxValue("settings-log-stdout", logging.saveStdout);
    setCheckboxValue("settings-log-stderr", logging.saveStderr);
}

function getNumber(id, fallback = null) {
    const raw = document.getElementById(id).value;
    if (raw === "") return fallback;
    const n = Number(raw);
    return Number.isFinite(n) ? n : fallback;
}

async function saveTaskSettings() {
    const taskId = document.getElementById("settings-task-id").value;
    if (!taskId) {
        showToast("请选择任务", "error");
        return;
    }

    const payload = {
        mode: document.getElementById("settings-mode").value,
        runBackend: document.getElementById("settings-run-backend").value,
        schedule: {
            cron: document.getElementById("settings-cron").value,
            intervalSeconds: getNumber("settings-interval", null),
            timezone: document.getElementById("settings-timezone").value,
            maxConcurrency: getNumber("settings-max-concurrency", 1),
            misfirePolicy: document.getElementById("settings-misfire").value,
        },
        input: {
            prompt: document.getElementById("settings-prompt").value,
        },
        execution: {
            timeoutSeconds: getNumber("settings-timeout", 600),
            workingDirectory: document.getElementById("settings-workdir").value,
        },
        modeConfig: {
            agent: {
                provider: document.getElementById("settings-agent-provider").value,
                model: document.getElementById("settings-agent-model").value,
                sandboxMode: document.getElementById("settings-agent-sandbox").value,
                systemPrompt: document.getElementById("settings-agent-system").value,
            },
            llm: {
                provider: document.getElementById("settings-llm-provider").value,
                model: document.getElementById("settings-llm-model").value,
                apiBase: document.getElementById("settings-llm-base").value,
                authRef: document.getElementById("settings-llm-auth").value,
                temperature: getNumber("settings-llm-temp", 0.2),
                maxTokens: getNumber("settings-llm-max-tokens", 4000),
            },
        },
        output: {
            sink: document.getElementById("settings-output-sink").value,
            format: document.getElementById("settings-output-format").value,
            pathTemplate: document.getElementById("settings-output-path").value,
        },
        logging: {
            eventJsonlPath: document.getElementById("settings-log-path").value,
            savePrompt: document.getElementById("settings-log-prompt").checked,
            saveToolCalls: document.getElementById("settings-log-toolcalls").checked,
            saveStdout: document.getElementById("settings-log-stdout").checked,
            saveStderr: document.getElementById("settings-log-stderr").checked,
        },
    };

    if (payload.schedule.intervalSeconds === null) delete payload.schedule.intervalSeconds;

    const res = await apiCall(`/api/tasks/${taskId}/settings`, {
        method: "PUT",
        body: JSON.stringify(payload),
    });

    if (!res || !res.success) {
        showToast(`保存失败: ${res?.error || "未知错误"}`, "error");
        return;
    }

    showToast("任务配置已保存", "success");
    await apiCall("/api/tasks/sync", { method: "POST" });
    await loadTasks();
}

async function loadJournalFiles() {
    const filesContainer = document.getElementById("journal-files");
    const detailContainer = document.getElementById("journal-detail");

    filesContainer.innerHTML = '<div class="loading">加载中...</div>';
    detailContainer.innerHTML = '<div class="journal-detail-placeholder"><p>选择一个文件查看内容</p></div>';

    const files = await apiCall(`/api/journal/${currentJournalPeriod}`);
    if (!files || files.length === 0) {
        filesContainer.innerHTML = '<div class="loading">暂无文件</div>';
        return;
    }

    filesContainer.innerHTML = files
        .map((file) => `<div class="journal-file" data-filename="${file.name}"><span class="journal-file-name">${file.name}</span></div>`)
        .join("");

    document.querySelectorAll(".journal-file").forEach((fileEl) => {
        fileEl.addEventListener("click", () => {
            document.querySelectorAll(".journal-file").forEach((f) => f.classList.remove("active"));
            fileEl.classList.add("active");
            loadJournalContent(fileEl.dataset.filename);
        });
    });
}

async function loadJournalContent(filename) {
    const detailContainer = document.getElementById("journal-detail");
    const result = await apiCall(`/api/journal/${currentJournalPeriod}/${filename}`);

    if (!result || !result.content) {
        detailContainer.innerHTML = '<div class="journal-detail-placeholder"><p>无法加载内容</p></div>';
        return;
    }

    detailContainer.innerHTML = `<div class="journal-content-markdown">${renderMarkdown(result.content)}</div>`;
}

async function loadLogs() {
    const dates = await apiCall("/api/records/dates");
    const dateSelect = document.getElementById("log-date");

    if (dates && dates.length > 0) {
        dateSelect.innerHTML = dates.map((d) => `<option value="${d}">${d}</option>`).join("");
    } else {
        dateSelect.innerHTML = '<option value="">暂无数据</option>';
    }

    loadLogsForDate(dateSelect.value);
}

async function loadLogsForDate(date) {
    const logsList = document.getElementById("logs-list");
    if (!date) {
        logsList.innerHTML = '<div class="log-empty">请选择日期</div>';
        return;
    }

    const records = await apiCall(`/api/records?date=${date}&limit=100`);
    if (!records || records.length === 0) {
        logsList.innerHTML = '<div class="log-empty">当日无记录</div>';
        return;
    }

    logsList.innerHTML = records
        .map((record) => {
            const time = new Date(record.timestamp).toLocaleString("zh-CN", {
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
            });
            return `<div class="log-item"><span class="log-time">${time}</span><span class="log-content">${escapeHtml(record.description)}</span></div>`;
        })
        .join("");
}

async function loadMessages() {
    const messagesList = document.getElementById("messages-list");
    messagesList.innerHTML = '<div class="loading">加载中...</div>';

    const messages = await apiCall("/api/messages?limit=100");
    if (!messages || messages.length === 0) {
        messagesList.innerHTML = '<div class="messages-empty">暂无消息</div>';
        return;
    }

    messagesList.innerHTML = messages
        .map((msg) => {
            const time = new Date(msg.timestamp).toLocaleString("zh-CN", {
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
            });
            const type = msg.type || "info";
            return `<div class="message-item msg-info"><span class="msg-time">${time}</span><span class="msg-type">${type}</span><span class="msg-period">${msg.period || "-"}</span></div>`;
        })
        .join("");
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

function renderInlineMarkdown(text) {
    return escapeHtml(text)
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/\*(.+?)\*/g, "<em>$1</em>")
        .replace(/`(.+?)`/g, "<code>$1</code>");
}

function renderMarkdown(content) {
    if (!content) return "<p>暂无内容</p>";
    const lines = content.replace(/\r\n/g, "\n").trim().split("\n");
    const html = [];
    const paragraph = [];

    const flush = () => {
        if (!paragraph.length) return;
        html.push(`<p>${paragraph.map(renderInlineMarkdown).join("<br>")}</p>`);
        paragraph.length = 0;
    };

    lines.forEach((line) => {
        const t = line.trim();
        if (!t) {
            flush();
            return;
        }
        const h = t.match(/^(#{1,6})\s+(.+)$/);
        if (h) {
            flush();
            const level = h[1].length;
            html.push(`<h${level}>${renderInlineMarkdown(h[2])}</h${level}>`);
            return;
        }
        if (/^-{3,}$/.test(t)) {
            flush();
            html.push("<hr>");
            return;
        }
        paragraph.push(t);
    });
    flush();
    return html.join("");
}

document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".nav-item").forEach((item) => {
        item.addEventListener("click", () => switchView(item.dataset.view));
    });

    document.getElementById("btn-start-capture").addEventListener("click", async () => {
        showToast("正在启动截图服务...", "info");
        const resumeResult = await apiCall("/api/tasks/capture-analyze/resume", { method: "POST" });
        const syncResult = await apiCall("/api/tasks/sync", { method: "POST" });
        if (resumeResult && resumeResult.success && syncResult && syncResult.success) {
            showToast("截图服务已启动", "success");
            loadStatus();
        } else {
            showToast("启动失败", "error");
        }
    });

    document.getElementById("btn-stop-capture").addEventListener("click", async () => {
        if (!confirm("确定要停止截图服务吗？")) return;
        showToast("正在停止截图服务...", "info");
        const pauseResult = await apiCall("/api/tasks/capture-analyze/pause", { method: "POST" });
        const syncResult = await apiCall("/api/tasks/sync", { method: "POST" });
        if (pauseResult && pauseResult.success && syncResult && syncResult.success) {
            showToast("截图服务已停止", "success");
            loadStatus();
        } else {
            showToast("停止失败", "error");
        }
    });

    document.getElementById("btn-start-summarizer").addEventListener("click", async () => {
        const ids = ["summary-daily", "summary-weekly", "summary-monthly"];
        let ok = true;
        for (const id of ids) {
            const r = await apiCall(`/api/tasks/${id}/resume`, { method: "POST" });
            if (!r || !r.success) ok = false;
        }
        const syncResult = await apiCall("/api/tasks/sync", { method: "POST" });
        if (ok && syncResult && syncResult.success) {
            showToast("汇总服务已启动", "success");
            loadStatus();
        } else {
            showToast("启动失败", "error");
        }
    });

    document.getElementById("btn-stop-summarizer").addEventListener("click", async () => {
        if (!confirm("确定要停止汇总服务吗？")) return;
        const ids = ["summary-daily", "summary-weekly", "summary-monthly"];
        let ok = true;
        for (const id of ids) {
            const r = await apiCall(`/api/tasks/${id}/pause`, { method: "POST" });
            if (!r || !r.success) ok = false;
        }
        const syncResult = await apiCall("/api/tasks/sync", { method: "POST" });
        if (ok && syncResult && syncResult.success) {
            showToast("汇总服务已停止", "success");
            loadStatus();
        } else {
            showToast("停止失败", "error");
        }
    });

    document.getElementById("settings-task-id").addEventListener("change", (e) => loadTaskSettings(e.target.value));
    document.getElementById("btn-save-task-settings").addEventListener("click", saveTaskSettings);

    document.querySelectorAll(".journal-tab").forEach((tab) => {
        tab.addEventListener("click", () => {
            document.querySelectorAll(".journal-tab").forEach((t) => t.classList.remove("active"));
            tab.classList.add("active");
            currentJournalPeriod = tab.dataset.period;
            loadJournalFiles();
        });
    });

    document.getElementById("log-date").addEventListener("change", (e) => loadLogsForDate(e.target.value));
    document.getElementById("btn-refresh-logs").addEventListener("click", () => loadLogsForDate(document.getElementById("log-date").value));
    document.getElementById("btn-refresh-messages").addEventListener("click", loadMessages);

    loadStatus();
});
