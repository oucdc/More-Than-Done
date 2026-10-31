const $ = (selector) => document.querySelector(selector);

const els = {
  newChat: $("#new-chat"),
  addProject: $("#add-project"),
  projectList: $("#project-list"),
  projectMenu: $("#project-context-menu"),
  renameProjectMenu: $("#rename-project-menu"), pinProjectMenu: $("#pin-project-menu"),
  relocateProjectMenu: $("#relocate-project-menu"), projectDialogTitle: $("#project-dialog-title"),
  pinProjectLabel: $("#pin-project-label"), archiveProjectMenu: $("#archive-project-menu"), deleteProjectMenu: $("#delete-project-menu"),
  renameProjectDialog: $("#rename-project-dialog"), renameProjectInput: $("#rename-project-input"), confirmRenameProject: $("#confirm-rename-project"),
  deleteProjectDialog: $("#delete-project-dialog"), deleteProjectName: $("#delete-project-name"), deleteProjectNote: $("#delete-project-note"), confirmDeleteProject: $("#confirm-delete-project"),
  conversationMenu: $("#conversation-context-menu"),
  deleteConversationMenu: $("#delete-conversation-menu"),
  renameConversationMenu: $("#rename-conversation-menu"),
  pinConversationMenu: $("#pin-conversation-menu"),
  pinConversationLabel: $("#pin-conversation-label"),
  archiveConversationMenu: $("#archive-conversation-menu"),
  moveConversationMenu: $("#move-conversation-menu"),
  renameConversationDialog: $("#rename-conversation-dialog"),
  renameConversationInput: $("#rename-conversation-input"),
  confirmRenameConversation: $("#confirm-rename-conversation"),
  moveConversationDialog: $("#move-conversation-dialog"),
  moveConversationSelect: $("#move-conversation-select"),
  confirmMoveConversation: $("#confirm-move-conversation"),
  openArchived: $("#open-archived"),
  archivedCount: $("#archived-count"),
  archivedDialog: $("#archived-conversations-dialog"),
  archivedList: $("#archived-conversations-list"),
  closeArchived: $("#close-archived"),
  deleteConversationDialog: $("#delete-conversation-dialog"),
  deleteConversationName: $("#delete-conversation-name"),
  confirmDeleteConversation: $("#confirm-delete-conversation"),
  projectName: $("#project-name"),
  projectPath: $("#project-path"),
  conversationTime: $("#conversation-time"),
  modelSelect: $("#model-select"),
  reasoningSelect: $("#reasoning-select"),
  permissionsButton: $("#permissions-button"),
  permissionSummary: $("#permission-summary"),
  permissionMenu: $("#permission-menu"),
  permissionMenuNote: $("#permission-menu-note"),
  settingsPermissionSummary: $("#settings-permission-summary"),
  runStatus: $("#run-status"),
  connectionStatus: $("#connection-status"),
  jumpLatest: $("#jump-latest"),
  environmentShell: $("#environment-shell"),
  environmentPython: $("#environment-python"),
  environmentNote: $("#environment-note"),
  apiStatus: $("#api-status"),
  chatViewport: $("#chat-viewport"),
  chat: $("#chat"),
  welcome: $("#welcome"),
  emptyProjectState: $("#empty-project-state"),
  createFirstProject: $("#create-first-project"),
  messages: $("#messages"),
  approval: $("#approval"),
  approvalTool: $("#approval-tool"),
  approvalArgs: $("#approval-args"),
  approvalContext: $("#approval-context"),
  approvalReason: $("#approval-reason"),
  approvalRaw: $("#approval-raw"),
  approve: $("#approve"),
  reject: $("#reject"),
  chatForm: $("#chat-form"),
  message: $("#message"),
  send: $("#send"),
  cancel: $("#cancel"),
  openSettings: $("#open-settings"),
  settingsDialog: $("#settings-dialog"),
  settingsForm: $("#settings-form"),
  settingsApiKey: $("#settings-api-key"),
  settingsBaseUrl: $("#settings-base-url"),
  settingsWorkspace: $("#settings-workspace"),
  settingClearKey: $("#setting-clear-key"),
  settingsNote: $("#settings-note"),
  saveSettings: $("#save-settings"),
  projectDialog: $("#project-dialog"),
  projectForm: $("#project-form"),
  projectNameInput: $("#project-name-input"),
  projectWorkspaceInput: $("#project-workspace-input"),
  createProject: $("#create-project"),
  chooseWorkspace: $("#choose-workspace"),
  toggleSidebar: $("#toggle-sidebar"),
  toggleLesson: $("#toggle-lesson"),
  toastStack: $("#toast-stack"),
  submitAttempt: $("#submit-attempt"),
  lessonMode: $("#lesson-mode"),
  lessonEmpty: $("#lesson-empty"),
  lessonContent: $("#lesson-content"),
  lessonGoal: $("#lesson-goal"),
  lessonProgressText: $("#lesson-progress-text"),
  lessonProgress: $("#lesson-progress"),
  lessonSteps: $("#lesson-steps"),
  currentStep: $("#current-step"),
  stepTitle: $("#step-title"),
  stepPosition: $("#step-position"),
  stepTransitionNote: $("#step-transition-note"),
  reviewPendingAttempt: $("#review-pending-attempt"),
  stepTask: $("#step-task"),
  stepCriteria: $("#step-criteria"),
  assistanceNote: $("#assistance-note"),
  nextStep: $("#next-step"),
  retryLearning: $("#retry-learning"),
  learningFeedback: $("#learning-feedback"),
  lessonSummary: $("#lesson-summary"),
  composerNote: $("#composer-note"),
};

const STATUS_LABELS = {
  idle: "等待你的下一步",
  running: "导师回应中",
  waiting_approval: "等待批准",
  error: "错误",
};

const ui = {
  data: null,
  retiredEpochs: new Set(),
  reviewingAttempt: false,
  syncing: false,
  lastChatKey: "",
  polling: false,
  toolCards: new Map(),
  pollTimer: null,
  lastLessonKey: "",
  sidebarKey: "",
  expanded: new Map(),
  chatStructure: "",
  renderedEvents: new Map(),
  eventSource: null,
  contextProjectId: null, editingProjectId: null, deleteProjectId: null, deletingProject: false,
  contextConversationId: null,
  deleteConversationId: null,
  deleting: false,
  editingConversationId: null,
  drafts: new Map(),
  pendingSend: false,
  composing: false,
  unread: false,
  streamConnected: false,
  toolDetails: new Set(),
};

function scrollRegion(node, label) {
  node.tabIndex = 0;
  node.setAttribute("role", "region");
  node.setAttribute("aria-label", label);
  return node;
}

function markdown(node, value) {
  // Streaming replaces Markdown nodes. Retain the reader's position inside
  // each code/table region independently from the outer conversation scroll.
  const savedScroll = [".code-block pre", ".table-scroll"].map(selector => ({
    selector,
    positions: [...node.querySelectorAll(selector)].map(region => ({
      left: region.scrollLeft, top: region.scrollTop,
      focused: document.activeElement === region,
    })),
  }));
  const source = String(value || "");
  if (!window.marked || !window.DOMPurify) {
    node.textContent = source;
    return;
  }
  node.innerHTML = DOMPurify.sanitize(marked.parse(source, { breaks: true }), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["img", "style", "form", "input", "iframe", "object", "video", "audio"],
    FORBID_ATTR: ["style"],
  });
  for (const link of node.querySelectorAll("a")) {
    link.target = "_blank";
    link.rel = "noopener noreferrer";
  }
  for (const block of node.querySelectorAll("pre")) {
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "copy-code";
    copy.textContent = "复制代码";
    const code = block.querySelector("code")?.textContent || block.textContent;
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(code); copy.textContent = "已复制"; }
      catch { toast("当前窗口无法访问剪贴板，请手动选择代码。", true); }
    });
    const wrapper = document.createElement("div");
    wrapper.className = "code-block";
    const toolbar = document.createElement("div");
    toolbar.className = "code-block-toolbar";
    const label = document.createElement("span");
    label.textContent = "代码 / 文本";
    toolbar.append(label, copy);
    block.before(wrapper);
    wrapper.append(toolbar, block);
    scrollRegion(block, "代码内容，可横向或纵向滚动");
  }
  for (const table of node.querySelectorAll("table")) {
    const wrapper = scrollRegion(document.createElement("div"), "表格内容，可横向或纵向滚动");
    wrapper.className = "table-scroll";
    table.before(wrapper);
    wrapper.append(table);
  }
  for (const group of savedScroll) {
    const regions = node.querySelectorAll(group.selector);
    group.positions.forEach((position, index) => {
      const region = regions[index];
      if (!region) return;
      region.scrollLeft = position.left;
      region.scrollTop = position.top;
      if (position.focused) region.focus({preventScroll: true});
    });
  }
}

