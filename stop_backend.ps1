$ErrorActionPreference = "Stop"
$port = 8000
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "backend")).Path
$pythonPath = (Resolve-Path (Join-Path $projectRoot ".venv\Scripts\python.exe") -ErrorAction SilentlyContinue).Path

$portProcessIds = @(
    Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
)
$projectProcesses = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    ($pythonPath -and $_.ExecutablePath -eq $pythonPath) -or
    ($_.CommandLine -and $_.CommandLine -match [regex]::Escape($projectRoot))
})
$projectProcessIds = @($projectProcesses | Select-Object -ExpandProperty ProcessId -Unique)

if (-not $portProcessIds -and -not $projectProcessIds) {
    Write-Host "后端未运行（端口 $port）。" -ForegroundColor DarkGray
    exit 0
}

$knownProcessIds = @($projectProcessIds)
$unknownPortProcessIds = @($portProcessIds | Where-Object { $_ -notin $knownProcessIds })
if ($unknownPortProcessIds) {
    $unknown = $unknownPortProcessIds -join ", "
    Write-Host "端口 $port 被无法识别的进程占用（PID $unknown），为安全起见不会强制结束。" -ForegroundColor Red
    Write-Host "请在任务管理器中确认后结束对应进程，或关闭占用 8000 的旧后端会话。" -ForegroundColor Yellow
    if (-not $knownProcessIds) { exit 1 }
}

foreach ($processId in $knownProcessIds) {
    Write-Host "正在停止本项目后端进程树 $processId ..." -ForegroundColor Yellow
    & taskkill.exe /PID $processId /T /F 2>$null | Out-Null
}

for ($attempt = 0; $attempt -lt 10; $attempt++) {
    Start-Sleep -Milliseconds 300
    $remaining = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    if (-not $remaining) {
        Write-Host "后端已停止（端口 $port）。" -ForegroundColor Green
        exit 0
    }
}

$remainingIds = ($remaining | Select-Object -ExpandProperty OwningProcess -Unique) -join ", "
Write-Host "本项目进程已停止，但端口 $port 仍被占用（PID $remainingIds）。" -ForegroundColor Red
exit 1