#Requires -Version 5.1
<#
.SYNOPSIS
    PowerContext 技术层验证：建独立 scope → 写两条记忆 → 中文检索 → 组装上下文 → 盘点。
    不依赖应用代码，产物写入 evidence/verify/<时间戳>/。

.EXAMPLE
    .\scripts\verify-powercontext.ps1
    .\scripts\verify-powercontext.ps1 -EnvFile .\.runtime\home\powercontext.env
#>
[CmdletBinding()]
param(
    [string]$EnvFile = (Join-Path $env:USERPROFILE ".recallcoach\powercontext.env"),
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$KpTag = "db/ch6/mvcc-readview"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outDir = Join-Path $root "evidence\verify\$stamp"
New-Item -ItemType Directory -Force $outDir | Out-Null

$script:results = New-Object System.Collections.ArrayList

function Add-Result {
    param([string]$Step, [string]$Verdict, [string]$Detail)
    [void]$script:results.Add([pscustomobject]@{ Step = $Step; Verdict = $Verdict; Detail = $Detail })
    $color = switch ($Verdict) {
        "PASS" { "Green" }
        "FAIL" { "Red" }
        "WARN" { "Yellow" }
        default { "Gray" }
    }
    Write-Host ("[{0}] {1} - {2}" -f $Verdict, $Step, $Detail) -ForegroundColor $color
}

function Read-EnvFile {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path $Path)) { return $map }
    foreach ($line in Get-Content $Path -Encoding UTF8) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#")) { continue }
        $i = $t.IndexOf("=")
        if ($i -lt 1) { continue }
        $map[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim().Trim('"')
    }
    return $map
}

$envMap = Read-EnvFile $EnvFile
$Token = $envMap["POWERCONTEXT_SERVER_AUTH_TOKEN"]
if (-not $Token) { throw "在 $EnvFile 里找不到 POWERCONTEXT_SERVER_AUTH_TOKEN。先运行 .\scripts\bootstrap.ps1" }

function Invoke-Pc {
    param([string]$Method, [string]$Path, [object]$Body = $null)
    $req = @{
        Uri             = "$BaseUrl$Path"
        Method          = $Method
        Headers         = @{ Authorization = "Bearer $Token" }
        TimeoutSec      = 30
        UseBasicParsing = $true
    }
    if ($null -ne $Body) {
        $req["Body"] = ($Body | ConvertTo-Json -Depth 12)
        $req["ContentType"] = "application/json; charset=utf-8"
    }
    try {
        $resp = Invoke-WebRequest @req
        $rid = $null
        try {
            $rid = $resp.Headers["X-PowerContext-Request-ID"]
            if ($rid -is [array]) { $rid = $rid[0] }
        } catch { }
        $json = $null
        try { $json = $resp.Content | ConvertFrom-Json } catch { }
        [pscustomobject]@{ Ok = $true; Status = [int]$resp.StatusCode; Raw = $resp.Content; Json = $json; RequestId = $rid; Error = $null }
    } catch {
        $status = $null
        if ($_.Exception.Response) {
            try { $status = [int]$_.Exception.Response.StatusCode } catch { }
        }
        $raw = $null
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $raw = $_.ErrorDetails.Message }
        if (-not $raw) { $raw = $_.Exception.Message }
        [pscustomobject]@{ Ok = $false; Status = $status; Raw = $raw; Json = $null; RequestId = $null; Error = $_.Exception.Message }
    }
}

function Save-Raw {
    param([string]$Name, $Result)
    $path = Join-Path $outDir $Name
    $payload = [pscustomobject]@{
        status     = $Result.Status
        request_id = $Result.RequestId
        ok         = $Result.Ok
        raw        = $Result.Raw
    }
    $payload | ConvertTo-Json -Depth 12 | Set-Content -Path $path -Encoding UTF8
}

function Get-EntryId {
    param($Response)
    if ($null -eq $Response) { return $null }
    if ($Response.entry -and $Response.entry.citation -and $Response.entry.citation.entry_id) { return $Response.entry.citation.entry_id }
    if ($Response.citation -and $Response.citation.entry_id) { return $Response.citation.entry_id }
    return $null
}

Write-Host "== PowerContext 技术层验证 ==" -ForegroundColor Cyan
Write-Host "Server: $BaseUrl"
Write-Host "产物:   $outDir"
Write-Host ""

