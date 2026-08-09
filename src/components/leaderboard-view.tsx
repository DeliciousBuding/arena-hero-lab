"use client";

import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import type { Dimension } from "@/lib/dimensions";
import { DimensionCard } from "./dimension-card";

/**
 * 榜单总览（客户端容器）：
 * - 搜索框按条目中文名 / 英文 id 过滤全部卡片
 * - 维度卡片网格（arena.ai 分类卡片风格）
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
      <div className="mb-6 flex items-center gap-3">
        <div className="relative max-w-sm flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-tertiary" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索条目（中文名 / 英文 id）…"
            className="w-full rounded-xl border border-border-primary bg-surface-primary py-2 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-tertiary focus:border-accent-primary focus:outline-none"
          />
        </div>
        <span className="hidden text-xs text-text-tertiary sm:block">
          {filtered[0]?.rows.length ?? 0} 个条目 · 可点击表头排序
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
