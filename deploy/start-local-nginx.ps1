# Start/reload local Nginx for CCTV direct-media dev entry (http://127.0.0.1:8080/)
$ErrorActionPreference = 'Stop'
$nginxDir = Join-Path $PSScriptRoot 'nginx-win'
$nginxExe = Join-Path $nginxDir 'nginx.exe'

foreach ($dir in @('logs', 'temp', 'temp/client_body_temp', 'temp/proxy_temp', 'temp/fastcgi_temp', 'temp/uwsgi_temp', 'temp/scgi_temp')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $nginxDir $dir) | Out-Null
}

Push-Location $nginxDir
try {
    & $nginxExe -t
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $existing = Get-Process nginx -ErrorAction SilentlyContinue
    if ($existing) {
        & $nginxExe -s reload
        Write-Host 'Nginx reloaded — http://127.0.0.1:8080/'
    } else {
        Start-Process -FilePath $nginxExe -WorkingDirectory $nginxDir -WindowStyle Hidden
        Start-Sleep -Seconds 1
        Write-Host 'Nginx started — http://127.0.0.1:8080/'
    }
} finally {
    Pop-Location
}
