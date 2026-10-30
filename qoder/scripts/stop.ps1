#Requires -Version 5.1
<#
.SYNOPSIS
    停止由 start.ps1 启动的进程（应用与 PowerContext）。
#>
[CmdletBinding()]
param(
    [switch]$All
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$pidDir = Join-Path $root ".runtime\pids"

function Stop-FromPidFile {
    param([string]$Name)
    $file = Join-Path $pidDir "$Name.pid"
    if (-not (Test-Path $file)) {
        Write-Host "$Name：无 PID 文件，跳过。"
        return
    }
    $procId = (Get-Content $file -Raw).Trim()
    try {
        $p = Get-Process -Id $procId -ErrorAction Stop
        Stop-Process -Id $p.Id -Force
        Write-Host "$Name：已停止 PID $($p.Id)"
    } catch {
        Write-Host "$Name：进程 $procId 已不存在。"
    }
    Remove-Item $file -Force -ErrorAction SilentlyContinue
}

Stop-FromPidFile "app"
Stop-FromPidFile "powercontext"

if ($All) {
    foreach ($port in @(8080, 8000)) {
        $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            try {
                Stop-Process -Id $c.OwningProcess -Force
                Write-Host "已停止占用 $port 的进程 $($c.OwningProcess)"
            } catch {
                Write-Warning "无法停止占用 $port 的进程 $($c.OwningProcess)"
            }
        }
    }
}