# 0. 健康检查（无需鉴权）
$live = $null
try {
    $live = Invoke-WebRequest -Uri "$BaseUrl/health/ready" -TimeoutSec 10 -UseBasicParsing
    Save-Raw "00-ready.json" ([pscustomobject]@{ Ok = $true; Status = [int]$live.StatusCode; Raw = $live.Content; RequestId = $null; Json = $null; Error = $null })
    Add-Result "ready" "PASS" "HTTP $($live.StatusCode)"
} catch {
    Add-Result "ready" "FAIL" "无法连接 $BaseUrl，先启动：.\scripts\start.ps1 -ServerOnly"
    throw "PowerContext 未就绪，终止验证。"
}

# 1. 能力
$caps = Invoke-Pc -Method GET -Path "/v1/capabilities"
Save-Raw "01-capabilities.json" $caps
if ($caps.Ok) {
    $modeText = $caps.Raw
    Add-Result "capabilities" "PASS" ($(if ($modeText -match "hybrid") { "含 vector/hybrid" } else { "仅全文检索（未配置 embedding，预期）" }))
} else {
    Add-Result "capabilities" "FAIL" "HTTP $($caps.Status)：$($caps.Raw)"
}

# 2. 独立 scope
$scopeKey = "recallcoach-verify-$stamp"
$scopeResp = Invoke-Pc -Method POST -Path "/v1/scopes" -Body @{
    title           = "RecallCoach 验证 $stamp"
    summary         = "scripts/verify-powercontext.ps1 自动创建的验证 scope，可随时删除。"
    idempotency_key = $scopeKey
}
Save-Raw "02-scope.json" $scopeResp
$scopeId = $null
if ($scopeResp.Ok -and $scopeResp.Json) {
    $scopeId = $scopeResp.Json.scope_id
    if (-not $scopeId) { $scopeId = $scopeResp.Json.id }
}
if ($scopeId) {
    Add-Result "create-scope" "PASS" "scope_id=$scopeId"
} else {
    Add-Result "create-scope" "FAIL" "未拿到 scope_id：$($scopeResp.Raw)"
    throw "无法创建 scope，终止验证。"
}

$meta = [pscustomobject]@{ base_url = $BaseUrl; scope_id = $scopeId; kp_tag = $KpTag; created_at = (Get-Date).ToString("o") }
$meta | ConvertTo-Json | Set-Content -Path (Join-Path $outDir "env.json") -Encoding UTF8

# 3. 写入两条记忆
$weakText = "[kp:$KpTag] MVCC 的 read view 创建时机反复出错：能说出「快照」，但说不清 RC 与 RR 的差别。"
$weak = Invoke-Pc -Method POST -Path "/v1/memory/remember" -Body @{
    scope_id = $scopeId
    kind     = "weakness"
    text     = $weakText
    reason   = "验证脚本写入：第二次同知识点失败"
}
Save-Raw "03-remember-weakness.json" $weak
$weakId = Get-EntryId $weak.Json
if ($weak.Ok -and $weakId) {
    Add-Result "remember:weakness" "PASS" "entry_id=$weakId"
} else {
    Add-Result "remember:weakness" "FAIL" "HTTP $($weak.Status)：$($weak.Raw)"
}

$prefText = "[pref] 先给结论再给推导；单个概念不超过 3 句；例子优先用「两个并发事务」。"
$pref = Invoke-Pc -Method POST -Path "/v1/memory/remember" -Body @{
    scope_id = $scopeId
    kind     = "preference"
    text     = $prefText
    reason   = "验证脚本写入：讲解偏好"
}
Save-Raw "04-remember-preference.json" $pref
$prefId = Get-EntryId $pref.Json
if ($pref.Ok -and $prefId) {
    Add-Result "remember:preference" "PASS" "entry_id=$prefId"
} else {
    Add-Result "remember:preference" "FAIL" "HTTP $($pref.Status)：$($pref.Raw)"
}

# 4. 中文检索（命中）
$hit = Invoke-Pc -Method POST -Path "/v1/memory/search" -Body @{
    scope_id = $scopeId
    query    = "事务 并发 隔离级别 read view 快照"
    limit    = 5
    mode     = "auto"
}
Save-Raw "05-search-hit.json" $hit
$hitCount = 0
if ($hit.Json -and $hit.Json.hits) { $hitCount = @($hit.Json.hits).Count }
if ($hit.Ok -and $hitCount -ge 1 -and $hit.Raw.Contains($KpTag)) {
    Add-Result "search:hit" "PASS" "命中 $hitCount 条，含 $KpTag；mode=$($hit.Json.mode)"
} elseif ($hit.Ok -and $hitCount -ge 1) {
    Add-Result "search:hit" "WARN" "命中 $hitCount 条但未包含 $KpTag；mode=$($hit.Json.mode)"
} else {
    Add-Result "search:hit" "FAIL" "未命中（HTTP $($hit.Status)）；mode=$($hit.Json.mode)"
}

