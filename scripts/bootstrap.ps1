# Cross-platform bootstrap for LiveClipper (Windows PowerShell).
# Pre-creates host files/dirs that the compose stack bind-mounts, then brings it up.
$ErrorActionPreference = 'Stop'

Set-Location (Join-Path $PSScriptRoot '..')

Write-Host '>>> Creating data directories on host...'
$dirs = @(
    'backend/clips',
    'backend/shorts',
    'backend/imports',
    'backend/.buffer',
    'backend/logs'
)
foreach ($d in $dirs) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d | Out-Null
    }
}

Write-Host '>>> Creating state files (if missing)...'
$files = @('backend/highlights.jsonl', 'backend/.youtube_tokens.json')
foreach ($f in $files) {
    if (-not (Test-Path $f)) {
        New-Item -ItemType File -Path $f | Out-Null
        Write-Host "    created $f"
    }
}

if (-not (Test-Path 'backend/.env')) {
    if (Test-Path 'backend/.env.example') {
        Copy-Item 'backend/.env.example' 'backend/.env'
        Write-Host '>>> Copied backend/.env.example -> backend/.env  (edit it before first run!)'
    } else {
        Write-Error 'No backend/.env found and no backend/.env.example to copy from.'
        exit 1
    }
}

Write-Host '>>> Building images (uses BuildKit cache mounts)...'
$env:DOCKER_BUILDKIT = '1'
docker compose build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host '>>> Starting stack...'
docker compose up -d
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ''
Write-Host 'Stack is up:'
docker compose ps --format "table {{.Service}}`t{{.Status}}`t{{.Ports}}"
Write-Host ''
Write-Host 'Open:   http://localhost:3000'
Write-Host 'Logs:   docker compose logs -f'
Write-Host 'Stop:   docker compose down'
