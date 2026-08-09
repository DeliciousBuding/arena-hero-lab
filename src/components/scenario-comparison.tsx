import { Users } from "lucide-react";
import Link from "next/link";
import { benchData, contestantOf } from "@/lib/bench";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RankBadge } from "./rank-badge";
import { KindBadge } from "./kind-badge";

/**
 * 场景对比图：每场景一张 Card，展示该场景内条目排名与指标条。
 * 资源/刻横向条 + 人口峰值副条；颜色取 token（brand-gradient）。
 */
export function ScenarioComparison() {
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      {benchData.scenarios.map((scenario) => {
        const entries = [...benchData.leaderboard]
          .map((row) => ({ row, stat: scenario.perEntry[row.contestantId] }))
          .filter((e): e is { row: (typeof benchData.leaderboard)[number]; stat: NonNullable<typeof e.stat> } =>
            e.stat != null,
          )
          .sort((a, b) => a.stat.avgRank - b.stat.avgRank);

        const maxResources = Math.max(1e-9, ...entries.map((e) => e.stat.resourcesPerTick));
        const maxPeak = Math.max(1e-9, ...entries.map((e) => e.stat.populationPeak));

        return (
          <Card key={scenario.name}>
            <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
              <div className="min-w-0">
                <CardTitle className="flex items-baseline gap-2 text-base">
                  {scenario.label}
                  <span className="font-sans text-xs font-normal text-muted-foreground tnum">
                    {scenario.name}
                  </span>
                </CardTitle>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  {scenario.template.configNote}
                </p>
              </div>
              <Badge variant="outline" className="shrink-0 gap-1">
                <Users className="h-3 w-3" />
                {scenario.matches.length} 场
              </Badge>
            </CardHeader>
            <CardContent className="space-y-1.5">
              {entries.map(({ row, stat }) => {
                const contestant = contestantOf(row.contestantId);
                const resourcesBar = (stat.resourcesPerTick / maxResources) * 100;
                const peakBar = (stat.populationPeak / maxPeak) * 100;
                return (
                  <Link
                    key={row.contestantId}
                    href={`/entry/${row.contestantId}`}
                    className="group block rounded-md border border-transparent px-2 py-2 transition-colors hover:border-border hover:bg-secondary/40"
                  >
                    <div className="flex items-center gap-3">
                      <span className="w-10 shrink-0">
                        <RankBadge rank={Math.round(stat.avgRank)} size="sm" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <span className="flex min-w-0 items-center gap-2">
                            <span className="truncate text-xs font-medium text-foreground group-hover:text-brand">
                              {contestant?.label ?? row.contestantId}
                            </span>
                            <KindBadge kind={contestant?.kind ?? "python"} />
                          </span>
                          <span className="shrink-0 text-[11px] text-muted-foreground tnum">
                            均排 {stat.avgRank.toFixed(1)} · 杀率 {stat.killRate.toFixed(2)}
                          </span>
                        </div>
                        <div className="mt-1.5 space-y-1">
                          <div className="flex items-center gap-2">
                            <span className="w-10 shrink-0 text-[10px] text-muted-foreground">资源/刻</span>
                            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                              <div
                                className="h-full rounded-full bg-brand-gradient transition-all"
                                style={{ width: `${Math.max(2, resourcesBar)}%` }}
                              />
                            </div>
                            <span className="w-12 shrink-0 text-right text-[10px] text-muted-foreground tnum">
                              {stat.resourcesPerTick.toFixed(3)}
                            </span>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="w-10 shrink-0 text-[10px] text-muted-foreground">人口峰值</span>
                            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                              <div
                                className="h-full rounded-full bg-brand/70 transition-all"
                                style={{ width: `${Math.max(2, peakBar)}%` }}
                              />
                            </div>
                            <span className="w-12 shrink-0 text-right text-[10px] text-muted-foreground tnum">
                              {stat.populationPeak.toFixed(1)}
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </Link>
                );
              })}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
