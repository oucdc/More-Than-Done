const stageFlow = [
  { id: "goal_clarification", label: "目标确认" },
  { id: "baseline_assessment", label: "基础诊断" },
  { id: "concept_explanation", label: "概念讲解" },
  { id: "guided_practice", label: "引导练习" },
  { id: "feedback", label: "练习反馈" },
  { id: "independent_practice", label: "独立练习" },
  { id: "mastery_check", label: "掌握度检验" },
  { id: "summary", label: "学习总结" },
];

const stageAliases = {
  completed: "summary",
  exited: "goal_clarification",
};

const app = {
  after: 0,
  events: [],
  snapshot: null,
  polling: false,
  lastConnectionToast: 0,
};

const elements = {
  primaryAction: document.querySelector("#primary-action"),
  primaryActionLabel: document.querySelector("#primary-action-label"),
  sidebarGoal: document.querySelector("#sidebar-goal"),
  sidebarStage: document.querySelector("#sidebar-stage"),
  sidebarProgress: document.querySelector("#sidebar-progress-fill"),
  stageList: document.querySelector("#stage-list"),
  archiveList: document.querySelector("#archive-list"),
  messages: document.querySelector("#messages"),
  chatScroll: document.querySelector("#chat-scroll"),
  welcomeCard: document.querySelector("#welcome-card"),
  conversationTitle: document.querySelector("#conversation-title"),
  conversationSubtitle: document.querySelector("#conversation-subtitle"),
  breadcrumbStage: document.querySelector("#breadcrumb-stage"),
  stagePillLabel: document.querySelector("#stage-pill-label"),
  composerForm: document.querySelector("#composer-form"),
  messageInput: document.querySelector("#message-input"),
  sendButton: document.querySelector("#send-button"),
  quickActions: document.querySelector("#quick-actions"),
  thinkingIndicator: document.querySelector("#thinking-indicator"),
  newSessionConfirm: document.querySelector("#new-session-confirm"),
  confirmNewSession: document.querySelector("#confirm-new-session"),
  returnSession: document.querySelector("#return-session"),
  overviewGoal: document.querySelector("#overview-goal"),
  progressStatus: document.querySelector("#progress-status"),
  overviewProgress: document.querySelector("#overview-progress-fill"),
  overviewStage: document.querySelector("#overview-stage"),
  overviewPercent: document.querySelector("#overview-percent"),
  learnerLevel: document.querySelector("#learner-level"),
  attemptCount: document.querySelector("#attempt-count"),
  hintLevel: document.querySelector("#hint-level"),
  masteryCard: document.querySelector("#mastery-card"),
  masteryTitle: document.querySelector("#mastery-title"),
  masteryDescription: document.querySelector("#mastery-description"),
  exerciseCard: document.querySelector("#exercise-card"),
  activeExercise: document.querySelector("#active-exercise"),
  toastRegion: document.querySelector("#toast-region"),
};

function stageIndex(stage) {
  const normalized = stageAliases[stage] || stage;
  return stageFlow.findIndex((item) => item.id === normalized);
}

