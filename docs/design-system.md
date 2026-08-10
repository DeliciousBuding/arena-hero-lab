# 设计系统 · arena-hero-leaderboard

最后更新：2026-08-10

> 本文档约束本站视觉与交互的"组件 / 视觉变量 / 交互状态 / 验收标准"。
> 改动任何 ui 原语或 token 前先读本文。token 定义在 `src/app/globals.css`。

## 一、四层栈哲学

真正有效的 vibe coding 不是堆砌"高级、现代、有科技感"形容词，而是明确组件、视觉变量、交互状态和验收标准。本站采用四层现代 UI 栈：

| 层 | 职责 | 本站实现 |
|---|---|---|
| **shadcn 语法** | 组件代码与组合方式 | `src/components/ui/` 原语 + `class-variance-authority` 变体约束 |
| **Radix / Base UI 行为** | 交互行为与可访问性 | `@radix-ui/react-*`（separator/tabs/tooltip/scroll-area/slot/switch） |
| **Tailwind token** | 布局、样式、设计令牌 | Tailwind v4 CSS-first `@theme inline` + `:root`/`.dark` 变量 |
| **Lucide 图标** | 统一图标语言 | `lucide-react`（v1 移除品牌图标，GitHub 用 `GitBranch` 语义图标） |

约束：同一组件的圆角、颜色、间距、字号必须取自 token，禁止就地造数。`cn()` = `clsx` + `tailwind-merge` 消解类名冲突。

## 二、设计 token 体系（视觉变量层）

### 2.1 颜色（语义化，深/浅双主题）

深色（对齐 arena.ai 2026-08-10 实测）：

| token | 值 | 用途 |
|---|---|---|
| `--background` | `#252523` | 页面底色（arena.ai body bg 实测） |
| `--foreground` | `#f4f0eb` | 主文字（arena.ai body fg 实测） |
| `--card` | `#33302e` | 卡片表面（arena.ai card bg 实测，比 bg 亮） |
| `--border` | `#413d39` | 边线（arena.ai card border 实测） |
| `--muted-foreground` | `#a8a29a` | 次要文字 |
| `--brand` | `#e8b941` | 琥珀强调（排名/链接 hover/数据高亮，克制使用） |
| `--rank-gold/silver/bronze` | `#e8b941` / `#b8c0cc` / `#c8743f` | 名次徽章 |
| `--heat-0..6` | 7 阶暖色 | 热图色阶（低=浅米，高=琥珀→橙→深棕） |

浅色（暖白，反色逻辑）：`--background #faf9f7` / `--card #ffffff` / `--border #e0d9cf` / `--brand #b8881a`。

### 2.2 圆角

`--radius: 0.5rem`（基准）。派生：`sm = radius-4px` / `md = radius-2px` / `lg = radius` / `xl = radius+4px`。
约束：卡片用 `lg`（8px）、按钮用 `md`、徽章用 `full`、输入框用 `md`。

### 2.3 阴影

arena.ai 实测 card **无阴影**（扁平）。`--shadow-xs: transparent`。hover 用 border 变化（`hover:border-foreground/25`）而非 shadow。

### 2.4 字体

- `--font-sans`：`baselGrotesk` → `Inter` → `ui-sans-serif` → 中文 `Noto Sans SC` / `PingFang SC`（正文）
- `--font-serif`：`martinaPlantijn`（arena.ai 实测 h1 字体）→ `Iowan Old Style` / `Charter` / `Georgia` → 中文 `Source Han Serif SC`（标题）
- `--font-mono`：`JetBrains Mono` → `SF Mono`（等宽，数字用）

`h1`/`h2` 用 serif + **weight 300**（细体，arena.ai 实测 h1 weight 300，编辑性质感）。

## 三、UI 原语清单（`src/components/ui/`）

| 原语 | 文件 | cva 变体 | 基于 |
|---|---|---|---|
| Card / CardHeader / CardTitle / CardContent / CardFooter | `card.tsx` | — | 原生 div（扁平无阴影） |
| Badge | `badge.tsx` | `variant`: default/primary/brand/gold/silver/bronze/outline/destructive/success | 原生 span |
| Button | `button.tsx` | `variant`: default/brand/outline/ghost/link/destructive × `size`: default/sm/lg/icon/icon-sm | `@radix-ui/react-slot`（asChild 组合 Link） |
| Separator | `separator.tsx` | `orientation`: horizontal/vertical | `@radix-ui/react-separator` |
| Tabs / TabsList / TabsTrigger / TabsContent | `tabs.tsx` | — | `@radix-ui/react-tabs`（方向键切换、roving tabindex） |
| Tooltip / TooltipTrigger / TooltipContent / TooltipProvider | `tooltip.tsx` | — | `@radix-ui/react-tooltip`（focus 触发、Esc 关闭） |
| ScrollArea / ScrollBar | `scroll-area.tsx` | `orientation`: vertical/horizontal | `@radix-ui/react-scroll-area` |
| Table / TableHeader / TableBody / TableRow / TableHead / TableCell | `table.tsx` | — | 原生 table 语义元素 |
| Stat / StatLabel / StatValue / StatHint | `stat.tsx` | — | 原生 div（三段式数值展示） |
| ResourceTimelinePanel | `resource-timeline.tsx` | — | client 组件（SVG 折线：资源/人口曲线，场景×种子切换，每 50 tick 采样） |

## 四、交互状态与可访问性（验收标准）

1. **焦点环统一**：`:focus-visible` → `outline: 2px solid var(--ring)` + `outline-offset: 2px`。键盘可见，鼠标不干扰。
2. **键盘可达**：所有交互组件（Button/Tabs/Tooltip/ScrollArea/Separator）基于 Radix，支持 Tab 导航 + 方向键 + Esc 关闭 + focus trap。
3. **ARIA**：Separator 带 `aria-orientation`；Tooltip 带 `aria-describedby`；Tabs 带 `role="tab"`/`tabpanel"`。
4. **hover 状态**：Card 用 `hover:border-foreground/25`（border 变化）；Button 用 `hover:bg-*` 色阶变化；链接用 `hover:text-brand`。
5. **disabled**：`disabled:pointer-events-none disabled:opacity-50`。
6. **等宽数字**：`tnum` utility（`font-variant-numeric: tabular-nums`）——榜单/表格数字不跳动。

## 五、维护约束

- **禁止就地造数**：圆角/颜色/间距/字号必须取自 token。新增颜色先加 token，再用。
- **变体不分裂**：同一组件的 variant 在 cva 定义，不在使用处堆 className。
- **图标统一**：只用 `lucide-react`，不用 emoji 或内联 SVG（热图/雷达等数据 SVG 除外）。
- **品牌图标**：lucide v1 移除了 GitHub/Twitter 等品牌图标。GitHub 仓库链接用 `GitBranch`（git 语义图标）。
- **改 token 先改 globals.css**：`:root`（浅色）+ `.dark`（深色）成对改，`@theme inline` 映射 Tailwind utility。
- **改组件先读本文**：新增 variant 在 cva 加，不在使用处堆类。
