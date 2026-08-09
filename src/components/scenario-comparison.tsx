import Link from "next/link";
import { benchData, contestantOf } from "@/lib/bench";
import { KindBadge } from "./kind-badge";

/**
 * 场景对比图：每个场景一张卡片，展示该场景内各条目的排名与指标条
 * （资源/刻横向条，人口峰值与击杀率作为副指标）。纯 React+SVG，无图表库。
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

        const maxResources = Math.max(
          1e-9,
          ...entries.map((e) => e.stat.resourcesPerTick),
        );
        const maxPeak = Math.max(1e-9, ...entries.map((e) => e.stat.populationPeak));

        return (
          <section key={scenario.name} className="card flex flex-col p-5">
            <header className="mb-4 flex items-start justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-text-primary">
                  {scenario.label}
                  <span className="ml-2 text-xs font-normal text-text-tertiary tnum">
                    {scenario.name}
                  </span>
                </h3>
                <p className="mt-0.5 text-xs leading-relaxed text-text-secondary">
                  {scenario.template.configNote}
                </p>
              </div>
              <span className="shrink-0 rounded-lg border border-border-primary px-2 py-1 text-[11px] text-text-tertiary tnum">
                {scenario.matches.length} 场
              </span>
            </header>

            <ul className="space-y-2.5">
              {entries.map(({ row, stat }) => {
                const contestant = contestantOf(row.contestantId);
                const resourcesBar = (stat.resourcesPerTick / maxResources) * 100;
                const peakBar = (stat.populationPeak / maxPeak) * 100;
                return (
                  <li
                    key={row.contestantId}
                    className="group rounded-xl border border-transparent px-2 py-2 transition-colors hover:border-border-primary hover:bg-surface-tertiary/40"
                  >
                    <Link href={`/entry/${row.contestantId}`} className="flex items-center gap-3">
                      <span className="w-12 shrink-0 text-right">
                        <span
                          className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-bold ${
                            stat.avgRank <= 1
                              ? "bg-rank-gold text-black/80"
                              : stat.avgRank <= 2
                                ? "bg-rank-silver text-black/70"
                                : stat.avgRank <= 3
                                  ? "bg-rank-bronze text-white/90"
                                  : "bg-surface-tertiary text-text-tertiary"
                          }`}
                        >
                          {stat.avgRank}
                        </span>
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <span className="flex min-w-0 items-center gap-2">
                            <span className="truncate text-xs font-medium text-text-primary">
                              {contestant?.label ?? row.contestantId}
                            </span>
                            <KindBadge kind={contestant?.kind ?? "python"} />
                          </span>
                          <span className="shrink-0 text-[11px] text-text-tertiary tnum">
                            均排 {stat.avgRank.toFixed(1)} · 杀率 {stat.killRate.toFixed(2)}
                          </span>
                        </div>
                        <div className="mt-1.5 space-y-1">
                          <div className="flex items-center gap-2">
                            <span className="w-10 shrink-0 text-[10px] text-text-tertiary">资源/刻</span>
                            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-tertiary">
                              <div
                                className="h-full rounded-full bg-gradient-accent transition-all"
                                style={{ width: `${Math.max(2, resourcesBar)}%` }}
                              />
                            </div>
                            <span className="w-12 shrink-0 text-right text-[10px] text-text-secondary tnum">
                              {stat.resourcesPerTick.toFixed(3)}
                            </span>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="w-10 shrink-0 text-[10px] text-text-tertiary">人口峰值</span>
                            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-tertiary">
                              <div
                                className="h-full rounded-full bg-accent-primary/70 transition-all"
                                style={{ width: `${Math.max(2, peakBar)}%` }}
                              />
                            </div>
                            <span className="w-12 shrink-0 text-right text-[10px] text-text-secondary tnum">
                              {stat.populationPeak.toFixed(1)}
                            </span>
                          </div>
                        </div>
                      </div>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