function showToast(message, isError = false) {
  const toast = document.createElement("div");
  toast.className = `toast${isError ? " error" : ""}`;
  toast.textContent = message;
  elements.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

function isSessionActive() {
  return ["running", "starting", "waiting"].includes(app.snapshot?.status);
}

async function request(path, options = {}) {
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

function appendMessage(event) {
  if (!event || typeof event.text !== "string") return;
  const row = document.createElement("article");
  row.className = `message-row ${event.kind || "assistant"}`;

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = event.kind === "user" ? "你" : "M";

  const body = document.createElement("div");
  body.className = "message-body";
  const meta = document.createElement("div");
  meta.className = "message-meta";
  const author = document.createElement("span");
  author.className = "message-author";
  author.textContent = event.kind === "user" ? "你" : event.kind === "system" ? "记录" : "More Than Done";
  const time = document.createElement("span");
  time.textContent = event.time || "";
  meta.append(author, time);

  const bubble = document.createElement("div");
  bubble.className = "message-bubble";
  if (event.kind === "user" && event.text === "") {
    bubble.textContent = "开始新会话";
  } else {
    bubble.textContent = event.text;
  }
  body.append(meta, bubble);
  row.append(avatar, body);
  elements.messages.append(row);
  elements.welcomeCard.hidden = app.events.some((item) => item.kind === "user");
}

function hasNewSessionPrompt() {
  const assistantEvents = app.events.filter((event) => event.kind === "assistant");
  const latestAssistant = assistantEvents[assistantEvents.length - 1];
  return Boolean(latestAssistant && latestAssistant.text.includes("按 Enter 开始新会话"));
}

function renderStages(state) {
  const completed = state.stage === "completed";
  const activeIndex = state.stage === "exited" ? -1 : stageIndex(state.stage);
  elements.stageList.replaceChildren();
  stageFlow.forEach((stage, index) => {
    const item = document.createElement("div");
    const done = completed || (activeIndex >= 0 && index < activeIndex);
    const active = !completed && index === activeIndex;
    item.className = `stage-item${active ? " active" : ""}${done ? " done" : ""}`;
    const node = document.createElement("span");
    node.className = "stage-node";
    node.textContent = done ? "✓" : String(index + 1).padStart(2, "0");
    const label = document.createElement("span");
    label.textContent = stage.label;
    item.append(node, label);
    if (active) {
      const activeTag = document.createElement("span");
      activeTag.className = "stage-state";
      activeTag.textContent = "当前";
      item.append(activeTag);
    }
    elements.stageList.append(item);
  });
  const percent = completed ? 100 : Math.max(0, Math.round((Math.max(activeIndex, 0) / 7) * 100));
  elements.sidebarProgress.style.width = `${percent}%`;
  elements.overviewProgress.style.width = `${percent}%`;
  elements.overviewPercent.textContent = `${percent}%`;
}

function renderAction(snapshot) {
  const state = snapshot.state;
  const active = snapshot.status === "running" || snapshot.status === "starting" || snapshot.status === "waiting";
  let label = "开始学习";
  if (active) label = snapshot.awaiting_input ? "等待你的输入" : "正在学习";
  else if (state.stage === "completed" || state.stage === "exited") label = "开始新会话";
  else if (snapshot.status === "error" || snapshot.status === "paused" || snapshot.status === "finished") label = "继续学习";
  elements.primaryActionLabel.textContent = label;
  elements.primaryAction.disabled = active;
  elements.primaryAction.classList.toggle("is-running", active);
}

function renderSnapshot(snapshot) {
  app.snapshot = snapshot;
  const state = snapshot.state;
  const stageName = state.stage_label || "目标确认";
  elements.sidebarGoal.textContent = state.goal || "正在准备学习";
  elements.sidebarStage.textContent = stageName;
  elements.breadcrumbStage.textContent = stageName;
  elements.stagePillLabel.textContent = stageName;
  elements.overviewStage.textContent = stageName;
  elements.overviewGoal.textContent = state.goal || "确认目标后，这里会显示本轮学习方向。";
  elements.learnerLevel.textContent = state.learner_level || "完成基础诊断后，会显示当前学习起点。";
  elements.progressStatus.textContent = state.progress_label || "尚未接触";
  elements.attemptCount.textContent = String(state.attempt_count || 0);
  elements.hintLevel.textContent = `L${state.hint_level || 0}`;
  elements.masteryCard.classList.toggle("verified", Boolean(state.mastery_verified));
  elements.masteryTitle.textContent = state.mastery_verified ? "掌握度已验证" : "掌握度待验证";
  elements.masteryDescription.textContent = state.mastery_verified
    ? "独立练习和掌握度检验均已满足要求。"
    : "完成无提示的独立练习后，可以进行掌握度检验。";
  elements.exerciseCard.hidden = !state.active_exercise;
  elements.activeExercise.textContent = state.active_exercise || "";
  elements.conversationTitle.textContent = state.goal || "从一个目标开始";
  elements.conversationSubtitle.textContent = state.goal
    ? "继续当前学习阶段，或使用下方快捷操作。"
    : "把你想学会的内容告诉我，我们一起一步步练习。";
  renderStages(state);
  renderAction(snapshot);

  const newSessionChoice = snapshot.awaiting_input && hasNewSessionPrompt();
  elements.newSessionConfirm.hidden = !newSessionChoice;
  elements.composerForm.hidden = newSessionChoice;
  elements.messageInput.disabled = !snapshot.awaiting_input || newSessionChoice;
  elements.sendButton.disabled = !snapshot.awaiting_input || newSessionChoice;
  elements.quickActions.querySelectorAll("button").forEach((button) => {
    button.disabled = !snapshot.awaiting_input || newSessionChoice;
  });
  document.querySelectorAll(".archive-restore").forEach((button) => {
    button.disabled = isSessionActive();
    button.title = button.disabled ? "请先退出当前会话，再恢复归档" : "恢复这条学习记录";
  });
  elements.thinkingIndicator.hidden = !(snapshot.status === "running" || snapshot.status === "starting");
}

async function refreshState() {
  if (app.polling) return;
  app.polling = true;
  try {
    const snapshot = await request(`/api/state?after=${app.after}`);
    const freshEvents = Array.isArray(snapshot.events) ? snapshot.events : [];
    const nextId = Number(snapshot.next_id) || 0;
    if (nextId < app.after) {
      app.events = [];
      elements.messages.replaceChildren();
      elements.welcomeCard.hidden = false;
    }
    freshEvents.forEach((event) => {
      app.events.push(event);
      appendMessage(event);
    });
    if (app.events.length > 1000) app.events = app.events.slice(-1000);
    app.after = nextId;
    renderSnapshot(snapshot);
    if (freshEvents.length) {
      elements.chatScroll.scrollTop = elements.chatScroll.scrollHeight;
    }
  } catch (error) {
    if (Date.now() - app.lastConnectionToast > 5000) {
      showToast(error.message || "无法连接本机服务。", true);
      app.lastConnectionToast = Date.now();
    }
  } finally {
    app.polling = false;
  }
}

async function refreshArchives() {
  try {
    const result = await request("/api/archives");
    const archives = Array.isArray(result.archives) ? result.archives : [];
    elements.archiveList.replaceChildren();
    if (!archives.length) {
      const empty = document.createElement("div");
      empty.className = "archive-empty";
      empty.textContent = "暂无归档";
      elements.archiveList.append(empty);
      return;
    }
    archives.forEach((archive) => {
      const row = document.createElement("div");
      row.className = "archive-row";
      const icon = document.createElement("div");
      icon.className = "archive-icon";
      icon.textContent = "◷";
      const copy = document.createElement("div");
      copy.className = "archive-copy";
      const goal = document.createElement("strong");
      goal.textContent = archive.goal || archive.label || "学习记录";
      const meta = document.createElement("span");
      meta.textContent = `${archive.stage_label || "归档"} · ${archive.time || ""}`;
      copy.append(goal, meta);
      const restore = document.createElement("button");
      restore.className = "archive-restore";
      restore.type = "button";
      restore.textContent = "恢复";
      restore.disabled = isSessionActive();
      restore.title = restore.disabled ? "请先退出当前会话，再恢复归档" : "恢复这条学习记录";
      restore.addEventListener("click", () => restoreArchive(archive));
      row.append(icon, copy, restore);
      elements.archiveList.append(row);
    });
  } catch (error) {
    showToast(error.message || "无法读取历史会话。", true);
  }
}

async function restoreArchive(archive) {
  const goal = archive.goal || archive.label || "这条学习记录";
  const accepted = window.confirm(`恢复“${goal}”？当前学习记录会先归档，然后再恢复所选记录。`);
  if (!accepted) return;
  try {
    await request("/api/restore", {
      method: "POST",
      body: JSON.stringify({ filename: archive.filename }),
    });
    app.after = 0;
    app.events = [];
    elements.messages.replaceChildren();
    elements.welcomeCard.hidden = true;
    await refreshArchives();
    await refreshState();
    showToast("归档已恢复。");
  } catch (error) {
    showToast(error.message || "恢复归档失败。", true);
  }
}

async function postAction(path, payload = {}) {
  try {
    await request(path, { method: "POST", body: JSON.stringify(payload) });
    await refreshState();
  } catch (error) {
    showToast(error.message || "操作失败。", true);
  }
}

async function submitText(text) {
  if (!app.snapshot?.awaiting_input) return;
  await postAction("/api/input", { text });
  elements.messageInput.value = "";
  resizeInput();
  elements.messageInput.focus();
}

function resizeInput() {
  elements.messageInput.style.height = "auto";
  elements.messageInput.style.height = `${Math.min(elements.messageInput.scrollHeight, 135)}px`;
}

elements.primaryAction.addEventListener("click", async () => {
  const snapshot = app.snapshot;
  if (!snapshot) return;
  const terminal = snapshot.state.stage === "completed" || snapshot.state.stage === "exited";
  if (snapshot.status === "idle") {
    await postAction("/api/start");
  } else if (terminal && snapshot.status === "finished") {
    await postAction("/api/new-session");
  } else {
    await postAction("/api/resume");
  }
});

elements.composerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = elements.messageInput.value.trim();
  if (!text) return;
  await submitText(text);
});

