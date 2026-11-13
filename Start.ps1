$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& .\.venv\Scripts\python.exe scripts/initialize.py
if ($LASTEXITCODE -ne 0) { throw 'Session initialization failed.' }
$composeArgs = @('--project-directory', '../privacygate', '--env-file', '../privacygate/.env',
    '--env-file', './.local/internal-admin.env', '-f', '../privacygate/compose.yaml',
    '-f', './compose.external.yaml', '--profile', 'external')
docker compose @composeArgs up -d --build
if ($LASTEXITCODE -ne 0) { throw 'Compose failed; inspect the logs. See README.md.' }
Write-Host 'Member lounge: https://localhost:8090'
