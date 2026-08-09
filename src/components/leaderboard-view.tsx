"use client";

import { Search, X } from "lucide-react";
import { useMemo, useState } from "react";
import type { Dimension } from "@/lib/dimensions";
import { Button } from "@/components/ui/button";
import { DimensionCard } from "./dimension-card";

/**
 * 榜单总览：搜索框（按中文名 / 英文 id 过滤全部卡片）+ 维度卡片网格。
 * 搜索 input 带 lucide Search 图标 + 清除按钮（键盘可访问）。
 */
export function LeaderboardView({ dimensions }: { dimensions: Dimension[] }) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return dimensions;
    return dimensions.map((dimension) => ({
      ...dimension,
      rows: dimension.rows.filter(
        (row) =>
          row.label.toLowerCase().includes(q) ||
          row.id.toLowerCase().includes(q),
      ),
    }));
  }, [dimensions, query]);

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div className="relative max-w-sm flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索条目（中文名 / 英文 id）…"
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
          {filtered[0]?.rows.length ?? 0} 个条目 · 列头可排序
        </span>
      </div>
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        {filtered.map((dimension) => (
          <DimensionCard key={dimension.id} dimension={dimension} />
        ))}
      </div>
    </div>
  );
}
