#Requires -Version 5.1
<#
.SYNOPSIS
    用本机 Edge/Chrome 的无头模式，把四个页签截成展示站要用的图。

.DESCRIPTION
    应用跑起来之后执行本脚本，产物默认写入 site/assets/。
    页签通过 URL hash 直达（#today / #replay / #compare / #evidence），不需要手工点击。

.EXAMPLE
    .\scripts\screenshots.ps1
    .\scripts\screenshots.ps1 -BaseUrl http://127.0.0.1:8080 -Width 1400 -Height 950
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8080",
    [string]$OutDir = (Join-Path $PSScriptRoot "..\site\assets"),
    [int]$Width = 1280,
    [int]$Height = 900
)

$ErrorActionPreference = "Stop"
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
New-Item -ItemType Directory -Force $OutDir | Out-Null

$candidates = @(
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$browser = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw "找不到 Edge 或 Chrome，请安装后再试。" }

if (-not (Test-Path "$BaseUrl")) {
    try { Invoke-WebRequest -Uri "$BaseUrl/api/health" -UseBasicParsing -TimeoutSec 5 | Out-Null }
    catch { throw "应用没在 $BaseUrl 运行：先执行 .\scripts\start.ps1，再截图。" }
}

$profile = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\app\.runtime\edge-shots"))
$tabs = [ordered]@{ today = "今日队列"; replay = "我记得你"; compare = "对照"; evidence = "证据" }
# 每次用干净的浏览器配置，避免缓存住旧的 JS/CSS
Remove-Item $profile -Recurse -Force -ErrorAction SilentlyContinue
$ok = 0

foreach ($name in $tabs.Keys) {
    $target = Join-Path $OutDir "$name.png"
    if (Test-Path $target) { Remove-Item $target -Force }
    Write-Host "截取 $($tabs[$name]) ..." -ForegroundColor Cyan
    # Chromium 会把无关警告写到 stderr，别让 ErrorActionPreference=Stop 把它当成致命错误。
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $browser --headless=new --disable-gpu --hide-scrollbars --no-first-run --no-default-browser-check `
        --window-size="$Width,$Height" --virtual-time-budget=8000 `
        --user-data-dir="$profile" --screenshot="$target" "$BaseUrl/#$name" 2>&1 | Out-Null
    $ErrorActionPreference = $previous
    if ((Test-Path $target) -and (Get-Item $target).Length -gt 10240) {
        Write-Host "  -> $target（$([math]::Round((Get-Item $target).Length / 1kb)) KB）" -ForegroundColor Green
        $ok++
    } else {
        Write-Warning "  $name 截图失败：可能是无头模式被安全软件拦了，改用系统截图工具手工截这一页。"
    }
}

Write-Host ""
if ($ok -gt 0) {
    Write-Host "完成 $ok/4 张，位置：$OutDir"
    Write-Host '接着改 site/index.html 的 #gallery，把占位块换成 <img src="assets/today.png"> 这类真实标签。' -ForegroundColor Yellow
} else {
    Write-Warning "一张都没成功。可以直接用 Win+Shift+S 手工截四个页签，存成同名文件。"
}
