$ErrorActionPreference = "Stop"
$backend = Join-Path $PSScriptRoot "backend"
Set-Location $backend
$port = 8000

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}

$existing = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
if ($existing) {
    $processIds = ($existing | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
    Write-Host "后端已经在运行中（端口 $port，进程 $processIds），无需重复启动。" -ForegroundColor Yellow
    Write-Host "如需停止，请在项目目录执行：.\stop_backend.ps1" -ForegroundColor DarkGray
    exit 0
}

Write-Host "正在启动后端（端口 $port），按 Ctrl+C 可停止。" -ForegroundColor Green
& .\.venv\Scripts\python.exe run.py
exit $LASTEXITCODE