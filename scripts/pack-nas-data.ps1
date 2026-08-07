# Copy SQLite + config JSON for NAS (no zip, no knowledge).
# Output folder: deploy/dist/anzai/data/  （整棵拷到 NAS /vol1/1000/Docker/anzai/）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$api = Join-Path $root "apps\api"
$db = Join-Path $api "anzai.db"
$data = Join-Path $api "data"
$outRoot = Join-Path $root "deploy\dist\anzai"
$dest = Join-Path $outRoot "data"

$configFiles = @(
  "llm_presets.json",
  "llm_profiles.json",
  "analysis_connection.json",
  "embedding_connection.json",
  "agent_chat.json",
  "analysis_tiers.json",
  "analysis_pending.json",
  "notify_digest_state.json"
)

if (-not (Test-Path $db)) { throw "missing $db" }
if (-not (Test-Path $data)) { throw "missing $data" }

if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item $db (Join-Path $dest "anzai.db") -Force

$copied = @("anzai.db")
foreach ($name in $configFiles) {
  $src = Join-Path $data $name
  if (Test-Path $src) {
    Copy-Item $src (Join-Path $dest $name) -Force
    $copied += $name
  }
}

# Also drop compose + env example beside data for one-folder copy
Copy-Item (Join-Path $root "deploy\docker-compose.yml") (Join-Path $outRoot "docker-compose.yml") -Force
Copy-Item (Join-Path $root "deploy\.env.example") (Join-Path $outRoot ".env.example") -Force
$localEnv = Join-Path $api ".env"
if (Test-Path $localEnv) {
  Copy-Item $localEnv (Join-Path $outRoot ".env") -Force
  # Force NAS sqlite path + no knowledge
  $envText = Get-Content (Join-Path $outRoot ".env") -Raw -Encoding UTF8
  if ($envText -match "(?m)^DATABASE_URL=.*") {
    $envText = $envText -replace "(?m)^DATABASE_URL=.*", "DATABASE_URL=sqlite:////app/data/anzai.db"
  } else {
    $envText = "DATABASE_URL=sqlite:////app/data/anzai.db`r`n" + $envText
  }
  if ($envText -match "(?m)^KNOWLEDGE_DATABASE_URL=.*") {
    $envText = $envText -replace "(?m)^KNOWLEDGE_DATABASE_URL=.*", "KNOWLEDGE_DATABASE_URL="
  } else {
    $envText = $envText.TrimEnd() + "`r`nKNOWLEDGE_DATABASE_URL=`r`n"
  }
  Set-Content -Path (Join-Path $outRoot ".env") -Value $envText -Encoding UTF8 -NoNewline
}

Write-Host "OK $outRoot"
Write-Host ("data/: " + ($copied -join ", "))
Write-Host "Copy folder to NAS:  deploy\dist\anzai  ->  /vol1/1000/Docker/anzai"
