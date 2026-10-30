#Requires -Version 5.1
<#
.SYNOPSIS
    生成受保护的 PowerContext 环境文件，准备数据目录，并在 app/ 存在时同步生成 app/.env。

.EXAMPLE
    .\scripts\bootstrap.ps1
    .\scripts\bootstrap.ps1 -HomeDir .\.runtime\home -Force
#>
[CmdletBinding()]
param(
    [string]$HomeDir = (Join-Path $env:USERPROFILE ".recallcoach"),
    [switch]$Force,
    [switch]$ShowToken
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $HomeDir "powercontext.env"
$dataDir = Join-Path $HomeDir "data"

Write-Host "== RecallCoach bootstrap ==" -ForegroundColor Cyan

foreach ($cmd in @("uv", "git")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        throw "缺少 $cmd。请先安装：https://docs.astral.sh/uv/getting-started/installation/"
    }
}
if (-not (Get-Command powercontext -ErrorAction SilentlyContinue)) {
    Write-Warning '未找到 powercontext。先运行：uv tool install --force "powercontext[cli,server]==1.2.0"'
}

New-Item -ItemType Directory -Force $HomeDir | Out-Null
New-Item -ItemType Directory -Force $dataDir | Out-Null

$token = $null
if ((Test-Path $envFile) -and -not $Force) {
    $line = Get-Content $envFile -Encoding UTF8 |
        Where-Object { $_ -match '^POWERCONTEXT_SERVER_AUTH_TOKEN=' } |
        Select-Object -First 1
    if ($line) {
        $token = $line.Substring($line.IndexOf('=') + 1).Trim()
        Write-Host "复用已有环境文件：$envFile"
    }
}

if (-not $token) {
    $token = ([guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N'))
    $lines = @(
        "# 由 scripts/bootstrap.ps1 生成。含 token，不要提交、不要贴进截图。",
        "POWERCONTEXT_SERVER_ACCESS_MODE=enforced",
        "POWERCONTEXT_SERVER_AUTH_TOKEN=$token",
        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true",
        "POWERCONTEXT_HOME=$dataDir"
    )
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($envFile, ($lines -join "`r`n") + "`r`n", $utf8)
    Write-Host "已生成环境文件：$envFile"
}

# 限制 ACL：只允许当前用户、SYSTEM、Administrators
try {
    icacls $envFile /inheritance:r /grant:r "$($env:USERNAME):(F)" "SYSTEM:(F)" "Administrators:(F)" | Out-Null
    Write-Host "已限制文件 ACL。"
} catch {
    Write-Warning "icacls 失败，请手工限制 $envFile 的权限：$($_.Exception.Message)"
}

# 同步 app/.env
$appDir = Join-Path $root "app"
$appEnv = Join-Path $appDir ".env"
$example = Join-Path $root "deploy\app.env.example"
if (Test-Path $appDir) {
    if ((Test-Path $appEnv) -and -not $Force) {
        Write-Host "app/.env 已存在，跳过（-Force 可覆盖）。"
    } elseif (Test-Path $example) {
        $appContent = Get-Content $example -Raw -Encoding UTF8
        $appContent = $appContent.Replace("replace-with-token-from-powercontext-env", $token)
        [System.IO.File]::WriteAllText($appEnv, $appContent, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "已生成 app/.env（token 已填入）。"
    }
} else {
    Write-Host "app/ 尚未创建（Qoder 完成 M1 后重跑本脚本即可生成 app/.env）。"
}

Write-Host ""
Write-Host "环境文件：$envFile"
Write-Host "数据目录：$dataDir"
if ($ShowToken) {
    Write-Host "Token：$token"
} else {
    Write-Host "Token：已隐藏（加 -ShowToken 可显示）"
}
Write-Host ""
Write-Host "下一步：" -ForegroundColor Yellow
Write-Host "  1) powercontext config validate --env-file `"$envFile`""
Write-Host "  2) .\scripts\start.ps1        # 或手工 powercontext server run --env-file `"$envFile`""
Write-Host "  3) .\scripts\verify-powercontext.ps1"
