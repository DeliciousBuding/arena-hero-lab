/** 名次徽章：前三名金/银/铜，其余为普通序号 */
export function RankBadge({ rank }: { rank: number }) {
  if (rank === 1) {
    return (
      <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-rank-gold text-[11px] font-bold text-black/80 ring-1 ring-black/10">
        {rank}
      </span>
    );
  }
  if (rank === 2) {
    return (
      <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-rank-silver text-[11px] font-bold text-black/70 ring-1 ring-black/10">
        {rank}
      </span>
    );
  }
  if (rank === 3) {
    return (
      <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-rank-bronze text-[11px] font-bold text-white/90 ring-1 ring-black/10">
        {rank}
      </span>
    );
  }
  return (
    <span className="inline-flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-medium text-text-tertiary tnum">
      {rank}
    </span>
  );
}
