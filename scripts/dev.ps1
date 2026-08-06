# Start Anzai ETF API + Web (ports 8515 / 3515)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $root) { $root = (Resolve-Path "$PSScriptRoot\..").Path }

$apiDir = Join-Path $root "apps\api"
$webDir = Join-Path $root "apps\web"
$uvicorn = Join-Path $apiDir ".venv\Scripts\uvicorn.exe"

if (-not (Test-Path $uvicorn)) {
  Write-Error "Missing API venv. Run: cd apps\api; python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt"
}

Write-Host "Starting API on 0.0.0.0:8515 ..."
Start-Process -FilePath $uvicorn -ArgumentList @(
  "app.main:app", "--reload", "--host", "0.0.0.0", "--port", "8515"
) -WorkingDirectory $apiDir

Write-Host "Starting Web on 0.0.0.0:3515 ..."
Start-Process -FilePath "npm" -ArgumentList @("run", "dev") -WorkingDirectory $webDir

Write-Host ""
Write-Host "Web  http://127.0.0.1:3515"
Write-Host "API  http://127.0.0.1:8515/health"
Write-Host "Docs http://127.0.0.1:8515/docs"