function toast(message, isError = false) {
  const node = document.createElement("div");
  node.className = `toast${isError ? " error" : ""}`;
  node.textContent = message;
  els.toastStack.append(node);
  window.setTimeout(() => node.remove(), 4200);
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
  } catch { throw new Error("无法连接本机服务，请检查桌面程序是否仍在运行。你的输入已保留。"); }
  let body = {};
  try {
    body = await response.json();
  } catch {
    throw new Error(`本机服务返回了无法读取的内容（${response.status}）。你的输入已保留。`);
  }
  if (!response.ok) {
    throw new Error(body.error || `请求失败（${response.status}）`);
  }
  return body;
}

function shortTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function pretty(value) {
  if (typeof value !== "string") return JSON.stringify(value, null, 2);
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function isBusy(status) {
  return status === "running" || status === "waiting_approval";
}

function resizeComposer() {
  els.message.style.height = "auto";
  const height = Math.min(180, Math.max(70, els.message.scrollHeight));
  els.message.style.height = `${height}px`;
  els.message.style.overflowY = els.message.scrollHeight > height ? "auto" : "hidden";
}

function setConnection(status, text) {
  els.connectionStatus.className = `connection-status ${status}`;
  els.connectionStatus.textContent = text;
  els.connectionStatus.title = status === "online" ? "与本机工作台的实时连接正常" : "连接状态与导师任务状态分别显示；恢复连接不会重新执行命令。";
}

function updateScrollPosition(follow = false) {
  if (follow) els.chatViewport.scrollTop = els.chatViewport.scrollHeight;
  const nearBottom = els.chatViewport.scrollHeight - els.chatViewport.scrollTop - els.chatViewport.clientHeight < 100;
  if (nearBottom) ui.unread = false;
  els.jumpLatest.hidden = !ui.unread || nearBottom;
}

function renderEnvironment(environment = {}) {
  if (ui.data?.workspace_error) {
    els.environmentShell.textContent = "工作区不可用";
    els.environmentPython.textContent = "重新选择文件夹后检测";
    els.environmentNote.textContent = ui.data.workspace_error;
    return;
  }
  if (!ui.data?.active_project) {
    els.environmentShell.textContent = "选择项目后显示";
    els.environmentPython.textContent = "选择项目后检测";
    els.environmentNote.textContent = "当前未选择工作区；创建或恢复项目后，将显示该项目的命令运行环境。";
    return;
  }
  environment ||= {};
  els.environmentShell.textContent = environment.shell || "暂未提供";
  els.environmentPython.textContent = environment.python || "未检测到可用 Python";
  const source = ({workspace: "项目虚拟环境", active_environment: "当前环境", path: "系统命令路径", registry: "系统安装记录", known_installation: "常见安装位置", application_runtime: "应用运行环境"})[environment.python_source] || "本机环境";
  els.environmentNote.textContent = environment.python
    ? `检测位置：${source}。新建虚拟环境后，下次执行时会重新检查。`
    : "可在工作区创建 .venv，或安装 Python 后重新启动应用。未检测到不代表电脑上完全没有安装。";
}

/* ---------- state application ---------- */

function applyState(data) {
  if (ui.retiredEpochs.has(data.state_epoch)) return;
  if (ui.data?.state_epoch === data.state_epoch && (data.version ?? 0) < (ui.data?.version ?? 0)) return;
  if (ui.data?.state_epoch && ui.data.state_epoch !== data.state_epoch) ui.retiredEpochs.add(ui.data.state_epoch);
  const previousId = ui.data?.active_conversation_id;
  if (previousId !== data.active_conversation_id) {
    if (previousId) ui.drafts.set(previousId, els.message.value);
    els.message.value = ui.drafts.get(data.active_conversation_id) || "";
    resizeComposer();
  }
  ui.data = data;
  const config = data.config || {};
  const busy = isBusy(data.status);
  if (busy || (ui.contextConversationId && !data.conversations?.some(item => item.id === ui.contextConversationId))) closeConversationMenu();
  if (busy || (ui.contextProjectId && !data.projects?.some(item => item.id === ui.contextProjectId))) closeProjectMenu();
  els.confirmDeleteConversation.disabled = busy || ui.deleting;
  els.confirmDeleteProject.disabled = busy || ui.deletingProject;
  els.confirmRenameProject.disabled = busy;
  if (els.deleteConversationDialog.open && !data.conversations?.some(item => item.id === ui.deleteConversationId)) {
    els.deleteConversationDialog.close();
  }
  if (els.deleteProjectDialog.open && !data.projects?.some(item => item.id === ui.deleteProjectId)) els.deleteProjectDialog.close();
  if (els.renameProjectDialog.open && !data.projects?.some(item => item.id === ui.editingProjectId)) els.renameProjectDialog.close();

  renderSidebar(data, busy);
  renderTopbar(data, config, busy);
  renderChat(data);
  const hasProject = Boolean(data.active_project);
  document.body.classList.toggle("no-active-project", !hasProject);
  els.emptyProjectState.hidden = hasProject;
  els.createFirstProject.disabled = busy;
  for (const button of document.querySelectorAll(".welcome-chips button")) button.disabled = busy || !hasProject;
  renderApproval(data.pending);
  renderStatus(data, busy);
  renderConfigState(config);
  renderLesson(data.lesson, busy);
  renderArchived(data, busy);
  renderEnvironment(data.environment);
  if (!els.settingsDialog.open) {
    els.settingsWorkspace.textContent = data.active_project?.workspace || "未选择";
  }
}

function renderSidebar(data, busy) {
  const key = JSON.stringify([data.projects, data.conversations, data.active_project_id, data.active_conversation_id, busy, [...ui.expanded]]);
  if (key === ui.sidebarKey) return;
  ui.sidebarKey = key;
  els.projectList.replaceChildren();
  const projects = Array.isArray(data.projects) ? data.projects : [];
  const conversations = Array.isArray(data.conversations) ? data.conversations : [];
  if (!projects.length) {
    const empty = document.createElement("p");
    empty.className = "side-empty project-empty-note";
    empty.textContent = "暂无项目，点击 ＋ 创建。";
    els.projectList.append(empty);
  }
  for (const project of [...projects].sort((a, b) => Number(b.pinned) - Number(a.pinned))) {
    const group = document.createElement("section");
    group.className = "project-group";
    group.dataset.projectId = project.id;
    const expanded = ui.expanded.get(project.id) ?? true;
    const row = document.createElement("div");
    row.className = "project-row";
    const button = document.createElement("button");
    button.type = "button";
    button.className = `project-item${project.id === data.active_project_id ? " active" : ""}`;
    button.disabled = busy;
    button.title = project.workspace;
    button.dataset.projectId = project.id;
    button.setAttribute("aria-expanded", String(expanded));
    const icon = document.createElement("span");
    icon.className = "folder-icon";
    icon.textContent = expanded ? "▾" : "▸";
    const copy = document.createElement("span");
    copy.className = "project-item-copy";
    const name = document.createElement("span");
    name.className = "project-item-name";
    name.textContent = project.name;
    copy.append(name);
    button.append(icon, copy);
    if (project.pinned) { const pin = document.createElement("span"); pin.className = "conversation-pin"; pin.textContent = "⌖"; pin.setAttribute("aria-hidden", "true"); button.append(pin); }
    button.addEventListener("contextmenu", event => { event.preventDefault(); openProjectMenu(project.id, event.clientX, event.clientY); });
    button.addEventListener("keydown", event => { if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) { event.preventDefault(); const rect = button.getBoundingClientRect(); openProjectMenu(project.id, rect.left, rect.bottom); } });
    button.addEventListener("click", () => {
      ui.expanded.set(project.id, project.id === ui.data.active_project_id ? !expanded : true);
      if (project.id !== ui.data.active_project_id) selectProject(project.id);
      else renderSidebar(ui.data, isBusy(ui.data.status));
    });
    const add = document.createElement("button");
    add.type = "button";
    add.className = "icon-button project-add-chat";
    add.title = `在 ${project.name} 中新建对话`;
    add.setAttribute("aria-label", add.title);
    add.textContent = "＋";
    add.disabled = busy;
    add.addEventListener("click", () => newConversation(project.id));
    row.append(button, add);
    group.append(row);
    const list = document.createElement("div");
    list.className = "conversation-list";
    list.hidden = !expanded;
    for (const conversation of conversations.filter(chat => chat.project_id === project.id).sort((a, b) => Number(b.pinned) - Number(a.pinned))) {
      const chat = document.createElement("button");
      chat.type = "button";
      chat.className = `conversation-item${conversation.id === data.active_conversation_id ? " active" : ""}`;
      chat.disabled = busy;
      chat.title = conversation.title || "新对话";
      chat.dataset.conversationId = conversation.id;
      if (conversation.id === data.active_conversation_id) chat.setAttribute("aria-current", "page");
      const title = document.createElement("span");
      title.className = "conversation-title";
      title.textContent = conversation.title || "新对话";
      if (conversation.pinned) {
        const pin = document.createElement("span");
        pin.className = "conversation-pin";
        pin.setAttribute("aria-hidden", "true");
        pin.textContent = "⌖";
        chat.append(pin);
      }
      chat.append(title);
      chat.addEventListener("click", () => selectConversation(conversation.id));
      chat.addEventListener("contextmenu", event => {
        event.preventDefault();
        openConversationMenu(conversation.id, event.clientX, event.clientY);
      });
      chat.addEventListener("keydown", event => {
        if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
          event.preventDefault();
          const rect = chat.getBoundingClientRect();
          openConversationMenu(conversation.id, rect.left + 12, rect.bottom);
        }
      });
      list.append(chat);
    }
    group.append(list);
    els.projectList.append(group);
  }
  els.newChat.disabled = busy;
  els.addProject.disabled = busy;
}

