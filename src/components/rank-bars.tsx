"use client";

import { Search, X } from "lucide-react";
import { useMemo, useState } from "react";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

/** 迷你柱状图数据点（如各场景名次：值越小柱越高）。 */
export interface MiniBarDatum {
  label: string;
  value: number;
  /** 值越小柱越高（如名次）；默认值越大柱越高。 */
  inverted?: boolean;
}

/** 排名条形图数据行（任意数值维度通用：综合分/经济/击杀/平均名次）。 */
export interface RankBarRow {
  rank: number;
  id: string;
  label: string;
  kind: "python" | "builtin";
  /** 条形长度依据（归一化基准由全部行最大值决定）。 */
  value: number;
  /** 数值越大条形越长；false = 越小越好（如平均名次）。 */
  ascending: boolean;
  /** 主值显示文案（如 "96.8%"）。 */
  primary: string;
  /** 副指标文案（如 "均排 1.20"）。 */
  secondary?: string;
  /** 迷你柱状图数据（arena.ai 风格，如各场景名次分布）。 */
  bars?: MiniBarDatum[];
  href?: string;
}

const RANK_BADGE_CLASS = [
  "border-rank-gold/40 text-rank-gold bg-rank-gold/10",
  "border-rank-silver/40 text-rank-silver bg-rank-silver/10",
  "border-rank-bronze/40 text-rank-bronze bg-rank-bronze/10",
  "border-border text-muted-foreground bg-secondary/60",
] as const;

/**
 * 排名条形图（arena.ai Leaderboard Agent 榜风格）：
 * 每行 = 方形排名徽标 + 名称 + 右侧迷你柱状图（各场景名次分布）+ 主值。
 * 前三名金银铜徽标；内置搜索过滤；点击条目进入详情页。
 */
export function RankBars({
  rows,
  valueLabel,
  placeholder = "搜索条目（中文名 / 英文 id）…",
}: {
  rows: RankBarRow[];
  valueLabel: string;
  placeholder?: string;
}) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (row) => row.label.toLowerCase().includes(q) || row.id.toLowerCase().includes(q),
    );
  }, [rows, query]);

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="relative max-w-sm flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={placeholder}
            aria-label="搜索条目"
            className="h-9 w-full rounded-md border border-input bg-background pl-9 pr-9 text-sm text-foreground placeholder:text-muted-foreground focus:border-ring focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          />
          {query !== "" ? (
            <Button
              type="button"
              onClick={() => setQuery("")}
              variant="ghost"
              size="icon-sm"
              aria-label="清除搜索"
              className="absolute right-1 top-1/2 h-7 w-7 -translate-y-1/2"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          ) : null}
        </div>
        <span className="text-xs text-muted-foreground">
          {filtered.length} / {rows.length} 条目 · 单位 {valueLabel}
        </span>
      </div>

      {filtered.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted-foreground">没有匹配的条目。</p>
      ) : (
        <div role="list" aria-label={`${valueLabel}排名条形图`}>
          {filtered.map((row) => (
            <div
              key={row.id}
              role="listitem"
              className="group border-b border-border-faint py-2.5 transition-colors first:pt-0 last:border-0"
            >
              {row.href !== undefined ? (
                <Link
                  href={row.href}
                  className="flex items-center gap-3 rounded-md px-1 py-1 transition-colors hover:bg-secondary/40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  <RankRowInner row={row} />
                </Link>
              ) : (
                <div className="flex items-center gap-3 rounded-md px-1 py-1">
                  <RankRowInner row={row} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** 行内容：排名徽标 + 名称 + 迷你柱状图 + 主值（arena.ai 布局）。 */
function RankRowInner({ row }: { row: RankBarRow }) {
  const badgeIndex = Math.min(row.rank - 1, RANK_BADGE_CLASS.length - 1);
  return (
    <>
      <span
        className={cn(
          "flex h-6 w-6 shrink-0 items-center justify-center rounded-sm border text-[11px] font-medium tnum",
          RANK_BADGE_CLASS[badgeIndex],
        )}
      >
        {row.rank}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="truncate text-sm font-medium text-foreground group-hover:text-brand">
            {row.label}
          </span>
          <span className="shrink-0 text-[11px] text-muted-foreground tnum">{row.primary}</span>
        </div>
        {row.secondary !== undefined ? (
          <p className="mt-0.5 text-[11px] text-muted-foreground tnum">{row.secondary}</p>
        ) : null}
      </div>
      {row.bars !== undefined && row.bars.length > 0 ? <MiniBars data={row.bars} /> : null}
    </>
  );
}

const MINI_BAR_WIDTH = 6;
const MINI_BAR_GAP = 3;
const MINI_BAR_HEIGHT = 36;
const MINI_PAD = { top: 3, bottom: 0 };

/** 迷你柱状图（arena.ai Agent 榜同款）：每柱一个数据点，悬浮显示详情。 */
export function MiniBars({ data, ariaLabel }: { data: MiniBarDatum[]; ariaLabel?: string }) {
  const maxAbs = Math.max(...data.map((d) => Math.abs(d.value)), 1);
  const chartHeight = MINI_BAR_HEIGHT + MINI_PAD.top + MINI_PAD.bottom;
  const chartWidth = data.length * (MINI_BAR_WIDTH + MINI_BAR_GAP) - MINI_BAR_GAP;

  return (
    <svg
      viewBox={`0 0 ${chartWidth} ${chartHeight}`}
      width={chartWidth}
      height={chartHeight}
      role="img"
      aria-label={ariaLabel ?? "各场景表现迷你柱状图"}
      className="shrink-0"
    >
      {data.map((d, i) => {
        const t = Math.abs(d.value) / maxAbs;
        const normalized = d.inverted === true ? 1 - t : t;
        const h = Math.max(2, normalized * MINI_BAR_HEIGHT);
        const x = i * (MINI_BAR_WIDTH + MINI_BAR_GAP);
        const y = MINI_PAD.top + (MINI_BAR_HEIGHT - h);
        return (
          <rect
            key={i}
            x={x}
            y={y}
            width={MINI_BAR_WIDTH}
            height={h}
            rx={1.5}
            className="fill-foreground/25 transition-colors group-hover:fill-foreground/50"
          >
            <title>{`${d.label}: ${d.value}`}</title>
          </rect>
        );
      })}
    </svg>
  );
}
