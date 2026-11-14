# Remove only the old synthetic runtime volumes explicitly listed below.
# Default is preview. -Apply performs the user-requested cleanup.
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
function Invoke-CheckedDocker {
    & docker @args
    if ($LASTEXITCODE -ne 0) { throw "Docker failed (exit $LASTEXITCODE). No forced removal will be attempted." }
}
$targets = @(
    'matchmaking_business-db-data',
    'matchmaking_evidence-storage',
    'matchmaking_external-session-data',
    'matchmaking_keycloak-data',
    'matchmaking_transfer-db-data',
    'matchmaking_transfer-staging',
    'matchmaking_transfer-verified'
)
# Do not enumerate and delete arbitrary volumes, or touch the new seed-20 project.
$existing = @(Invoke-CheckedDocker volume ls --filter 'label=com.docker.compose.project=matchmaking' --format '{{.Name}}')
$selected = @($targets | Where-Object { $existing -contains $_ })
foreach ($name in $selected) {
    $labels = (Invoke-CheckedDocker volume inspect $name --format '{{json .Labels}}') | ConvertFrom-Json
    if ($labels.'com.docker.compose.project' -ne 'matchmaking') { throw "Unexpected owner label on $name. Stopping." }
}
Write-Host 'Old runtime volumes selected for deletion:'
$selected | ForEach-Object { Write-Host "  $_" }
Write-Host 'Preserved: matchmaking_audit-logs, all ieum-wazuh data, source files, local keys, backups, matchmaking-20 volumes.'
if (-not $Apply) {
    Write-Host 'Preview only. Run .\Reset-OldMatchingLab.ps1 -Apply to perform this cleanup.'
    return
}
& .\.venv\Scripts\python.exe scripts/initialize.py
if ($LASTEXITCODE -ne 0) { throw 'Local environment initialization failed.' }
$oldArgs = @('-p','matchmaking','--project-directory','../privacygate',
    '--env-file','../privacygate/.env','--env-file','./.local/internal-admin.env',
    '-f','../privacygate/compose.yaml','-f','./compose.external.yaml',
    '-f','./compose.matching-lab.yaml','--profile','external')
Invoke-CheckedDocker compose @oldArgs config --quiet
if (Test-Path -LiteralPath '.local/wazuh/docker-compose.yml') {
    & .\.venv\Scripts\python.exe scripts/wazuh.py stop
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop Wazuh. No old volumes removed.' }
}
# Remove the old containers and networks only; volumes are removed by allowlist below.
Invoke-CheckedDocker compose @oldArgs down --remove-orphans
# Check every target for remaining container references before removing any volume.
foreach ($name in $selected) {
    $attached = @(Invoke-CheckedDocker ps -a --filter "volume=$name" --format '{{.Names}}')
    if ($attached.Count -gt 0) { throw "$name is still used by: $($attached -join ', '). No forced removal performed." }
}
foreach ($name in $selected) {
    # Verify the owner immediately before deletion as well.
    $labels = (Invoke-CheckedDocker volume inspect $name --format '{{json .Labels}}') | ConvertFrom-Json
    if ($labels.'com.docker.compose.project' -ne 'matchmaking') { throw "Owner changed on $name. Stopping." }
    Invoke-CheckedDocker volume rm $name
}
Write-Host 'Old synthetic runtime data cleanup completed. Start the seed-20 lab with .\Start-Internal20.ps1'
