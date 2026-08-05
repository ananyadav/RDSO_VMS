# One-command local dev startup (Windows).
# Usage: .\start_dev.ps1
# Opens http://127.0.0.1:3000 when ready — do NOT use localhost (Cursor hijacks it).

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$BackendPort = if ($env:CCTV_BACKEND_PORT) { [int]$env:CCTV_BACKEND_PORT } else { 10000 }
$FrontendPort = 3000

Write-Host "=== CCTV dev startup ===" -ForegroundColor Cyan

function Stop-StalePythonBackend {
    param([int]$Port)
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object {
            $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
            if ($proc -and $proc.ProcessName -eq "python") {
                Write-Host "Stopping stale python backend (PID $($proc.Id)) on port $Port"
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
        }
}

function Get-LanIp {
    Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
        Select-Object -ExpandProperty IPAddress -First 1
}

Stop-StalePythonBackend -Port $BackendPort

Write-Host "Starting backend on port $BackendPort ..."
$backendCmd = "Set-Location '$Root\backend'; python -m app.main --api-port $BackendPort"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd | Out-Null

$healthHost = Get-LanIp
if (-not $healthHost) { $healthHost = "127.0.0.1" }
$healthUrl = "http://${healthHost}:${BackendPort}/api/health"
Write-Host "Waiting for backend readiness at $healthUrl (up to 3 min)..."

$ready = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5 -ErrorAction Stop
        if ($health.ready -eq $true) {
            Write-Host "[OK] Backend ready — $($health.cameraCount) cameras in database" -ForegroundColor Green
            $ready = $true
            break
        }
        if ($health.error) {
            throw "Backend startup error: $($health.error)"
        }
    } catch {
        # still starting
    }
    Start-Sleep -Seconds 2
}

if (-not $ready) {
    Write-Host "[WARN] Backend not ready yet — frontend will wait automatically." -ForegroundColor Yellow
}

Write-Host "Starting frontend on http://127.0.0.1:$FrontendPort ..."
$frontendCmd = "Set-Location '$Root\frontend'; npm run dev"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd | Out-Null

Start-Sleep -Seconds 4
Start-Process "http://127.0.0.1:$FrontendPort/"

Write-Host ""
Write-Host "Dev URLs:" -ForegroundColor Cyan
Write-Host "  UI:      http://127.0.0.1:$FrontendPort/"
Write-Host "  API:     http://127.0.0.1:$BackendPort/api/health"
Write-Host "  Login:   admin123 / admin123  (or admin / admin)"
Write-Host ""
Write-Host "Keep both terminal windows open. Use Ctrl+C in each to stop."
