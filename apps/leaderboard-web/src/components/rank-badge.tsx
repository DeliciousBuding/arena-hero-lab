import { cn } from "@/lib/utils";

/**
 * 名次徽章：前三名金/银/铜（serif 大号细体），其余为等宽序号。
 * 圆形 token，圆角 full，颜色取自 rank-* 语义色。
 */
export function RankBadge({
  rank,
  size = "default",
}: {
  rank: number;
  size?: "default" | "sm";
}) {
  const dimension = size === "sm" ? "h-6 w-6 text-[11px]" : "h-7 w-7 text-xs";
  const medal =
    rank === 1
      ? "bg-rank-gold text-black/80 ring-1 ring-rank-gold/30"
      : rank === 2
        ? "bg-rank-silver text-black/80 ring-1 ring-rank-silver/30"
        : rank === 3
          ? "bg-rank-bronze text-white/90 ring-1 ring-rank-bronze/30"
          : "bg-transparent text-muted-foreground ring-1 ring-border";

  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full font-medium tnum",
        dimension,
        medal,
      )}
    >
      {rank}
    </span>
  );
}
