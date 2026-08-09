"use client";

import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { contestantOf, type LeaderboardRow } from "@/lib/bench";
import { KindBadge } from "./kind-badge";
import { RankBadge } from "./rank-badge";

type SortKey = "rank" | "composite" | "avgRank" | "killRate" | "economyScore";
type SortDir = "asc" | "desc";

/**
 * 榜单增强表（v3）：名次徽章 + 对照组视觉区分 + composite/rank/kill/economy 多列排序。
 * survivalScore 为 v2 兼容字段（v3 恒 1.0），按需求不在表中展示。
 */
export function OverallTable({
  rows,
  limit,
}: {
  rows: LeaderboardRow[];
  limit?: number;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const sorted = useMemo(() => {
    const list = limit ? rows.slice(0, limit) : rows;
    if (sortKey === "rank") return [...list];
    const dir = sortDir === "asc" ? 1 : -1;
    return [...list].sort((a, b) => (a[sortKey] - b[sortKey]) * dir);
  }, [rows, limit, sortKey, sortDir]);

  const COLUMNS: { key: SortKey; label: string; align?: "right"; hint: string }[] = [
    { key: "composite", label: "综合分", align: "right", hint: "composite（0–1）" },
    { key: "avgRank", label: "平均名次", align: "right", hint: "avgRank（越小越好）" },
    { key: "killRate", label: "击杀/场", align: "right", hint: "killRate" },
    { key: "economyScore", label: "经济分", align: "right", hint: "economyScore（0–1）" },
  ];

  function cycleSort(key: SortKey) {
    if (key === "rank") {
      setSortKey("rank");
      setSortDir("asc");
      return;
    }
    if (sortKey !== key) {
      setSortKey(key);
      setSortDir(key === "avgRank" ? "asc" : "desc");
      return;
    }
    setSortDir((d) => (d === "asc" ? "desc" : "asc"));
  }

  const activeCol = (key: SortKey): boolean => sortKey === key;
  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

  return (
    <div className="thin-scroll overflow-x-auto">
      <table className="w-full min-w-[680px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-border-primary text-xs text-text-tertiary">
            <th className="w-12 pb-2 pl-1 text-left font-medium">名次</th>
            <th className="pb-2 text-left font-medium">条目</th>
            {COLUMNS.map((col) => (
              <th key={col.key} className={`pb-2 ${col.align === "right" ? "text-right" : ""}`}>
                <button
                  type="button"
                  onClick={() => cycleSort(col.key)}
                  title={`按 ${col.hint} 排序`}
                  className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 transition-colors hover:bg-surface-tertiary hover:text-text-primary ${
                    activeCol(col.key) ? "font-semibold text-accent-primary" : "font-medium"
                  }`}
                >
                  {col.label}
                  {activeCol(col.key) ? (
                    sortDir === "asc" ? (
                      <ArrowUp className="h-3 w-3" />
                    ) : (
                      <ArrowDown className="h-3 w-3" />
                    )
                  ) : (
                    <ArrowUpDown className="h-3 w-3 opacity-50" />
                  )}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            const contestant = contestantOf(row.contestantId);
            const isBaseline = contestant?.kind === "builtin";
            return (
              <tr
                key={row.contestantId}
                className={`group border-b border-border-primary/60 transition-colors last:border-b-0 ${
                  isBaseline ? "bg-rank-gold/[0.045] hover:bg-rank-gold/[0.09]" : "hover:bg-surface-tertiary/50"
                }`}
              >
                <td className="py-2.5 pl-1">
                  <RankBadge rank={row.rank} />
                </td>
                <td className="py-2.5">
                  <Link
                    href={`/entry/${row.contestantId}`}
                    className="flex items-center gap-2 transition-colors group-hover:text-accent-primary"
                  >
                    <span className="max-w-56 truncate font-medium leading-tight text-text-primary">
                      {contestant?.label ?? row.contestantId}
                    </span>
                    <KindBadge kind={contestant?.kind ?? "python"} />
                  </Link>
                  <span className="block text-[11px] leading-tight text-text-tertiary tnum">
                    {row.contestantId}
                  </span>
                </td>
                <td className="py-2.5 pr-1 text-right">
                  <span className={`font-semibold tnum ${activeCol("composite") ? "text-accent-primary" : "text-text-primary"}`}>
                    {pct(row.composite)}
                  </span>
                  <span className="block text-[11px] text-text-tertiary tnum">
                    rankScore {pct(row.rankScore)}
                  </span>
                </td>
                <td className="py-2.5 pr-1 text-right">
                  <span className={`font-medium tnum ${activeCol("avgRank") ? "text-accent-primary" : "text-text-primary"}`}>
                    {row.avgRank.toFixed(2)}
                  </span>
                  <span className="block text-[11px] text-text-tertiary tnum">± {row.rankStddev.toFixed(2)}</span>
                </td>
                <td className="py-2.5 pr-1 text-right">
                  <span className={`font-medium tnum ${activeCol("killRate") ? "text-accent-primary" : "text-text-primary"}`}>
                    {row.killRate.toFixed(2)}
                  </span>
                  <span className="block text-[11px] text-text-tertiary tnum">
                    killScore {pct(row.killScore)}
                  </span>
                </td>
                <td className="py-2.5 pr-1 text-right">
                  <span className={`font-medium tnum ${activeCol("economyScore") ? "text-accent-primary" : "text-text-primary"}`}>
                    {pct(row.economyScore)}
                  </span>
                  <span className="block text-[11px] text-text-tertiary tnum">
                    rankScore {pct(row.rankScore)}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
