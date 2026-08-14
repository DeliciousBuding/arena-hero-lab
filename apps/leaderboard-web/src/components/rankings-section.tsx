"use client";

import { useState } from "react";
import { RankBars, type RankBarRow } from "@/components/rank-bars";
import { SectionHeader } from "@/components/section-header";
import { Card, CardContent } from "@/components/ui/card";
import { benchData, contestantOf, type LeaderboardRow } from "@/lib/bench";
import { cn } from "@/lib/utils";

/** 榜单排序维度（arena.ai "Edit View" 的极简对应：一个切换，不建复杂视图）。 */
type SortDimension = "composite" | "killScore" | "economyScore" | "avgRank";

const DIM_OPTIONS: { key: SortDimension; label: string; ascending: boolean }[] = [
  { key: "composite", label: "综合分", ascending: true },
  { key: "killScore", label: "击杀", ascending: true },
  { key: "economyScore", label: "经济", ascending: true },
  { key: "avgRank", label: "名次", ascending: false },
];

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

function rowFor(entry: LeaderboardRow, dim: SortDimension, rank: number): RankBarRow {
  const contestant = contestantOf(entry.contestantId);
  const option = DIM_OPTIONS.find((o) => o.key === dim) ?? DIM_OPTIONS[0];
  // 综合分视图附 bootstrap 95% 置信区间（按场次重采样，非按选手行重采样）。
  const band = benchData.bootstrap?.[entry.contestantId];
  const ciText =
    dim === "composite" && band
      ? ` · 95% CI ${(band.composite[0] * 100).toFixed(1)}–${(band.composite[1] * 100).toFixed(1)}%`
      : "";
  return {
    rank,
    id: entry.contestantId,
    label: contestant?.label ?? entry.contestantId,
    kind: contestant?.kind ?? "python",
    value: entry[dim] as number,
    ascending: option.ascending,
    primary: dim === "avgRank" ? entry.avgRank.toFixed(2) : pct(entry[dim] as number),
    secondary: `均排 ${entry.avgRank.toFixed(2)} · 综合 ${pct(entry.composite)}${ciText}`,
    href: `/entry/${entry.contestantId}`,
    repoUrl: contestant?.repoUrl,
  };
}

/** 按选定维度重排榜单（名次徽章 = 该维度下排名）。 */
function rowsFor(dim: SortDimension): RankBarRow[] {
  return [...benchData.leaderboard]
    .sort((a, b) => {
      const av = a[dim] as number;
      const bv = b[dim] as number;
      return dim === "avgRank" ? av - bv : bv - av;
    })
    .map((entry, index) => rowFor(entry, dim, index + 1));
}

/**
 * 综合排名榜：极简维度切换（综合分 / 击杀 / 经济 / 名次）。
 * 切换即按该维度重排榜单，条形长度随维度值归一化。
 */
export function RankingsSection() {
  const [dim, setDim] = useState<SortDimension>("composite");

  return (
    <section className="mb-16">
      <SectionHeader
        id="rankings"
        title="Overall Rankings"
        enTitle="综合排名"
        description="按综合分（composite）排序，可切换维度查看不同视角；点击条目进入详情页。"
        action={
          <div className="inline-flex rounded-md border border-border bg-secondary/50 p-1">
            {DIM_OPTIONS.map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => setDim(option.key)}
                className={cn(
                  "rounded-sm px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                  dim === option.key
                    ? "bg-background text-foreground shadow-xs"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
        }
      />
      <Card>
        <CardContent className="p-5">
          <RankBars rows={rowsFor(dim)} valueLabel={DIM_OPTIONS.find((o) => o.key === dim)?.label ?? "综合分"} />
        </CardContent>
      </Card>
    </section>
  );
}
