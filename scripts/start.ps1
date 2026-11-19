# Start the tokensfree gateway with the repo-local virtualenv and .env (if present).
param([int]$Port = 8766)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) { $python = "python" }
    $envFile = Join-Path $root ".env"
    $dbDir = Join-Path $root ".tokensfree"
    New-Item -ItemType Directory -Force -Path $dbDir | Out-Null
    $cliArgs = @("-m", "tokensfree", "--port", "$Port", "--db", (Join-Path $dbDir "state.db"))
    if (Test-Path $envFile) { $cliArgs += @("--env-file", $envFile) }
    Write-Host ""
    Write-Host "tokensfree 控制台: http://127.0.0.1:$Port/" -ForegroundColor Green
    Write-Host "按 Ctrl+C 停止网关"
    Write-Host ""
    & $python @cliArgs
} finally {
    Pop-Location
}
