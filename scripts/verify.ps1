# 本地全门禁一条命令（Windows PowerShell 版）：与 verify.sh 读同一份 verify-steps.txt。
#
# 用法：
#   .\scripts\verify.ps1              # 只跑 fast 档
#   .\scripts\verify.ps1 -All         # 连 slow 档（E2E + 视觉基线）一起跑
#   .\scripts\verify.ps1 -Group backend
#
# 退出码：0 = 全绿；1 = 有步骤失败。成败一律看 $LASTEXITCODE（uv/git 往 stderr 写进度会染红，
# 红字不等于失败——memory 27⑤ 记过的坑）。

param(
    [switch]$All,
    [string]$Group = ''
)

$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $Here
$Manifest = Join-Path $Here 'verify-steps.txt'
if (-not (Test-Path $Manifest)) { Write-Error "缺少步骤清单 $Manifest"; exit 2 }

$env:PYTHONUTF8 = '1'
$env:NO_PROXY = '*'
$Tier = if ($All) { 'all' } else { 'fast' }
New-Item -ItemType Directory -Force -Path (Join-Path $Repo '.verify') | Out-Null

$summary = @()
$failed = 0
$overall = [System.Diagnostics.Stopwatch]::StartNew()

foreach ($raw in Get-Content $Manifest -Encoding UTF8) {
    $line = $raw.Trim()
    if ($line -eq '' -or $line.StartsWith('#')) { continue }
    $parts = $line.Split('|') | ForEach-Object { $_.Trim() }
    if ($parts.Count -lt 4) { continue }
    $group, $label, $tier, $cmd = $parts
    if ($tier -eq 'slow' -and $Tier -eq 'fast') { continue }
    if ($Group -ne '' -and $group -ne $Group) { continue }

    $dir = if ($group -eq 'repo') { $Repo } else { Join-Path $Repo $group }
    Write-Host "`n=== [$group] $label ===" -ForegroundColor Cyan
    $step = [System.Diagnostics.Stopwatch]::StartNew()
    Push-Location $dir
    try {
        & cmd.exe /d /c $cmd
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    $step.Stop()
    $secs = [math]::Round($step.Elapsed.TotalSeconds)
    if ($code -ne 0) {
        $failed += 1
        $summary += "FAIL | ${secs}s | $group | $label"
    } else {
        $summary += "ok   | ${secs}s | $group | $label"
    }
}
$overall.Stop()

Write-Host "`n===== verify 汇总（总耗时 $([math]::Round($overall.Elapsed.TotalSeconds))s）====="
$summary | ForEach-Object { Write-Host $_ }
if ($failed -gt 0) {
    Write-Host "`n$failed 个步骤失败" -ForegroundColor Red
    exit 1
}
Write-Host "`n全部门禁通过（$($summary.Count) 步）" -ForegroundColor Green
exit 0
