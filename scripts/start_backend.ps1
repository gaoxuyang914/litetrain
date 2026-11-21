param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Python environment not found at $Python. Run scripts/setup.ps1 first."
}

$env:PYTHONPATH = (Join-Path $Root "backend") + ";" + $env:PYTHONPATH
$env:RAY_ENABLE_UV_RUN_RUNTIME_ENV = "0"
$env:RAY_AUTH_MODE = "disabled"
$env:LITETRAIN_HOST = $HostAddress
$env:LITETRAIN_PORT = $Port
if (-not $env:LITETRAIN_WORKER_API_BASE) {
    $env:LITETRAIN_WORKER_API_BASE = "http://${HostAddress}:$Port/api/v1"
}
if (-not $env:LITETRAIN_WORKER_PYTHON) {
    $env:LITETRAIN_WORKER_PYTHON = $Python
}

$arguments = @(
    "-m", "uvicorn", "app.main:app",
    "--app-dir", (Join-Path $Root "backend"),
    "--host", $HostAddress,
    "--port", $Port
)
if ($Reload) { $arguments += "--reload" }

Write-Host "Starting LiteTrain API on http://${HostAddress}:$Port"
Write-Host "OpenAPI docs: http://${HostAddress}:$Port/docs"
& $Python @arguments
