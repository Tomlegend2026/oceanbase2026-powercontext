#Requires -Version 5.1
<#
.SYNOPSIS
    RecallCoach 端到端验证：建独立 scope → 起一个隔离的应用实例 → 跑 V1–V7 → 出证据包。

.DESCRIPTION
    不会碰你日常用的 app.db 与 scope：验证用独立的 scope、独立的数据库、独立的端口。
    结束后自动停掉验证实例（-KeepRunning 可保留）。

.EXAMPLE
    .\scripts\verify.ps1
    .\scripts\verify.ps1 -PcUrl http://127.0.0.1:8099 -PcToken fake-token -Today 2026-11-01
#>
[CmdletBinding()]
param(
    [string]$EnvFile = (Join-Path $env:USERPROFILE ".recallcoach\powercontext.env"),
    [string]$PcUrl = "http://127.0.0.1:8000",
    [string]$PcToken = "",
    [int]$AppPort = 8082,
    [string]$Today = (Get-Date -Format "yyyy-MM-dd"),
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$appDir = Join-Path $root "app"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outDir = Join-Path $root "evidence\verify\$stamp"
New-Item -ItemType Directory -Force $outDir | Out-Null

$script:results = New-Object System.Collections.ArrayList
$script:appProc = $null
$script:scopeId = $null
$script:sessionId = $null

function Add-Result {
    param([string]$Step, [string]$Verdict, [string]$Expected, [string]$Actual)
    [void]$script:results.Add([pscustomobject]@{ Step = $Step; Verdict = $Verdict; Expected = $Expected; Actual = $Actual })
    $color = switch ($Verdict) { "PASS" { "Green" } "FAIL" { "Red" } "WARN" { "Yellow" } default { "Gray" } }
    Write-Host ("[{0}] {1}" -f $Verdict, $Step) -ForegroundColor $color
    Write-Host ("      预期: {0}" -f $Expected)
    Write-Host ("      实际: {0}" -f $Actual)
}

function Check {
    param([string]$Step, [bool]$Condition, [string]$Expected, [string]$Actual)
    Add-Result -Step $Step -Verdict $(if ($Condition) { "PASS" } else { "FAIL" }) -Expected $Expected -Actual $Actual
    return $Condition
}

function Read-EnvFile {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path $Path)) { return $map }
    foreach ($line in Get-Content $Path -Encoding UTF8) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#") -or $t.IndexOf("=") -lt 1) { continue }
        $i = $t.IndexOf("=")
        $map[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim().Trim('"')
    }
    return $map
}

function Invoke-Json {
    param(
        [string]$Method,
        [string]$Url,
        [object]$Body = $null,
        [hashtable]$Headers = @{}
    )
    $params = @{ Uri = $Url; Method = $Method; Headers = $Headers; TimeoutSec = 30 }
    if ($null -ne $Body) {
        $json = $Body | ConvertTo-Json -Depth 12
        $params["Body"] = [System.Text.Encoding]::UTF8.GetBytes($json)
        $params["ContentType"] = "application/json; charset=utf-8"
    }
    return Invoke-RestMethod @params
}

function Save-Json {
    param([string]$Name, $Value)
    $Value | ConvertTo-Json -Depth 12 | Set-Content -Path (Join-Path $outDir $Name) -Encoding UTF8
}

