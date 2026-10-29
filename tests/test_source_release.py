import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from scripts.package_source import (
    ROOT_FILES, SourceExportError, SourcePrivacyError, collect_source,
    package_source, scan_source,
)


VERSION = "9.8.7"


def source_root(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    for name in ROOT_FILES:
        (root / name).write_text("Public source placeholder.\n", encoding="utf-8")
    (root / ".env.example").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
    (root / f"RELEASE_NOTES_{VERSION}.zh-CN.md").write_text("Public release notes.\n", encoding="utf-8")
    for name in ("agent", "learning", "storage", "scripts", "tests"):
        directory = root / name
        directory.mkdir()
        (directory / "example.py").write_text("value = 1\n", encoding="utf-8")
    (root / "web").mkdir()
    (root / "web" / "workspace.html").write_text("<p>Public UI</p>\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / ".gitkeep").touch()
    return root


def test_explicit_allowlist_ignores_git_tracking_and_private_files(tmp_path):
    root = source_root(tmp_path)
    private = [".env", "HANDOFF.md", "model prompt.md", ".git/config", "data/agent_config.json",
               "data/conversations.json", "data/archive/learner.json", "verification/screenshot.png",
               "agent/__pycache__/cache.py", "scripts/.venv/leak.py", "web/debug.log", "web/private.env",
               "tests/results.json", "MoreThanDone.exe"]
    for name in private:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("DO NOT EXPORT\n", encoding="utf-8")
    workflow = root / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: CI\n", encoding="utf-8")
    snapshot = collect_source(root, VERSION)
    assert not set(private).intersection(snapshot)
    assert "data/.gitkeep" in snapshot and "web/workspace.html" in snapshot
    assert ".github/workflows/ci.yml" in snapshot
    assert "agent/example.py" in snapshot
    assert all(b"DO NOT EXPORT" not in data for data in snapshot.values())


def test_source_zip_contains_only_snapshot_and_verifiable_manifest(tmp_path):
    root = source_root(tmp_path)
    (root / ".env").write_text("DEEPSEEK_API_KEY=local-only-secret-value\n", encoding="utf-8")
    (root / "data" / "agent_config.json").write_text(json.dumps({"api_key": "another-local-only-secret"}), encoding="utf-8")
    snapshot = collect_source(root, VERSION)
    release = package_source(root, version=VERSION)
    assert release.file_count == len(snapshot)
    assert not (release.directory / ".git").exists()
    assert not (release.directory / ".env").exists()
    assert {p.name for p in (release.directory / "data").iterdir()} == {".gitkeep"}
    prefix = f"MoreThanDone-{VERSION}-source/"
    with zipfile.ZipFile(release.archive) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {prefix + name for name in [*snapshot, "SOURCE_MANIFEST.sha256"]}
        for line in archive.read(prefix + "SOURCE_MANIFEST.sha256").decode().splitlines():
            digest, name = line.split("  ", 1)
            assert hashlib.sha256(archive.read(prefix + name)).hexdigest() == digest
    digest, filename = release.checksum.read_text(encoding="ascii").strip().split("  ", 1)
    assert filename == release.archive.name
    assert digest == hashlib.sha256(release.archive.read_bytes()).hexdigest()


def test_known_local_key_leak_stops_before_creating_artifacts_without_echoing_key(tmp_path):
    root = source_root(tmp_path)
    secret = "a-private-value-known-only-to-local-config"
    (root / "data" / "agent_config.json").write_text(json.dumps({"api_key": secret}), encoding="utf-8")
    (root / "agent" / "example.py").write_text(f"key = {secret!r}\n", encoding="utf-8")
    with pytest.raises(SourcePrivacyError) as error:
        package_source(root, version=VERSION)
    assert "agent/example.py" in str(error.value)
    assert secret not in str(error.value)
    assert not (root / "dist").exists()


@pytest.mark.parametrize("token", ["ghp_" + "A" * 36, "github_pat_" + "B" * 45,
                                   "sk-" + "C" * 32, "-----BEGIN " + "PRIVATE KEY-----"])
def test_recognizable_credentials_abort_without_echoing_values(token):
    with pytest.raises(SourcePrivacyError) as error:
        scan_source({"agent/leak.py": token.encode()})
    assert "agent/leak.py" in str(error.value)
    assert token not in str(error.value)


def test_export_never_overwrites_existing_stage_or_release(tmp_path):
    root = source_root(tmp_path)
    first = package_source(root, version=VERSION)
    sentinel = first.directory / "work-in-progress.txt"
    sentinel.write_text("Keep this file.\n", encoding="utf-8")
    original_archive = first.archive.read_bytes()
    with pytest.raises(SourceExportError, match="already exists"):
        package_source(root, version=VERSION)
    assert sentinel.read_text(encoding="utf-8") == "Keep this file.\n"
    assert first.archive.read_bytes() == original_archive
    second = package_source(root, output_dir=root / "other-artifacts", version=VERSION)
    assert second.archive.is_file()


@pytest.mark.parametrize("directory_link", [False, True])
def test_links_in_allowlisted_trees_are_rejected_not_followed(tmp_path, directory_link):
    root = source_root(tmp_path)
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "private.py").write_text("private = True\n", encoding="utf-8")
    link = root / "agent" / ("linked" if directory_link else "linked.py")
    try:
        link.symlink_to(outside if directory_link else outside / "private.py", target_is_directory=directory_link)
    except OSError:
        pytest.skip("This Windows environment does not permit test symlinks")
    with pytest.raises(SourceExportError, match="Linked source path"):
        package_source(root, version=VERSION)
    assert not (root / "dist").exists()


def test_version_cannot_escape_output_directory(tmp_path):
    root = source_root(tmp_path)
    with pytest.raises(SourceExportError, match="safe semantic version"):
        package_source(root, version="../../private")


def test_link_detection_is_fail_closed_before_public_file_read(tmp_path, monkeypatch):
    root = source_root(tmp_path)
    candidate = root / "agent" / "example.py"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == candidate or original(path))
    with pytest.raises(SourceExportError, match="Linked source path"):
        package_source(root, version=VERSION)
    assert not (root / "dist").exists()
