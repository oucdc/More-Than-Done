const els = {
  apiKey: document.querySelector("#api-key"),
  baseUrl: document.querySelector("#base-url"),
  model: document.querySelector("#model"),
  workspace: document.querySelector("#workspace"),
  allowRead: document.querySelector("#allow-read"),
  allowWrite: document.querySelector("#allow-write"),
  allowShell: document.querySelector("#allow-shell"),
  confirmWrite: document.querySelector("#confirm-write"),
  confirmShell: document.querySelector("#confirm-shell"),
  clearKey: document.querySelector("#clear-key"),
  configForm: document.querySelector("#config-form"),
  saveConfig: document.querySelector("#save-config"),
  configNote: document.querySelector("#config-note"),
  status: document.querySelector("#agent-status"),
  workspaceLabel: document.querySelector("#workspace-label"),
  messages: document.querySelector("#messages"),
  approval: document.querySelector("#approval"),
  approvalTool: document.querySelector("#approval-tool"),
  approvalArgs: document.querySelector("#approval-args"),
  approve: document.querySelector("#approve"),
  reject: document.querySelector("#reject"),
  chatForm: document.querySelector("#chat-form"),
  message: document.querySelector("#message"),
  send: document.querySelector("#send"),
  cancel: document.querySelector("#cancel"),
  newConversation: document.querySelector("#new-conversation"),
  toastRegion: document.querySelector("#toast-region"),
};

const STATUS_LABELS = {
  idle: "空闲",
  running: "Agent 正在工作",
  waiting_approval: "等待你的批准",
  error: "需要处理错误",
};

const state = {
  config: null,
  snapshot: null,
  events: [],
  after: 0,
  polling: false,
  toolCards: new Map(),
};

