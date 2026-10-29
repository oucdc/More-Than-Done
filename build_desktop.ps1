param([string]$OutputDirectory = $PSScriptRoot)

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$buildRequirements = Join-Path $projectRoot "requirements-build.txt"
$desktopRequirements = Join-Path $projectRoot "requirements-desktop.txt"
$workPath = Join-Path $projectRoot "build\desktop"
$outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Project virtual environment not found: $pythonPath. Install dependencies first."
}

& $pythonPath -m pip install -r $desktopRequirements -r $buildRequirements
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install desktop build dependencies (exit $LASTEXITCODE)."
}

New-Item -ItemType Directory -Force -Path $workPath | Out-Null
$sslSidecarPath = (& $pythonPath -c "import ssl; from pathlib import Path; p = Path(ssl.__file__).with_name('codex-rejected-cas.pem'); print(p if p.is_file() else '')").Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the Python SSL runtime."
}

$pyinstallerArguments = @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "MoreThanDone",
    "--distpath", $outputPath,
    "--version-file", (Join-Path $projectRoot "windows_version.txt"),
    "--workpath", $workPath,
    "--specpath", $workPath,
    "--add-data", "$projectRoot\web;web",
    "--collect-all", "webview",
    "--collect-all", "pythonnet",
    "--collect-all", "clr_loader",
    "--hidden-import", "clr",
    "--hidden-import", "webview.platforms.edgechromium"
)
if ($sslSidecarPath) {
    $pyinstallerArguments += @("--add-data", "$sslSidecarPath;.")
}
$pyinstallerArguments += "desktop_app.py"

Push-Location -LiteralPath $projectRoot
try {
    & $pythonPath -m PyInstaller @pyinstallerArguments
    $buildExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($buildExitCode -ne 0) {
    throw "Desktop EXE build failed (exit $buildExitCode)."
}

$executablePath = Join-Path $outputPath "MoreThanDone.exe"
if (-not (Test-Path -LiteralPath $executablePath)) {
    throw "Build finished but output was not found: $executablePath"
}

Write-Host "Desktop build complete: $executablePath"
Write-Host "Double-click MoreThanDone.exe to open the native window."
