"""Assemble a clean, portable Windows release from an already built executable."""
from __future__ import annotations
import hashlib
from importlib import metadata
from pathlib import Path
import shutil
import sys
import zipfile

from config import APP_VERSION

root = Path(__file__).resolve().parents[1]
release_name = f"MoreThanDone-{APP_VERSION}-windows-x64"
release_dir = root / "dist" / release_name
executable = release_dir / "MoreThanDone.exe"
if not executable.is_file():
    raise SystemExit(f"Build the executable first: {executable}")
shutil.copy2(root / f"RELEASE_NOTES_{APP_VERSION}.zh-CN.md", release_dir / "使用说明.md")
for public_notice in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
    notice = root / public_notice
    if notice.is_file():
        text = notice.read_text(encoding="utf-8")
        if public_notice == "THIRD_PARTY_NOTICES.md":
            text = text.replace("(web/vendor/README.md)", "(licenses/vendor-README.md)")
            text = text.replace("(web/vendor/", "(licenses/")
        (release_dir / public_notice).write_text(text, encoding="utf-8")
licenses = release_dir / "licenses"
licenses.mkdir(exist_ok=True)
shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python-LICENSE.txt")
for source in sorted((root / "web" / "vendor").glob("LICENSE-*")):
    shutil.copy2(source, licenses / source.name)
shutil.copy2(root / "web" / "vendor" / "README.md", licenses / "vendor-README.md")
for dist in metadata.distributions():
    name = dist.metadata.get("Name", "unknown")
    folder = licenses / f"{name}-{dist.version}"
    paths = []
    for entry in dist.files or []:
        entry_name = str(entry).replace("\\", "/")
        filename = Path(entry_name).name.lower()
        if ".dist-info/" in entry_name and ("license" in filename or "licence" in filename or "copying" in filename or filename == "notice"):
            source = Path(dist.locate_file(entry))
            if source.is_file():
                paths.append((entry_name, source))
    folder.mkdir(exist_ok=True)
    # Preserve distribution metadata even when a wheel omitted its license file.
    (folder / "METADATA.txt").write_text(dist.read_text("METADATA") or "", encoding="utf-8")
    for entry_name, source in paths:
        relative = entry_name.split(".dist-info/", 1)[1]
        dest = folder / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
archive = root / "dist" / f"{release_name}.zip"
with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
    for path in sorted(release_dir.rglob("*")):
        if path.is_file() and (path == executable or path.name in {"使用说明.md", "LICENSE", "THIRD_PARTY_NOTICES.md"} or licenses in path.parents):
            output.write(path, Path(release_name) / path.relative_to(release_dir))
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(root / "dist" / f"{release_name}.sha256.txt").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
print(f"Archive: {archive}\nSHA-256: {digest}")
