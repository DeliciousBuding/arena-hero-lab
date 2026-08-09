# arena-hero-leaderboard

arena-hero 模拟器评测 v2 的产品级 Leaderboard 网站（独立 git 仓库）。
前端视觉复刻 [arena.ai/leaderboard](https://arena.ai/leaderboard)（sidebar + 分类卡片 + 榜单表 + footer、深色为主），
数据来自评测产物 `arena.bench.report.v2`（10 条目 × 5 场景 × 3 种子），全部静态生成，无后端。

## 技术栈

- Next.js 16（App Router）+ React 19 + TypeScript
- Tailwind CSS v4（CSS 变量令牌，类名风格参考原站 `bg-surface-primary` / `text-text-primary`）
- lucide-react（图标）
- 图表：五维雷达为自绘 SVG（无图表库）；科研图集为评测流程产出的白底 PNG

## 页面结构

| 路由 | 内容 |
|---|---|
| `/` | 榜单总览：项目说明 hero + 搜索 + 6 张维度卡片（综合分/击杀/生存规模/场景梯度/五维画像/生态）+ 研究报告（7 张白底图 + CSV 汇总表）+ 关于本站 |
| `/leaderboard?dim=<id>` | 全量榜单：6 张卡片纵向排列，`dim` 参数定位高亮 |
| `/entry/[id]` | 条目详情：五维雷达 + 画像原始值 + 分场景表现（3 场均值）+ 单场明细（15 场全指标） |

## 启动

```bash
pnpm install
pnpm dev        # http://localhost:3000
pnpm build      # 生产构建（零错误验证）
pnpm start      # 生产模式运行
pnpm lint       # eslint
```

## 数据刷新

```bash
# 使用仓库内置副本（scripts/input/results.json）
node scripts/convert.mts

# 或直接指向最新评测产物目录
node scripts/convert.mts --source="D:/Code/Projects/arena/data/runs/sim/<run-id>/results.json"
```

`scripts/convert.mts`（Node 24+ 原生运行 .mts）做确定性变换：

1. 校验 `schema === "arena.bench.report.v2"`
2. 聚合 15 场 matches → 每条目×每场景统计（均值/最好最差/击杀/采集/上交等）
3. 计算跨场景名次标准差（`±` 误差展示）
4. 解析 `06_summary_table.csv`（支持 BOM/空字段）
5. 输出 `src/data/bench.json`（构建时静态 import）

转换后需要 `pnpm build`（或 dev 会自动热更）让新数据生效。
`src/data/bench.json` 与 `scripts/input/results.json` 均已提交，仓库可离线复现。

## 目录结构

```
arena-hero-leaderboard/
├── scripts/
│   ├── convert.mts          # 数据转换脚本（可复跑）
│   └── input/results.json   # 评测产物副本（只读源）
├── src/
│   ├── app/                 # 路由：/ /leaderboard /entry/[id]
│   ├── components/          # sidebar/维度卡片/榜单表/雷达图/研究区块/footer
│   ├── lib/                 # 类型 + 数据层 + 维度定义
│   └── data/bench.json      # 转换产物（静态数据）
├── public/research/         # 白底科研图集（7 PNG + CSV）
└── docs/research/           # 侦察与验证记录
```

## 数据说明

- 所有数字均派生自 `results.json`，转换脚本只做聚合/排序/格式化，无任何 mock 榜单数字。
- 综合分公式 `rankScore×0.6 + killScore×0.2 + survivalScore×0.2` 由本数据集最小二乘拟合精确验证（最大误差 8e-16）。
- 交互：表格列头点击排序（升/降/名次）、搜索过滤、明/暗主题切换（localStorage 持久化）、移动端 sidebar 抽屉。
