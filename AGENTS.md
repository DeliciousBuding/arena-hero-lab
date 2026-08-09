<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

# arena-hero-leaderboard 设计系统规则

## 四层 UI 栈（不可绕过）

所有组件必须经此四层，禁止就地造 UI：

1. **shadcn 语法**（`src/components/ui/`）：card/badge/button/separator/tabs/tooltip/scroll-area/table/stat——cva 变体约束，改组件先改 cva
2. **Radix 行为**：separator/tabs/tooltip/scroll-area 基于 @radix-ui/react-*，键盘可访问 + ARIA 由 Radix 保证
3. **Tailwind token**：颜色/圆角/阴影/字体取自 `globals.css` 的 CSS 变量，禁止硬编码十六进制
4. **Lucide 图标**：唯一图标来源（lucide v1 移除了品牌图标，GitHub 仓库用 `GitBranch` 替代）

## 视觉变量约束

- 圆角：卡片 `rounded-lg`、按钮 `rounded-md`、徽章 `rounded-full`——不可混用
- 颜色：语义色（primary/secondary/muted/accent/brand/rank-*/heat-*），禁止 raw hex
- 字体：标题 `font-serif`（Noto Serif SC，font-weight 400）、正文 `font-sans`（Inter）、数字 `tnum`
- 间距：section 间 `mb-16`、card 内 `p-6`、stat 网格 `gap-3`
- 阴影：`shadow-xs`（默认）→ `shadow-sm`（hover），克制使用

## 交互状态（cva 内统一）

- hover/active/focus-visible/disabled 全部在 cva 变体定义，不分裂
- 焦点环：`:focus-visible { outline: 2px solid var(--ring); }`——键盘可见、鼠标不干扰
- Tooltip 必须用 `TooltipProvider` 包裹（已在 `Providers` 全局开启）

## 类名合并

用 `cn()`（`src/lib/utils.ts`，clsx + tailwind-merge），保证变体合并不冲突。禁止直接字符串拼接类名。

## 数据层

- `src/data/bench.json` 由 `scripts/convert.mts` 生成（确定性变换，不编造数字）
- `src/lib/bench.ts` 定义类型 + `contestantOf`/`leaderboardRowOf` 访问器
- `src/lib/dimensions.ts` 把 benchData 组织成 4 张维度卡片
- agent 仓库/LinuxDO 帖子映射在 `convert.mts` 的 `CONTESTANT_REPO_URL` / `CONTESTANT_LINUXDO_URL`，不在前端硬编码

