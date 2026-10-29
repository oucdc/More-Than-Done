$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$buildRequirements = Join-Path $projectRoot "requirements-build.txt"
$workPath = Join-Path $projectRoot "build\pyinstaller"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "找不到项目虚拟环境：$pythonPath。请先按 README 安装依赖。"
}

& $pythonPath -m pip install -r $buildRequirements
$installExitCode = $LASTEXITCODE
if ($installExitCode -ne 0) {
    throw "安装 EXE 打包工具失败（退出码 $installExitCode）。"
}

New-Item -ItemType Directory -Force -Path $workPath | Out-Null
$sslSidecarPath = (& $pythonPath -c "import ssl; from pathlib import Path; p = Path(ssl.__file__).with_name('codex-rejected-cas.pem'); print(p if p.is_file() else '')").Trim()
if ($LASTEXITCODE -ne 0) {
    throw "无法检查 Python SSL 运行时文件。"
}

$pyinstallerArguments = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "MoreThanDoneWeb",
    "--distpath", $projectRoot,
    "--workpath", $workPath,
    "--specpath", $workPath,
    "--add-data", "$projectRoot\web;web"
)
if ($sslSidecarPath) {
    # This Python runtime's ssl.py loads this additional CA distrust list at startup.
    $pyinstallerArguments += @("--add-data", "$sslSidecarPath;.")
}
$pyinstallerArguments += "web_ui.py"

Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath -m PyInstaller @pyinstallerArguments
    $buildExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($buildExitCode -ne 0) {
    throw "EXE 构建失败（退出码 $buildExitCode）。"
}

$executablePath = Join-Path $projectRoot "MoreThanDoneWeb.exe"
if (-not (Test-Path -LiteralPath $executablePath)) {
    throw "构建结束，但没有找到输出文件：$executablePath"
}

Write-Host "构建完成：$executablePath"
Write-Host "这是浏览器版本；桌面原生窗口请使用 build_desktop.ps1 生成 MoreThanDone.exe。"
