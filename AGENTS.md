<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# 数据更新与发布（站点线规则）

## 数据链路（全静态，无后端）

```
评测方产物 results.json（schema arena.bench.report.v3）
  → scripts/convert.mts（确定性转换：schema 校验/裁剪/聚合/标签映射，不编造数字）
  → src/data/bench.json（构建时 import）
  → pnpm build（静态导出 out/）
  → gh-pages 分支（legacy 模式）→ GitHub Pages
```

## 一键发布（推荐）

```powershell
powershell -File scripts/release.ps1 -LatestRun    # 自动检测最新产物并发布
powershell -File scripts/release.ps1 -Source <path> # 指定产物
powershell -File scripts/release.ps1 -SkipDeploy    # 只转换+构建不部署
```

- 流程：检测/校验产物 → convert → build → lint → 部署 gh-pages → 打印核对信息
- 无更新检测：产物与当前 bench.json 的 generatedAt/schema 相同则跳过（-Force 强制）
- 部署用 worktree 方式（gh-pages 分支），不要直接调 deploy-gh-pages.sh（bash 脚本在 PowerShell 会话下会挂起）
- 产物对比：`npx tsx scripts/compare-runs.mts <old.json> <new.json>`（榜单 Δ / 胜方 / 参数）

## 版本语义（三层，各管各的）

| 层 | 载体 | 什么时候变 | 站点要做什么 |
|---|---|---|---|
| 评测产物 | run 名 `v3.3-control10` / `v3.4` | 每次跑批 | 无（数据驱动自动适配）|
| 数据契约 | schema `arena.bench.report.v3` | 字段增删/语义变化才升 | **改 convert 的 SCHEMA 校验 + 字段映射**（需评测方提供契约说明，双端同步）|
| 站点 | git commit（master + gh-pages） | 每次发布 | 无（commit 即版本）|

- run 版本号 ≠ schema 版本号：v3.3/v3.4 仍是 `arena.bench.report.v3`，convert 契约不动
- 页面版本痕迹（hero meta 行 / footer）全部数据驱动，不硬编码

## 展示纪律

- 所有数字来自评测产物，convert 只做确定性变换，不编造、不归一化干预
- 榜单/热图/场景卡/详情页对全部参赛条目一视同仁（无内置对照特化）
- 文案覆盖（label/configNote/repoUrl/linuxdoUrl）集中在 convert.mts 的映射表，改文案只改那里
