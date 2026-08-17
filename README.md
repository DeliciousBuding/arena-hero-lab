# Arena Hero Lab

Arena Hero Lab 是一个公开的单仓库（monorepo），用于确定性模拟、可复现评测、研究工作流、回放产物，以及 Arena Hero 排行榜 Web 应用。

![Python](https://img.shields.io/badge/Python-3.12-3776ab)
![Next.js](https://img.shields.io/badge/Next.js-16-black)
![React](https://img.shields.io/badge/React-19-61dafb)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178c6)
![Static Export](https://img.shields.io/badge/Static-Export-ffffff)
![License](https://img.shields.io/badge/License-Apache--2.0-blue)

仓库围绕可替换的包组织，依赖关系单向：

```text
arena-hero-sim <- arena-hero-bench <- arena-hero-research
                         |
                         +-> apps/leaderboard-web
```

## 仓库结构

- `packages/arena-hero-sim` — 确定性领域与序列化基础。
- `packages/arena-hero-bench` — 评测契约、清单、编排与报告转换。
- `packages/arena-hero-research` — 基于不可变产物的统计与研究工具。
- `apps/leaderboard-web` — Next.js 静态评测与回放浏览器。
- `docs` — 架构、设计系统与研究文档。

Python 包使用 `uv` 管理；浏览器应用使用 `pnpm`。TypeScript 仅用于浏览器代码、生成的类型，以及一个为迁移验证保留的临时转换器 oracle。

## 快速开始

环境要求：

- Python 3.12
- `uv`
- Node.js 22 或更新
- pnpm 10.32.1

```bash
uv sync --locked --all-groups
pnpm install --frozen-lockfile

uv run pytest -q
pnpm lint
pnpm build
```

本地运行 Web 应用：

```bash
pnpm dev
```

与 GitHub Pages 兼容的 base path 默认是 `/arena-hero-lab`，与公开仓库和 Pages 站点一致。为其他主机覆盖它：

```bash
NEXT_PUBLIC_BASE_PATH="" pnpm build
```

## 评测报告转换

Python 是权威转换器：

```bash
pnpm convert
```

它将 `arena.bench.report.v3` 转换为 `apps/leaderboard-web/src/data/bench.json` 的静态 Web 数据集。旧的 TypeScript 转换器仅保留为差分测试 oracle，其公开兼容源码位于 [arena-hero-agent-ts](https://github.com/DeliciousBuding/arena-hero-agent-ts)：

```bash
pnpm convert:oracle
```

发布命令默认在本地校验，仅当显式传入 `--deploy` 时才会发布（一个手动 legacy 路径，会更新 `gh-pages` 分支）：

```bash
python apps/leaderboard-web/scripts/release.py --force
```

注意：推送到 `main` 也会触发现有的 GitHub Actions Pages 工作流（`.github/workflows/deploy.yml`），它会自动构建并部署静态导出到 GitHub Pages。`--deploy` 标志是额外的手动路径，CI 部署不需要它。

## 公开排行榜

当前静态站点仍可在 <https://deliciousbuding.github.io/arena-hero-lab/> 访问。

## 平台状态证据

仓库会生成 `apps/leaderboard-web/src/data/platform.json`，作为有版本、fail-closed 的发布证据，覆盖 Python agent 一致性、模拟器差分校验，以及 研究 fit → 证书 → 报告 链路。公开 Leaderboard 只渲染排名；不暴露平台概览、平台导航项或平台详情页。

文档 schema 为 `arena.platform.status.v2`。用以下命令重新生成并校验：

```bash
pnpm convert:platform
```

该产物是**可复现性**的证据，不是竞技成绩或生产性能的声明。

## 架构与政策

- [架构](docs/architecture.md)
- [贡献指南](CONTRIBUTING.md)
- [行为准则](CODE_OF_CONDUCT.md)
- [许可证](LICENSE)
- [安全政策](SECURITY.md)
- [Web 设计系统](docs/design-system.md)

## 致谢

Arena Hero Lab 构建在公开的 [Arena Hero](https://doc.arenahero.io/) 生态之上。评测 fixture 引用了以下社区项目：
[Drew-Z](https://github.com/Drew-Z/arena-hero-agent)、
[VelvetEvening](https://github.com/VelvetEvening/ArenaHero-nearly-perfect-guide)、
[Waaiging](https://github.com/Waaiging/ArenaHero)、
[feixingwawa](https://github.com/feixingwawa/arena-hero-tactic) 和
[Torther](https://github.com/Torther/arena-evolve)。它们的代码与内容仍受各自仓库和许可证的约束。

排行榜的视觉设计参考了公开的
[LM Arena leaderboard](https://arena.ai/leaderboard)。

## 友情链接

| 站点 | 说明 |
| --- | --- |
| [Linux DO](https://linux.do/) | 开放技术交流社区，Arena Hero 智能体开源分享与玩法讨论的聚集地 |
| [Arena Hero 官网](https://app.arenahero.io/) | arena-hero 官方游戏入口：实时对局、段位与赛季玩法 |
| [Arena Hero 文档](https://doc.arenahero.io/) | 官方规则文档与游戏玩法说明 |