function renderTopbar(data, config, busy) {
  const project = data.active_project || {};
  const hasProject = Boolean(data.active_project);
  els.projectName.textContent = project.name || "暂无项目";
  els.projectPath.textContent = project.workspace || "";
  els.conversationTime.textContent = data.active_conversation?.updated_at
    ? shortTime(data.active_conversation.updated_at)
    : "";

  if (els.modelSelect.options.length === 0) {
    for (const model of data.models || []) {
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = model.label || model.id;
      option.title = model.note || "";
      els.modelSelect.append(option);
    }
  }
  if (config.model && !Array.from(els.modelSelect.options).some((option) => option.value === config.model)) {
    const option = document.createElement("option");
    option.value = config.model;
    option.textContent = config.model;
    els.modelSelect.append(option);
  }
  if (!Array.from(els.modelSelect.options).some((option) => option.value === "__custom__")) {
    const custom = document.createElement("option");
    custom.value = "__custom__";
    custom.textContent = "自定义…";
    els.modelSelect.append(custom);
  }
  if (els.reasoningSelect.options.length === 0) {
    for (const level of data.reasoning_levels || []) {
      const option = document.createElement("option");
      option.value = level.id;
      option.textContent = level.label || level.id;
      option.title = level.note || "";
      els.reasoningSelect.append(option);
    }
  }

  ui.syncing = true;
  els.modelSelect.value = config.model || "deepseek-flash";
  els.reasoningSelect.value = config.reasoning_level || "medium";
  ui.syncing = false;

  els.modelSelect.disabled = busy;
  els.reasoningSelect.disabled = busy;
  els.send.disabled = busy || ui.pendingSend || !hasProject;
  els.message.disabled = !hasProject;
  els.cancel.disabled = !busy;
  els.saveSettings.disabled = busy;
  els.createProject.disabled = busy;
  els.submitAttempt.disabled = busy || ui.pendingSend || !hasProject || Boolean(data.lesson?.pending_review);
  els.submitAttempt.hidden = !data.lesson?.current_step;

  const label = permissionLabel(config);
  els.permissionSummary.textContent = config.permission_mode === "full" ? "完全访问" : label;
  els.permissionsButton.classList.toggle("full-access", config.permission_mode === "full");
  els.permissionsButton.disabled = busy;
  if (busy) closePermissions();
  for (const option of els.permissionMenu.querySelectorAll("[data-permission-mode]")) {
    option.setAttribute("aria-checked", String(option.dataset.permissionMode === config.permission_mode));
    option.disabled = busy;
  }
  els.permissionMenuNote.textContent = config.permission_mode === "custom"
    ? `当前为旧版${label}配置；选择一种模式后生效。`
    : "命令按当前 Windows 用户权限运行。凭据文件仍受文件工具保护。";

}

function renderConfigState(config) {
  if (!config) {
    els.apiStatus.textContent = "配置不可用";
    return;
  }
  if (!config.api_key_configured) {
    els.apiStatus.textContent = "API Key 未配置";
    els.apiStatus.className = "api-status";
  } else if (config.api_key_source === "config") {
    els.apiStatus.textContent = "API Key · 本地保存";
    els.apiStatus.className = "api-status ok";
  } else {
    els.apiStatus.textContent = "API Key · .env";
    els.apiStatus.className = "api-status ok";
  }
}

function renderStatus(data, busy) {
  const status = data.status || "idle";
  els.runStatus.textContent = !data.active_project ? "等待创建项目" : STATUS_LABELS[status] || status;
  els.runStatus.className = `run-status ${status}`;
  els.message.placeholder = !data.active_project ? "请先创建或恢复项目，再开始对话。" : data.status === "waiting_approval"
    ? "请先处理上方的批准请求。"
    : "写下你的思路、操作结果或卡住的地方；作答时点击‘提交我的尝试’…";
  els.composerNote.textContent = !data.active_project ? "创建项目时由你选择工作区文件夹。" : busy
    ? "导师回应中，你可以先写下一条消息。停止不会清空学习记录。"
    : data.lesson?.pending_review ? "你的尝试正在等待证据审阅；可提问或请导师继续检查。" : "按你的节奏学习 · 演示和独立完成分别记录 · 本机文件访问由你授权";
}

