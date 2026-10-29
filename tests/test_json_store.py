import json
import re

import pytest

from learning.state import LearningEvent, LearningStage, LearningState
from storage.json_store import (
    LearningDataFormatError,
    LearningStoreReadError,
    LearningStoreWriteError,
    JsonLearningStore,
    UnsupportedSchemaVersionError,
    SCHEMA_VERSION,
)


def test_missing_file_returns_none_and_load_or_start_is_fresh(tmp_path):
    store = JsonLearningStore(tmp_path / "missing.json")

    assert store.load() is None
    assert store.load_or_start() == LearningState()
    assert not store.path.exists()


def test_save_and_load_round_trip_complete_state(tmp_path):
    store = JsonLearningStore(tmp_path / "nested" / "learning.json")
    state = LearningState(
        stage=LearningStage.MASTERY_CHECK,
        goal="理解函数",
        hint_level=1,
        events=(
            LearningEvent.GOAL_CONFIRMED,
            LearningEvent.ANSWER_REQUESTED,
            LearningEvent.MASTERY_ANSWER_SUBMITTED,
        ),
        skipped_stages=(LearningStage.BASELINE_ASSESSMENT,),
        answer_viewed_for_current_exercise=True,
        mastery_answer_submitted=True,
        mastery_exercise_repeated=True,
        active_exercise="题目",
        active_success_criteria="说明通分步骤",
        active_reference_answer="3/4",
        recent_exercises=("旧题", "题目"),
        baseline_result="correct:sound",
        learner_level="基础扎实",
    )

    store.save(state)

    assert store.load() == state
    assert not store.path.read_bytes().startswith(b"\xef\xbb\xbf")
    document = json.loads(store.path.read_text(encoding="utf-8"))
    assert document["schema_version"] == 4
    assert document["state"]["goal"] == "理解函数"
    assert list(store.path.parent.glob(f".{store.path.name}.*.tmp")) == []


def test_utf8_bom_file_loads_without_changing_original_bytes(tmp_path):
    state = LearningState(goal="读取带 BOM 的存档")
    content = json.dumps(
        {"schema_version": SCHEMA_VERSION, "state": state.to_dict()},
        ensure_ascii=False,
    ).encode("utf-8")
    original = b"\xef\xbb\xbf" + content
    path = tmp_path / "learning.json"
    path.write_bytes(original)

    restored = JsonLearningStore(path).load()

    assert restored == state
    assert path.read_bytes() == original