function toast(message, isError = false) {
  const node = document.createElement("div");
  node.className = `toast${isError ? " error" : ""}`;
  node.textContent = message;
  els.toastRegion.append(node);
  window.setTimeout(() => node.remove(), 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  if (!response.ok) {
    throw new Error(body.error || `请求失败（${response.status}）`);
  }
  return body;
}

function setStatus(status) {
  els.status.textContent = STATUS_LABELS[status] || status;
  els.status.className = `agent-status ${status}`;
}

function configNote(config) {
  if (!config || !config.workspace) {
    els.configNote.textContent = "还没有可用配置。请填写 API Key 并保存。";
    return;
  }
  const keyText = !config.api_key_configured
    ? "未配置 API Key"
    : config.api_key_source === "config"
      ? "API Key 已保存在本机配置"
      : "使用 .env / 环境变量中的 API Key";
  els.configNote.innerHTML = "";
  const line = document.createElement("div");
  line.textContent = `${keyText}。工作区：${config.workspace}`;
  els.configNote.append(line);
}

function applyConfig(config) {
  state.config = config;
  if (!config) return;
  els.baseUrl.value = config.base_url || "";
  els.model.value = config.model || "";
  els.workspace.value = config.workspace || "";
  els.allowRead.checked = Boolean(config.allow_read);
  els.allowWrite.checked = Boolean(config.allow_write);
  els.allowShell.checked = Boolean(config.allow_shell);
  els.confirmWrite.checked = Boolean(config.confirm_write);
  els.confirmShell.checked = Boolean(config.confirm_shell);
  els.apiKey.value = "";
  els.apiKey.placeholder = config.api_key_configured
    ? "已配置，留空保持不变"
    : "输入 DeepSeek API Key";
  els.clearKey.checked = false;
  els.workspaceLabel.textContent = config.workspace || "尚未选择工作区";
  configNote(config);
}

async function loadConfig() {
  try {
    const result = await api("/api/agent/config");
    applyConfig(result.config);
  } catch (error) {
    els.configNote.textContent = error.message;
    els.status.textContent = "配置错误";
    els.status.className = "agent-status error";
  }
}

async function saveConfig(event) {
  event.preventDefault();
  els.saveConfig.disabled = true;
  try {
    const result = await api("/api/agent/config", {
      method: "POST",
      body: JSON.stringify({
        api_key: els.apiKey.value,
        clear_api_key: els.clearKey.checked,
        base_url: els.baseUrl.value,
        model: els.model.value,
        workspace: els.workspace.value,
        allow_read: els.allowRead.checked,
        allow_write: els.allowWrite.checked,
        allow_shell: els.allowShell.checked,
        confirm_write: els.confirmWrite.checked,
        confirm_shell: els.confirmShell.checked,
      }),
    });
    applyConfig(result.config);
    toast("设置已保存。");
    await poll();
  } catch (error) {
    toast(error.message, true);
  } finally {
    els.saveConfig.disabled = false;
  }
}

function pretty(value) {
  if (typeof value !== "string") {
    return JSON.stringify(value, null, 2);
  }
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function appendMessage(kind, text, label) {
  const node = document.createElement("div");
  node.className = `message ${kind}`;
  if (label) {
    const meta = document.createElement("span");
    meta.className = "message-label";
    meta.textContent = label;
    node.append(meta);
  }
  const body = document.createElement("span");
  body.textContent = text;
  node.append(body);
  els.messages.append(node);
  els.messages.scrollTop = els.messages.scrollHeight;
  return node;
}

function appendToolEvent(event) {
  if (event.kind === "tool_start") {
    const card = document.createElement("div");
    card.className = "tool-card";
    const title = document.createElement("div");
    title.className = "tool-title";
    title.textContent = `工具 · ${event.tool || "unknown"}`;
    const args = document.createElement("pre");
    args.textContent = pretty(event.arguments || {});
    card.append(title, args);
    els.messages.append(card);
    state.toolCards.set(event.tool, card);
    els.messages.scrollTop = els.messages.scrollHeight;
    return;
  }
  let card = state.toolCards.get(event.tool);
  if (!card) {
    card = document.createElement("div");
    card.className = "tool-card";
    const title = document.createElement("div");
    title.className = "tool-title";
    title.textContent = `工具结果 · ${event.tool || "unknown"}`;
    card.append(title);
    els.messages.append(card);
  }
  const result = document.createElement("pre");
  result.textContent = pretty(event.result || "");
  card.append(result);
  state.toolCards.delete(event.tool);
  els.messages.scrollTop = els.messages.scrollHeight;
}

function appendEvent(event) {
  if (!event || !event.kind) return;
  if (event.kind === "user") {
    appendMessage("user", event.text, "你");
  } else if (event.kind === "assistant") {
    appendMessage("assistant", event.text, "More Than Done");
  } else if (event.kind === "system") {
    appendMessage("system", event.text);
  } else if (event.kind === "error") {
    appendMessage("error", event.text, "错误");
  } else if (event.kind === "tool_start" || event.kind === "tool_result") {
    appendToolEvent(event);
  } else if (event.kind === "approval") {
    // Pending approval is rendered from snapshot.pending.
  }
}

function renderApproval(pending) {
  if (!pending) {
    els.approval.hidden = true;
    els.approvalTool.textContent = "";
    els.approvalArgs.textContent = "";
    return;
  }
  els.approval.hidden = false;
  els.approvalTool.textContent = `工具：${pending.tool}`;
  els.approvalArgs.textContent = pretty(pending.arguments || {});
}

function renderSnapshot(snapshot) {
  state.snapshot = snapshot;
  const status = snapshot.status || "idle";
  setStatus(status);
  const busy = status === "running" || status === "waiting_approval";
  const waitingApproval = status === "waiting_approval";
  els.send.disabled = busy;
  els.message.disabled = busy;
  els.cancel.disabled = !busy;
  els.newConversation.disabled = busy;
  els.saveConfig.disabled = busy;
  renderApproval(snapshot.pending);
  if (snapshot.config && snapshot.config.workspace) {
    els.workspaceLabel.textContent = snapshot.config.workspace;
  }
  if (snapshot.config_error) {
    els.configNote.textContent = snapshot.config_error;
  }
  if (waitingApproval) {
    els.message.placeholder = "请先处理上方的批准请求。";
  } else {
    els.message.placeholder = "告诉 Agent 要做什么，例如：读取 11.pdf 并总结目录结构";
  }
}

async function poll() {
  if (state.polling) return;
  state.polling = true;
  try {
    const snapshot = await api(`/api/agent/state?after=${state.after}`);
    const fresh = Array.isArray(snapshot.events) ? snapshot.events : [];
    const nextId = Number(snapshot.next_id) || 0;
    if (nextId < state.after) {
      state.events = [];
      state.after = 0;
      state.toolCards.clear();
      els.messages.replaceChildren();
    }
    fresh.forEach((event) => {
      state.events.push(event);
      appendEvent(event);
    });
    if (state.events.length > 500) state.events = state.events.slice(-500);
    state.after = nextId;
    renderSnapshot(snapshot);
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.polling = false;
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const text = els.message.value.trim();
  if (!text) return;
  els.send.disabled = true;
  try {
    await api("/api/agent/chat", {
      method: "POST",
      body: JSON.stringify({ message: text }),
    });
    els.message.value = "";
    await poll();
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (!state.snapshot || !["running", "waiting_approval"].includes(state.snapshot.status)) {
      els.send.disabled = false;
    }
  }
}

async function approve(allow) {
  const pending = state.snapshot?.pending;
  if (!pending) return;
  try {
    await api("/api/agent/approve", {
      method: "POST",
      body: JSON.stringify({ id: pending.id, allow }),
    });
    await poll();
  } catch (error) {
    toast(error.message, true);
  }
}

async function cancel() {
  try {
    await api("/api/agent/cancel", { method: "POST", body: "{}" });
    toast("已请求取消。");
    await poll();
  } catch (error) {
    toast(error.message, true);
  }
}

async function newConversation() {
  try {
    await api("/api/agent/new", { method: "POST", body: "{}" });
    state.events = [];
    state.after = 0;
    state.toolCards.clear();
    els.messages.replaceChildren();
    await poll();
  } catch (error) {
    toast(error.message, true);
  }
}

els.configForm.addEventListener("submit", saveConfig);
els.chatForm.addEventListener("submit", sendMessage);
els.message.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    els.chatForm.requestSubmit();
  }
});
els.approve.addEventListener("click", () => approve(true));
els.reject.addEventListener("click", () => approve(false));
els.cancel.addEventListener("click", cancel);
els.newConversation.addEventListener("click", newConversation);

async function boot() {
  await loadConfig();
  await poll();
  window.setInterval(poll, 700);
}

boot();