function renderLesson(lesson = {}, busy) {
  const key = JSON.stringify([lesson, busy, ui.reviewingAttempt, ui.data?.active_project_id || null, ui.data?.active_conversation_id || null]);
  if (key === ui.lastLessonKey) return;
  ui.lastLessonKey = key;
  const steps = lesson.steps || [];
  const step = lesson.current_step;
  const stepKey = `${ui.data?.active_conversation_id || ""}|${step?.id || ""}`;
  if (ui.lessonStepKey !== stepKey) { const panel = document.querySelector(".lesson-panel"); if (panel) panel.scrollTop = 0; ui.lessonStepKey = stepKey; }
  els.lessonEmpty.hidden = steps.length > 0;
  els.lessonEmpty.querySelector("strong").textContent = ui.data?.active_project ? "先定一个小目标" : "先创建一个项目";
  els.lessonEmpty.querySelector("p").textContent = ui.data?.active_project ? "导师会把目标变成可尝试的步骤。你可以随时要提示、看一次演示，或自己来。" : "选择工作区后，导师会在这里安排你的学习步骤。";

  els.lessonContent.hidden = !steps.length;
  els.lessonMode.textContent = lesson.mode === "independent" ? "独立尝试" : "引导学习";
  els.lessonGoal.textContent = lesson.goal || "";
  els.lessonProgressText.textContent = lesson.completed ? "本轮计划已完成" : `当前第 ${(lesson.current || 0) + 1} / ${steps.length} 步 · ${lesson.current || 0} 步已走过`;
  els.lessonProgress.max = steps.length || 1;
  els.lessonProgress.value = lesson.current || 0;
  els.lessonSteps.replaceChildren();
  for (const [index, item] of steps.entries()) {
    const li = document.createElement("li");
    li.className = `${index === lesson.current ? "active" : ""} ${item.status}`;
    if (index === lesson.current) li.setAttribute("aria-current", "step");
    const number = document.createElement("span");
    number.className = "step-number";
    number.textContent = item.status === "skipped" ? "↷" : ["completed", "discussed"].includes(item.status) ? "✓" : String(index + 1).padStart(2, "0");
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.title;
    const label = document.createElement("span");
    const stepStatus = ({submitted: "待审阅", reviewed: "可继续", needs_revision: "待补充", completed: "已完成", discussed: "已讨论", skipped: "已跳过"})[item.status];
    label.textContent = [(lesson.kind_labels || {})[item.kind] || item.kind, stepStatus].filter(Boolean).join(" · ");
    copy.append(title, label);
    li.append(number, copy);
    els.lessonSteps.append(li);
  }
  els.currentStep.hidden = !step;
  if (step) {
    els.stepPosition.textContent = `当前第 ${(lesson.current || 0) + 1} / ${steps.length} 步`;
    els.stepTitle.textContent = step.title;
    markdown(els.stepTask, step.task);
    els.stepCriteria.textContent = step.success_criteria;
    els.assistanceNote.textContent = step.answer_viewed ? "本步已看过演示；后续完成会保留帮助记录。" : step.hint_level ? `本步已使用 L${step.hint_level} 提示；你可以继续尝试。` : "本步尚未使用提示或演示。";
    els.nextStep.textContent = step.kind === "explain" ? "我理解了，继续" : "继续下一步";
  }
  for (const button of document.querySelectorAll("[data-learning-action]")) button.disabled = busy || !step;
  els.nextStep.disabled = busy || !lesson.can_continue;
  els.retryLearning.hidden = !lesson.can_retry;
  els.retryLearning.disabled = busy || !lesson.can_retry;
  els.reviewPendingAttempt.hidden = !lesson.pending_review;
  els.reviewPendingAttempt.disabled = busy || ui.reviewingAttempt;
  els.reviewPendingAttempt.textContent = busy || ui.reviewingAttempt ? "正在审阅，请稍候…" : "请导师审阅这次尝试";
  els.stepTransitionNote.dataset.state = lesson.pending_review ? "pending" : lesson.can_continue ? "ready" : "waiting";
  els.stepTransitionNote.textContent = lesson.pending_review
    ? "你的尝试已保存，尚未完成审阅。审阅通过后才能进入下一步；不用重复提交。"
    : lesson.can_continue ? "本步可以继续。点击下方按钮，或在对话中输入“继续”，导航会同步推进。"
      : lesson.can_retry ? "本次检验尚未通过。点击‘重新练习并检验’，完成新的独立练习和迁移题；原有记录会保留。"
      : step?.status === "needs_revision" ? "本步仍需补充，请根据审阅反馈提交新的尝试。若要先往下学，可以明确跳过，本步不会记为掌握。"
        : "先提交本步的尝试并完成审阅，再继续下一步。";
  const feedback = lesson.last_feedback;
  els.learningFeedback.hidden = !feedback;
  els.learningFeedback.replaceChildren();
  if (feedback) {
    const label = document.createElement("strong");
    let heading;
    if (feedback.practice?.correctness === "correct") {
      heading = lesson.can_continue && step?.status === "reviewed" ? "本次尝试审阅通过" : feedback.practice?.reasoning_quality !== "sound" ? "结果正确，仍需补充依据" : "结果正确，尚未满足本步标准";
    } else {
      heading = ({partially_correct: "部分完成，再试一小步", incorrect: "需要调整", uncertain: "证据不足，尚未验证"})[feedback.practice?.correctness] || "导师反馈";
    }
    label.textContent = lesson.pending_review ? `上次审阅：${heading}；新尝试待审阅` : heading;
    const body = document.createElement("p");
    body.textContent = feedback.feedback;
    const evidence = document.createElement("p");
    evidence.className = "feedback-evidence";
    evidence.textContent = `审阅依据：${feedback.evidence}`;
    els.learningFeedback.append(label, body, evidence);
    if (feedback.mastery_reason) {
      const note = document.createElement("p");
      note.textContent = feedback.mastery_reason;
      els.learningFeedback.append(note);
    }
  }
  els.lessonSummary.textContent = lesson.mastery_verified
    ? "本轮独立练习与迁移检验满足掌握规则。"
    : `已记录 ${lesson.attempts?.length || 0} 次尝试 · ${lesson.independent_successes || 0} 次无帮助独立通过。正确性与推理由导师审阅。`;
}

function renderChat(data) {
  const events = Array.isArray(data.events) ? data.events : [];
  const pendingId = data.pending?.id || "";
  const key = JSON.stringify([data.active_conversation_id, events, data.status, pendingId]);
  if (key === ui.lastChatKey) return;
  ui.lastChatKey = key;
  const changedConversation = !ui.chatStructure.startsWith(`${data.active_conversation_id}|`);
  const nearBottom = changedConversation || els.chatViewport.scrollHeight - els.chatViewport.scrollTop - els.chatViewport.clientHeight < 140;
  const previousScrollTop = els.chatViewport.scrollTop;
  if (changedConversation) ui.unread = false;
  else if (!nearBottom) ui.unread = true;
  const structure = `${data.active_conversation_id}|${events.map(event => event.id).join(",")}`;
  if (structure === ui.chatStructure) {
    for (const event of events) {
      const previous = ui.renderedEvents.get(event.id);
      if (event.kind === "assistant" && JSON.stringify(previous) !== JSON.stringify(event)) {
        const node = els.messages.querySelector(`[data-event-id="${event.id}"]`);
        if (node) {
          markdown(node.querySelector(".markdown-body"), event.text);
          node.classList.toggle("streaming", Boolean(event.streaming));
          node.querySelector(".message-label").textContent = event.streaming ? "导师 · 正在输出" : event.interrupted ? "导师 · 回复已中断" : "导师";
        }
      }
    }
    ui.renderedEvents = new Map(events.map(event => [event.id, { ...event }]));
    updateScrollPosition(nearBottom);
    return;
  }
  ui.chatStructure = structure;
  ui.renderedEvents = new Map(events.map(event => [event.id, { ...event }]));
  ui.toolCards.clear();
  els.messages.replaceChildren();
  els.welcome.hidden = !data.active_project || events.some((event) => ["user", "assistant"].includes(event.kind));

  for (const event of events) {
    if (!event || !event.kind) continue;
    if (event.kind === "user") appendMessage("user", event.text, "YOU");
    else if (event.kind === "assistant") {
      const node = appendMessage("assistant", event.text, event.streaming ? "导师 · 正在输出" : event.interrupted ? "导师 · 回复已中断" : "导师");
      node.dataset.eventId = event.id;
      node.classList.toggle("streaming", Boolean(event.streaming));
    }
    else if (event.kind === "system") appendMessage("system", event.text);
    else if (event.kind === "error") appendMessage("error", event.text, "ERROR");
    else if (event.kind === "tool_start") appendToolStart(event);
    else if (event.kind === "tool_result") appendToolResult(event);
  }
  if (!nearBottom) els.chatViewport.scrollTop = previousScrollTop;
  updateScrollPosition(nearBottom);
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
  const body = document.createElement("div");
  body.className = kind === "assistant" ? "markdown-body" : "message-text";
  if (kind === "assistant") markdown(body, text);
  else {
    body.textContent = text;
    if (kind === "user" && (String(text).length > 1000 || String(text).split("\n").length > 12)) scrollRegion(body, "你的消息，可滚动查看完整内容");
  }
  node.append(body);
  els.messages.append(node);
  return node;
}

function toolLabel(name) {
  return ({ plan_learning: "安排学习步骤", review_learning_attempt: "审阅你的尝试", record_learning_help: "记录教学帮助", read_file: "查看本地文件", write_file: "演示文件修改", run_command: "执行本机命令", list_dir: "查看目录", grep: "搜索相关内容" })[name] || name || "工具";
}

function copyTextButton(label, value) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "tool-copy";
  button.textContent = label;
  button.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(value); button.textContent = "已复制"; }
    catch { toast("无法访问剪贴板，请选择下方文本手动复制。", true); }
  });
  return button;
}

function appendToolStart(event) {
  const card = document.createElement("div");
  card.className = "tool-card";
  const head = document.createElement("div");
  head.className = "tool-card-head";
  const name = document.createElement("span");
  name.className = "tool-name";
  name.textContent = toolLabel(event.tool);
  const badge = document.createElement("span");
  badge.className = "tool-badge";
  badge.textContent = "进行中";
  head.append(name, badge);
  const detail = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "查看操作详情";
  detail.append(summary);
  const key = event.call_id || event.tool;
  detail.open = ui.toolDetails.has(key);
  detail.addEventListener("toggle", () => {
    if (!detail.isConnected) return;
    if (detail.open) ui.toolDetails.add(key); else ui.toolDetails.delete(key);
  });
  const args = scrollRegion(document.createElement("pre"), "操作参数，可滚动查看");
  args.textContent = pretty(event.arguments || {});
  detail.append(args);
  if (event.tool === "run_command" && event.arguments?.command) detail.append(copyTextButton("复制命令", event.arguments.command));
  card.append(head, detail);
  els.messages.append(card);
  const item = { card, badge, detail, args: event.arguments || {} };
  ui.toolCards.set(key, item);
  return item;
}