def test_save_replaces_previous_state_atomically(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    first = LearningState(goal="First goal")
    second = LearningState(goal="Second goal")

    store.save(first)
    store.save(second)

    assert store.load() == second


def test_archive_copies_current_state_without_changing_original(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    store.save(LearningState(goal="Archive this session"))
    original = store.path.read_bytes()

    archived_path = store.archive()

    assert archived_path is not None
    assert archived_path.parent == tmp_path / "archive"
    assert re.fullmatch(r"learning-\d{8}T\d{6}Z\.json", archived_path.name)
    assert archived_path.read_bytes() == original
    assert store.path.read_bytes() == original


def test_archive_missing_save_returns_none_without_creating_directory(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")

    assert store.archive() is None
    assert not (tmp_path / "archive").exists()


def test_archive_names_are_unique_and_keep_all_previous_copies(tmp_path):
    store = JsonLearningStore(tmp_path / "learning.json")
    store.save(LearningState(goal="Archive twice"))
    original = store.path.read_bytes()

    first = store.archive()
    second = store.archive()

    assert first is not None and second is not None
    assert first != second
    assert first.read_bytes() == original
    assert second.read_bytes() == original


def test_archive_replace_failure_preserves_source_and_cleans_temporary_file(tmp_path, monkeypatch):
    store = JsonLearningStore(tmp_path / "learning.json")
    store.save(LearningState(goal="Keep source"))
    original = store.path.read_bytes()

    def fail_replace(_source, _destination):
        raise PermissionError("simulated archive failure")

    monkeypatch.setattr("storage.json_store.os.replace", fail_replace)

    with pytest.raises(LearningStoreWriteError):
        store.archive()

    assert store.path.read_bytes() == original
    assert list((tmp_path / "archive").glob("*.json")) == []
    assert list((tmp_path / "archive").glob("*.tmp")) == []


def test_failed_replace_keeps_previous_file_and_cleans_temp_file(tmp_path, monkeypatch):
    store = JsonLearningStore(tmp_path / "learning.json")
    previous = LearningState(goal="Saved goal")
    store.save(previous)
    original_bytes = store.path.read_bytes()

    def fail_replace(source, destination):
        raise PermissionError("simulated replace failure")

    monkeypatch.setattr("storage.json_store.os.replace", fail_replace)
    with pytest.raises(LearningStoreWriteError):
        store.save(LearningState(goal="New goal"))

    assert store.path.read_bytes() == original_bytes
    assert store.load() == previous
    assert list(store.path.parent.glob(f".{store.path.name}.*.tmp")) == []


def test_write_failure_is_reported(tmp_path):
    blocker = tmp_path / "existing-file"
    blocker.write_text("file blocks parent directory", encoding="utf-8")
    store = JsonLearningStore(blocker / "learning.json")

    with pytest.raises(LearningStoreWriteError):
        store.save(LearningState())


@pytest.mark.parametrize("content", ["{", "not json", "[]", "null"])
def test_corrupt_json_is_rejected_without_overwriting_original(tmp_path, content):
    path = tmp_path / "learning.json"
    path.write_text(content, encoding="utf-8")
    store = JsonLearningStore(path)

    with pytest.raises(LearningDataFormatError):
        store.load()

    assert path.read_text(encoding="utf-8") == content


def test_invalid_utf8_is_reported_as_data_format_error(tmp_path):
    path = tmp_path / "learning.json"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(LearningDataFormatError, match="UTF-8"):
        JsonLearningStore(path).load()


def test_unreadable_path_is_reported(tmp_path):
    path = tmp_path / "directory"
    path.mkdir()

    with pytest.raises(LearningStoreReadError):
        JsonLearningStore(path).load()


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"schema_version": 1},
        {"schema_version": 1, "state": {}},
        {"schema_version": 1, "state": {"stage": "not-a-stage"}},
        {"schema_version": 1, "state": {}, "unexpected": True},
        {"schema_version": True, "state": {}},
    ],
)
def test_incomplete_or_invalid_envelopes_are_rejected(tmp_path, document):
    path = tmp_path / "learning.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(LearningDataFormatError):
        JsonLearningStore(path).load()


def test_incomplete_state_is_rejected_and_preserved(tmp_path):
    path = tmp_path / "learning.json"
    original = json.dumps(
        {
            "schema_version": 1,
            "state": {
                "stage": "goal_clarification",
                "goal": None,
                "hint_level": 0,
            },
        }
    )
    path.write_text(original, encoding="utf-8")

    with pytest.raises(LearningDataFormatError, match="missing required fields"):
        JsonLearningStore(path).load()

    assert path.read_text(encoding="utf-8") == original


def test_unknown_state_fields_are_rejected(tmp_path):
    state = LearningState().to_dict()
    state["mastery_score"] = 1.0
    path = tmp_path / "learning.json"
    path.write_text(json.dumps({"schema_version": 1, "state": state}), encoding="utf-8")

    with pytest.raises(LearningDataFormatError, match="unsupported fields"):
        JsonLearningStore(path).load()


def test_unsupported_schema_version_is_not_overwritten(tmp_path):
    path = tmp_path / "learning.json"
    original = json.dumps({"schema_version": 99, "state": {}})
    path.write_text(original, encoding="utf-8")

    with pytest.raises(UnsupportedSchemaVersionError, match="版本 99"):
        JsonLearningStore(path).load()

    assert path.read_text(encoding="utf-8") == original


def legacy_v3_state(**updates):
    state = LearningState(goal="Learn fractions").to_dict()
    for field_name in (
        "answer_viewed_for_current_exercise", "mastery_exercise_repeated",
        "active_success_criteria", "active_reference_answer", "recent_exercises",
        "baseline_result", "learner_level",
    ):
        state.pop(field_name)
    state.update({
        "answer_viewed_in_independent": False,
        "answer_viewed_in_mastery": False,
        "answer_viewed_in_guided": False,
    })
    state.update(updates)
    return state


