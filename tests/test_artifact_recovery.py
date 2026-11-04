"""Practice creation, cleanup and reset must preserve recoverable learner work."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.agent_config import AgentConfig
from agent.artifacts import create_practice, discard_practice, practice_state


def source_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "first.txt").write_bytes(b"first original\r\n")
    (source / "second.txt").write_bytes(b"second original\r\n")
    return AgentConfig(source, permission_mode="request"), tmp_path / "data"


@pytest.mark.parametrize("failed_name", ["first.txt", "second.txt", "originals.json"])
def test_partial_creation_removes_only_its_new_directory(tmp_path, monkeypatch, failed_name):
    config, data = source_files(tmp_path)
    existing = create_practice(config, ["first.txt"], data)
    first_uuid = existing.parent
    replace = __import__("os").replace

    def fail_selected(source, destination):
        if Path(destination).name == failed_name:
            raise OSError("creation write failed")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr("agent.artifacts.os.replace", fail_selected)
        with pytest.raises(OSError, match="creation write failed"):
            create_practice(config, ["first.txt", "second.txt"], data)
    assert set((data / "practice").iterdir()) == {first_uuid}
    assert (existing / "first.txt").read_bytes() == b"first original\r\n"
    assert (config.workspace / "second.txt").read_bytes() == b"second original\r\n"


def test_uuid_collision_never_deletes_an_existing_practice(tmp_path, monkeypatch):
    config, data = source_files(tmp_path)
    existing = create_practice(config, ["first.txt"], data)
    monkeypatch.setattr("agent.artifacts.uuid4", lambda: SimpleNamespace(hex=existing.parent.name))
    with pytest.raises(FileExistsError):
        create_practice(config, ["second.txt"], data)
    assert (existing / "first.txt").read_bytes() == b"first original\r\n"
    assert not (existing / "second.txt").exists()


def test_discard_checks_the_exact_uuid_boundary_and_is_retryable(tmp_path):
    config, data = source_files(tmp_path)
    workspace = create_practice(config, ["first.txt"], data)
    invalid = [data, data / "practice", workspace.parent,
               workspace.parent / "other", data / "practice/not-a-uuid/workspace", config.workspace]
    for path in invalid:
        with pytest.raises(ValueError):
            discard_practice(path, data)
        assert workspace.exists()
    discard_practice(workspace, data)
    discard_practice(workspace, data)
    assert not workspace.parent.exists()
    assert (config.workspace / "first.txt").read_bytes() == b"first original\r\n"


def test_discard_rejects_links_and_preserves_their_external_targets(tmp_path):
    config, data = source_files(tmp_path)
    workspace = create_practice(config, ["first.txt"], data)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    try:
        (workspace / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable")
    with pytest.raises(ValueError, match="链接"):
        discard_practice(workspace, data)
    assert workspace.exists()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_discard_is_fail_closed_for_a_detected_directory_junction(tmp_path, monkeypatch):
    config, data = source_files(tmp_path)
    workspace = create_practice(config, ["first.txt"], data)
    linked = workspace / "junction"
    linked.mkdir()
    original = getattr(Path, "is_junction", lambda path: False)
    monkeypatch.setattr(Path, "is_junction", lambda path: path == linked or original(path), raising=False)
    with pytest.raises(ValueError, match="链接"):
        discard_practice(workspace, data)
    assert workspace.exists() and linked.exists()


def test_failed_cleanup_reports_creation_error_and_retains_the_owned_folder(tmp_path, monkeypatch):
    config, data = source_files(tmp_path)

    def fail_write(*args):
        raise OSError("initial creation error")

    def fail_cleanup(*args):
        raise OSError("cleanup also denied")

    with monkeypatch.context() as patch:
        patch.setattr("agent.artifacts._write_text", fail_write)
        patch.setattr("agent.artifacts.shutil.rmtree", fail_cleanup)
        with pytest.raises(OSError, match="initial creation error") as failure:
            create_practice(config, ["first.txt"], data)
        assert "cleanup also denied" in str(failure.value)
        assert isinstance(failure.value.__cause__, OSError)
    leftovers = list((data / "practice").iterdir())
    assert len(leftovers) == 1
    discard_practice(leftovers[0] / "workspace", data)


def test_reset_failure_on_second_file_restores_the_first_edit(tmp_path, monkeypatch):
    config, data = source_files(tmp_path)
    workspace = create_practice(config, ["first.txt", "second.txt"], data)
    (workspace / "first.txt").write_bytes(b"first learner edit\r\n")
    (workspace / "second.txt").write_bytes(b"second learner edit\r\n")
    replace = __import__("os").replace

    def fail_second(source, destination):
        if Path(destination) == workspace / "second.txt":
            raise OSError("reset second failure")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr("agent.artifacts.os.replace", fail_second)
        with pytest.raises(OSError, match="reset second failure"):
            practice_state(workspace, data, reset=True)
    assert (workspace / "first.txt").read_bytes() == b"first learner edit\r\n"
    assert (workspace / "second.txt").read_bytes() == b"second learner edit\r\n"
    assert list(workspace.parent.glob("reset-recovery-*.json")) == []
    assert (config.workspace / "first.txt").read_bytes() == b"first original\r\n"


def test_failed_rollback_keeps_a_durable_backup_and_reports_original_error(tmp_path, monkeypatch):
    config, data = source_files(tmp_path)
    workspace = create_practice(config, ["first.txt", "second.txt"], data)
    (workspace / "first.txt").write_bytes(b"first learner edit\r\n")
    (workspace / "second.txt").write_bytes(b"second learner edit\r\n")
    replace = __import__("os").replace
    first_writes = 0

    def fail_reset_and_rollback(source, destination):
        nonlocal first_writes
        target = Path(destination)
        if target == workspace / "second.txt":
            raise OSError("original second-file failure")
        if target == workspace / "first.txt":
            first_writes += 1
            if first_writes == 2:
                raise OSError("rollback denied")
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr("agent.artifacts.os.replace", fail_reset_and_rollback)
        with pytest.raises(OSError, match="original second-file failure") as failure:
            practice_state(workspace, data, reset=True)
    backups = list(workspace.parent.glob("reset-recovery-*.json"))
    assert len(backups) == 1 and str(backups[0]) in str(failure.value)
    saved = json.loads(backups[0].read_text(encoding="utf-8"))
    before = {item["path"]: item for item in saved["files"]}
    assert before["first.txt"]["content"] == "first learner edit\r\n"
    assert before["second.txt"]["content"] == "second learner edit\r\n"
    assert "original second-file failure" in str(failure.value.__cause__)
    assert (workspace / "first.txt").read_bytes() == b"first original\r\n"
    assert (config.workspace / "first.txt").read_bytes() == b"first original\r\n"