function Test-HttpOk {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

# ---------------- 前置 ----------------

Write-Host "== RecallCoach 端到端验证 ==" -ForegroundColor Cyan
Write-Host "PowerContext : $PcUrl"

if (-not $PcToken) {
    $envMap = Read-EnvFile $EnvFile
    $PcToken = $envMap["POWERCONTEXT_SERVER_AUTH_TOKEN"]
}
if (-not $PcToken) {
    throw "拿不到 PowerContext token：用 -PcToken，或确认 $EnvFile 存在。"
}
if (-not (Test-HttpOk "$PcUrl/health/ready")) {
    throw "PowerContext 未就绪：$PcUrl。先运行 .\scripts\start.ps1 -ServerOnly"
}

$pcHeaders = @{ Authorization = "Bearer $PcToken" }

# 1) 独立 scope
$scope = Invoke-Json -Method POST -Url "$PcUrl/v1/scopes" -Headers $pcHeaders -Body @{
    title           = "RecallCoach 验证 $stamp"
    summary         = "scripts/verify.ps1 自动创建，可随时删除。"
    idempotency_key = "recallcoach-verify-$stamp"
}
$script:scopeId = $scope.scope_id
if (-not $script:scopeId) { $script:scopeId = $scope.id }
if (-not $script:scopeId) { throw "创建 scope 失败：$($scope | ConvertTo-Json -Depth 5)" }
Add-Result "01 scope" "PASS" "新建独立 scope" "scope_id=$($script:scopeId)"

# 2) 起隔离的应用实例
$runDb = Join-Path $outDir "app.db"
$runAudit = Join-Path $outDir "memory_audit.jsonl"
$env:POWERCONTEXT_BASE_URL = $PcUrl
$env:POWERCONTEXT_TOKEN = $PcToken
$env:POWERCONTEXT_SCOPE_ID = $script:scopeId
$env:RECALLCOACH_DB = $runDb
$env:RECALLCOACH_AUDIT = $runAudit
$env:RECALLCOACH_TODAY = $Today
$env:APP_PORT = "$AppPort"
$env:PYTHONPATH = Join-Path $appDir "src"

$stdout = Join-Path $outDir "app.stdout.log"
$stderr = Join-Path $outDir "app.stderr.log"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $file = (Get-Command uv).Source
    $arguments = @("run", "uvicorn", "recallcoach.main:app", "--port", "$AppPort")
} else {
    $file = (Get-Command python -ErrorAction Stop).Source
    $arguments = @("-m", "uvicorn", "recallcoach.main:app", "--port", "$AppPort")
}
$script:appProc = Start-Process -FilePath $file -ArgumentList $arguments -WorkingDirectory $appDir `
    -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr

$base = "http://127.0.0.1:$AppPort"
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    if (Test-HttpOk "$base/api/health") { $ready = $true; break }
    Start-Sleep -Milliseconds 800
}
if (-not $ready) {
    if (Test-Path $stderr) { Get-Content $stderr -Tail 20 | Write-Host }
    throw "应用 40 秒内未就绪，日志：$stderr"
}
Add-Result "02 app" "PASS" "隔离实例可访问" "$base（db=$runDb）"

try {
    # ---------------- V1 冷启动 ----------------
    $todayPayload = Invoke-Json -Method GET -Url "$base/api/today?personalization=on"
    Save-Json "v1-today.json" $todayPayload
    $script:sessionId = $todayPayload.session_id
    $zeroWeak = @($todayPayload.items | Where-Object { $_.breakdown.weakness -ne 0 }).Count -eq 0
    Check "V1 冷启动" ($todayPayload.mode -eq "on" -and $todayPayload.items.Count -gt 0 -and $zeroWeak -and -not $todayPayload.coach_note) `
        "mode=on、有到期卡、weakness 全为 0、无教练备忘" `
        "mode=$($todayPayload.mode) items=$($todayPayload.items.Count) 非零weakness=$(@($todayPayload.items | Where-Object { $_.breakdown.weakness -ne 0 }).Count) coach_note='$($todayPayload.coach_note)'"

    # ---------------- V2 写入闭环 ----------------
    $actions = @()
    $entryId = $null
    $version = $null
    foreach ($n in 1..3) {
        $answer = Invoke-Json -Method POST -Url "$base/api/answer" -Body @{
            card_id = "db-022"; correct = $false; confidence = 1; session_id = $script:sessionId
        }
        $actions += "$($answer.decision.action)/$($answer.memory.status)"
        if ($answer.memory.entry_id) { $entryId = $answer.memory.entry_id }
        if ($answer.memory.version) { $version = $answer.memory.version }
        Save-Json "v2-answer-$n.json" $answer
    }
    Check "V2 写入闭环" ($actions[0] -like "skip/*" -and $actions[1] -like "write/*" -and $actions[2] -like "revise/*" -and $version -eq 2) `
        "skip → write（v1）→ revise（v2，同一 entry_id）" `
        "序列=$($actions -join ' → ') entry=$entryId v=$version"

    # ---------------- V3 记忆改变排序 ----------------
    $compare = Invoke-Json -Method GET -Url "$base/api/compare?limit=5"
    Save-Json "v3-compare.json" $compare
    $onTop = if ($compare.on.Count -gt 0) { $compare.on[0].id } else { "" }
    Check "V3 排序改变" ($onTop -eq "db-022" -and $compare.diff.Count -gt 0) `
        "个性化后 db-022 第 1 位，diff 非空" `
        "on[0]=$onTop diff=$($compare.diff.Count) 条"

    # ---------------- V5 上下文注入 ----------------
    $withNote = Invoke-Json -Method GET -Url "$base/api/today?personalization=on"
    Save-Json "v5-today-with-memory.json" $withNote
    $hasEntryChip = @($withNote.items | Where-Object { $_.memory_hits.Count -gt 0 }).Count -gt 0
    Check "V5 教练备忘与命中标注" ([bool]$withNote.coach_note -and $hasEntryChip) `
        "coach_note 非空，且卡片上标注命中 entry_id" `
        "coach_note 长度=$($withNote.coach_note.Length) 有命中的卡片=$(@($withNote.items | Where-Object { $_.memory_hits.Count -gt 0 }).Count)"

    # ---------------- V4 修订与退休 ----------------
    $revise = Invoke-Json -Method POST -Url "$base/api/memory/$entryId/revise" -Body @{
        text = "[kp:db/ch6/mvcc-readview] 只需记住：RR 在事务第一次快照读时创建 read view，RC 每条语句都创建。"
    }
    Save-Json "v4-revise.json" $revise
    $afterRevise = Invoke-Json -Method GET -Url "$base/api/today?personalization=on"
    $stillTop = if ($afterRevise.items.Count -gt 0) { $afterRevise.items[0].id } else { "" }

    $retire = Invoke-Json -Method POST -Url "$base/api/memory/$entryId/retire" -Body @{ reason = "验证：标记已掌握" }
    Save-Json "v4-retire.json" $retire
    $afterRetire = Invoke-Json -Method GET -Url "$base/api/compare?limit=5"
    Save-Json "v4-compare-after-retire.json" $afterRetire
    Check "V4 修订与退休" ($revise.ok -and $stillTop -eq "db-022" -and $afterRetire.diff.Count -eq 0) `
        "修订后仍是第 1 位；退休后 diff 归零" `
        "revise.v=$($revise.version) 修订后首位=$stillTop 退休后 diff=$($afterRetire.diff.Count)"

    # ---------------- V6 重启后仍在 ----------------
    $before = (Invoke-Json -Method GET -Url "$base/api/replay").entries.Count
    Stop-Process -Id $script:appProc.Id -Force
    Start-Sleep -Seconds 1
    $script:appProc = Start-Process -FilePath $file -ArgumentList $arguments -WorkingDirectory $appDir `
        -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        if (Test-HttpOk "$base/api/health") { $ready = $true; break }
        Start-Sleep -Milliseconds 800
    }
    $after = (Invoke-Json -Method GET -Url "$base/api/replay").entries.Count
    Save-Json "v6-replay-after-restart.json" @{ before = $before; after = $after }
    Check "V6 重启后记忆仍在" ($ready -and $after -eq $before -and $after -gt 0) `
        "应用重启后条目数不变" `
        "重启前=$before 重启后=$after"

    # ---------------- V7 Dashboard ----------------
    $dashboardUrl = "$PcUrl/dashboard/home"
    $status = $null
    try {
        $response = Invoke-WebRequest -Uri $dashboardUrl -TimeoutSec 5 -UseBasicParsing
        $status = $response.StatusCode
    } catch {
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        else { $status = -1 }
    }
    Add-Result "V7 Dashboard 入口" $(if ($status -ge 200 -and $status -lt 400) { "PASS" } else { "WARN" }) `
        "浏览器用 token 登录 $dashboardUrl，能看到同一 entry_id" `
        "HTTP $status（需人工登录核对，脚本只验证入口可达）"
}
finally {
    if (-not $KeepRunning -and $script:appProc -and -not $script:appProc.HasExited) {
        Stop-Process -Id $script:appProc.Id -Force -ErrorAction SilentlyContinue
    }
}

