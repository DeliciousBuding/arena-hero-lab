"use client";

import { useMemo } from "react";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { GitHubIcon } from "@/components/app-chrome";

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
  href?: string;
  /** GitHub 仓库链接（社区 agent 有，官方内置无）。 */
  repoUrl?: string;
}

const RANK_BADGE_CLASS = [
  "border-rank-gold/40 text-rank-gold bg-rank-gold/10",
  "border-rank-silver/40 text-rank-silver bg-rank-silver/10",
  "border-rank-bronze/40 text-rank-bronze bg-rank-bronze/10",
  "border-border text-muted-foreground bg-secondary/60",
] as const;

/**
 * 排名条形图（arena.ai Leaderboard Agent 榜两栏设计）：
 * 左栏 = 方形排名徽标 + 名称 + 副指标；右栏 = 圆角槽 + 横向条形 + 末端数值。
 * 前三名金银铜徽标；点击条目进入详情页。
 */
export function RankBars({
  rows,
  valueLabel,
}: {
  rows: RankBarRow[];
  valueLabel: string;
}) {
  const maxAbs = useMemo(() => {
    const values = rows.map((r) => Math.abs(r.value));
    return values.length ? Math.max(...values) : 1;
  }, [rows]);

  /** 条形长度：ascending = 越大越长；反向维度（如名次）= 越小越长。 */
  const widthPctOf = (row: RankBarRow): number => {
    if (maxAbs <= 0) return 2;
    const t = row.ascending ? Math.abs(row.value) / maxAbs : 1 - Math.abs(row.value) / maxAbs;
    return Math.max(2, t * 100);
  };

  return (
    <div className="@container">
      <div className="mb-4 text-xs text-muted-foreground">
        {rows.length} 条目 · 单位 {valueLabel}
      </div>

      <div role="list" aria-label={`${valueLabel}排名条形图`}>
        {rows.map((row) => (
          <div
            key={row.id}
            role="listitem"
            className="group border-b border-border-faint py-1.5 transition-colors first:pt-0 last:border-0"
          >
            {row.href !== undefined ? (
              <Link
                href={row.href}
                className="grid grid-cols-1 items-center gap-x-4 gap-y-2 rounded-md px-1 py-0.5 transition-colors hover:bg-secondary/40 @md:grid-cols-[minmax(0,280px)_minmax(0,1fr)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              >
                <RankRowLeft row={row} />
                <RankRowBar row={row} widthPct={widthPctOf(row)} />
              </Link>
            ) : (
              <div className="grid grid-cols-1 items-center gap-x-4 gap-y-2 rounded-md px-1 py-0.5 @md:grid-cols-[minmax(0,280px)_minmax(0,1fr)]">
                <RankRowLeft row={row} />
                <RankRowBar row={row} widthPct={widthPctOf(row)} />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/** 左栏：排名徽标 + 名称 + 副指标。 */
function RankRowLeft({ row }: { row: RankBarRow }) {
  const badgeIndex = Math.min(row.rank - 1, RANK_BADGE_CLASS.length - 1);
  return (
    <div className="flex min-w-0 items-center gap-3">
      <span
        className={cn(
          "flex h-6 w-6 shrink-0 items-center justify-center rounded-sm border text-[11px] font-medium tnum",
          RANK_BADGE_CLASS[badgeIndex],
        )}
      >
        {row.rank}
      </span>
      <div className="min-w-0">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="line-clamp-1 break-words text-sm font-medium text-foreground group-hover:text-brand">
            {row.label}
          </span>
          {row.repoUrl !== undefined ? (
            <span
              role="link"
              tabIndex={0}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                window.open(row.repoUrl, "_blank", "noreferrer");
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  e.stopPropagation();
                  window.open(row.repoUrl, "_blank", "noreferrer");
                }
              }}
              aria-label={`${row.label} 的 GitHub 仓库`}
              className="shrink-0 cursor-pointer text-muted-foreground opacity-60 transition-colors hover:text-foreground hover:opacity-100"
            >
              <GitHubIcon className="h-3 w-3" />
            </span>
          ) : null}
        </span>
        {row.secondary !== undefined ? (
          <span className="mt-0.5 block text-[11px] text-muted-foreground tnum">
            {row.secondary}
          </span>
        ) : null}
      </div>
    </div>
  );
}

/** 右栏：圆角槽 + 横向条形 + 末端主值（arena.ai Agent 榜同款）。 */
function RankRowBar({ row, widthPct }: { row: RankBarRow; widthPct: number }) {
  return (
    <div className="flex min-w-0 items-center gap-3">
      <div
        className="relative h-7 min-w-0 flex-1 overflow-hidden rounded-sm bg-secondary"
        role="img"
        aria-label={`${row.label} ${row.primary}`}
      >
        <div
          className={cn(
            "absolute inset-y-0 left-0 rounded-sm bg-gradient-to-r transition-[width] duration-500",
            row.rank === 1 ? "from-brand/75 to-brand" : "from-foreground/25 to-foreground/50",
          )}
          style={{ width: `${widthPct}%` }}
        />
      </div>
      <span className="w-14 shrink-0 text-right text-sm font-medium text-foreground tnum">
        {row.primary}
      </span>
    </div>
  );
}
