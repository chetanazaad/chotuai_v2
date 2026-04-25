const API_BASE = "";

let currentTaskId = null;
let statusInterval = null;
let logInterval = null;


function $(id) {
    return document.getElementById(id);
}

async function apiCall(endpoint, options = {}) {
    try {
        const res = await fetch(API_BASE + endpoint, {
            ...options,
            headers: {
                "Content-Type": "application/json",
                ...options.headers,
            },
        });
        return await res.json();
    } catch (e) {
        console.error("API error:", e);
        return null;
    }
}

async function pollSystemStatus() {
    const status = await apiCall("/system/status");
    if (!status) return;

    const internet = $("internet-status");
    const ollama = $("ollama-status");
    const models = $("models-status");
    const tools = $("tools-status");

    setStatusDot(internet, status.internet.status === "ok");
    setStatusDot(ollama, status.ollama.status === "ok");

    const modelList = [];
    if (status.models.phi3) modelList.push("phi3");
    if (status.models.qwen) modelList.push("qwen");
    setStatusText(models, modelList.length ? modelList.join("+") + " ✓" : "None");

    const toolList = [];
    if (status.tools.browser) toolList.push("browser");
    if (status.tools.scraper) toolList.push("scraper");
    setStatusText(tools, toolList.length ? toolList.join("+") + " ✓" : "None");

    const input = $("task-input");
    const sendBtn = $("send-btn");
    const ready = status.ready;
    input.disabled = !ready;
    sendBtn.disabled = !ready;
    input.placeholder = ready ? "Describe your task..." : "System not ready...";
}

function setStatusDot(el, ok) {
    const dot = el.querySelector(".status-dot");
    dot.classList.remove("ok", "fail");
    dot.classList.add(ok ? "ok" : "fail");
}

function setStatusText(el, text) {
    const label = el.querySelector(".status-label");
    label.textContent = text;
}

async function loadTaskList() {
    const tasks = await apiCall("/task/list");
    const list = $("task-list");
    if (!tasks || !tasks.length) {
        list.innerHTML = '<div class="task-item"><div class="task-name" style="color: var(--fg-dim)">No tasks yet</div></div>';
        return;
    }

    list.innerHTML = tasks.map(t => `
        <div class="task-item ${t.task_id === currentTaskId ? "active" : ""}" onclick="selectTask('${t.task_id}')">
            <div class="task-name">${t.task_name || t.task_id}</div>
            <div class="task-meta">
                <span class="task-status ${t.status}">${t.status}</span>
                <span>${t.created_at || ""}</span>
            </div>
        </div>
    `).join("");
}

async function selectTask(taskId) {
    currentTaskId = taskId;
    await loadTaskList();

    const task = await apiCall(`/task/${taskId}`);
    if (!task) return;

    showChatView(task);
}

function showChatView(task) {
    $("welcome-screen").style.display = "none";
    $("chat-container").style.display = "block";
    $("output-section").style.display = "block";

    const messages = $("chat-messages");
    const taskDesc = task.state?.core_task?.description || "Task";
    messages.innerHTML = `
        <div class="message-bubble user">${taskDesc}</div>
    `;

    const steps = task.state?.todo_list || [];
    const stepCards = $("step-cards");
    stepCards.innerHTML = steps.map((step, i) => {
        const status = step.completed ? "completed" : (step.failed ? "failed" : "running");
        return `
            <div class="step-card">
                <div class="step-header">
                    <span class="step-number">Step ${i + 1}/${steps.length}</span>
                    <span class="step-status ${status}">${status}</span>
                </div>
                <div class="step-description">${step.description || ""}</div>
                <div class="step-source">${step.source || "[llm]"}</div>
            </div>
        `;
    }).join("");

    const files = task.output_files || [];
    const outputFiles = $("output-files");
    if (files.length) {
        outputFiles.innerHTML = files.map(f => `
            <span class="output-file" onclick="openPreview('${currentTaskId}', '${f.name}')">${f.name}</span>
        `).join("");
    } else {
        outputFiles.innerHTML = '<div style="color: var(--fg-dim)">No output files</div>';
    }
}

async function submitTask() {
    const input = $("task-input");
    const taskDesc = input.value.trim();
    if (!taskDesc) return;

    currentTaskId = null;
    const sendBtn = $("send-btn");
    input.disabled = true;
    sendBtn.disabled = true;

    $("welcome-screen").style.display = "none";
    $("chat-container").style.display = "block";

    const messages = $("chat-messages");
    messages.innerHTML = `
        <div class="message-bubble user">${taskDesc}</div>
        <div class="message-bubble system">Starting task...</div>
    `;

    const result = await apiCall("/task/new", {
        method: "POST",
        body: JSON.stringify({ task: taskDesc, auto_run: true }),
    });

    if (result && result.task_id) {
        currentTaskId = result.task_id;
        input.value = "";
        startLogPolling(currentTaskId);
    } else {
        messages.innerHTML += `<div class="message-bubble system" style="color: var(--accent-red)">Failed: ${result?.error || "Unknown error"}</div>`;
        input.disabled = false;
        sendBtn.disabled = false;
    }

    await loadTaskList();
}

async function pollTaskLogs() {
    if (!currentTaskId) return;

    const task = await apiCall(`/task/${currentTaskId}`);
    if (!task) return;

    showChatView(task);

    const status = task.status;
    if (status === "completed" || status === "failed") {
        clearInterval(logInterval);
        logInterval = null;

        const input = $("task-input");
        const sendBtn = $("send-btn");
        input.disabled = false;
        sendBtn.disabled = false;

        stopLogPolling();
    }
}

function startLogPolling(taskId) {
    if (logInterval) clearInterval(logInterval);
    logInterval = setInterval(pollTaskLogs, 1000);
}

function stopLogPolling() {
    if (logInterval) {
        clearInterval(logInterval);
        logInterval = null;
    }
}

function openPreview(taskId, filename) {
    const modal = $("preview-modal");
    const frame = $("preview-frame");
    const title = $("preview-filename");

    title.textContent = filename;
    frame.src = `/output/${taskId}/${filename}`;
    modal.style.display = "flex";
}

function closePreview() {
    $("preview-modal").style.display = "none";
    $("preview-frame").src = "";
}

$("new-task-btn").addEventListener("click", () => {
    $("task-input").focus();
});

$("send-btn").addEventListener("click", submitTask);

$("task-input").addEventListener("keypress", (e) => {
    if (e.key === "Enter") submitTask();
});

document.addEventListener("DOMContentLoaded", async () => {
    await pollSystemStatus();
    await loadTaskList();

    statusInterval = setInterval(pollSystemStatus, 10000);
});

window.selectTask = selectTask;
window.openPreview = openPreview;
window.closePreview = closePreview;