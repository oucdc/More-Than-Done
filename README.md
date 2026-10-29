# More Than Done

[English](README.md) | [简体中文](README.zh-CN.md)

**Version 0.1.5 · Windows desktop learning workspace**

More Than Done is an educational AI agent that helps you understand a task, try it yourself, receive feedback, and build the ability to work independently. It combines a local project workspace, streaming chat, and a learning plan whose progress is recorded by the application.

## Features

- Select a folder as a project workspace and keep conversations under that project.
- Rename, pin, archive, restore, move, and delete conversations; manage projects from their context menus. Deleting a project removes its saved conversations, not the workspace files.
- Receive streaming model replies and inspect readable tool results, exit codes, timeouts, and command output.
- Follow small learning steps, submit your own attempts, request graded help, and advance after review. Assisted work is recorded separately from independent evidence.
- Read long code, tables, and messages inside their own scroll regions without widening the window.
- Choose an approval mode for file changes and commands. Inspect the detected shell and Python path in settings.
- Keep settings, project history, and learning records on the local machine.

## Use the Windows release

1. Download the Windows release ZIP from the [GitHub Releases page](https://github.com/oucdc/More-Than-Done/releases) and extract it to a writable folder.
2. Open `MoreThanDone.exe`. The app detects Microsoft Edge WebView2 before opening. If missing, it asks to install it, downloads the official Microsoft bootstrapper, verifies its Microsoft digital signature, shows setup progress, and opens the workspace after successful installation. Existing installations start directly. The packaged app can open without a separate Python installation; executing Python tasks still requires an available Python interpreter.
3. Open **设置与权限** to configure your API key, model, and service address. The code defaults to DeepSeek's API and the `deepseek-flash` model. A model must support tool calls to use workspace tools.
4. Create a project and choose its workspace folder. A new installation starts with no projects.

To upgrade, close every running copy before replacing the executable. Keep the existing `data` folder beside the new executable to retain settings and conversations. Do not include that folder when sharing the app with another person.

First-time runtime installation needs internet access; this ZIP does not bundle a complete offline runtime. Failed setup provides the [Microsoft download page](https://developer.microsoft.com/microsoft-edge/webview2/#download-section). Install the Evergreen Standalone Installer x64 manually (it can be downloaded on another computer and copied over), then reopen the app. You do not need to open the Edge browser.

## Where archives are stored

Archived projects and conversations stay in `data/conversations.json` beside the EXE, marked as archived. Archiving does not move, compress, or copy the workspace folder; its files remain at the original location. Use **已归档** in the sidebar to restore records. Source runs use the source directory; `--data-dir` overrides the data directory. Archiving is not a backup: back up both `data` and workspace files. Never share personal `data` with a release because it can contain keys and private conversations.

## Run from source

The desktop app and its native folder picker target Windows. Development and local tests have been verified with Python 3.12. Use an installed Python 3.12 interpreter; if `python` opens an installation prompt, use its full executable path or a working Python launcher.

From PowerShell in the source directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-desktop.txt
.\.venv\Scripts\python.exe -m desktop_app
```

Virtual-environment activation is optional. The settings page accepts an API key after startup. Alternatively, copy the empty template and edit the new local file:

```powershell
Copy-Item .env.example .env
```

Only put a real key in `.env` or the app's local settings. Keep `DEEPSEEK_API_KEY` empty in `.env.example`. `DEEPSEEK_MODEL` and `DEEPSEEK_BASE_URL` can also be set in `.env`.

Other entry points, after configuring `.env`:

```powershell
.\.venv\Scripts\python.exe -m web_ui --no-open
.\.venv\Scripts\python.exe -m main
```

The browser server listens on `127.0.0.1:8765` by default. Open `/workspace` for the project workspace; use `--port` to choose another port. The desktop window uses an automatically chosen loopback port internally.

## Test and build

Automated tests use fake model clients and do not require a live API key:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check web/workspace.js
```

Node.js is only needed for the JavaScript syntax check. Native layout checks use Edge WebView2 and open a disposable test window:

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_message_layout
```

Build the Windows executable and assemble a portable release package:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_desktop.ps1 -OutputDirectory .\dist\MoreThanDone-0.1.5-windows-x64
.\.venv\Scripts\python.exe -m scripts.package_release
```

The build script installs the desktop/build dependencies. The release directory contains the executable, instructions, and third-party license information; the ZIP and SHA-256 file are written under `dist`. Use the release/source packaging scripts rather than compressing your entire working folder.

## Data, privacy, and permissions

- `data/agent_config.json` may contain the API key in **plain text**. `.env` may also contain credentials. Neither file is an encrypted credential store.
- `data/conversations.json` contains project paths, chat messages, tool events, and learning records, including submitted attempt text. The CLI uses separate learning state and archives under `data`.
- Prompts, conversation content, and tool results needed for a task are sent to the configured model provider. Local storage does not mean that all processing is offline.
- The file tools protect credential files, and command environments remove common API-key variables. Command execution still runs with your operating-system user permissions; it is **not an OS sandbox**. Review operations before approving them and use full access only when you intend to allow it.
- Runtime records, environment files, virtual environments, build output, and local verification artifacts do not belong in a public source archive. Review staged files before every push. See [SECURITY.md](SECURITY.md).

The model supplies teaching content and qualitative reviews. The application records help usage and advancement, but it cannot guarantee that a model's assessment is correct. Check consequential output yourself.

## Source layout

| Path | Purpose |
| --- | --- |
| `agent/` | Model integration, workspace tools, permissions, and project/conversation management |
| `learning/` | Learning state, evidence, and progress rules |
| `storage/` | Local JSON persistence |
| `web/` | Desktop/browser interface and bundled UI dependencies |
| `scripts/` | Local checks, isolated previews, and packaging helpers |
| `tests/` | Automated regression tests using fake model responses |
| `desktop_app.py` | Native desktop entry point |
| `web_ui.py` | Loopback HTTP interface |

## License

Project code is provided under the [MIT License](LICENSE). Third-party components retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Contributions should include a reproducible description and relevant tests, with no real credentials or private learner data.
