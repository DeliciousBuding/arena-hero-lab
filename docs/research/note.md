# 侦察与验证记录（L-D 线）

## 1. arena.ai/leaderboard 侦察（2026-08-09）

素材来自总负责人实测摘要（总负责人已在浏览器实测，本线直接采用）：

- 技术栈推断：Next.js + Tailwind v4 + shadcn/ui 风格；变量名如 `text-text-primary` / `bg-surface-primary` / `sidebar`
- 布局：左侧固定 sidebar（New Chat / Leaderboard / Search 导航 + Log In + Terms/Privacy/Cookies 底部），右侧主区垂直滚动
- 主区顶部：页头（"Leaderboard Overview" + 说明文字 + "Edit View" 按钮）
- 分类卡片：Agent / Text / WebDev / Vision / Document / Text-to-Image / Image Edit / Image-to-WebDev / Search / Text-to-Video / Image-to-Video / Video Edit
- 卡片结构：左侧 🏆 Overall 标题，右侧 top10 表（名次 | 条目名 | 分数 ± 置信区间；Agent 类可展示胜率百分比与误差）+ "View all" 链接
- 底部 footer 五列：USE CASES / LEADERBOARD RANKINGS / COMPANY / LEGAL / FOLLOW
- 深色主题倾向，图标密度高，排名徽章金/银/铜

**本线取舍**（用户裁定"风格神似即可"）：保留 sidebar + 分类卡片 + top10 表 + 五列 footer + 深色主打的骨架；
分类换成我们的六维度（综合分/击杀/生存规模/场景梯度/五维画像/生态），Elo 换成真实评测指标，置信区间换成场景名次标准差。

## 2. 本线浏览器验证（2026-08-09，codex-browser 桥实测）

验证方式：`pnpm dev` 启动后经 codex-browser MCP 导航 localhost:3000，
DOM 快照 + Runtime.evaluate 抽查 + 视口截图。以下为实测证据：

### 首页 `/`
- 结构齐备：左侧 sidebar（logo + 导航 + 数据版本 chip + 主题按钮）、hero（h1"评测榜单总览" + 元信息 chips）、6 张维度卡片、研究报告区块、关于本站、五列 footer ✓
- 深色主题生效：`document.documentElement.classList.contains('dark') === true`，body 背景 `rgb(16,16,22)`（#101016）✓
- 综合分卡片前三行（DOM 实测）：`1 ts-aggressive（内置军事压制）97.4% 均排 4.73 · rankScore 95.7%`、`2 ts-safety 80.8%`、`3 farmer-eco 80.0%`
- 对照 results.json：ts-aggressive composite=0.974468 → 97.4% ✓；farmer-eco=0.8 → 80.0% ✓；ts-safety=0.8078 → 80.8% ✓

### 条目详情 `/entry/ts-aggressive`
- h1"ts-aggressive（内置军事压制）"；雷达 SVG 存在；四个区块齐备（五维画像/画像原始值/分场景表现/单场明细 15 场）✓
- 数据抽查（DOM）：综合分 97.4%、平均名次 4.73、击杀/场 1.13 —— 与 results.json（composite 0.974468 / avgRank 4.7333 / killRate 1.1333）一致 ✓
- ffa-dense 行：均排 1.67（1/3 最好最差）、击杀 10、采集 318、上交 275、人口峰值 12.3、兵损 3.67 —— 与聚合脚本输出一致 ✓

### 全量榜单 `/leaderboard?dim=scenario`
- h1"全量榜单"，6 卡片；`dim-scenario` 卡片带 accent ring（`rgb(139,139,247) 0px 0px 0px 2px`）✓

### 交互
- 主题切换：点击后 `dark=false`、body `rgb(246,246,247)`、localStorage `arena-leaderboard-theme=light`，再点回 dark ✓
- 移动端（390×844 模拟）：桌面 sidebar `display:none`，顶栏汉堡按钮出现，点击弹出 288px 抽屉；卡片网格单列 ✓

### 收尾冒烟（浏览器桥断开后改用 curl）
- `GET /`、`/entry/ts-aggressive`、`/leaderboard?dim=scenario` 均 200，HTML 含关键标记
- `GET /research/04_radar.png` 200（白底图集正常伺服）

## 3. 构建验证

- `pnpm build`（Next 16.3 + Turbopack）：Compiled + TypeScript + 静态生成全部通过，零错误；
  路由：`/` 静态，`/entry/[id]`、`/leaderboard` 动态
- `pnpm lint`：0 errors / 0 warnings
- `node scripts/convert.mts` 可复跑：10 entries / 5 scenarios / 15 matches / 50 CSV rows

## 4. 综合分公式拟合（2026-08-09）

对 results.json 10 条 (rankScore, killScore, survivalScore, composite) 做最小二乘：
权重 rankScore=0.6000 / killScore=0.2000 / survivalScore=0.2000，最大绝对误差 8.05e-16（精确）。
已写入页面说明（"由本数据集最小二乘拟合验证"），未声称官方权重。
