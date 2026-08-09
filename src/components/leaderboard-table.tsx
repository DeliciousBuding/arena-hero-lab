"use client";

import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import type { DimensionRow } from "@/lib/dimensions";
import { RankBadge } from "./rank-badge";

type SortMode = "rank" | "asc" | "desc";

/**
 * arena.ai 风格 top10 表：名次 | 条目 | 数值。
 * 点击数值列头可循环切换排序（升序/降序/按名次）。
 */
export function LeaderboardTable({
  rows,
  valueLabel,
  showAll = false,
}: {
  rows: DimensionRow[];
  valueLabel: string;
  showAll?: boolean;
}) {
  const [mode, setMode] = useState<SortMode>("rank");

  const sorted = useMemo(() => {
    const list = showAll ? rows : rows.slice(0, 10);
    if (mode === "asc") {
      return [...list].sort((a, b) => a.sortValue - b.sortValue);
    }
    if (mode === "desc") {
      return [...list].sort((a, b) => b.sortValue - a.sortValue);
    }
    return [...list].sort((a, b) => a.rank - b.rank);
  }, [rows, mode, showAll]);

  function cycleSort() {
    setMode((m) => (m === "rank" ? "desc" : m === "desc" ? "asc" : "rank"));
  }

  return (
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr className="border-b border-border-primary text-xs text-text-tertiary">
          <th className="w-10 pb-2 pl-1 text-left font-medium">名次</th>
          <th className="pb-2 text-left font-medium">条目</th>
          <th className="pb-2 pr-1 text-right font-medium">
            <button
              type="button"
              onClick={cycleSort}
              className="inline-flex items-center gap-1 rounded px-1 py-0.5 transition-colors hover:bg-surface-tertiary hover:text-text-primary"
              title="点击切换排序（升序/降序/名次）"
            >
              {valueLabel}
              {mode === "rank" ? (
                <ArrowUpDown className="h-3 w-3" />
              ) : mode === "asc" ? (
                <ArrowUp className="h-3 w-3" />
              ) : (
                <ArrowDown className="h-3 w-3" />
              )}
            </button>
          </th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((row) => (
          <tr
            key={row.id}
            className="group border-b border-border-primary/60 transition-colors last:border-b-0 hover:bg-surface-tertiary/50"
          >
            <td className="py-2 pl-1">
              <RankBadge rank={row.rank} />
            </td>
            <td className="py-2">
              <Link
                href={`/entry/${row.id}`}
                className="flex flex-col leading-tight transition-colors group-hover:text-accent-primary"
              >
                <span className="font-medium text-text-primary">{row.label}</span>
                <span className="text-[11px] text-text-tertiary tnum">{row.id}</span>
              </Link>
            </td>
            <td className="py-2 pr-1 text-right">
              <span className="font-semibold text-text-primary tnum">{row.primary}</span>
              {row.delta ? (
                <span className="ml-1 text-xs font-normal text-text-tertiary tnum">{row.delta}</span>
              ) : null}
              <span className="block text-[11px] font-normal text-text-tertiary tnum">
                {row.secondary}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
