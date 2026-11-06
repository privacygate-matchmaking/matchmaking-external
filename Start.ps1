$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& .\.venv\Scripts\python.exe scripts/initialize.py
if ($LASTEXITCODE -ne 0) { throw 'Session initialization failed.' }
docker compose --project-directory ../privacygate -f ../privacygate/compose.yaml -f ./compose.external.yaml --profile external up -d --build
if ($LASTEXITCODE -ne 0) { throw 'Compose failed; inspect the logs. See README.md.' }
Write-Host 'Member lounge: https://localhost:8090'
