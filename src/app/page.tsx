import { Heatmap } from "@/components/heatmap";
import { RankBars, type RankBarRow } from "@/components/rank-bars";
import { ScenarioComparison } from "@/components/scenario-comparison";
import { ScoreBars, type ScoreBarEntry } from "@/components/score-bars";
import { SectionHeader } from "@/components/section-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { ACTIVE_SCORE_DIMENSIONS, benchData, contestantOf } from "@/lib/bench";
import Link from "next/link";

/** 综合排名图数据行（composite 升序绘制，榜首条最长）。 */
function rankRows(): RankBarRow[] {
  return benchData.leaderboard.map((entry) => {
    const contestant = contestantOf(entry.contestantId);
    return {
      rank: entry.rank,
      id: entry.contestantId,
      label: contestant?.label ?? entry.contestantId,
      kind: contestant?.kind ?? "python",
      value: entry.composite,
      ascending: true,
      primary: `${(entry.composite * 100).toFixed(1)}%`,
      secondary: `均排 ${entry.avgRank.toFixed(2)} · rankScore ${(entry.rankScore * 100).toFixed(1)}%`,
      href: `/entry/${entry.contestantId}`,
      repoUrl: contestant?.repoUrl,
    };
  });
}

/** 三维画像图数据（击杀/名次/经济；生存维度 v3 恒 1.0 无区分度，已弃用不展示）。 */
function scoreEntries(): ScoreBarEntry[] {
  return benchData.leaderboard.map((entry) => {
    const contestant = contestantOf(entry.contestantId);
    return {
      id: entry.contestantId,
      label: contestant?.label ?? entry.contestantId,
      scores: ACTIVE_SCORE_DIMENSIONS.map((dim) => ({
        key: dim.key as string,
        label: dim.label,
        value: entry[dim.key] as number,
      })),
    };
  });
}

/**
 * 对照组（内置基线）：与主榜同场对抗但独立排名。
 * composite/killScore 为同基准外推量纲（可 >1），不参与主榜 0–1 归一化，故独立展示。
 */
function ControlGroup() {
  const rows = benchData.leaderboardControl ?? [];
  if (rows.length === 0) return null;
  const players = benchData.params.players;
  const barOf = (rank: number) =>
    Math.max(0.04, Math.min(1, (players - rank) / Math.max(1, players - 1)));

  return (
    <section className="mt-10">
      <SectionHeader
        id="control"
        title="Control Group"
        enTitle="对照组（内置基线）"
        description={`TS 内置实现与主榜同场对抗（v3.3 产物；分数为同基准外推量纲，不参与主榜归一化排名）。`}
      />
      <Card>
        <CardContent className="divide-y divide-border-faint">
          {rows.map((entry) => {
            const contestant = contestantOf(entry.contestantId);
            const rank = entry.avgRank;
            return (
              <Link
                key={entry.contestantId}
                href={`/entry/${entry.contestantId}`}
                className="flex items-center gap-4 px-5 py-4 transition-colors hover:bg-secondary/40"
              >
                <Badge
                  variant="outline"
                  className={cn(
                    "tnum",
                    rank <= 3
                      ? rank <= 1
                        ? "border-rank-gold text-rank-gold"
                        : rank <= 2
                          ? "border-rank-silver text-rank-silver"
                          : "border-rank-bronze text-rank-bronze"
                      : "text-muted-foreground",
                  )}
                >
                  #{rank.toFixed(2)}
                </Badge>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-foreground">
                    {contestant?.label ?? entry.contestantId}
                    <span className="ml-2 text-xs font-normal text-muted-foreground tnum">
                      {entry.contestantId}
                    </span>
                  </div>
                  <div className="mt-1.5 h-1.5 w-full max-w-[420px] overflow-hidden rounded-full bg-muted">
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
                  </div>
                </div>
                <div className="shrink-0 text-right text-xs text-muted-foreground tnum">
                  <div>外推综合分 {entry.composite.toFixed(3)}</div>
                  <div>击杀/场 {entry.killRate.toFixed(2)}</div>
                </div>
              </Link>
            );
          })}
        </CardContent>
      </Card>
    </section>
  );
}

export default function HomePage() {
  return (
    <div className="container-page px-4 py-10 sm:px-6">
      {/* ===== Hero ===== */}
      <header className="mb-8">
        <h1 className="font-serif text-4xl font-normal leading-tight tracking-tight text-foreground sm:text-5xl">
          Arena Hero
          <span className="ml-3 text-brand">Leaderboard</span>
        </h1>
      </header>

      {/* ===== 1. 综合排名榜 ===== */}
      <section className="mb-16">
        <SectionHeader
          id="rankings"
          title="Overall Rankings"
          description="综合分（v3 composite 加权合成）总榜，点击条目进入详情页。"
        />
        <Card>
          <CardContent className="p-5">
            <RankBars rows={rankRows()} valueLabel="综合分" />
          </CardContent>
        </Card>
      </section>

      {/* ===== 1b. 对照组（内置基线，独立量纲） ===== */}
      <ControlGroup />

      {/* ===== 2. 场景榜 ===== */}
      <section className="mb-16">
        <SectionHeader
          id="scenarios"
          title="Scenario Leaderboards"
          enTitle="场景榜"
          description="每个场景一场独立擂台：按平均名次排序，金银铜徽章 + 资源/刻条。"
        />
        <ScenarioComparison />
      </section>

      {/* ===== 3. 三维画像 ===== */}
      <section className="mb-16">
        <SectionHeader
          id="scores"
          title="Score Profile"
          enTitle="三维画像"
          description="击杀 / 名次 / 经济 三项归一化分数（0–100%）。生存维度 v3 评测规则下恒 1.0 无区分度，已弃用不展示。"
        />
        <ScoreBars entries={scoreEntries()} />
      </section>

      {/* ===== 4. 场景热图（全览收尾） ===== */}
      <section className="mb-16">
        <SectionHeader
          id="heatmap"
          title="Scenario Heatmap"
          enTitle="场景热图"
          description="场景 × 条目指标矩阵，可切换资源/刻 · 击杀率 · 平均名次。"
        />
        <Heatmap />
      </section>
    </div>
  );
}
