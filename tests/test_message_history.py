from copy import deepcopy
import json

from agent.conversations import ConversationStore
from agent.message_history import model_history


def tool_turn(call_id="check"):
    return [
        {"role": "user", "content": "check files"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": call_id, "content": "{}"},
        {"role": "assistant", "content": "done"},
    ]


def test_context_limit_never_splits_a_tool_turn():
    messages = tool_turn() + [{"role": "user", "content": str(i)} for i in range(199)]
    context = model_history(messages)
    assert len(context) == 199
    assert all(m["role"] == "user" for m in context)
    assert model_history(tool_turn(), limit=2) == tool_turn()


def test_old_orphan_and_missing_tool_results_are_repaired_only_for_model():
    messages = [tool_turn()[2], *tool_turn()[:2], {"role": "user", "content": "new turn"}]
    original = deepcopy(messages)
    context = model_history(messages)
    assert context[0]["role"] == "user"
    assert context[-2]["role"] == "tool"
    assert context[-2]["tool_call_id"] == "check"
    assert json.loads(context[-2]["content"])["cancelled"]
    assert messages == original


def test_long_history_survives_save_reload_and_has_safe_bounded_context(tmp_path):
    store = ConversationStore(tmp_path / "chats.json")
    document = store.empty()
    project = store.make_project("project", str(tmp_path))
    chat = store.make_conversation(project["id"])
    # Previously the 200-message cutoff retained this result but lost its call.
    chat["messages"] = tool_turn() + [{"role": "user", "content": str(i)} for i in range(198)]
    document.update(projects=[project], conversations=[chat])
    store.save(document)
    restored = store.load()["conversations"][0]["messages"]
    assert restored == chat["messages"] and len(restored) == 202
    assert model_history(restored)[0]["role"] == "user"
