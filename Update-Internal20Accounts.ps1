# Rename/link all 20 synthetic members. No DB reset or application rebuild.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& .\.venv\Scripts\python.exe scripts/internal20_accounts.py --apply
if ($LASTEXITCODE -ne 0) { throw 'Account setup failed. Inspect the error; do not reset the DB or delete the accounts.' }
