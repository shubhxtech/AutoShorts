# AutoShorts setup for Windows 10/11.
# Run from the repository root in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
# Re-running is safe; finished steps are skipped.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Step($msg) { Write-Host "`n-> $msg" -ForegroundColor Cyan }
function WingetInstall($id, $name) {
    if (-not (Have "winget")) { throw "$name is missing and winget is not available. Install $name manually, then re-run." }
    winget install --id $id -e --accept-source-agreements --accept-package-agreements --silent
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}

Step "Checking tools"
if (-not (Have "python")) { WingetInstall "Python.Python.3.12" "Python 3.12" }
$pyver = (python -c "import sys; print('%d.%d' % sys.version_info[:2])")
if ([version]$pyver -lt [version]"3.11") { throw "Python 3.11+ is required (found $pyver)." }
if (-not (Have "node")) { WingetInstall "OpenJS.NodeJS.LTS" "Node.js" }
if (-not (Have "ffmpeg")) { WingetInstall "Gyan.FFmpeg" "ffmpeg" }

Step "Creating the Python environment"
if (-not (Test-Path "backend\.venv")) { python -m venv backend\.venv }
$py = "backend\.venv\Scripts\python.exe"
& $py -m pip install --upgrade pip wheel | Out-Null

# A CUDA build of PyTorch makes voices, images and word alignment 10-50x faster on NVIDIA GPUs.
if (Have "nvidia-smi") {
    Step "NVIDIA GPU found: installing PyTorch with CUDA"
    & $py -m pip install torch --index-url https://download.pytorch.org/whl/cu124
} else {
    Write-Host "No NVIDIA GPU found: everything runs on the CPU (clips work; faceless images are slow)." -ForegroundColor Yellow
}

Step "Installing the engine"
& $py -m pip install -e "backend[all]"

Step "Building the dashboard"
Push-Location frontend
npm install --no-audit --no-fund
npm run build
Pop-Location

Step "Local language model (Ollama)"
if (-not (Have "ollama")) { WingetInstall "Ollama.Ollama" "Ollama" }
try { ollama pull qwen3:8b } catch { Write-Host "Couldn't pull the model now; run 'ollama pull qwen3:8b' later." -ForegroundColor Yellow }

$start = @"
@echo off
cd /d "%~dp0.."
backend\.venv\Scripts\autoshorts.exe up
"@
Set-Content -Path "scripts\start_windows.bat" -Value $start -Encoding ASCII

Write-Host "`nDone. Start AutoShorts with:  scripts\start_windows.bat" -ForegroundColor Green
Write-Host "Then open http://localhost:3000"
