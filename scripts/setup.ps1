param(
    [string]$PythonLauncher = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Host "Creating Python virtual environment at $Venv..."
    & $PythonLauncher -m venv $Venv
}

Write-Host "Installing CPU PyTorch..."
& $Python -m pip install --upgrade pip
& $Python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

Write-Host "Installing backend and test dependencies (this can take a few minutes)..."
& $Python -m pip install -r (Join-Path $Root "backend\requirements-dev.txt")

Write-Host "Checking demo assets..."
$env:PYTHONPATH = (Join-Path $Root "backend")
& $Python (Join-Path $Root "scripts\download_assets.py")

Write-Host "Installing frontend dependencies..."
Push-Location (Join-Path $Root "frontend")
try {
    npm install --no-audit --no-fund
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Setup complete. Start the services in three terminals:"
Write-Host "  1. .\scripts\start_ray.ps1"
Write-Host "  2. .\scripts\start_backend.ps1"
Write-Host "  3. .\scripts\start_frontend.ps1"
