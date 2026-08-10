"use client";

import { Search, X } from "lucide-react";
import { useMemo, useState } from "react";
import Link from "next/link";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

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
}

const RANK_COLOR = [
  "text-rank-gold",
  "text-rank-silver",
  "text-rank-bronze",
  "text-muted-foreground",
] as const;

/**
 * 排名横向条形图（arena.ai Leaderboard 风格）：
 * 每行 = 名次徽标 + 名称 + 渐变条形 + 主值；前三名金银铜色，其余浅灰。
 * 内置搜索过滤（按中文名 / 英文 id），支持任意数值维度（综合分/经济/击杀/名次）。
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

  const maxAbs = useMemo(() => {
    const values = filtered.map((r) => Math.abs(r.value));
    return values.length ? Math.max(...values) : 1;
  }, [filtered]);

  /** 条形长度：ascending = 越大越长；反向维度（如名次）= 越小越长。 */
  const widthPctOf = (row: RankBarRow): number => {
    if (maxAbs <= 0) return 2;
    const t = row.ascending ? Math.abs(row.value) / maxAbs : 1 - Math.abs(row.value) / maxAbs;
    return Math.max(2, t * 100);
  };

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
          {filtered.map((row) => {
            const widthPct = widthPctOf(row);
            const inner = (
              <>
                <span
                  className={cn(
                    "w-7 shrink-0 text-right font-serif text-xl font-normal tnum",
                    RANK_COLOR[Math.min(row.rank - 1, RANK_COLOR.length - 1)],
                  )}
                >
                  {row.rank}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="truncate text-sm font-medium text-foreground">
                      {row.label}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground tnum">
                      {row.primary}
                    </span>
                  </div>
                  <div
                    className="mt-1.5 h-2 overflow-hidden rounded-full bg-secondary"
                    role="img"
                    aria-label={`${row.label} ${row.primary}`}
                  >
                    <div
                      className={cn(
                        "h-full rounded-full bg-gradient-to-r transition-[width] duration-500",
                        row.rank === 1
                          ? "from-brand/80 to-brand"
                          : "from-foreground/30 to-foreground/55",
                      )}
                      style={{ width: `${widthPct}%` }}
                    />
                  </div>
                  {row.secondary !== undefined ? (
                    <p className="mt-1 text-[11px] text-muted-foreground tnum">
                      {row.secondary}
                    </p>
                  ) : null}
                </div>
              </>
            );
            return (
              <div
                key={row.id}
                role="listitem"
                className={cn(
                  "group border-b border-border-faint py-3 first:pt-0 last:border-0",
                  "transition-colors",
                )}
              >
                {row.href !== undefined ? (
                  <Link
                    href={row.href}
                    className="flex items-start gap-3 rounded-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                  >
                    {inner}
                  </Link>
                ) : (
                  <div className="flex items-start gap-3">{inner}</div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