def test_version_one_state_is_loaded_with_v4_defaults(tmp_path):
    state = legacy_v3_state()
    state.pop("answer_viewed_in_guided")
    state.pop("independent_practice_skipped")
    state.pop("active_exercise")
    state.pop("practice_attempts")
    path = tmp_path / "learning.json"
    original = json.dumps({"schema_version": 1, "state": state})
    path.write_text(original, encoding="utf-8")

    store = JsonLearningStore(path)
    restored = store.load()

    assert restored == LearningState(goal="Learn fractions")
    assert path.read_text(encoding="utf-8") == original

    store.save(restored)
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 4


def test_version_two_state_migrates_active_exercise_to_recent_list(tmp_path):
    state = legacy_v3_state(active_exercise="Solve x + 2 = 5")
    path = tmp_path / "learning.json"
    path.write_text(json.dumps({"schema_version": 2, "state": state}), encoding="utf-8")

    restored = JsonLearningStore(path).load()

    assert restored.active_exercise == "Solve x + 2 = 5"
    assert restored.recent_exercises == ("Solve x + 2 = 5",)
    assert restored.answer_viewed_for_current_exercise is False


def test_version_two_state_without_active_exercise_migrates_with_empty_recent_list(tmp_path):
    state = legacy_v3_state()
    state.pop("active_exercise")
    path = tmp_path / "learning.json"
    path.write_text(json.dumps({"schema_version": 2, "state": state}), encoding="utf-8")

    restored = JsonLearningStore(path).load()

    assert restored.active_exercise is None
    assert restored.recent_exercises == ()


def test_version_two_missing_newer_fields_are_filled_with_defaults(tmp_path):
    state = legacy_v3_state()
    state.pop("independent_practice_skipped")
    state.pop("practice_attempts")
    path = tmp_path / "learning.json"
    path.write_text(json.dumps({"schema_version": 2, "state": state}), encoding="utf-8")

    restored = JsonLearningStore(path).load()

    assert restored.independent_practice_skipped is False
    assert restored.practice_attempts == ()


def test_version_three_maps_old_answer_flag_and_active_exercise(tmp_path):
    state = legacy_v3_state(
        active_exercise="Calculate 1/2 + 1/4",
        answer_viewed_in_independent=True,
    )
    path = tmp_path / "learning.json"
    path.write_text(json.dumps({"schema_version": 3, "state": state}), encoding="utf-8")

    restored = JsonLearningStore(path).load()

    assert restored.answer_viewed_for_current_exercise is True
    assert restored.recent_exercises == ("Calculate 1/2 + 1/4",)


def test_version_three_migrates_when_active_exercise_is_missing(tmp_path):
    state = legacy_v3_state()
    state.pop("active_exercise")
    path = tmp_path / "learning.json"
    path.write_text(json.dumps({"schema_version": 3, "state": state}), encoding="utf-8")

    restored = JsonLearningStore(path).load()

    assert restored.active_exercise is None
    assert restored.recent_exercises == ()


def test_v4_missing_fields_are_rejected_and_original_is_preserved(tmp_path):
    state = LearningState().to_dict()
    state.pop("learner_level")
    path = tmp_path / "learning.json"
    original = json.dumps({"schema_version": 4, "state": state})
    path.write_text(original, encoding="utf-8")

    with pytest.raises(LearningDataFormatError, match="missing required fields"):
        JsonLearningStore(path).load()

    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "mutation",
    [
        lambda state: state.update(unexpected_field=True),
        lambda state: state.update(answer_viewed_for_current_exercise="false"),
        lambda state: state.update(recent_exercises=["q"] * 11),
    ],
)
def test_v4_unknown_or_invalid_fields_are_rejected_and_preserved(tmp_path, mutation):
    state = LearningState().to_dict()
    mutation(state)
    path = tmp_path / "learning.json"
    original = json.dumps({"schema_version": 4, "state": state})
    path.write_text(original, encoding="utf-8")

    with pytest.raises(LearningDataFormatError):
        JsonLearningStore(path).load()

    assert path.read_text(encoding="utf-8") == original


def test_store_requires_a_learning_state(tmp_path):
    with pytest.raises(TypeError, match="LearningState"):
        JsonLearningStore(tmp_path / "learning.json").save({})
