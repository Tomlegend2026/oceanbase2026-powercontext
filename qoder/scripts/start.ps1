#Requires -Version 5.1
<#
.SYNOPSIS
    启动 PowerContext（若未运行）与应用，写 PID 文件，按需打开浏览器。

.EXAMPLE
    .\scripts\start.ps1
    .\scripts\start.ps1 -ServerOnly
    .\scripts\start.ps1 -EnvFile .\.runtime\home\powercontext.env -Port 8081
#>
[CmdletBinding()]
param(
    [string]$EnvFile = (Join-Path $env:USERPROFILE ".recallcoach\powercontext.env"),
    [int]$Port = 8080,
    [string]$PcUrl = "http://127.0.0.1:8000",
    [switch]$ServerOnly,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$runDir = Join-Path $root ".runtime"
$pidDir = Join-Path $runDir "pids"
New-Item -ItemType Directory -Force $pidDir | Out-Null

if (-not (Test-Path $EnvFile)) {
    throw "找不到环境文件 $EnvFile，先运行 .\scripts\bootstrap.ps1"
}

function Test-Ready {
    try {
        $r = Invoke-WebRequest -Uri "$PcUrl/health/ready" -TimeoutSec 3 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

# 1. PowerContext
if (Test-Ready) {
    Write-Host "PowerContext 已在运行：$PcUrl" -ForegroundColor Green
} else {
    $pc = Get-Command powercontext -ErrorAction SilentlyContinue
    if (-not $pc) {
        throw '未安装 powercontext。运行：uv tool install --force "powercontext[cli,server]==1.2.0"'
    }
    Write-Host "启动 PowerContext ..." -ForegroundColor Cyan
    $pcProc = Start-Process -FilePath $pc.Source `
        -ArgumentList @("server", "run", "--env-file", $EnvFile) `
        -PassThru -WindowStyle Minimized
    Set-Content -Path (Join-Path $pidDir "powercontext.pid") -Value $pcProc.Id -Encoding ASCII

    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Test-Ready)) {
        if ((Get-Date) -gt $deadline) {
            throw "PowerContext 60 秒内未就绪。看它的窗口日志，或手工运行：powercontext server run --env-file `"$EnvFile`""
        }
        Start-Sleep -Milliseconds 800
    }
    Write-Host "PowerContext 就绪：$PcUrl" -ForegroundColor Green
}

if ($ServerOnly) { return }

# 2. 应用
$appDir = Join-Path $root "app"
if (-not (Test-Path (Join-Path $appDir "pyproject.toml"))) {
    Write-Warning "app/pyproject.toml 不存在：应用代码尚未生成（Qoder M1 起）。当前只启动了 PowerContext。"
    return
}
if (-not (Test-Path (Join-Path $appDir ".env"))) {
    Write-Warning "app/.env 不存在，先运行 .\scripts\bootstrap.ps1"
    return
}

$uv = Get-Command uv -ErrorAction Stop
Write-Host "启动应用 ..." -ForegroundColor Cyan
$appProc = Start-Process -FilePath $uv.Source `
    -ArgumentList @("run", "uvicorn", "recallcoach.main:app", "--port", "$Port") `
    -WorkingDirectory $appDir -PassThru -WindowStyle Minimized
Set-Content -Path (Join-Path $pidDir "app.pid") -Value $appProc.Id -Encoding ASCII

$appUrl = "http://127.0.0.1:$Port"
$ok = $false
$deadline = (Get-Date).AddSeconds(40)
while (-not $ok) {
    if ((Get-Date) -gt $deadline) { break }
    try {
        $r = Invoke-WebRequest -Uri "$appUrl/api/health" -TimeoutSec 3 -UseBasicParsing
        $ok = $r.StatusCode -eq 200
    } catch {
        Start-Sleep -Milliseconds 800
    }
}

if ($ok) {
    Write-Host "应用就绪：$appUrl" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $appUrl | Out-Null }
} else {
    Write-Warning "应用未在 40 秒内就绪，检查 app 窗口日志或手工运行：cd app; uv run uvicorn recallcoach.main:app --port $Port"
}

Write-Host "停止：.\scripts\stop.ps1"
