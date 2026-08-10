# 一键发布脚本（站点线数据更新链路）
#
# 用法：
#   powershell -File scripts/release.ps1                         # 内置副本 scripts/input/results.json
#   powershell -File scripts/release.ps1 -Source <results.json>  # 指定评测产物
#   powershell -File scripts/release.ps1 -LatestRun              # 自动检测最新产物（arena 主仓库 data/runs/sim）
#   powershell -File scripts/release.ps1 -LatestRun -RunRoot <dir>  # 自定义 run 根目录
#   powershell -File scripts/release.ps1 -SkipDeploy             # 只转换+构建，不部署
#   powershell -File scripts/release.ps1 -Force                  # 跳过"无更新"检查强制发布
#
# 流程：检测/校验产物 → convert（确定性转换）→ build → lint → 部署 gh-pages → 打印核对信息
param(
  [string]$Source = "",
  [switch]$LatestRun,
  [string]$RunRoot = "",
  [switch]$Force,
  [switch]$SkipDeploy
)
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot/.."

function Fail([string]$msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

Write-Host "==> release.ps1 站点发布" -ForegroundColor Cyan

# ---------- 1. 确定评测产物 ----------
$sourcePath = ""
if ($Source -ne "") {
  $sourcePath = (Resolve-Path $Source).Path
  if (-not (Test-Path $sourcePath)) { Fail "产物不存在: $Source" }
} elseif ($LatestRun) {
  $runRoot = if ($RunRoot -ne "") { $RunRoot } else { Join-Path $PSScriptRoot "..\..\data\runs\sim" }
  if (-not (Test-Path $runRoot)) {
    Fail "run 目录不存在: $runRoot（用 -RunRoot 指定，或 -Source 直接给产物路径）"
  }
  $runs = Get-ChildItem -Path $runRoot -Directory -Filter "arena-bench-*" | ForEach-Object {
    $rj = Join-Path $_.FullName "results.json"
    if (Test-Path $rj) {
      $meta = Get-Content $rj -Raw -Encoding UTF8 | ConvertFrom-Json
      [PSCustomObject]@{ Dir = $_.FullName; Results = $rj; GeneratedAt = $meta.generatedAt; Schema = $meta.schema }
    }
  } | Sort-Object GeneratedAt -Descending
  $latest = $runs | Select-Object -First 1
  if ($null -eq $latest) { Fail "未找到任何带 results.json 的 run（$runRoot）" }
  $sourcePath = $latest.Results
  Write-Host "==> 最新 run: $($latest.Dir)" -ForegroundColor DarkGray
  Write-Host "    generatedAt: $($latest.GeneratedAt) · schema: $($latest.Schema)"
} else {
  $sourcePath = Join-Path $PSScriptRoot "input\results.json"
  if (-not (Test-Path $sourcePath)) { Fail "内置副本不存在，请用 -Source 或 -LatestRun" }
}
Write-Host "==> 产物: $sourcePath"

# ---------- 2. 无更新检测（与当前 bench.json 对比） ----------
$benchPath = Join-Path $PSScriptRoot "..\src\data\bench.json"
$meta = Get-Content $sourcePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ((Test-Path $benchPath) -and -not $Force) {
  $cur = Get-Content $benchPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $same = ($cur.generatedAt -eq $meta.generatedAt) -and ($cur.schema -eq $meta.schema)
  if ($same) {
    Write-Host "==> 当前 bench.json 与该产物一致（generatedAt/schema 相同），无更新。用 -Force 强制发布。" -ForegroundColor Yellow
    exit 0
  }
}

# ---------- 3. 转换 + 构建 + 检查 ----------
Write-Host "==> convert（确定性转换）"
pnpm convert $sourcePath
if ($LASTEXITCODE -ne 0) { Fail "convert 失败（schema 校验不通过？）" }

Write-Host "==> build（静态导出）"
pnpm build
if ($LASTEXITCODE -ne 0) { Fail "build 失败" }

Write-Host "==> lint"
pnpm lint
if ($LASTEXITCODE -ne 0) { Fail "lint 失败" }

# ---------- 4. 部署 gh-pages（legacy 分支模式，worktree 方式） ----------
if (-not $SkipDeploy) {
  Write-Host "==> 部署到 gh-pages"
  git worktree remove -f .worktrees/gh-pages 2>$null
  git worktree prune
  git worktree add -B gh-pages .worktrees/gh-pages origin/gh-pages 2>$null
  if ($LASTEXITCODE -ne 0) { Fail "worktree 创建失败（origin/gh-pages 不存在？先手动部署一次）" }

  Push-Location .worktrees/gh-pages
  try {
    git rm -rq --ignore-unmatch . 2>$null
    Get-ChildItem -Force | Where-Object { $_.Name -ne ".git" } | Remove-Item -Recurse -Force
    Copy-Item -Path "$PSScriptRoot\..\out\*" -Destination . -Recurse -Force
    New-Item -ItemType File -Name .nojekyll -Force | Out-Null
    git add -A
    git -c user.name="deploy" -c user.email="deploy@localhost" commit -m "deploy: $(Get-Date -Format o)" 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Host "==> 无变更，跳过推送" -ForegroundColor Yellow }
    else { git push origin HEAD:gh-pages; if ($LASTEXITCODE -ne 0) { Fail "gh-pages 推送失败" } }
  } finally {
    Pop-Location
    git worktree remove -f .worktrees/gh-pages 2>$null
    git worktree prune
  }
} else {
  Write-Host "==> -SkipDeploy：跳过部署"
}

# ---------- 5. 核对信息 ----------
$scenarios = $meta.scenarios.Count
$matches = $meta.scenarios | ForEach-Object { $_.matches.Count } | Measure-Object -Sum
Write-Host ""
Write-Host "==> 发布完成核对" -ForegroundColor Green
Write-Host "    产物: $sourcePath"
Write-Host "    schema: $($meta.schema) · generatedAt: $($meta.generatedAt)"
Write-Host "    参数: $($meta.params.players) 条目 × $($meta.params.seeds.Count) 种子 × $($meta.params.ticks) ticks · $scenarios 场景 · $($matches.Sum) 场"
if (-not $SkipDeploy) {
  Write-Host "    线上: https://deliciousbuding.github.io/arena-hero-leaderboard/"
  Write-Host "    注意: Pages 同步有 ~1-3 分钟延迟"
}
