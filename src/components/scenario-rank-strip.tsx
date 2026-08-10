import type { BenchmarkData } from "@/lib/bench";
import { cn } from "@/lib/utils";

/** 名次 → 徽章色（1 金 / 2 银 / 3 铜 / 其余中性）。 */
function rankColor(rank: number): string {
  if (rank <= 1) return "bg-rank-gold text-rank-gold";
  if (rank <= 2) return "bg-rank-silver text-rank-silver";
  if (rank <= 3) return "bg-rank-bronze text-rank-bronze";
  return "bg-muted-foreground/15 text-muted-foreground";
}

/**
 * 分场景名次条：一眼看清该条目在哪些场景强势/弱势。
 * 每场景一行：条长 = (参赛人数 − 名次) / (参赛人数 − 1)（第 1 名满条），
 * 名次徽章金/银/铜/中性。数据源 = 场景级 perEntry.avgRank（跨种子均值）。
 */
export function ScenarioRankStrip({
  contestantId,
  data,
}: {
  contestantId: string;
  data: BenchmarkData;
}) {
  const rows = data.scenarios.map((scenario) => ({
    scenario,
    rank: scenario.perEntry[contestantId]?.avgRank ?? null,
  }));

  const players = data.params.players;
  const barOf = (rank: number) =>
    Math.max(0.04, Math.min(1, (players - rank) / Math.max(1, players - 1)));

  return (
    <div className="grid grid-cols-1 gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {rows.map(({ scenario, rank }) => (
        <div key={scenario.name} className="flex items-center gap-3">
          <span className="w-20 shrink-0 text-xs font-medium text-foreground">
            {scenario.label}
          </span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
            {rank !== null && (
              <div
                className={cn(
                  "h-full rounded-full",
                  rank <= 1
                    ? "bg-rank-gold"
                    : rank <= 2
                      ? "bg-rank-silver"
                      : rank <= 3
                        ? "bg-rank-bronze"
                        : "bg-muted-foreground/40",
                )}
                style={{ width: `${barOf(rank) * 100}%` }}
              />
            )}
          </div>
          <span
            className={cn(
              "w-14 shrink-0 text-right text-xs font-semibold tnum",
              rank !== null ? rankColor(rank) : "text-muted-foreground",
            )}
          >
            {rank === null ? "—" : `#${rank.toFixed(2)}`}
          </span>
        </div>
      ))}
    </div>
  );
}
