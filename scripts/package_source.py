"""Export a clean source snapshot, excluding local history and private runtime data.

Run ``python -m scripts.package_source`` after preparing the public documentation.
The explicit allowlist is independent of Git tracking and ignore rules.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable
import zipfile

from dotenv import dotenv_values

from config import APP_VERSION


ROOT_FILES = (
    ".env.example", ".gitignore", "README.md", "README.zh-CN.md", "LICENSE",
    "SECURITY.md", "pyproject.toml", "requirements.txt", "requirements-desktop.txt",
    "requirements-build.txt", "windows_version.txt", "build_desktop.ps1",
    "build_exe.ps1", "main.py", "cli.py", "config.py", "desktop_app.py", "web_ui.py",
)
OPTIONAL_ROOT_FILES = ("THIRD_PARTY_NOTICES.md",)
PYTHON_TREES = ("agent", "learning", "storage", "scripts", "tests")
WEB_SUFFIXES = {".css", ".js", ".html", ".txt", ".md"}
SKIP_DIRECTORIES = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "node_modules", "build", "dist", "verification", "data",
}
TOKEN_PATTERNS = (
    re.compile(rb"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{40,}"),
    re.compile(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{24,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
)


class SourceExportError(RuntimeError):
    """A source snapshot cannot be safely exported."""


class SourcePrivacyError(SourceExportError):
    """A candidate public file contains a possible private credential."""


@dataclass(frozen=True)
class SourceRelease:
    directory: Path
    archive: Path
    checksum: Path
    manifest: Path
    file_count: int


def _reject_link(path: Path) -> None:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise SourceExportError(f"Linked source path is not allowed: {path.name}")


def _unlinked_absolute(path: Path) -> Path:
    absolute = path.absolute()
    for candidate in (absolute, *absolute.parents):
        _reject_link(candidate)
    return absolute.resolve()


def _tree_files(directory: Path, suffixes: set[str]) -> Iterable[Path]:
    _reject_link(directory)
    if not directory.is_dir():
        raise SourceExportError(f"Required source directory is missing: {directory.name}")
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        _reject_link(path)
        if path.is_dir():
            if path.name not in SKIP_DIRECTORIES and not path.name.startswith("."):
                yield from _tree_files(path, suffixes)
        elif path.is_file() and path.suffix.lower() in suffixes:
            yield path


def collect_source(root: Path, version: str = APP_VERSION) -> dict[str, bytes]:
    """Read only allowlisted regular files into one immutable export snapshot."""
    root = _unlinked_absolute(root)
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise SourceExportError("Release version is not a safe semantic version.")
    required = [*ROOT_FILES, f"RELEASE_NOTES_{version}.zh-CN.md", "data/.gitkeep"]
    paths: list[Path] = []
    for name in required:
        path = root / name
        for parent in path.parents:
            if parent == root:
                break
            _reject_link(parent)
        _reject_link(path)
        if not path.is_file():
            raise SourceExportError(f"Required public source file is missing: {name}")
        paths.append(path)
    for name in OPTIONAL_ROOT_FILES:
        path = root / name
        _reject_link(path)
        if path.is_file():
            paths.append(path)
    for name in PYTHON_TREES:
        paths.extend(_tree_files(root / name, {".py"}))
    paths.extend(_tree_files(root / "web", WEB_SUFFIXES))
    workflow_dir = root / ".github" / "workflows"
    _reject_link(root / ".github")
    _reject_link(workflow_dir)
    if workflow_dir.is_dir():
        for path in sorted(workflow_dir.iterdir()):
            _reject_link(path)
            if path.is_file() and path.suffix == ".yml":
                paths.append(path)
    snapshot = {}
    for path in sorted(set(paths)):
        relative = path.relative_to(root).as_posix()
        try:
            snapshot[relative] = path.read_bytes()
        except OSError:
            raise SourceExportError(f"Cannot read public source file: {relative}") from None
    return snapshot


def local_credential_values(root: Path) -> set[bytes]:
    """Read local secrets for comparison only; values never enter output artifacts."""
    secrets: set[bytes] = set()
    dotenv_path = root / ".env"
    config_path = root / "data" / "agent_config.json"
    if dotenv_path.exists():
        _reject_link(dotenv_path)
        try:
            values = dotenv_values(dotenv_path, interpolate=False)
        except (OSError, UnicodeError):
            raise SourceExportError("Cannot inspect local credential file: .env") from None
        for name, value in values.items():
            if value and ("KEY" in name.upper() or "TOKEN" in name.upper() or "SECRET" in name.upper() or "PASSWORD" in name.upper()) and len(value) >= 8:
                secrets.add(value.encode("utf-8"))
    if config_path.exists():
        _reject_link(config_path.parent)
        _reject_link(config_path)
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError):
            raise SourceExportError("Cannot inspect local credential file: data/agent_config.json") from None
        if not isinstance(config, dict):
            raise SourceExportError("Cannot inspect local credential file: data/agent_config.json")
        value = config.get("api_key")
        if isinstance(value, str) and len(value) >= 8:
            secrets.add(value.encode("utf-8"))
    return secrets


def scan_source(snapshot: dict[str, bytes], secrets: Iterable[bytes] = ()) -> None:
    """Fail closed with filenames only, never an excerpt of a matched secret."""
    needles = tuple(value for value in secrets if value)
    matches = [name for name, data in snapshot.items()
               if any(needle in data for needle in needles)
               or any(pattern.search(data) for pattern in TOKEN_PATTERNS)]
    if matches:
        raise SourcePrivacyError("Possible credentials found in public source files: " + ", ".join(sorted(matches)))


def package_source(root: Path, output_dir: Path | None = None, version: str = APP_VERSION) -> SourceRelease:
    """Create new source artifacts without copying development history or data."""
    root = _unlinked_absolute(root)
    snapshot = collect_source(root, version)
    scan_source(snapshot, local_credential_values(root))
    output_dir = _unlinked_absolute(output_dir) if output_dir is not None else root / "dist"
    name = f"MoreThanDone-{version}-source"
    directory = output_dir / name
    archive = output_dir / f"{name}.zip"
    checksum = output_dir / f"{name}.sha256.txt"
    for target in (output_dir, directory, archive, checksum):
        _reject_link(target)
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise SourceExportError("Source staging directory already exists and is not empty. Use a new --output-dir.")
    if archive.exists() or checksum.exists():
        raise SourceExportError("Source release artifacts already exist. Use a new --output-dir.")
    output_dir.mkdir(parents=True, exist_ok=True)
    directory.mkdir(exist_ok=True)
    manifest_data = "".join(f"{hashlib.sha256(data).hexdigest()}  {relative}\n" for relative, data in sorted(snapshot.items())).encode("utf-8")
    contents = {**snapshot, "SOURCE_MANIFEST.sha256": manifest_data}
    for relative, data in contents.items():
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    # Fixed ZIP timestamps keep clean source archives reproducible and avoid
    # carrying local file timestamps or absolute paths into the public artifact.
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for relative, data in sorted(contents.items()):
            info = zipfile.ZipInfo(f"{name}/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, data)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return SourceRelease(directory, archive, checksum, directory / "SOURCE_MANIFEST.sha256", len(snapshot))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="A fresh output folder; existing artifacts are never overwritten.")
    args = parser.parse_args()
    try:
        result = package_source(Path(__file__).resolve().parents[1], args.output_dir)
    except SourceExportError as error:
        parser.exit(1, f"Source export stopped: {error}\n")
    print(f"Source files: {result.file_count}\nDirectory: {result.directory}\nArchive: {result.archive}\nChecksum: {result.checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
