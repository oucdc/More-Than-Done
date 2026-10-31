"""Run production state application and composer code against a minimal DOM."""

from pathlib import Path
import json
import shutil
import subprocess

import pytest

from agent.agent_config import AgentConfigStore
from agent.conversations import ConversationStore
from agent.local_agent import AgentSession
from agent.workspace import WorkspaceManager
from config import Settings


def test_delayed_state_cannot_switch_chat_or_permission_and_sending_keeps_explicit_target(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend state tests")
    source = (Path(__file__).resolve().parents[1] / "web" / "workspace.js").read_text(encoding="utf-8")
    apply_state = "function applyState" + source.split("function applyState", 1)[1].split("\nfunction renderSidebar", 1)[0]
    composer = "async function submitComposer" + source.split("async function submitComposer", 1)[1].split("\nasync function approve", 1)[0]
    session_patch = "function applySessionPatch" + source.split("function applySessionPatch", 1)[1].split("\nfunction connectEvents", 1)[0]
    config = AgentConfigStore(tmp_path / "config.json", settings_loader=lambda: Settings(api_key="", model="test"))
    config.update({"workspace": str(tmp_path), "permission_mode": "request"})
    manager = WorkspaceManager(config, ConversationStore(tmp_path / "chats.json"), AgentSession(config))
    before = manager.create_project({"workspace": str(tmp_path)})
    after = manager.save_config({"permission_mode": "full"})
    assert after["version"] > before["version"]
    assert after["workspace_version"] > before["workspace_version"]
    script = r'''
const assert = require('node:assert/strict');
const stub = () => {};
const ui = {data: null, drafts: new Map(), composing: false, retiredEpochs: new Set()};
const message = {value: 'draft B'};
const els = new Proxy({message}, {get: (target, key) => target[key] ||= {disabled: false, hidden: false, open: false}});
const document = {body: {classList: {toggle: stub}}, querySelectorAll: () => []};
const isBusy = status => status === 'running';
const resizeComposer = stub, closeConversationMenu = stub, closeProjectMenu = stub;
const renderSidebar = stub, renderTopbar = stub, renderChat = stub, renderApproval = stub;
const renderStatus = stub, renderConfigState = stub, renderLesson = stub;
const renderArchived = stub, renderEnvironment = stub, updateScrollPosition = stub;
function toast(message) { throw Error(message); }
let sent;
async function api(path, options) { sent = JSON.parse(options.body); return ui.data; }
''' + apply_state + composer + session_patch + "\nconst permissionStates = " + json.dumps([before, after]) + r''';
(async () => {
  applyState({state_epoch: 'server', active_conversation_id: 'B', active_project: {}, version: 11});
  message.value = 'draft B';
  applyState({state_epoch: 'server', active_conversation_id: 'A', active_project: {}, version: 10});
  assert.equal(ui.data.active_conversation_id, 'B');
  assert.equal(message.value, 'draft B');
  await submitComposer('/api/workspace/chat', {message: 'draft B'});
  assert.equal(sent.conversation_id, 'B');
  assert.equal(sent.message, 'draft B');
  applyState({state_epoch: 'restarted-server', active_conversation_id: 'C', active_project: {}, version: 1});
  assert.equal(ui.data.active_conversation_id, 'C');
  applyState({state_epoch: 'server', active_conversation_id: 'A', active_project: {}, version: 99});
  assert.equal(ui.data.active_conversation_id, 'C');
  applyState(permissionStates[0]);
  applyState(permissionStates[1]);
  applyState(permissionStates[0]);
  assert.equal(ui.data.config.permission_mode, 'full');
  const current = ui.data;
  applySessionPatch({state_epoch: 'server', active_conversation_id: current.active_conversation_id, version: 999, events: [{id: 999, text: 'stale'}]});
  assert.equal(ui.data, current);
  applySessionPatch({...current, version: current.version + 1, events: [{id: 999, text: 'fresh'}]});
  assert.equal(ui.data.events.at(-1).text, 'fresh');
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert completed.returncode == 0, completed.stderr


def test_relocating_project_uses_existing_project_instead_of_creating_another():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend state tests")
    source = (Path(__file__).resolve().parents[1] / "web" / "workspace.js").read_text(encoding="utf-8")
    open_dialog = "function openProjectDialog" + source.split("function openProjectDialog", 1)[1].split("\nasync function chooseWorkspace", 1)[0]
    create_project = "async function createProject" + source.split("async function createProject", 1)[1].split("\n/* ---------- bind", 1)[0]
    script = r'''
const assert = require('node:assert/strict');
const ui = {};
const els = new Proxy({}, {get: (target, key) => target[key] ||= {value: '', focus() {}, showModal() {}, close() {}}});
let sent, endpoint;
async function api(path, options) { endpoint = path; sent = JSON.parse(options.body); return {}; }
function applyState() {}
function toast(message, error) { if (error) throw Error(message); }
''' + open_dialog + create_project + r'''
(async () => {
  openProjectDialog({id: 'original', name: 'keep project', workspace: 'C:/missing'});
  assert.equal(els.projectNameInput.readOnly, true);
  els.projectWorkspaceInput.value = 'C:/moved';
  await createProject();
  assert.equal(endpoint, '/api/workspace/edit-project');
  assert.deepEqual(sent, {id: 'original', action: 'workspace', value: 'C:/moved'});
  openProjectDialog();
  assert.equal(els.projectNameInput.readOnly, false);
  els.projectNameInput.value = 'new';
  els.projectWorkspaceInput.value = 'C:/new';
  await createProject();
  assert.equal(endpoint, '/api/workspace/project');
  assert.deepEqual(sent, {name: 'new', workspace: 'C:/new'});
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert completed.returncode == 0, completed.stderr
