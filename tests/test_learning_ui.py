"""Production learner UI behavior with a small DOM and controlled requests."""

from pathlib import Path
import re
import shutil
import subprocess

import pytest


SOURCE = (Path(__file__).resolve().parents[1] / "web/workspace.js").read_text(encoding="utf-8")


def functions(*names):
    selected = []
    for name in names:
        match = re.search(r"(?:async )?function " + re.escape(name) + r"\(", SOURCE)
        assert match, name
        following = re.search(r"\n(?:async )?function ", SOURCE[match.end():])
        end = match.end() + following.start() if following else len(SOURCE)
        selected.append(SOURCE[match.start():end])
    return "\n".join(selected)


def run_js(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for learner UI tests")
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert completed.returncode == 0, completed.stderr


def test_only_attempt_submission_sends_artifacts_and_prediction():
    run_js(r'''
const assert = require('node:assert/strict');
const els = new Proxy({}, {get: (target, key) => target[key] ||= {value: '', disabled: false, focus() {}}});
const ui = {data: {active_conversation_id: 'A', active_project: {}, status: 'idle'}, drafts: new Map(), attachmentDrafts: new Map()};
const isBusy = () => false, resizeComposer = () => {}, updateScrollPosition = () => {};
const applyState = value => { ui.data = value; };
function toast(value) { throw Error(value); }
const sent = [];
async function api(path, options) { sent.push({path, body: JSON.parse(options.body)}); return ui.data; }
''' + functions("selectedEvidencePaths", "attemptEvidencePayload", "evidenceFormValues", "submitAttempt", "sendMessage", "submitComposer") + r'''
(async () => {
  els.evidencePaths.value = ['src/main.py', 'notes.txt'].join('\n');
  els.attemptPrediction.value = 'I expect an empty result';
  els.attemptConfidence.value = '0';
  els.attemptObservedResult.value = 'It returned two rows';
  els.message.value = 'ordinary question';
  await sendMessage({preventDefault() {}});
  assert.deepEqual(sent[0].body, {message: 'ordinary question', conversation_id: 'A'});
  assert.equal(els.attemptPrediction.value, 'I expect an empty result');
  els.message.value = 'my explanation';
  await submitAttempt();
  assert.equal(sent[1].path, '/api/workspace/learning/attempt');
  assert.deepEqual(sent[1].body, {answer: 'my explanation', paths: ['src/main.py', 'notes.txt'],
    prediction: 'I expect an empty result', confidence: 0, observed_result: 'It returned two rows', conversation_id: 'A'});
  assert.equal(els.evidencePaths.value, '');
  els.evidencePaths.value = '../secret.txt';
  assert.throws(selectedEvidencePaths, /相对文件路径/);
  els.evidencePaths.value = 'a\nb\nc\nd';
  assert.throws(selectedEvidencePaths, /最多/);
})().catch(error => { console.error(error); process.exitCode = 1; });
''')


def test_pre_action_prediction_is_recorded_separately_and_not_overwritten_on_submission():
    run_js(r'''
const assert = require('node:assert/strict');
const els = new Proxy({}, {get: (target, key) => target[key] ||= {value: '', disabled: false}});
const ui = {data: {active_conversation_id: 'A', status: 'idle', lesson: {current_step: {id: 'step-1'}}}};
const isBusy = () => false;
let sent;
async function api(path, options) {
  sent = {path, body: JSON.parse(options.body)};
  return {...ui.data, lesson: {...ui.data.lesson, pending_prediction: {
    step_id: 'step-1', text: sent.body.prediction, confidence: sent.body.confidence, recorded_at: '2026-11-04T00:00:00+00:00'}}};
}
function applyState(data) { ui.data = data; }
function toast(message) { throw Error(message); }
''' + functions("selectedEvidencePaths", "attemptEvidencePayload", "recordPrediction") + r'''
(async () => {
  els.attemptPrediction.value = 'It will print two lines';
  els.attemptConfidence.value = '80';
  await recordPrediction();
  assert.equal(sent.path, '/api/workspace/learning/prediction');
  assert.deepEqual(sent.body, {prediction: 'It will print two lines', confidence: 80, conversation_id: 'A'});
  assert.equal(els.recordPrediction.disabled, true);
  els.attemptObservedResult.value = 'It printed one line';
  els.attemptPrediction.value = 'A later accidental edit';
  const attempt = attemptEvidencePayload();
  assert.equal(attempt.prediction, undefined);
  assert.equal(attempt.confidence, undefined);
  assert.equal(attempt.observed_result, 'It printed one line');
})().catch(error => { console.error(error); process.exitCode = 1; });
''')


def test_history_response_cannot_be_merged_into_another_conversation():
    run_js(r'''
const assert = require('node:assert/strict');
const els = {chatViewport: {scrollHeight: 100, scrollTop: 20}, loadHistory: {}};
const ui = {data: {active_conversation_id: 'A', state_epoch: 'server', events: [{id: 101}]},
  historyEvents: new Map(), historyHasMore: null, historyRequest: null};
let resolve, renders = 0;
function api() { return new Promise(done => { resolve = done; }); }
function renderChat() { renders += 1; els.chatViewport.scrollHeight = 200; }
function toast(message) { throw Error(message); }
''' + functions("updateHistoryButton", "loadEarlierHistory") + r'''
(async () => {
  const pending = loadEarlierHistory();
  ui.data = {active_conversation_id: 'B', state_epoch: 'server', events: [{id: 201}]};
  ui.historyRequest = null;
  resolve({conversation_id: 'A', events: [{id: 1, text: 'old A'}], has_more: false});
  await pending;
  assert.equal(ui.historyEvents.size, 0);
  assert.equal(renders, 0);
  const current = loadEarlierHistory();
  resolve({conversation_id: 'B', events: [{id: 101, text: 'old B'}], has_more: false});
  await current;
  assert.equal(ui.historyEvents.get(101).text, 'old B');
  assert.equal(els.chatViewport.scrollTop, 120);
  assert.equal(els.loadHistory.hidden, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
''')


def test_evidence_cards_and_export_show_checks_without_raw_snapshot_content():
    run_js(r'''
const assert = require('node:assert/strict');
const node = () => ({children: [], append(...items) { this.children.push(...items); }, replaceChildren() {this.children = [];}});
const document = {createElement: node};
const els = {evidenceCards: node()};
function textOf(item) { return [item.textContent || '', ...(item.children || []).map(textOf)].join('\n'); }
''' + functions("studyText", "renderEvidenceCards", "portfolioMarkdown") + r'''
const artifact = {path: 'essay.txt', hash: 'a'.repeat(64), size: 5, content: 'SNAPSHOT_MUST_NOT_BE_RENDERED',
  checks: [{name: 'UTF-8 text', passed: true, detail: 'snapshot captured'}]};
renderEvidenceCards([{task: 'Explain the change', explanation: 'I changed the opening',
  prediction: 'A clear claim', observed_result: 'Readers understood it', confidence: 75,
  feedback: 'Good explanation', practice: {hint_level: 1}, artifacts: [artifact]}]);
const rendered = textOf(els.evidenceCards);
assert.match(rendered, /UTF-8 text/);
assert.match(rendered, /Good explanation/);
assert.match(rendered, /75%/);
assert.doesNotMatch(rendered, /SNAPSHOT_MUST_NOT_BE_RENDERED/);
const result = portfolioMarkdown({skills: [{goal: 'Write clearly', attempt_count: 2, independent_successes: 1,
  delayed_verified: true, artifacts: [artifact]}]});
assert.match(result, /隔日迁移已验证/);
assert.match(result, /essay.txt/);
assert.doesNotMatch(result, /SNAPSHOT_MUST_NOT_BE_RENDERED/);
''')


def test_practice_reset_requires_current_context_and_explicit_confirmation():
    run_js(r'''
const assert = require('node:assert/strict');
const ui = {data: {active_conversation_id: 'B', state_epoch: 'new', status: 'idle'},
  practiceContext: {conversationId: 'A', epoch: 'old'}, practiceLoading: false};
const els = {resetPractice: {}, practiceDialog: {close() {}}};
let confirmed = false, prompts = 0, calls = 0;
const window = {confirm() { prompts += 1; return confirmed; }};
const isBusy = () => false, applyState = () => {}, toast = () => {};
async function api() { calls += 1; return ui.data; }
''' + functions("resetPracticeCopy") + r'''
(async () => {
  await resetPracticeCopy();
  assert.equal(prompts, 0);
  assert.equal(calls, 0);
  ui.practiceContext = {conversationId: 'B', epoch: 'new'};
  await resetPracticeCopy();
  assert.equal(prompts, 1);
  assert.equal(calls, 0);
  confirmed = true;
  await resetPracticeCopy();
  assert.equal(calls, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
''')