function toolOutcome(parsed) {
  const error = String(parsed.error || "");
  if (parsed.cancelled || /取消/.test(error)) return {kind: "cancelled", label: "已停止", summary: "这次操作已停止，已保留收到的输出。"};
  if (parsed.timed_out) return {kind: "timeout", label: "执行超时", summary: "命令在限定时间内没有结束。下方保留了已收到的输出。"};
  if (parsed.ok === false && /拒绝|没有授予|批准通道|未授权/.test(error)) return {kind: "denied", label: "未获批准", summary: error};
  if (parsed.ok === false || (typeof parsed.returncode === "number" && parsed.returncode !== 0)) {
    const detail = error || String(parsed.stderr || "").trim().split(/\r?\n/)[0];
    return {kind: "failed", label: typeof parsed.returncode === "number" ? `退出码 ${parsed.returncode}` : "操作失败", summary: detail || "命令未成功结束，也没有返回错误文本。请检查命令与运行环境。"};
  }
  return {kind: "success", label: "已完成", summary: ""};
}

function appendToolResult(event) {
  const key = event.call_id || event.tool;
  const item = ui.toolCards.get(key) || appendToolStart({...event, arguments: {}});
  let parsed = {};
  try { parsed = typeof event.result === "string" ? JSON.parse(event.result) : event.result || {}; } catch { /* legacy plain result */ }
  if (!parsed || typeof parsed !== "object") parsed = {};
  const outcome = toolOutcome(parsed);
  item.badge.textContent = outcome.label;
  item.card.classList.toggle("failed", !["success", "cancelled"].includes(outcome.kind));
  item.card.dataset.outcome = outcome.kind;
  const duration = typeof parsed.duration_ms === "number" ? `用时 ${(parsed.duration_ms / 1000).toFixed(1)} 秒` : "";
  const meta = [parsed.shell, duration].filter(Boolean).join(" · ");
  if (meta) { const node = document.createElement("div"); node.className = "tool-result-meta"; node.textContent = meta; item.card.insertBefore(node, item.detail); }
  const explanation = outcome.kind === "success" ? "" : typeof parsed.summary === "string" ? parsed.summary : typeof parsed.diagnostic === "string" ? parsed.diagnostic : outcome.summary;
  if (explanation) { const node = document.createElement("p"); node.className = "tool-result-summary"; node.textContent = explanation.slice(0, 600); item.card.insertBefore(node, item.detail); }
  let hint = typeof parsed.hint === "string" ? parsed.hint : "";
  if (!hint && /Python was not found|不是内部或外部命令|not recognized|command not found/i.test(String(parsed.stderr || ""))) hint = "命令未被识别。打开“设置与权限”查看实际使用的命令行和 Python 路径。";
  if (hint) { const node = document.createElement("p"); node.className = "tool-result-hint"; node.textContent = hint; item.card.insertBefore(node, item.detail); }
  const hasStreams = "stdout" in parsed || "stderr" in parsed;
  if (hasStreams) {
    for (const [field, label] of [["stdout", "标准输出"], ["stderr", "错误输出"]]) {
      const value = String(parsed[field] || "");
      if (!value.trim()) continue;
      const heading = document.createElement("div"); heading.className = "tool-output-heading"; heading.textContent = label;
      heading.append(copyTextButton("复制", value));
      const output = scrollRegion(document.createElement("pre"), `${label}，可滚动查看`); output.textContent = value;
      item.detail.append(heading, output);
    }
    if (!String(parsed.stdout || "").trim() && !String(parsed.stderr || "").trim()) {
      const empty = document.createElement("p"); empty.className = "tool-result-meta"; empty.textContent = "此命令没有产生文本输出。"; item.detail.append(empty);
    }
  } else {
    const output = scrollRegion(document.createElement("pre"), "操作结果，可滚动查看"); output.textContent = pretty(event.result || ""); item.detail.append(output);
  }
  if (event.tool === "run_command" && parsed.command && !item.args.command) item.detail.append(copyTextButton("复制命令", parsed.command));
  if (hasStreams) {
    const raw = document.createElement("details");
    const rawSummary = document.createElement("summary"); rawSummary.textContent = "原始结果";
    const rawText = scrollRegion(document.createElement("pre"), "原始结果，可滚动查看"); rawText.textContent = pretty(event.result || "");
    raw.append(rawSummary, rawText); item.detail.append(raw);
  }
  ui.toolCards.delete(key);
}

function renderApproval(pending) {
  els.approval.hidden = !pending;
  if (!pending) return;
  const args = pending.arguments || {};
  const labels = { run_command: "执行本机命令", write_file: "写入文件", read_file: "读取文件", list_dir: "查看文件夹", grep: "搜索文件内容" };
  els.approvalTool.textContent = labels[pending.tool] || pending.tool || "执行操作";
  els.approvalContext.textContent = pending.tool === "run_command"
    ? `执行目录：${args.resolved_path || (args.cwd && args.cwd !== "." ? args.cwd : ui.data?.active_project?.workspace) || "."}`
    : `文件位置：${args.resolved_path || args.path || "."}`;
  els.approvalArgs.textContent = pending.tool === "run_command" ? args.command || ""
    : pending.tool === "write_file" ? args.content || "（空文件）" : args.pattern || args.resolved_path || args.path || ".";
  els.approvalRaw.textContent = pretty(args);
  els.approvalReason.textContent = args.outside_workspace
    ? "此操作将访问工作区以外的路径，需要你批准。"
    : ui.data?.config?.permission_mode === "auto"
      ? "这项操作未能自动确认为内置只读检查，需要你批准。"
      : "当前模式需要你确认这次操作。";
}

function permissionLabel(config = {}) {
  if (config.permission_mode === "custom") return config.allow_write || config.allow_shell ? "自定义权限" : "仅查看";
  return ({request: "请求批准", auto: "帮我批准", full: "完全访问权限"})[config.permission_mode] || "请求批准";
}

function closePermissions() {
  els.permissionMenu.hidden = true;
  els.permissionsButton.setAttribute("aria-expanded", "false");
}

function togglePermissions() {
  const open = els.permissionMenu.hidden;
  els.permissionMenu.hidden = !open;
  els.permissionsButton.setAttribute("aria-expanded", String(open));
  if (open) (els.permissionMenu.querySelector('[aria-checked="true"]') || els.permissionMenu.querySelector('[role="menuitemradio"]')).focus();
}

async function selectPermissionMode(mode) {
  closePermissions();
  try {
    const data = await api("/api/workspace/config", {method: "POST", body: JSON.stringify({permission_mode: mode})});
    applyState(data);
    toast(`已切换为${permissionLabel(data.config)}。`);
  } catch (error) { toast(error.message, true); }
}

/* ---------- actions ---------- */

function closeProjectMenu(restoreFocus = false) {
  const id = ui.contextProjectId;
  els.projectMenu.hidden = true;
  ui.contextProjectId = null;
  for (const row of els.projectList.querySelectorAll(".project-item")) {
    row.classList.remove("context-target");
    if (restoreFocus && row.dataset.projectId === id) row.focus();
  }
}
function openProjectMenu(id, x, y) {
  if (isBusy(ui.data?.status)) { toast("导师正在运行，请先停止或等待完成后再管理项目。", true); return; }
  closePermissions(); closeConversationMenu(); closeProjectMenu();
  const project = ui.data?.projects?.find(item => item.id === id);
  if (!project) return;
  ui.contextProjectId = id;
  els.pinProjectLabel.textContent = project.pinned ? "取消置顶" : "置顶";
  for (const row of els.projectList.querySelectorAll(".project-item")) row.classList.toggle("context-target", row.dataset.projectId === id);
  els.projectMenu.hidden = false;
  const rect = els.projectMenu.getBoundingClientRect();
  els.projectMenu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - rect.width - 8))}px`;
  els.projectMenu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - rect.height - 8))}px`;
  els.renameProjectMenu.focus();
}
function contextProject() { return ui.data?.projects?.find(item => item.id === ui.contextProjectId); }
async function editProject(id, action, value, success) {
  try {
    const data = await api("/api/workspace/edit-project", {method: "POST", body: JSON.stringify({id, action, value})});
    applyState(data);
    if (success) toast(success);
    return true;
  } catch (error) { toast(error.message, true); return false; }
}

