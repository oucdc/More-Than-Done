"""User continuation and model review must keep chat and learning state aligned."""
import json
import time
from types import SimpleNamespace

import pytest

from agent.agent_config import AgentConfigStore
from agent.local_agent import AgentSession
from config import Settings


def response(content=None, *, tool=None, arguments=None):
    calls = [SimpleNamespace(id='review-1', function=SimpleNamespace(name=tool, arguments=json.dumps(arguments, ensure_ascii=False)))] if tool else None
    return SimpleNamespace(content=content, tool_calls=calls)


class ReviewingClient:
    def __init__(self, quality='sound', *, prose_only=False):
        self.quality = quality
        self.prose_only = prose_only
        self.contexts = []

    def complete_tool_call(self, messages, *, tools, max_tokens):
        context = json.loads(messages[1]['content'].split('\n', 1)[1])
        self.contexts.append(context)
        if self.prose_only:
            return response('好，进入第 2 步。PREMATURE_NEXT_STEP')
        if context['submission']:
            return response(tool='review_learning_attempt', arguments={
                'submission_id': context['submission']['id'],
                'correctness': 'correct', 'reasoning_quality': self.quality,
                'evidence': '依据真实作答，检查输出和解释是否齐全。',
                'feedback': '输出正确。' if self.quality == 'sound' else '输出正确，但还缺少解释。',
            })
        if context['current_step']['id'] == 'step-2':
            return response('当前是第二步，请自己尝试新任务。')
        # Intentionally misbehaving provider response after an incomplete review.
        return response('好，进入第 2 步。PREMATURE_NEXT_STEP')


class StreamingProseClient(ReviewingClient):
    def stream_tool_call(self, messages, *, tools, max_tokens, on_delta, cancel_event):
        answer = self.complete_tool_call(messages, tools=tools, max_tokens=max_tokens)
        for text in ('好，进入第 2 步。', 'PREMATURE_NEXT_STEP'):
            on_delta(text)
        return answer


def make_session(tmp_path, client):
    config = AgentConfigStore(tmp_path / 'agent.json', settings_loader=lambda: Settings(api_key=''))
    config.update({'workspace': str(tmp_path), 'api_key': 'isolated-test-key'})
    session = AgentSession(config, client_factory=lambda _: client)
    session.lesson.plan({'goal': '学习 Python', 'steps': [
        {'title': '运行一行 Python', 'task': '运行一行代码并解释 -c 的含义', 'success_criteria': '说明真实输出及命令的含义', 'kind': 'guided'},
        {'title': '独立创建脚本', 'task': '自己创建并运行脚本', 'success_criteria': '提供自己的脚本和运行依据', 'kind': 'independent'},
    ]})
    return session


def finish(session):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if session.worker is not None:
            session.worker.join(timeout=.05)
        if session.worker is None or not session.worker.is_alive():
            assert session.status != 'error', session.snapshot()['events']
            return session.snapshot()
    raise AssertionError('Teaching turn did not finish')


def submit_and_review(session, quality='sound'):
    submission = session.lesson.submit('输出是 hello 7；-c 后面的代码交给解释器直接执行，没有保存成文件。')
    session.lesson.review({'submission_id': submission['id'], 'correctness': 'correct', 'reasoning_quality': quality, 'evidence': '依据真实作答。', 'feedback': '已检查作答。'})


def assistant_text(state):
    return '\n'.join(event.get('text', '') for event in state['events'] if event['kind'] == 'assistant')


def test_chat_continue_advances_reviewed_step_before_model_receives_context(tmp_path):
    client = ReviewingClient()
    session = make_session(tmp_path, client)
    submit_and_review(session)
    session.send('继续')
    state = finish(session)
    assert state['lesson']['current'] == 1
    assert state['lesson']['steps'][0]['status'] == 'completed'
    assert client.contexts[0]['current_step']['id'] == 'step-2'
    assert len(state['lesson']['attempts']) == 1
    assert not state['lesson']['mastery_verified']


def test_chat_continue_reviews_pending_answer_then_advances_only_if_successful(tmp_path):
    client = ReviewingClient()
    session = make_session(tmp_path, client)
    submitted = session.lesson.submit('输出 hello 7；-c 直接执行命令字符串，没有生成文件。')
    session.send('下一步')
    state = finish(session)
    assert client.contexts[0]['submission']['id'] == submitted['id']
    assert state['lesson']['current'] == 1
    assert not state['lesson']['pending_review']
    assert state['lesson']['attempts'][0]['id'] == submitted['id']
    assert client.contexts[-1]['current_step']['id'] == 'step-2'


def test_partial_reasoning_cannot_be_advanced_or_presented_as_second_step(tmp_path):
    client = ReviewingClient(quality='partial')
    session = make_session(tmp_path, client)
    session.lesson.submit('hello 7')
    session.send('继续')
    state = finish(session)
    assert state['lesson']['current'] == 0
    assert state['lesson']['current_step']['status'] == 'needs_revision'
    assert not state['lesson']['can_continue']
    assert 'PREMATURE_NEXT_STEP' not in assistant_text(state)
    assert 'PREMATURE_NEXT_STEP' not in str(session.export_conversation()[0])


@pytest.mark.parametrize('streaming', [False, True])
def test_pending_answer_without_review_never_publishes_premature_next_step(tmp_path, streaming):
    client_class = StreamingProseClient if streaming else ReviewingClient
    client = client_class(prose_only=True)
    session = make_session(tmp_path, client)
    submitted = session.lesson.submit('没有生成文件。')
    session.send('继续')
    state = finish(session)
    assert state['lesson']['current'] == 0
    assert state['lesson']['pending_review']
    assert session.lesson.export()['submission']['id'] == submitted['id']
    assert 'PREMATURE_NEXT_STEP' not in assistant_text(state)
    assert 'PREMATURE_NEXT_STEP' not in str(session.export_conversation()[0])
    assert len(client.contexts) <= 3  # bounded repair attempts, never a full tool-loop spin


def test_continue_explaining_text_does_not_implicitly_advance_reviewed_step(tmp_path):
    client = ReviewingClient()
    session = make_session(tmp_path, client)
    submit_and_review(session)
    session.send('继续解释这段命令，不要进入下一步')
    state = finish(session)
    assert state['lesson']['current'] == 0
    assert client.contexts[0]['current_step']['id'] == 'step-1'


def test_unreviewed_step_without_submission_cannot_be_advanced_by_continue(tmp_path):
    client = ReviewingClient()
    session = make_session(tmp_path, client)
    session.send('继续')
    state = finish(session)
    assert state['lesson']['current'] == 0
    assert state['lesson']['current_step']['status'] == 'pending'
    assert not state['lesson']['attempts']
    assert 'PREMATURE_NEXT_STEP' not in assistant_text(state)
    assert 'PREMATURE_NEXT_STEP' not in str(session.export_conversation()[0])
