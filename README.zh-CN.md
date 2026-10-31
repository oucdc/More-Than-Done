# More Than Done

[English](README.md) | [简体中文](README.zh-CN.md)

**0.1.6 版 · Windows 桌面学习工作台**

More Than Done 是一款教育 AI Agent，帮助你理解任务、亲手尝试、获得反馈，并逐步独立完成。它把本地项目、流式聊天和由程序记录进度的学习计划放在同一个工作台中。

## 功能

- 选择文件夹作为项目工作区，在项目下组织多段对话。
- 对话支持重命名、置顶、归档恢复、移动和删除；项目也有右键管理菜单。删除项目会删除其中的对话记录，工作区文件会保留。
- 模型流式回复，工具卡片显示退出码、超时、原因和命令输出。
- 按小步骤学习，提交自己的尝试、请求分级帮助，并在审阅后继续。获得帮助和独立完成分别记录。
- 项目文件夹移动后，可在项目右键菜单中“重新选择文件夹”，聊天和学习记录会保留。迁移检验未通过时，可点击“重新练习并检验”，完成新的独立练习和迁移题。
- 长代码、宽表格和长消息在各自框内滚动，不会撑宽整个界面。
- 选择文件修改与命令操作的批准方式，在设置中查看实际命令行和 Python 路径。
- 设置、项目、对话和学习记录保存在本机。

## 使用 Windows 发行版

1. 从[GitHub Releases 页面](https://github.com/oucdc/More-Than-Done/releases)下载 Windows ZIP，完整解压到可写入的文件夹。
2. 双击 `MoreThanDone.exe`。程序会自动检测 Microsoft Edge WebView2；缺少时询问是否安装，确认后联网下载、验证微软数字签名并显示安装进度，成功后继续打开软件。已有组件时直接启动。打开打包后的应用不需要另装 Python；执行 Python 学习任务仍需要可用的 Python 解释器。
3. 在“设置与权限”中填写自己的 API Key、模型和服务地址。代码默认使用 DeepSeek API 和 `deepseek-flash` 模型；工作区工具需要模型支持工具调用。
4. 创建项目并选择工作区文件夹。首次启动显示“暂无项目”。

升级前请关闭所有正在运行的版本，再替换 EXE。把原来的 `data` 文件夹保留在新 EXE 同目录即可沿用设置和对话。向别人分享程序时不要带上自己的 `data` 文件夹。

首次补装需要联网，当前 ZIP 不包含完整离线运行时。自动安装失败时会提供[微软官方下载页](https://developer.microsoft.com/microsoft-edge/webview2/#download-section)，可手动安装 Evergreen Standalone Installer x64（也可在其他电脑下载后拷贝安装），然后重新打开软件。WebView2 是界面组件，使用软件时不需要打开 Edge 浏览器。

## 归档保存在哪里

项目/对话归档仍保存在 EXE 同目录的 `data/conversations.json`，只标记归档，不移动、压缩或复制工作区文件夹；原文件夹仍在你选择的位置。点击侧栏“已归档”可查看并恢复。源码运行时位于源码目录的 `data/conversations.json`；用 `--data-dir` 启动时位于指定目录。归档不等于备份，备份时请同时保存 `data` 和工作区文件。`data` 可能含密钥和私人对话，不能随发行包分享。

## 从源码运行

桌面窗口和原生文件夹选择器面向 Windows，开发与本地测试已在 Python 3.12 验证。请使用已安装的 Python 3.12；如果 `python` 打开安装提示，请改用解释器完整路径或可用的 Python 启动器。

在源码目录的 PowerShell 中运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-desktop.txt
.\.venv\Scripts\python.exe -m desktop_app
```

无需激活虚拟环境，可以直接使用上面的解释器路径。应用启动后可以在设置页填写 API Key。也可以复制空白模板，再编辑本机生成的文件：

```powershell
Copy-Item .env.example .env
```

真实密钥只填写在 `.env` 或本机设置中，`.env.example` 的 `DEEPSEEK_API_KEY` 应保持为空。`.env` 还可以配置 `DEEPSEEK_MODEL` 和 `DEEPSEEK_BASE_URL`。

设置 `.env` 后，也可以使用浏览器和命令行入口：

```powershell
.\.venv\Scripts\python.exe -m web_ui --no-open
.\.venv\Scripts\python.exe -m main
```

浏览器服务默认监听 `127.0.0.1:8765`，打开 `/workspace` 可进入项目工作台，用 `--port` 指定其他端口。桌面版内部使用自动选择的本机回环端口。

## 测试与打包

自动测试使用模拟模型客户端，不需要真实 API Key：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check web/workspace.js
```

Node.js 只用于 JavaScript 语法检查。原生布局验收依赖 Edge WebView2，会打开使用临时数据的验收窗口：

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_message_layout
```

构建 Windows EXE 并整理便携发行包：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_desktop.ps1 -OutputDirectory .\dist\MoreThanDone-0.1.6-windows-x64
.\.venv\Scripts\python.exe -m scripts.package_release
```

构建脚本会安装桌面与打包依赖。发行目录包含 EXE、使用说明和第三方许可证信息；ZIP 和 SHA-256 校验文件输出到 `dist`。请使用发行版、源码打包脚本，不要直接压缩整个开发文件夹。

## 数据、隐私与权限

- `data/agent_config.json` 可能以**明文**保存 API Key，`.env` 也可能含凭据。两者都不是加密凭据保险箱。
- `data/conversations.json` 包含项目路径、聊天消息、工具事件和学习记录，其中包括真实提交的作答文本。命令行模式另有保存在 `data` 下的学习状态和归档。
- 模型所需的提示词、对话内容和工具结果会发送给你配置的模型服务商。保存在本机不代表所有处理都离线完成。
- 文件工具会保护凭据文件，命令环境会剔除常见的 API Key 变量。但命令仍按当前操作系统用户权限运行，**不是操作系统沙箱**。批准前请查看操作内容，仅在确实需要时使用完全访问权限。
- 运行数据、环境文件、虚拟环境、构建产物和本机验收资料不应进入公开源码包。每次推送前都应检查暂存文件，详见 [SECURITY.md](SECURITY.md)。

教学内容和定性审阅由模型提供，程序记录帮助程度及步骤推进，但无法保证模型的评估正确。重要结果请自行核实。

## 源码结构

| 路径 | 用途 |
| --- | --- |
| `agent/` | 模型接入、工作区工具、权限、项目及对话管理 |
| `learning/` | 学习状态、证据和进度规则 |
| `storage/` | 本地 JSON 存储 |
| `web/` | 桌面/浏览器界面及随附 UI 依赖 |
| `scripts/` | 本地检查、隔离预览和打包辅助脚本 |
| `tests/` | 使用模拟模型响应的回归测试 |
| `desktop_app.py` | 原生桌面入口 |
| `web_ui.py` | 本机 HTTP 界面 |

## 许可证

项目代码采用 [MIT 许可证](LICENSE)。第三方组件保留各自许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。提交贡献时请提供复现方式和相关测试，不要包含真实密钥或私人学习数据。
