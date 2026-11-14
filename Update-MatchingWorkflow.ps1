$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
function Invoke-WorkflowDocker {
    & docker @args
    if ($LASTEXITCODE -ne 0) { throw "Docker failed (exit $LASTEXITCODE). Inspect the error; do not reset volumes." }
}
foreach ($file in @('../privacygate/.env','./.local/internal-admin.env','./compose.internal20-lab.yaml')) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Required configuration missing: $file" }
}
$workflowArgs = @('-p','matchmaking-20','--project-directory','../privacygate',
    '--env-file','../privacygate/.env','--env-file','./.local/internal-admin.env',
    '-f','../privacygate/compose.yaml','-f','./compose.external.yaml','-f','./compose.internal20-lab.yaml',
    '--profile','external')
Invoke-WorkflowDocker compose @workflowArgs config --quiet
Invoke-WorkflowDocker compose @workflowArgs build internal-api external-api transfer-api
Invoke-WorkflowDocker compose @workflowArgs stop internal-api external-api transfer-api
Invoke-WorkflowDocker compose @workflowArgs up -d --no-deps internal-api external-api transfer-api
Invoke-WorkflowDocker compose @workflowArgs up -d --no-deps --force-recreate internal-web external-web
Invoke-WorkflowDocker compose @workflowArgs ps -a
Write-Host 'Workflow images updated. Member: https://localhost:8090 Staff: https://localhost:8080'
Write-Host 'No volumes, accounts, consents or approvals were reset. No database migration was required.'
Write-Host 'If startup failed after stopping APIs, fix the reported error and rerun this script.'