function closeConversationMenu(restoreFocus = false) {
  const id = ui.contextConversationId;
  els.conversationMenu.hidden = true;
  ui.contextConversationId = null;
  for (const row of els.projectList.querySelectorAll("[data-conversation-id]")) {
    row.classList.remove("context-target");
    if (restoreFocus && row.dataset.conversationId === id) row.focus();
  }
}

function openConversationMenu(id, x, y) {
  if (isBusy(ui.data?.status)) { toast("导师正在运行，请先停止或等待完成后再管理对话。", true); return; }
  closePermissions();
  closeProjectMenu();
  closeConversationMenu();
  ui.contextConversationId = id;
  const conversation = ui.data?.conversations?.find(item => item.id === id);
  if (!conversation) return;
  els.pinConversationLabel.textContent = conversation.pinned ? "取消置顶" : "置顶";
  els.moveConversationMenu.disabled = (ui.data?.projects?.length || 0) < 2;
  for (const row of els.projectList.querySelectorAll("[data-conversation-id]")) {
    row.classList.toggle("context-target", row.dataset.conversationId === id);
  }
  els.conversationMenu.hidden = false;
  const rect = els.conversationMenu.getBoundingClientRect();
  els.conversationMenu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - rect.width - 8))}px`;
  els.conversationMenu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - rect.height - 8))}px`;
  els.renameConversationMenu.focus();
}

function contextConversation() {
  return ui.data?.conversations?.find(item => item.id === ui.contextConversationId);
}

async function editConversation(id, action, value, success) {
  try {
    const data = await api("/api/workspace/edit-conversation", {method: "POST", body: JSON.stringify({id, action, value})});
    applyState(data);
    if (success) toast(success);
    return true;
  } catch (error) { toast(error.message, true); return false; }
}

function requestRenameConversation() {
  const conversation = contextConversation();
  closeConversationMenu();
  if (!conversation) return;
  ui.editingConversationId = conversation.id;
  els.renameConversationInput.value = conversation.title || "";
  els.renameConversationDialog.showModal();
  els.renameConversationInput.focus();
  els.renameConversationInput.select();
}

async function confirmRenameConversation() {
  const title = els.renameConversationInput.value.trim();
  if (!title) { toast("请输入对话名称。", true); els.renameConversationInput.focus(); return; }
  els.confirmRenameConversation.disabled = true;
  const ok = await editConversation(ui.editingConversationId, "rename", title, "对话已重命名。");
  if (ok) els.renameConversationDialog.close();
  els.confirmRenameConversation.disabled = false;
}

function requestMoveConversation() {
  const conversation = contextConversation();
  closeConversationMenu();
  if (!conversation) return;
  ui.editingConversationId = conversation.id;
  els.moveConversationSelect.replaceChildren();
  for (const project of ui.data.projects.filter(item => item.id !== conversation.project_id)) {
    const option = document.createElement("option");
    option.value = project.id;
    option.textContent = project.name;
    option.title = project.workspace;
    els.moveConversationSelect.append(option);
  }
  if (!els.moveConversationSelect.options.length) { toast("请先添加其他项目。", true); return; }
  els.moveConversationDialog.showModal();
  els.moveConversationSelect.focus();
}

async function confirmMoveConversation() {
  const projectId = els.moveConversationSelect.value;
  els.confirmMoveConversation.disabled = true;
  const ok = await editConversation(ui.editingConversationId, "move", projectId, "对话已移至目标项目。");
  if (ok) { ui.expanded.set(projectId, true); ui.sidebarKey = ""; renderSidebar(ui.data, isBusy(ui.data.status)); els.moveConversationDialog.close(); }
  els.confirmMoveConversation.disabled = false;
}

function renderArchived(data, busy) {
  const archived = data.archived_conversations || [];
  const projects = data.archived_projects || [];
  const count = archived.length + projects.length;
  els.archivedCount.textContent = count ? String(count) : "";
  els.openArchived.disabled = busy;
  if (!els.archivedDialog.open) return;
  els.archivedList.replaceChildren();
  if (!count) {
    const empty = document.createElement("p");
    empty.className = "dialog-note";
    empty.textContent = "还没有归档的项目或对话。";
    els.archivedList.append(empty);
  }
  for (const project of projects) {
    const row = document.createElement("div"); row.className = "archived-conversation";
    const copy = document.createElement("div"); const title = document.createElement("strong"); title.textContent = project.name;
    const note = document.createElement("span"); note.textContent = `项目 · ${project.total_conversation_count} 段对话`;
    copy.append(title, note);
    const restore = document.createElement("button"); restore.type = "button"; restore.className = "button"; restore.textContent = "恢复"; restore.disabled = busy;
    restore.setAttribute("aria-label", `恢复项目 ${project.name}`);
    restore.addEventListener("click", () => editProject(project.id, "archive", false, "项目已恢复。"));
    row.append(copy, restore); els.archivedList.append(row);
  }
  for (const conversation of archived) {
    const row = document.createElement("div");
    row.className = "archived-conversation";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = conversation.title || "新对话";
    const project = document.createElement("span");
    project.textContent = data.projects.find(item => item.id === conversation.project_id)?.name || "项目";
    copy.append(title, project);
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "button";
    restore.textContent = "恢复";
    restore.disabled = busy;
    restore.setAttribute("aria-label", `恢复 ${conversation.title || "新对话"}`);
    restore.addEventListener("click", () => editConversation(conversation.id, "archive", false, "对话已恢复。"));
    row.append(copy, restore);
    els.archivedList.append(row);
  }
}

function requestDeleteConversation() {
  const conversation = ui.data?.conversations?.find(item => item.id === ui.contextConversationId);
  closeConversationMenu();
  if (!conversation) return;
  ui.deleteConversationId = conversation.id;
  els.deleteConversationName.textContent = conversation.title || "新对话";
  els.deleteConversationDialog.showModal();
  // The cancel action gets initial focus to avoid deleting through a stray Enter.
  els.deleteConversationDialog.querySelector('.dialog-actions [value="cancel"]').focus();
}

async function deleteConversation() {
  const id = ui.deleteConversationId;
  if (!id || ui.deleting) return;
  ui.deleting = true;
  els.confirmDeleteConversation.disabled = true;
  try {
    const data = await api("/api/workspace/delete-conversation", {method: "POST", body: JSON.stringify({id})});
    els.deleteConversationDialog.close();
    applyState(data);
    toast("对话已删除。");
    els.message.focus();
  } catch (error) { toast(error.message, true); }
  finally { ui.deleting = false; els.confirmDeleteConversation.disabled = isBusy(ui.data?.status); }
}

async function refresh() {
  if (ui.polling) return;
  ui.polling = true;
  try {
    const data = await api("/api/workspace/state");
    applyState(data);
    setConnection(ui.streamConnected ? "online" : "connecting", ui.streamConnected ? "已连接" : "实时连接恢复中");
  } catch (error) {
    setConnection("offline", "连接中断");
    els.connectionStatus.title = error.message;
  } finally {
    ui.polling = false;
  }
}

async function learningAction(action) {
  try {
    const data = await api("/api/workspace/learning/action", { method: "POST", body: JSON.stringify({ action, conversation_id: ui.data?.active_conversation_id }) });
    applyState(data);
  } catch (error) { toast(error.message, true); }
}

async function submitAttempt() {
  const answer = els.message.value.trim();
  if (!answer) { toast("先写下你的思路、操作或观察到的结果。", true); els.message.focus(); return; }
  await submitComposer("/api/workspace/learning/attempt", {answer});
}

async function selectProject(projectId) {
  try {
    const data = await api("/api/workspace/select-project", {
      method: "POST",
      body: JSON.stringify({ id: projectId }),
    });
    applyState(data);
  } catch (error) {
    toast(error.message, true);
  }
}

async function selectConversation(conversationId) {
  try {
    const data = await api("/api/workspace/select-conversation", {
      method: "POST",
      body: JSON.stringify({ id: conversationId }),
    });
    applyState(data);
    document.body.classList.remove("sidebar-open");
    els.toggleSidebar.setAttribute("aria-expanded", "false");
  } catch (error) {
    toast(error.message, true);
  }
}

