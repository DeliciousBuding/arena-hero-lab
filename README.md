# Arena Hero Leaderboard

模拟器评测榜单站点：把 arena-hero 多智能体对抗评测（7 场景 × 5 种子，10 条目同场对抗）的数据变成一组直观的图表。

**在线地址：<https://deliciousbuding.github.io/arena-hero-leaderboard/>**

![Next.js](https://img.shields.io/badge/Next.js-16-black)
![React](https://img.shields.io/badge/React-19-61dafb)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178c6)
![Tailwind](https://img.shields.io/badge/TailwindCSS-v4-38bdf8)
![Static Export](https://img.shields.io/badge/Static-Export-ffffff)
![GitHub Pages](https://img.shields.io/badge/GitHub-Pages-4078c0)

## 这是什么

arena-hero 是一个基于 [Arena Hero](https://github.com/HenryXiaoYang/arena-hero) 的模拟器评测平台（评测引擎在 [DeliciousBuding/arena-hero-ts](https://github.com/DeliciousBuding/arena-hero-ts)）。本仓库是它的评测结果展示站：

- 10 个参赛 agent 同场对抗：8 个社区开源实现 + arena-ts 自家 TypeScript 客户端（ts-aggressive / ts-safety，同为参赛方，代码在 [DeliciousBuding/arena-hero-ts](https://github.com/DeliciousBuding/arena-hero-ts)）
- 7 种场景 × 5 个随机种子，共 35 场完整对局
- 每场输出资源曲线、人口曲线、击杀时序等可观测数据

站点把全部评测数据渲染成图表——排名、四维能力对比、场景热图、效率曲线、击杀时序——纯前端 SVG 绘制，零图表库依赖，静态部署，秒开。

## 页面

| 区块 | 内容 |
|---|---|
| Overall Rankings | 综合分排名（arena.ai 风格条形图，可搜索） |
| Scenario Leaderboards | 每个场景独立擂台（平均名次 + 资源/刻条） |
| Score Profile | 击杀 / 名次 / 经济三维归一化对比 |
| Scenario Heatmap | 场景 × 条目指标矩阵（资源/刻 · 击杀率 · 平均名次） |
| Entry 详情页 | 单智能体深度页：三维雷达、分场景名次条、效率曲线、击杀时序、单场明细 |

## 快速开始

```bash
pnpm install
pnpm dev        # 本地开发，http://localhost:3000/arena-hero-leaderboard
pnpm build      # 生产构建（静态导出 out/）
pnpm preview    # 本地预览构建产物
pnpm lint       # 代码检查
```

## 数据更新

评测完成后，把评测产物转换为站点数据并重新构建：

```bash
npx tsx scripts/convert.mts <path-to>/results.json
pnpm build
```

`scripts/convert.mts` 做确定性变换：校验 schema → 裁剪字段 → 聚合派生（排名 / 场景统计 / 击杀时序 / per-tick 采样）→ 输出 `src/data/bench.json`。可重复运行，不编造任何数字。

## 部署

线上采用 **GitHub Pages 静态部署**：本地 `pnpm build` 后把 `out/` 推送到 `gh-pages` 分支（Pages 以 legacy 分支模式托管）。

```bash
pnpm build
pnpm deploy:gh-pages   # 把 out/ 推送到 gh-pages 分支
```

> `.github/workflows/deploy.yml`（Actions 自动部署）保留在仓库中，账号计费问题解决后可切回自动部署。

## 技术栈

- **Next.js 16**（App Router，静态导出）+ **React 19** + **TypeScript**
- **Tailwind CSS v4** 设计令牌体系（暖黑/暖白双主题）
- **shadcn/ui** 原语 + **Radix UI**（键盘可达 + ARIA）+ **lucide-react**
- 图表全部自绘 SVG（热图 / 条形 / 雷达 / 折线），零图表库

## 仓库结构

```
├── .github/workflows/deploy.yml   # GitHub Pages 自动部署（Actions 恢复后启用）
├── scripts/
│   ├── convert.mts                # 评测产物 → 站点数据（确定性变换）
│   ├── deploy-gh-pages.sh         # 手动部署到 gh-pages 分支
│   └── preview.mjs                # 本地静态预览
├── src/
│   ├── app/                       # 路由与页面
│   ├── components/                # 图表与 UI 组件
│   ├── lib/                       # 类型与数据层
│   └── data/bench.json            # 转换产物（静态数据）
└── public/                        # 静态资源
```

## 致谢

- 评测引擎与智能体实现：[DeliciousBuding/arena-hero-ts](https://github.com/DeliciousBuding/arena-hero-ts)
- 上游游戏：[HenryXiaoYang/arena-hero](https://github.com/HenryXiaoYang/arena-hero)
- 参赛智能体均为社区开源实现（Drew-Z / VelvetEvening / Waaiging / feixingwawa / Torther）
- 视觉参考：[arena.ai/leaderboard](https://arena.ai/leaderboard)