# ---------------- 汇总 ----------------

$pass = @($script:results | Where-Object { $_.Verdict -eq "PASS" }).Count
$fail = @($script:results | Where-Object { $_.Verdict -eq "FAIL" }).Count
$warn = @($script:results | Where-Object { $_.Verdict -eq "WARN" }).Count

$md = New-Object System.Collections.Generic.List[string]
$md.Add("# RecallCoach 端到端验证 $stamp")
$md.Add("")
$md.Add("- PowerContext：$PcUrl")
$md.Add("- 验证 scope：``$($script:scopeId)``")
$md.Add("- 业务日期：$Today")
$md.Add("- 结果：PASS $pass / WARN $warn / FAIL $fail")
$md.Add("")
$md.Add("| 步骤 | 结论 | 预期 | 实际 |")
$md.Add("|---|---|---|---|")
foreach ($row in $script:results) {
    $md.Add("| $($row.Step) | $($row.Verdict) | $($row.Expected) | $($row.Actual) |")
}
$md.Add("")
$md.Add("原始请求/响应见同目录 ``v*.json``；审计日志 ``memory_audit.jsonl`` 中每条都带 request_id。")
$summaryPath = Join-Path $outDir "summary.md"
$md -join "`r`n" | Set-Content -Path $summaryPath -Encoding UTF8

Write-Host ""
Write-Host "结果：PASS $pass / WARN $warn / FAIL $fail" -ForegroundColor $(if ($fail -gt 0) { "Red" } else { "Green" })
Write-Host "证据目录：$outDir"
Write-Host "汇总：$summaryPath"
if ($KeepRunning) { Write-Host "应用仍在 $base 运行，端口 $AppPort" }
if ($fail -gt 0) { exit 1 } else { exit 0 }
