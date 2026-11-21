param(
    [int]$Cpus = 8,
    [int]$GcsPort = 6380,
    [int]$DashboardPort = 8265,
    [switch]$StopRay
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Python environment not found at $Python. Run scripts/setup.ps1 first."
}

$env:RAY_ENABLE_UV_RUN_RUNTIME_ENV = "0"
$env:RAY_AUTH_MODE = "disabled"
$env:RAY_TMPDIR = Join-Path $Root ".runtime\ray"
$env:PYTHONPATH = (Join-Path $Root "backend") + ";" + $env:PYTHONPATH
New-Item -ItemType Directory -Force -Path $env:RAY_TMPDIR | Out-Null

$RayCli = "import sys; from ray.scripts.scripts import main; sys.argv[0] = 'ray'; main()"

if ($StopRay) {
    & $Python -c $RayCli stop
    exit $LASTEXITCODE
}

try {
    $version = Invoke-RestMethod -Uri "http://127.0.0.1:$DashboardPort/api/version" -TimeoutSec 2
    if ($version.ray_version) {
        Write-Host "Ray is already running (v$($version.ray_version), dashboard http://127.0.0.1:$DashboardPort)."
        exit 0
    }
} catch {
    # Dashboard is not up yet; start a head below.
}

Write-Host "Starting Ray head with $Cpus logical CPUs (GCS $GcsPort, dashboard $DashboardPort)..."
& $Python -c $RayCli start --head --num-cpus=$Cpus --port=$GcsPort `
    --dashboard-host=127.0.0.1 --dashboard-port=$DashboardPort `
    --disable-usage-stats
if ($LASTEXITCODE -ne 0) {
    throw "Ray failed to start. Check .runtime/ray and make sure ports 6380/8265 are free."
}

$deadline = (Get-Date).AddSeconds(40)
while ((Get-Date) -lt $deadline) {
    try {
        $version = Invoke-RestMethod -Uri "http://127.0.0.1:$DashboardPort/api/version" -TimeoutSec 2
        if ($version.ray_version) {
            Write-Host "Ray ready: v$($version.ray_version), cpu budget $Cpus."
            Write-Host "Dashboard: http://127.0.0.1:$DashboardPort"
            exit 0
        }
    } catch {
        Start-Sleep -Milliseconds 800
    }
}
throw "Ray was started but the dashboard did not become healthy in time."
