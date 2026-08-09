"use client";

import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { contestantOf, type LeaderboardRow } from "@/lib/bench";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ContestantLinks } from "./contestant-links";
import { KindBadge } from "./kind-badge";
import { RankBadge } from "./rank-badge";

type SortKey = "rank" | "composite" | "avgRank" | "killRate" | "economyScore";
type SortDir = "asc" | "desc";

/**
 * 主榜单表：名次徽章 + 对照组视觉区分 + composite/rank/kill/economy 多列排序。
 * 用 Table 原语统一排版；列头 Button cycle sort，键盘可访问。
 * 行内带 GitHub/LinuxDO 外链群（agent 来源）。
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

  const COLUMNS: { key: SortKey; label: string; hint: string }[] = [
    { key: "composite", label: "综合分", hint: "composite（0–1）" },
    { key: "avgRank", label: "平均名次", hint: "avgRank（越小越好）" },
    { key: "killRate", label: "击杀/场", hint: "killRate" },
    { key: "economyScore", label: "经济分", hint: "economyScore（0–1）" },
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
      <Table className="min-w-[760px]">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="w-14 pl-1">名次</TableHead>
            <TableHead>条目</TableHead>
            {COLUMNS.map((col) => (
              <TableHead key={col.key} className="pr-1 text-right">
                <Button
                  type="button"
                  onClick={() => cycleSort(col.key)}
                  variant="ghost"
                  size="sm"
                  className={cn(
                    "h-7 gap-1.5 px-1.5 text-xs font-medium hover:bg-secondary hover:text-foreground",
                    activeCol(col.key)
                      ? "text-brand"
                      : "text-muted-foreground",
                  )}
                  title={`按 ${col.hint} 排序`}
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
                </Button>
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((row) => {
            const contestant = contestantOf(row.contestantId);
            const isBaseline = contestant?.kind === "builtin";
            return (
              <TableRow
                key={row.contestantId}
                className={cn(
                  "group",
                  isBaseline && "bg-rank-gold/[0.04] hover:bg-rank-gold/[0.08]",
                )}
              >
                <TableCell className="pl-1">
                  <RankBadge rank={row.rank} />
                </TableCell>
                <TableCell>
                  <div className="flex flex-col gap-1">
                    <div className="flex items-center gap-2">
                      <Link
                        href={`/entry/${row.contestantId}`}
                        className="max-w-56 truncate font-medium leading-tight text-foreground transition-colors group-hover:text-brand"
                      >
                        {contestant?.label ?? row.contestantId}
                      </Link>
                      <KindBadge kind={contestant?.kind ?? "python"} />
                    </div>
                    <span className="text-[11px] leading-tight text-muted-foreground tnum">
                      {row.contestantId}
                    </span>
                    {contestant && (contestant.repoUrl !== undefined || contestant.linuxdoUrl !== undefined) ? (
                      <ContestantLinks contestant={contestant} variant="inline" />
                    ) : null}
                  </div>
                </TableCell>
                <TableCell className="pr-1 text-right">
                  <span className={cn("font-medium tnum", activeCol("composite") ? "text-brand" : "text-foreground")}>
                    {pct(row.composite)}
                  </span>
                  <span className="block text-[11px] text-muted-foreground tnum">
                    rankScore {pct(row.rankScore)}
                  </span>
                </TableCell>
                <TableCell className="pr-1 text-right">
                  <span className={cn("font-medium tnum", activeCol("avgRank") ? "text-brand" : "text-foreground")}>
                    {row.avgRank.toFixed(2)}
                  </span>
                  <span className="block text-[11px] text-muted-foreground tnum">
                    ± {row.rankStddev.toFixed(2)}
                  </span>
                </TableCell>
                <TableCell className="pr-1 text-right">
                  <span className={cn("font-medium tnum", activeCol("killRate") ? "text-brand" : "text-foreground")}>
                    {row.killRate.toFixed(2)}
                  </span>
                  <span className="block text-[11px] text-muted-foreground tnum">
                    killScore {pct(row.killScore)}
                  </span>
                </TableCell>
                <TableCell className="pr-1 text-right">
                  <span className={cn("font-medium tnum", activeCol("economyScore") ? "text-brand" : "text-foreground")}>
                    {pct(row.economyScore)}
                  </span>
                  <span className="block text-[11px] text-muted-foreground tnum">
                    rankScore {pct(row.rankScore)}
                  </span>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
