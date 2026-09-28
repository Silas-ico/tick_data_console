$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "stop_backend.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& (Join-Path $PSScriptRoot "start_backend.ps1")
exit $LASTEXITCODE