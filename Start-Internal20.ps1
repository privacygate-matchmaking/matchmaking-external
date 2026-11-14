# Start a separate seed-20 lab. Preserve all volumes of the existing matchmaking project.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
function Invoke-CheckedDocker {
    & docker @args
    if ($LASTEXITCODE -ne 0) { throw "Docker failed (exit $LASTEXITCODE). Stop here and inspect the error." }
}
& .\.venv\Scripts\python.exe scripts/normalize-infra-scripts.py
if ($LASTEXITCODE -ne 0) { throw 'Startup script line-ending normalization failed.' }
& .\.venv\Scripts\python.exe scripts/initialize.py
if ($LASTEXITCODE -ne 0) { throw 'Local initialization failed.' }
$baseArgs = @('--project-directory','../privacygate',
    '--env-file','../privacygate/.env','--env-file','./.local/internal-admin.env',
    '-f','../privacygate/compose.yaml','-f','./compose.external.yaml','--profile','external')
$newArgs = @('-p','matchmaking-20') + $baseArgs + @('-f','./compose.internal20-lab.yaml')
Invoke-CheckedDocker compose @newArgs config --quiet
# Stop monitoring first to free memory; its volumes and credentials remain.
if (Test-Path -LiteralPath '.local/wazuh/docker-compose.yml') {
    & .\.venv\Scripts\python.exe scripts/wazuh.py stop
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop Wazuh. Inspect before starting another stack.' }
}
Invoke-CheckedDocker compose -p matchmaking @baseArgs stop
# A prior CRLF failure can leave the schema initialized but app_user missing.
# Starting PostgreSQL again does not replay docker-entrypoint-initdb.d on existing data.
Invoke-CheckedDocker compose @newArgs up -d --no-deps --wait transfer-db
$schemaReady = Invoke-CheckedDocker compose @newArgs exec -T transfer-db psql -U db_owner -d matchmaking_transfer -At -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('transfer_files','export_requests','approvals','deliveries','audit_log');"
if ($schemaReady.Trim() -ne '5') { throw 'Transfer schema is incomplete. Inspect it; do not reset the volume automatically.' }
$appRole = Invoke-CheckedDocker compose @newArgs exec -T transfer-db psql -U db_owner -d matchmaking_transfer -At -c "SELECT count(*) FROM pg_roles WHERE rolname='app_user';"
if ($appRole.Trim() -eq '0') {
    Write-Host 'Repairing the missing transfer DB app role after interrupted initialization.'
    Invoke-CheckedDocker compose @newArgs exec -T transfer-db sh /docker-entrypoint-initdb.d/02-app-role.sh
} elseif ($appRole.Trim() -ne '1') {
    throw 'Unexpected transfer app role state; inspect before continuing.'
}
Invoke-CheckedDocker compose @newArgs up -d --build
# Initial role/client setup must finish before adding the member accounts.
Invoke-CheckedDocker compose @newArgs wait keycloak-setup
& .\.venv\Scripts\python.exe scripts/internal20_accounts.py --apply
if ($LASTEXITCODE -ne 0) { throw 'Member account setup failed. Do not reset the DB; inspect and rerun the account update.' }
Invoke-CheckedDocker compose @newArgs ps -a
Write-Host 'Seed-20 lab started: https://localhost:8090 (member), https://localhost:8080 (staff)'
Write-Host 'This startup did not delete volumes. Wazuh is stopped; reconnect it to the new audit volume after app checks.'