async function newConversation(projectId = null) {
  if (!projectId && !ui.data?.active_project) { openProjectDialog(); return; }
  try {
    const data = await api("/api/workspace/conversation", {
      method: "POST",
      body: JSON.stringify({ project_id: projectId }),
    });
    ui.expanded.set(data.active_project_id, true);
    applyState(data);
    els.message.focus();
  } catch (error) {
    toast(error.message, true);
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const text = els.message.value.trim();
  if (!text) return;
  await submitComposer("/api/workspace/chat", {message: text});
}

async function submitComposer(path, payload) {
  if (ui.pendingSend || isBusy(ui.data?.status) || ui.composing || !ui.data?.active_project) return;
  const sentDraft = els.message.value;
  const conversationId = ui.data?.active_conversation_id;
  ui.pendingSend = true;
  els.send.disabled = true;
  els.submitAttempt.disabled = true;
  try {
    const data = await api(path, {
      method: "POST",
      body: JSON.stringify({...payload, conversation_id: conversationId}),
    });
    // A new draft may have been typed while the request was in flight.
    if (ui.data?.active_conversation_id === conversationId && els.message.value === sentDraft) els.message.value = "";
    else if (ui.drafts.get(conversationId) === sentDraft) ui.drafts.delete(conversationId);
    resizeComposer();
    applyState(data);
    updateScrollPosition(true);
  } catch (error) {
    toast(error.message, true);
  } finally {
    ui.pendingSend = false;
    els.send.disabled = isBusy(ui.data?.status) || !ui.data?.active_project;
    els.submitAttempt.disabled = isBusy(ui.data?.status) || !ui.data?.active_project || Boolean(ui.data?.lesson?.pending_review);
  }
}

async function approve(allow) {
  const pending = ui.data?.pending;
  if (!pending) return;
  try {
    const data = await api("/api/workspace/approve", {
      method: "POST",
      body: JSON.stringify({ id: pending.id, allow }),
    });
    applyState(data);
  } catch (error) {
    toast(error.message, true);
  }
}

async function cancelRun() {
  try {
    const data = await api("/api/workspace/cancel", { method: "POST", body: "{}" });
    applyState(data);
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveTopbarConfig() {
  if (ui.syncing) return;
  let model = els.modelSelect.value;
  if (model === "__custom__") {
    const entered = window.prompt("输入模型名", ui.data?.config?.model || "");
    if (!entered || !entered.trim()) {
      await refresh();
      return;
    }
    model = entered.trim();
    if (!Array.from(els.modelSelect.options).some((option) => option.value === model)) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      els.modelSelect.append(option);
    }
    els.modelSelect.value = model;
  }
  try {
    const data = await api("/api/workspace/config", {
      method: "POST",
      body: JSON.stringify({
        model,
        reasoning_level: els.reasoningSelect.value,
      }),
    });
    applyState(data);
    toast("模型与推理强度已更新。");
  } catch (error) {
    toast(error.message, true);
    await refresh();
  }
}

/* ---------- settings dialog ---------- */

function openSettings() {
  const data = ui.data;
  if (!data) return;
  const config = data.config || {};
  els.settingsApiKey.value = "";
  els.settingsApiKey.placeholder = config.api_key_configured
    ? "已配置，留空保持不变"
    : "输入 DeepSeek API Key";
  els.settingsBaseUrl.value = config.base_url || "";
  els.settingsWorkspace.textContent = data.active_project?.workspace || "未选择";
  els.settingClearKey.checked = false;
  els.settingsPermissionSummary.textContent = `操作权限：${permissionLabel(config)}。在输入框下方的权限菜单中切换。`;
  els.settingsNote.textContent = config.api_key_configured
    ? "Key 已保存在本机；留空会保留现有 Key。"
    : "Key 只保存在本机 data/agent_config.json，不会提交 Git。";
  els.settingsDialog.showModal();
}

async function saveSettings() {
  els.saveSettings.disabled = true;
  try {
    const data = await api("/api/workspace/config", {
      method: "POST",
      body: JSON.stringify({
        api_key: els.settingsApiKey.value,
        clear_api_key: els.settingClearKey.checked,
        base_url: els.settingsBaseUrl.value,
      }),
    });
    els.settingsDialog.close();
    applyState(data);
    toast("设置已保存。");
  } catch (error) {
    toast(error.message, true);
  } finally {
    els.saveSettings.disabled = false;
  }
}

function openProjectDialog(project = null) {
  const relocating = Boolean(project?.id);
  ui.relocatingProjectId = relocating ? project.id : null;
  els.projectDialogTitle.textContent = relocating ? "重新选择工作区文件夹" : "新建项目";
  els.createProject.textContent = relocating ? "保存路径" : "创建项目";
  els.projectNameInput.readOnly = relocating;
  els.projectNameInput.value = relocating ? project.name : "";
  els.projectWorkspaceInput.value = relocating ? project.workspace : "";
  els.projectDialog.showModal();
  els.projectNameInput.focus();
}

async function chooseWorkspace() {
  if (!window.pywebview?.api?.choose_workspace) {
    toast("请在桌面版中选择文件夹；浏览器中可粘贴文件夹完整路径。", true);
    return;
  }
  els.chooseWorkspace.disabled = true;
  try {
    const selected = await window.pywebview.api.choose_workspace(els.projectWorkspaceInput.value.trim() || "");
    if (selected) {
      els.projectWorkspaceInput.value = selected;
      if (!els.projectNameInput.value.trim()) els.projectNameInput.value = selected.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "项目";
    }
  } catch (error) { toast(error.message || "无法打开文件夹选择器，请重试。", true); }
  finally { els.chooseWorkspace.disabled = false; }
}

function applySessionPatch(patch) {
  if (!ui.data || patch.state_epoch !== ui.data.state_epoch || patch.active_conversation_id !== ui.data.active_conversation_id || patch.version < ui.data.version) return;
  const events = new Map(ui.data.events.map(item => [item.id, item]));
  for (const item of patch.events) events.set(item.id, item);
  applyState({ ...ui.data, ...patch, events: [...events.values()].sort((a, b) => a.id - b.id).slice(-500) });
}

function connectEvents() {
  if (!window.EventSource) { setConnection("connecting", "轮询连接"); ui.pollTimer = window.setInterval(refresh, 3000); return; }
  const source = new EventSource("/api/workspace/events");
  ui.eventSource = source;
  source.addEventListener("state", event => applyState(JSON.parse(event.data)));
  source.addEventListener("session", event => applySessionPatch(JSON.parse(event.data)));
  source.onopen = () => { ui.streamConnected = true; setConnection("online", "已连接"); clearInterval(ui.pollTimer); ui.pollTimer = null; };
  source.onerror = () => {
    ui.streamConnected = false;
    setConnection("connecting", "重连中");
    if (!ui.pollTimer) ui.pollTimer = window.setInterval(refresh, 3000);
  };
}

async function createProject() {
  if (!els.projectWorkspaceInput.value.trim()) {
    toast("请先选择工作区文件夹，或填写完整路径。", true);
    els.projectWorkspaceInput.focus();
    return;
  }
  els.createProject.disabled = true;
  try {
    const relocating = ui.relocatingProjectId;
    const data = await api(relocating ? "/api/workspace/edit-project" : "/api/workspace/project", {
      method: "POST",
      body: JSON.stringify(relocating ? {id: relocating, action: "workspace", value: els.projectWorkspaceInput.value} : {
        name: els.projectNameInput.value,
        workspace: els.projectWorkspaceInput.value,
      }),
    });
    els.projectDialog.close();
    applyState(data);
    toast(relocating ? "工作区路径已更新，聊天和学习记录已保留。" : "项目已创建。");
  } catch (error) {
    toast(error.message, true);
  } finally {
    els.createProject.disabled = false;
  }
}

/* ---------- bind ---------- */

els.renameProjectMenu.addEventListener("click", () => { const item = contextProject(); closeProjectMenu(); if (!item) return; ui.editingProjectId = item.id; els.renameProjectInput.value = item.name; els.renameProjectDialog.showModal(); els.renameProjectInput.focus(); els.renameProjectInput.select(); });
async function confirmRenameProject() {
  const name = els.renameProjectInput.value.trim(); if (!name) { toast("请输入项目名称。", true); return; }
  els.confirmRenameProject.disabled = true;
  if (await editProject(ui.editingProjectId, "rename", name, "项目已重命名。")) els.renameProjectDialog.close();
  els.confirmRenameProject.disabled = isBusy(ui.data?.status);
}
els.confirmRenameProject.addEventListener("click", confirmRenameProject);
els.renameProjectInput.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); confirmRenameProject(); } });
els.relocateProjectMenu.addEventListener("click", () => { const item = contextProject(); closeProjectMenu(); if (item) openProjectDialog(item); });
els.pinProjectMenu.addEventListener("click", () => { const item = contextProject(); closeProjectMenu(); if (item) editProject(item.id, "pin", !item.pinned, item.pinned ? "已取消项目置顶。" : "项目已置顶。"); });
els.archiveProjectMenu.addEventListener("click", () => { const item = contextProject(); closeProjectMenu(); if (item) editProject(item.id, "archive", true, (ui.data.projects.length === 1 ? "项目已归档，当前暂无项目；" : "项目已归档，") + "可在左下角恢复。"); });
els.deleteProjectMenu.addEventListener("click", () => {
  const item = contextProject(); closeProjectMenu(); if (!item) return;
  ui.deleteProjectId = item.id; els.deleteProjectName.textContent = item.name;
  els.deleteProjectNote.textContent = `将删除此项目的 ${item.total_conversation_count} 段对话及其学习记录，删除后无法恢复。工作区文件会保留。${ui.data.projects.length === 1 ? "删除后将显示“暂无项目”，可随时重新创建。" : ""}`;
  els.deleteProjectDialog.showModal(); els.deleteProjectDialog.querySelector('.dialog-actions [value="cancel"]').focus();
});
els.confirmDeleteProject.addEventListener("click", async () => {
  const id = ui.deleteProjectId; if (!id || ui.deletingProject) return; ui.deletingProject = true; els.confirmDeleteProject.disabled = true;
  const ok = await editProject(id, "delete", null, "项目及其对话已删除，工作区文件已保留。");
  if (ok) els.deleteProjectDialog.close(); ui.deletingProject = false; els.confirmDeleteProject.disabled = isBusy(ui.data?.status);
});
els.deleteProjectDialog.addEventListener("close", () => { ui.deleteProjectId = null; });
els.projectMenu.addEventListener("keydown", event => {
  if (event.key === "Escape") { event.preventDefault(); closeProjectMenu(true); }
  else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) { event.preventDefault(); const items = [...els.projectMenu.querySelectorAll('[role="menuitem"]')]; const index = items.indexOf(document.activeElement); const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length; items[next].focus(); }
});
document.addEventListener("click", event => { if (!event.target.closest("#project-context-menu")) closeProjectMenu(); });
document.addEventListener("scroll", () => closeProjectMenu(), true);
window.addEventListener("resize", () => closeProjectMenu());

