"use client";

import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import type { DimensionRow } from "@/lib/dimensions";
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

type SortMode = "rank" | "asc" | "desc";

/**
 * 维度榜单表：名次 | 条目 | 数值。
 * 用 Table 原语统一排版；列头 Button 切换排序（升/降/名次）。
 * 条目名旁加 KindBadge + 外链群（GitHub/LinuxDO）。
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
    if (mode === "asc") return [...list].sort((a, b) => a.sortValue - b.sortValue);
    if (mode === "desc") return [...list].sort((a, b) => b.sortValue - a.sortValue);
    return [...list].sort((a, b) => a.rank - b.rank);
  }, [rows, mode, showAll]);

  function cycleSort() {
    setMode((m) => (m === "rank" ? "desc" : m === "desc" ? "asc" : "rank"));
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-12 pl-1">名次</TableHead>
          <TableHead>条目</TableHead>
          <TableHead className="pr-1 text-right">
            <Button
              type="button"
              onClick={cycleSort}
              variant="ghost"
              size="sm"
              className="h-7 gap-1.5 px-1.5 text-xs font-medium text-muted-foreground hover:bg-secondary hover:text-foreground"
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
            </Button>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sorted.map((row) => (
          <TableRow key={row.id} className="group">
            <TableCell className="pl-1">
              <RankBadge rank={row.rank} size="sm" />
            </TableCell>
            <TableCell>
              <div className="flex flex-col gap-1">
                <div className="flex items-center gap-1.5">
                  <Link
                    href={`/entry/${row.id}`}
                    className="font-medium text-foreground transition-colors group-hover:text-brand"
                  >
                    {row.label}
                  </Link>
                  <KindBadge kind={row.kind} />
                </div>
                <div className="flex items-center gap-2 text-[11px] text-muted-foreground tnum">
                  <span>{row.id}</span>
                </div>
                {row.repoUrl !== undefined || row.linuxdoUrl !== undefined ? (
                  <ContestantLinks
                    contestant={{
                      id: row.id,
                      label: row.label,
                      kind: row.kind,
                      configNote: "",
                      ...(row.repoUrl !== undefined ? { repoUrl: row.repoUrl } : {}),
                      ...(row.linuxdoUrl !== undefined ? { linuxdoUrl: row.linuxdoUrl } : {}),
                    }}
                    variant="inline"
                  />
                ) : null}
              </div>
            </TableCell>
            <TableCell className="pr-1 text-right">
              <span className="font-semibold text-foreground tnum">{row.primary}</span>
              {row.delta ? (
                <span className="ml-1 text-xs font-normal text-muted-foreground tnum">
                  {row.delta}
                </span>
              ) : null}
              <span className="block text-[11px] font-normal text-muted-foreground tnum">
                {row.secondary}
              </span>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
