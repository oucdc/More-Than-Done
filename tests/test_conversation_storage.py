import json
import pytest

from agent.conversations import ConversationStore, ConversationStoreError


def saved_document(tmp_path):
    store = ConversationStore(tmp_path / "conversations.json")
    project = store.make_project("test", str(tmp_path))
    conversation = store.make_conversation(project["id"])
    document = {
        "schema_version": 1, "projects": [project], "conversations": [conversation],
        "active_project_id": project["id"], "active_conversation_id": conversation["id"],
    }
    store.save(document)
    return store, document, conversation


def test_normalization_preserves_history_beyond_visible_event_window(tmp_path):
    store, document, conversation = saved_document(tmp_path)
    conversation["events"] = [{"id": number, "kind": "user", "text": str(number)} for number in range(1, 701)]
    store.save(document)
    original = store.path.read_bytes()
    loaded = store.load()["conversations"][0]
    assert loaded["events"] == conversation["events"]
    assert loaded["events"][0]["id"] == 1
    assert store.path.read_bytes() == original


def test_repeated_load_uses_cache_but_returns_independent_documents(tmp_path, monkeypatch):
    store, document, conversation = saved_document(tmp_path)
    calls = []
    original = store.normalize

    def counted(value):
        calls.append(True)
        return original(value)

    monkeypatch.setattr(store, "normalize", counted)
    first = store.load()
    first["conversations"][0]["messages"].append({"role": "user", "content": "unsaved"})
    assert store.load()["conversations"][0]["messages"] == []
    assert len(calls) == 1
    conversation["title"] = "saved change"
    store.save(document)
    assert store.load()["conversations"][0]["title"] == "saved change"
    assert len(calls) == 2


def test_external_replacement_invalidates_cache(tmp_path):
    store, document, conversation = saved_document(tmp_path)
    store.load()
    other = ConversationStore(store.path)
    conversation["title"] = "another writer"
    other.save(document)
    assert store.load()["conversations"][0]["title"] == "another writer"


def test_corrupt_external_file_is_rejected_without_rewriting_or_returning_cache(tmp_path):
    store, document, _ = saved_document(tmp_path)
    store.load()
    damaged = b"{broken"
    store.path.write_bytes(damaged)
    with pytest.raises(ConversationStoreError, match="JSON"):
        store.load()
    assert store.path.read_bytes() == damaged
    store.path.write_text(json.dumps(document), encoding="utf-8")
    assert store.load()["active_project_id"] == document["active_project_id"]


def test_failed_save_keeps_cached_and_persisted_document(tmp_path, monkeypatch):
    store, document, conversation = saved_document(tmp_path)
    before = store.load()
    original = store.path.read_bytes()
    conversation["title"] = "failed change"

    def fail_replace(*args):
        raise PermissionError("simulated write failure")

    monkeypatch.setattr("agent.conversations.os.replace", fail_replace)
    with pytest.raises(ConversationStoreError, match="无法保存"):
        store.save(document)
    assert store.path.read_bytes() == original
    assert store.load() == before
    assert not list(tmp_path.glob(".conversations.json.*.tmp"))


def test_deleting_file_discards_cached_state(tmp_path):
    store, _, _ = saved_document(tmp_path)
    assert store.load()["projects"]
    store.path.unlink()
    assert store.load() == store.empty()
