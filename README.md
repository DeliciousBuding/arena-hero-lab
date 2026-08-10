# arena-hero-leaderboard

arena-hero 模拟器评测的产品级 Leaderboard 网站。
视觉参考 [arena.ai/leaderboard](https://arena.ai/leaderboard)（sidebar + 卡片 + 精致表格 + 深色为主），
**所有图表（热图 / 雷达 / 榜单 / 对比条）全部由前端 React + SVG 渲染，无 Python 出图、无图表库依赖**。
数据来自评测产物 `arena.bench.report.v3`，静态导出（`output: "export"`）部署于 GitHub Pages。

## 技术栈

四层现代 UI 栈（shadcn 语法 + Radix 行为 + Tailwind token + Lucide 图标）：

- **Next.js 16**（App Router）+ **React 19** + **TypeScript**
- **Tailwind CSS v4**（CSS 变量设计令牌：surface/text/accent/heat 色阶、阴影、圆角、字体族）
- **shadcn/ui 体系**（`src/components/ui/`）：card / badge / button / separator / tabs / tooltip / scroll-area / table / stat 原语，用 **class-variance-authority** 定义变体约束（variant × size），`cn()` = clsx + tailwind-merge 消解冲突
- **Radix UI primitives**（键盘可访问 + ARIA + focus trap）：@radix-ui/react-separator / tabs / tooltip / scroll-area / slot / switch
- **lucide-react**（统一图标语言，v1 移除品牌图标——GitHub 仓库用 GitBranch 语义图标）
- 图表：全部自绘 SVG（热图 / v3 四维雷达 / 迷你柱状图 / 指标条 / 击杀时序），零图表库依赖
- 设计 token 对齐 arena.ai 实测值（详见 `docs/design-system.md`）

## 页面结构

| 路由 | 内容 |
|---|---|
| `/` | 榜单总览：hero + 数据 Stat 卡群 + 前三名领奖台 + 综合排名增强表（多列排序）+ 场景×条目热图（资源/杀率/存活切换）+ 场景对比 |
| `/leaderboard` | 全量榜单：4 张 v3 维度卡片（综合分 / 经济 / 击杀 / 场景梯度），带搜索 |
| `/entry/[id]` | 条目详情：头部卡片（RankBadge + Badge + GitHub/LinuxDO 外链群 + 指标 stat 群）+ v3 四维雷达 + 排名分项条形 + 场景×指标小图 + 分场景表 + 单场明细表 + 击杀时序图（场景×种子切换） |

## 启动

```bash
pnpm install
pnpm dev        # 本地开发（basePath /arena-hero-leaderboard 下访问）
pnpm build      # 生产构建（静态导出，产出 out/）
pnpm preview    # 本地预览 out/（自动剥离 basePath，等效 npx serve out）
pnpm lint       # eslint
```

## 数据更新（评测跑完 → 转换 → 构建）

```bash
# 方式一：仓库内置副本（scripts/input/results.json，当前为 v3 冒烟样例）
npx tsx scripts/convert.mts

# 方式二：直接指向最新评测产物（推荐，全量评测完成后用此覆盖刷新）
npx tsx scripts/convert.mts <path-to>/results.json

pnpm build      # 重新构建，让新数据生效
```

`scripts/convert.mts` 做**确定性变换**（可重复运行、覆盖输出，不编造任何数字）：

1. 校验 `schema === "arena.bench.report.v3"`
2. 字段裁剪：contestants / leaderboard（含 economyScore、v2 兼容字段 survivalMedian/survivalScore 恒 1.0）/ scenarios（perEntry + matches）
3. 场景名 → 中文标签映射（如 `ffa-resource-race` → 中央矿争夺）
4. 派生计算：按综合分排序得 rank、由各场景 avgRank 算 rankStddev 与 scenarioRanks、由 matches 聚合每条目×每场景统计
5. 输出 `src/data/bench.json`（构建时静态 import，前端不再直接读 results.json）

刷新数据后：全量评测产物灌入 → 重新运行同一命令 → `pnpm build` → 推送即自动部署。

## 部署（GitHub Pages）

采用 **GitHub Pages 官方 Actions 方案**（`.github/workflows/deploy.yml`）：

1. 将本仓库推到 GitHub（第一次推送前先 `git remote add origin <repo-url>`）
2. 仓库 Settings → Pages → **Build and deployment → Source 选择 "GitHub Actions"**
3. 之后每次 `push` 到 `master` 即自动构建 `pnpm build` 并部署到
   `https://<user>.github.io/arena-hero-leaderboard/`（也可在 Actions 页手动 Run workflow）

说明：

- `next.config.ts`：`output: "export"` + `basePath: "/arena-hero-leaderboard"` + `images.unoptimized: true`
  —— 所有 Link/资源由 Next 自动处理 basePath 前缀，无动态 API（数据全静态 import）。
- 本地预览：`pnpm build && pnpm preview`（或 `npx serve out`，但需自行处理 basePath 前缀）。
- 若 Actions 方案受阻（如组织限制），退化为 push `gh-pages` 分支方案：
  `pnpm build` 后把 `out/` 内容提交到 `gh-pages` 分支，Pages Source 选择该分支。
- `public/.nojekyll`：避免 GitHub Pages 的 Jekyll 处理。

## 目录结构

```
arena-hero-leaderboard/
├── .github/workflows/deploy.yml   # GitHub Pages 官方 Actions 部署
├── scripts/
│   ├── convert.mts                # 数据转换脚本（npx tsx 运行，可重跑覆盖）
│   ├── preview.mjs                # 本地静态预览（basePath 感知）
│   └── input/results.json         # 评测产物副本（只读源，v3 冒烟样例）
├── src/
│   ├── app/                       # 路由：/ /leaderboard /entry/[id]
│   ├── components/                # 热图/雷达/榜单表/场景对比/迷你柱状图/sidebar/footer
│   ├── lib/                       # 类型 + 数据层 + 维度定义
│   └── data/bench.json            # 转换产物（静态数据，提交入库可离线复现）
└── public/                        # .nojekyll 等静态资源
```

## 数据说明

- 所有数字均派生自 `results.json`，转换脚本只做聚合/排序/格式化，无任何 mock 榜单数字。
- v3 榜单分项：composite / rankScore / killScore / economyScore；survivalScore 与
  survivalMedian 为 v2 兼容字段（v3 恒 1.0，已弃用，前端标注并隐藏）。
- 内置对照组（kind: builtin，如 ts-aggressive / ts-safety）以琥珀色徽章与底纹区分。
- 交互：表格列头点击排序、热图指标切换、明/暗主题切换（localStorage 持久化）、移动端 sidebar 抽屉。