els.chatForm.addEventListener("submit", sendMessage);
els.deleteConversationMenu.addEventListener("click", requestDeleteConversation);
els.renameConversationMenu.addEventListener("click", requestRenameConversation);
els.pinConversationMenu.addEventListener("click", () => { const item = contextConversation(); closeConversationMenu(); if (item) editConversation(item.id, "pin", !item.pinned, item.pinned ? "已取消置顶。" : "对话已置顶。"); });
els.archiveConversationMenu.addEventListener("click", () => { const item = contextConversation(); closeConversationMenu(); if (item) editConversation(item.id, "archive", true, "对话已归档，可在左下角恢复。"); });
els.moveConversationMenu.addEventListener("click", requestMoveConversation);
els.confirmRenameConversation.addEventListener("click", confirmRenameConversation);
els.confirmMoveConversation.addEventListener("click", confirmMoveConversation);
els.renameConversationInput.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); confirmRenameConversation(); } });
els.openArchived.addEventListener("click", () => { els.archivedDialog.showModal(); renderArchived(ui.data, isBusy(ui.data.status)); });
els.closeArchived.addEventListener("click", () => els.archivedDialog.close());
els.confirmDeleteConversation.addEventListener("click", deleteConversation);
els.deleteConversationDialog.addEventListener("close", () => { ui.deleteConversationId = null; });
els.conversationMenu.addEventListener("keydown", event => {
  if (event.key === "Escape") { event.preventDefault(); closeConversationMenu(true); }
  else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    const items = [...els.conversationMenu.querySelectorAll('[role="menuitem"]:not(:disabled)')];
    const index = items.indexOf(document.activeElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next].focus();
  }
});
document.addEventListener("click", event => { if (!event.target.closest("#conversation-context-menu")) closeConversationMenu(); });
document.addEventListener("scroll", () => closeConversationMenu(), true);
window.addEventListener("resize", () => closeConversationMenu());
els.message.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !ui.composing && event.keyCode !== 229) {
    event.preventDefault();
    els.chatForm.requestSubmit();
  }
});
els.message.addEventListener("compositionstart", () => { ui.composing = true; });
els.message.addEventListener("compositionend", () => { ui.composing = false; resizeComposer(); });
els.message.addEventListener("input", resizeComposer);
els.chatViewport.addEventListener("scroll", () => updateScrollPosition(), {passive: true});
els.jumpLatest.addEventListener("click", () => { updateScrollPosition(true); els.message.focus(); });
els.newChat.addEventListener("click", () => newConversation());
els.chooseWorkspace.addEventListener("click", chooseWorkspace);
els.toggleSidebar.addEventListener("click", () => {
  const open = document.body.classList.toggle("sidebar-open");
  els.toggleSidebar.setAttribute("aria-expanded", String(open));
});
els.toggleLesson.addEventListener("click", () => {
  const open = document.body.classList.toggle("lesson-open");
  els.toggleLesson.setAttribute("aria-expanded", String(open));
});
els.addProject.addEventListener("click", openProjectDialog);
els.createFirstProject.addEventListener("click", openProjectDialog);
els.openSettings.addEventListener("click", openSettings);
els.permissionsButton.addEventListener("click", togglePermissions);
for (const option of els.permissionMenu.querySelectorAll("[data-permission-mode]")) {
  option.addEventListener("click", () => selectPermissionMode(option.dataset.permissionMode));
}
document.addEventListener("click", event => { if (!event.target.closest(".permission-control")) closePermissions(); });
els.permissionMenu.addEventListener("keydown", event => {
  const options = [...els.permissionMenu.querySelectorAll('[role="menuitemradio"]')];
  const index = options.indexOf(document.activeElement);
  if (event.key === "Escape") { closePermissions(); els.permissionsButton.focus(); event.preventDefault(); }
  else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
      : (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
    options[next].focus();
  }
});
els.saveSettings.addEventListener("click", saveSettings);
els.createProject.addEventListener("click", createProject);
els.approve.addEventListener("click", () => approve(true));
els.reject.addEventListener("click", () => approve(false));
els.cancel.addEventListener("click", cancelRun);
els.submitAttempt.addEventListener("click", submitAttempt);
els.reviewPendingAttempt.addEventListener("click", async () => {
  if (ui.reviewingAttempt || isBusy(ui.data?.status) || !ui.data?.lesson?.pending_review) return;
  ui.reviewingAttempt = true;
  renderLesson(ui.data.lesson, false);
  try {
    const data = await api("/api/workspace/chat", {method: "POST", body: JSON.stringify({conversation_id: ui.data?.active_conversation_id, message: "请先审阅我已经提交的这次尝试，并记录审阅结果。请保留我的原始作答，审阅通过后再决定是否进入下一步。"})});
    applyState(data);
  } catch (error) { toast(error.message, true); }
  finally { ui.reviewingAttempt = false; renderLesson(ui.data?.lesson || {}, isBusy(ui.data?.status)); }
});
for (const button of document.querySelectorAll("[data-learning-action]")) {
  button.addEventListener("click", () => learningAction(button.dataset.learningAction));
}
els.modelSelect.addEventListener("change", saveTopbarConfig);
els.reasoningSelect.addEventListener("change", saveTopbarConfig);

for (const chip of document.querySelectorAll(".welcome-chips button")) {
  chip.addEventListener("click", () => {
    if (!ui.data?.active_project) { openProjectDialog(); return; }
    els.message.value = chip.dataset.prompt || "";
    resizeComposer();
    els.message.focus();
  });
}

window.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    newConversation();
  }
});

async function boot() {
  scrollRegion(els.approvalArgs, "待批准操作内容，可滚动查看");
  scrollRegion(els.approvalRaw, "完整操作参数，可滚动查看");
  await refresh();
  if (window.location.hash === "#settings") openSettings();
  if (window.location.hash === "#project") openProjectDialog();
  connectEvents();
}

window.addEventListener("beforeunload", () => ui.eventSource?.close());
boot();