# 5. 无意义检索（应为空）
$miss = Invoke-Pc -Method POST -Path "/v1/memory/search" -Body @{
    scope_id = $scopeId
    query    = "zzzqqqxxx"
    limit    = 5
    mode     = "auto"
}
Save-Raw "06-search-miss.json" $miss
$missCount = 0
if ($miss.Json -and $miss.Json.hits) { $missCount = @($miss.Json.hits).Count }
if ($miss.Ok -and $missCount -eq 0) {
    Add-Result "search:miss" "PASS" "无关 query 返回 0 条"
} else {
    Add-Result "search:miss" "WARN" "返回 $missCount 条（全文检索的正常噪声，记录以便复核）"
}

# 6. 上下文组装
$prepareBody = @{
    scope_id  = $scopeId
    query     = "数据库系统 第6章 事务与并发 MVCC read view"
    max_bytes = 2000
    assembly  = @{ sections = @(@{ family = "memory"; limit = 4 }); show = @("recall_rank") }
}
$prep = Invoke-Pc -Method POST -Path "/v1/context/prepare" -Body $prepareBody
if (-not $prep.Ok -and $prep.Status -eq 422) {
    $prep = Invoke-Pc -Method POST -Path "/v1/context/prepare" -Body @{
        scope_id = $scopeId; query = "数据库系统 第6章 事务与并发 MVCC read view"; max_bytes = 2000
    }
}
Save-Raw "07-prepare.json" $prep
$prepStatus = $null
$prepContent = $null
if ($prep.Json) {
    $prepStatus = $prep.Json.status
    $prepContent = $prep.Json.content
}
if ($prep.Ok -and $prepStatus -eq "ready" -and $prepContent) {
    Add-Result "context:prepare" "PASS" ("status=ready，content {0} 字符" -f $prepContent.Length)
} elseif ($prep.Ok -and $prepStatus -eq "empty") {
    Add-Result "context:prepare" "WARN" "status=empty（召回为空，检查检索词）"
} else {
    Add-Result "context:prepare" "FAIL" "HTTP $($prep.Status)：$($prep.Raw)"
}

# 7. 盘点
$list = Invoke-Pc -Method POST -Path "/v1/memory/entries/list" -Body @{ scope_id = $scopeId }
Save-Raw "08-entries-list.json" $list
$listCount = 0
if ($list.Json) {
    if ($list.Json.entries) { $listCount = @($list.Json.entries).Count }
    elseif ($list.Json.items) { $listCount = @($list.Json.items).Count }
}
if ($list.Ok) {
    Add-Result "entries:list" "PASS" "HTTP 200；条目数 $listCount（原始响应见 08-entries-list.json）"
} else {
    Add-Result "entries:list" "FAIL" "HTTP $($list.Status)：$($list.Raw)"
}

# 汇总
$passCount = @($script:results | Where-Object { $_.Verdict -eq "PASS" }).Count
$failCount = @($script:results | Where-Object { $_.Verdict -eq "FAIL" }).Count
$warnCount = @($script:results | Where-Object { $_.Verdict -eq "WARN" }).Count

$md = New-Object System.Collections.Generic.List[string]
$md.Add("# PowerContext 技术层验证 $stamp")
$md.Add("")
$md.Add("- Server：$BaseUrl")
$md.Add("- 验证 scope：``$scopeId``")
$md.Add("- 知识点：``$KpTag``")
$md.Add("- 结果：PASS $passCount / WARN $warnCount / FAIL $failCount")
$md.Add("")
$md.Add("| 步骤 | 结论 | 说明 |")
$md.Add("|---|---|---|")
foreach ($r in $script:results) { $md.Add("| $($r.Step) | $($r.Verdict) | $($r.Detail) |") }
$md.Add("")
$md.Add("原始请求/响应见同目录 `00-*.json` 到 `08-*.json`；审计请求 ID 记录在对应 json 的 ``request_id`` 字段。")
$mdPath = Join-Path $outDir "summary.md"
$md -join "`r`n" | Set-Content -Path $mdPath -Encoding UTF8

Write-Host ""
Write-Host "结果：PASS $passCount / WARN $warnCount / FAIL $failCount" -ForegroundColor $(if ($failCount -gt 0) { "Red" } else { "Green" })
Write-Host "汇总：$mdPath"
if ($failCount -gt 0) { exit 1 } else { exit 0 }
