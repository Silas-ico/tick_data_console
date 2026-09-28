$ErrorActionPreference = "Stop"
$frontend = Join-Path $PSScriptRoot "frontend"
Set-Location $frontend

$systemNode = Get-Command node -ErrorAction SilentlyContinue

if ($systemNode -and (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    pnpm install
    pnpm dev
    exit
}

if ($systemNode -and (Get-Command npm -ErrorAction SilentlyContinue)) {
    npm install
    npm run dev
    exit
}

$bundledNodeDirectory = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin"
$bundledPnpm = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"

if ((Test-Path (Join-Path $bundledNodeDirectory "node.exe")) -and (Test-Path $bundledPnpm)) {
    $env:Path = "$bundledNodeDirectory;$env:Path"
    & $bundledPnpm install
    $bundledNode = Join-Path $bundledNodeDirectory "node.exe"
    $viteEntry = Join-Path $frontend "node_modules\vite\bin\vite.js"
    & $bundledNode $viteEntry --host 127.0.0.1 --port 5173
    exit
}

throw "Node.js was not found. Install Node.js 20.19 or later."
