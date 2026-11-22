$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Frontend = Join-Path $Root "frontend"

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm is required. Install Node.js 20+ and retry."
}

Push-Location $Frontend
try {
    if (-not (Test-Path (Join-Path $Frontend "node_modules"))) {
        Write-Host "Installing frontend dependencies..."
        npm install --no-audit --no-fund
    }
    Write-Host "Starting admin console on http://127.0.0.1:5174 (proxying API to :8000)"
    npm run dev:admin
} finally {
    Pop-Location
}