elements.messageInput.addEventListener("input", resizeInput);
elements.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.composerForm.requestSubmit();
  }
});

elements.quickActions.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-command]");
  if (button && !button.disabled) await submitText(button.dataset.command);
});

document.querySelector("#welcome-suggestions").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-suggestion]");
  if (button && app.snapshot?.awaiting_input) await submitText(button.dataset.suggestion);
});

elements.confirmNewSession.addEventListener("click", async () => {
  await submitText("");
  elements.newSessionConfirm.hidden = true;
});

elements.returnSession.addEventListener("click", async () => {
  await submitText("q");
  elements.newSessionConfirm.hidden = true;
});

document.querySelector("#refresh-archives").addEventListener("click", refreshArchives);
document.querySelector("#refresh-state").addEventListener("click", refreshState);
async function quitApplication() {
  if (!window.confirm("关闭 More Than Done 图形界面？已保存的学习进度会保留。")) return;
  try {
    await request("/api/shutdown", { method: "POST", body: "{}" });
    showToast("图形界面已关闭，可以关闭此浏览器标签页。请重新双击程序再次启动。");
  } catch (error) {
    showToast(error.message || "无法关闭图形界面。", true);
  }
}

document.querySelector("#quit-app").addEventListener("click", quitApplication);
document.querySelector("#quit-app-top").addEventListener("click", quitApplication);

async function boot() {
  await refreshArchives();
  await refreshState();
  if (app.snapshot?.status === "idle") {
    await postAction("/api/start");
  }
  window.setInterval(refreshState, 700);
  window.setInterval(refreshArchives, 12_000);
}

boot();
