import { ArrowLeft, CircleAlert, Trophy } from "lucide-react";
import { notFound } from "next/navigation";
import { StaticLink } from "@/components/static-link";
import { ContestantLinks } from "@/components/contestant-links";
import { KillStats } from "@/components/kill-stats";
import { KillTimelinePanel } from "@/components/kill-timeline-panel";
import { RadarChart } from "@/components/radar-chart";
import { RankBadge } from "@/components/rank-badge";
import { ResourceTimelinePanel } from "@/components/resource-timeline";
import { ScenarioRankStrip } from "@/components/scenario-rank-strip";
import { SectionHeader } from "@/components/section-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ACTIVE_SCORE_DIMENSIONS,
  benchData,
  contestantOf,
  dimensionRankOf,
  type BenchmarkMatch,
  type BenchmarkScenario,
  type ScenarioEntryStat,
} from "@/lib/bench";

/** 静态导出：预渲染所有条目页。 */
export function generateStaticParams() {
  return benchData.leaderboard.map((row) => ({ id: row.contestantId }));
}

export const dynamicParams = false;

/** 单场明细列（15 → 10 精简：终局人口/兵损/信标刻/首杀刻/终局资源移入分场景聚合视角）。 */
const MATCH_COLUMNS: { key: string; label: string; align?: "right" }[] = [
  { key: "scenario", label: "场景" },
  { key: "seed", label: "种子", align: "right" },
  { key: "rank", label: "名次", align: "right" },
  { key: "isWinner", label: "胜方" },
  { key: "kills", label: "击杀", align: "right" },
  { key: "damageDealt", label: "伤害", align: "right" },
  { key: "harvested", label: "采集", align: "right" },
  { key: "deposited", label: "上交", align: "right" },
  { key: "populationPeak", label: "人口峰值", align: "right" },
  { key: "aliveTicks", label: "存活刻", align: "right" },
];

const fmt = (v: number | null | undefined, digits = 2): string =>
  v == null ? "—" : Number(v.toFixed(digits)).toLocaleString("zh-CN");

/** 条目详情页动线：身份 → 画像（雷达+分项+排名） → 效率时序 → 击杀时序 → 分场景 → 单场明细。 */
export default async function EntryPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const entry = benchData.leaderboard.find((e) => e.contestantId === id);
  const contestant = contestantOf(id);
  if (!entry || !contestant) {
    notFound();
  }

  const radarValues = ACTIVE_SCORE_DIMENSIONS.map((dim) => ({
    key: dim.key as string,
    label: dim.label,
    value: entry[dim.key] as number,
  }));

  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

  const scenarioStats: { scenario: BenchmarkScenario; stat: ScenarioEntryStat | null }[] =
    benchData.scenarios.map((scenario) => ({
      scenario,
      stat: scenario.perEntry[id] ?? null,
    }));

  const stats: [string, string][] = [
    ["综合分", pct(entry.composite)],
    ["平均名次", entry.avgRank.toFixed(2)],
    ["击杀/场", entry.killRate.toFixed(2)],
    ["场景名次波动", `±${entry.rankStddev.toFixed(2)}`],
  ];

  return (
    <div className="container-page px-4 py-8 sm:px-6 lg:py-10">
      <Button asChild variant="ghost" size="sm" className="mb-6 gap-1.5 text-muted-foreground hover:text-foreground">
        <StaticLink href="/">
          <ArrowLeft className="h-4 w-4" />
          返回榜单总览
        </StaticLink>
      </Button>

      {/* ===== 条目头部 ===== */}
      <Card className="p-6">
        <div className="flex flex-wrap items-start gap-5">
          <RankBadge rank={entry.rank} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-serif text-2xl font-normal leading-tight tracking-tight text-foreground">
                {contestant.label}
              </h1>
              <Badge variant="outline" className="tnum">{contestant.id}</Badge>
              <Badge variant="brand" className="gap-1">
                <Trophy className="h-3 w-3" />
                {contestant.kind === "control" ? "对照 bot" : "社区第三方 agent"}
              </Badge>
            </div>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
              {contestant.configNote}
            </p>
            <div className="mt-4">
              <ContestantLinks contestant={contestant} variant="compact" />
            </div>
            <div className="mt-4 flex flex-wrap gap-2 text-xs">
              {stats.map(([label, value]) => (
                <span
                  key={label}
                  className="rounded-md border border-border bg-secondary/50 px-2.5 py-1 text-muted-foreground"
                >
                  {label}{" "}
                  <span className="font-semibold text-foreground tnum">{value}</span>
                </span>
              ))}
              <span
                title="survivalScore 为 v2 兼容字段：v3 恒 1.0，已弃用"
                className="inline-flex items-center gap-1 rounded-md border border-dashed border-border bg-secondary/30 px-2.5 py-1 text-muted-foreground"
              >
                <CircleAlert className="h-3 w-3" />
                survivalScore{" "}
                <span className="font-semibold tnum line-through">{pct(entry.survivalScore)}</span>
              </span>
            </div>
          </div>
        </div>
      </Card>

      {/* ===== 画像：雷达 + 分项（带全体排名参照） ===== */}
      <section className="mt-6">
        <SectionHeader
          title="Score Profile"
          enTitle="三维画像"
          description={`kill / rank / economy 三项 0–1 分数（survival 恒 1.0 已弃用），右列为该条目在全体 ${benchData.leaderboard.length} 条目中的维度排名。`}
        />
        <Card className="p-6">
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
            <div className="mx-auto flex w-full max-w-[320px] items-center">
              <RadarChart values={radarValues} size={280} />
            </div>
            <div className="space-y-4">
              {ACTIVE_SCORE_DIMENSIONS.map((dim) => {
                const value = entry[dim.key] as number;
                const dimRank = dimensionRankOf(id, dim.key);
                return (
                  <div key={dim.key}>
                    <div className="mb-1 flex items-baseline justify-between text-sm">
                      <span className="font-medium text-foreground">
                        {dim.label}
                        <span className="ml-1.5 text-[11px] text-muted-foreground">{dim.enLabel}</span>
                        {dimRank !== null && (
                          <span className="ml-2 rounded bg-brand/10 px-1.5 py-0.5 text-[11px] font-semibold text-brand tnum">
                            全体第 {dimRank} 名
                          </span>
                        )}
                      </span>
                      <span className="tnum text-muted-foreground">{pct(value)}</span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-brand-gradient transition-all"
                        style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }}
                      />
                    </div>
                  </div>
                );
              })}
              <p className="pt-1 text-[11px] leading-relaxed text-muted-foreground">
                综合分 = rank 60% + kill 30% + economy 10%。维度排名由各分项在全体主榜中的降序位置得出。
              </p>
            </div>
          </div>
        </Card>
      </section>

      {/* ===== 效率时序（资源/人口曲线，前置抓眼球） ===== */}
      <section className="mt-6">
        <SectionHeader
          title="Efficiency Timeline"
          enTitle="效率时序"
          description={`每 50 tick 采样的 per-player 资源/人口曲线（v3.1 可观测性；同场 ${benchData.params.players} 玩家对比，可切换场景 × 种子）。`}
        />
        <Card className="p-6">
          <ResourceTimelinePanel
            scenarios={benchData.scenarios}
            roster={benchData.contestants.map((c) => ({ id: c.id, label: c.label }))}
            ticks={benchData.params.ticks}
          />
        </Card>
      </section>

      {/* ===== 击杀时序 ===== */}
      <section className="mt-6">
        <SectionHeader
          title="Kill Timeline"
          enTitle="击杀时序"
          description="核心摧毁事件沿 tick 轴展开：每行一个玩家，标记位置 = 摧毁时刻、颜色 = 击杀者（悬浮查看击杀者 → 被击杀者）。"
        />
        <Card className="p-6">
          <KillStats contestantId={id} />
          <Separator className="my-6" />
          <KillTimelinePanel
            scenarios={benchData.scenarios}
            roster={benchData.contestants.map((c) => ({ id: c.id, label: c.label }))}
            ticks={benchData.params.ticks}
          />
        </Card>
      </section>

      {/* ===== 分场景表现：名次条导览 + 指标表 ===== */}
      <section className="mt-6">
        <SectionHeader
          title="Per Scenario"
          enTitle="分场景表现"
          description={`名次条 = 该条目各场景平均名次（第 1 名满条，金/银/铜高亮）；下表为场景级指标明细。`}
        />
        <Card className="mb-4 p-6">
          <ScenarioRankStrip contestantId={id} data={benchData} />
        </Card>
        <Card>
          <div className="thin-scroll overflow-x-auto">
            <Table className="min-w-[720px]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>场景</TableHead>
                  <TableHead className="text-right">平均名次</TableHead>
                  <TableHead className="text-right">最好/最差</TableHead>
                  <TableHead className="text-right">击杀率</TableHead>
                  <TableHead className="text-right">资源/刻</TableHead>
                  <TableHead className="text-right">人口峰值</TableHead>
                  <TableHead className="text-right">信标刻</TableHead>
                  <TableHead className="text-right">首杀刻</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {scenarioStats.map(({ scenario, stat }) => {
                  if (!stat) return null;
                  const derived = benchData.entryScenarioStats[id]?.[scenario.name];
                  return (
                    <TableRow key={scenario.name}>
                      <TableCell>
                        <span className="font-medium text-foreground">{scenario.label}</span>
                        <span className="ml-2 text-xs text-muted-foreground tnum">{scenario.name}</span>
                      </TableCell>
                      <TableCell className="text-right text-foreground tnum">
                        {stat.avgRank.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground tnum">
                        {derived ? `${derived.bestRank} / ${derived.worstRank}` : "—"}
                      </TableCell>
                      <TableCell className="text-right text-foreground tnum">
                        {stat.killRate.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-right text-foreground tnum">
                        {stat.resourcesPerTick.toFixed(3)}
                      </TableCell>
                      <TableCell className="text-right text-foreground tnum">
                        {stat.populationPeak.toFixed(1)}
                      </TableCell>
                      <TableCell className="text-right text-foreground tnum">
                        {stat.beaconTicks.toFixed(1)}
                      </TableCell>
                      <TableCell className="text-right text-foreground tnum">
                        {stat.firstKillTick == null ? "—" : stat.firstKillTick}
                      </TableCell>
                    </TableRow>
                  );
                })}
                <TableRow className="bg-secondary/40 font-medium hover:bg-secondary/40">
                  <TableCell className="text-foreground">跨场景汇总</TableCell>
                  <TableCell className="text-right text-foreground tnum">
                    {entry.avgRank.toFixed(2)}
                  </TableCell>
                  <TableCell className="text-right text-muted-foreground tnum">
                    ± {entry.rankStddev.toFixed(2)}
                  </TableCell>
                  <TableCell className="text-right text-foreground tnum">
                    {entry.killRate.toFixed(2)}
                  </TableCell>
                  <TableCell className="text-right text-foreground tnum">
                    {fmt(
                      scenarioStats.reduce((n, s) => n + (s.stat ? s.stat.resourcesPerTick : 0), 0) /
                        Math.max(1, scenarioStats.filter((s) => s.stat).length),
                      3,
                    )}
                  </TableCell>
                  <TableCell className="text-right text-foreground tnum">
                    {fmt(
                      scenarioStats.reduce((n, s) => n + (s.stat ? s.stat.populationPeak : 0), 0) /
                        Math.max(1, scenarioStats.filter((s) => s.stat).length),
                      1,
                    )}
                  </TableCell>
                  <TableCell className="text-right text-foreground tnum">
                    {fmt(
                      scenarioStats.reduce((n, s) => n + (s.stat ? s.stat.beaconTicks : 0), 0) /
                        Math.max(1, scenarioStats.filter((s) => s.stat).length),
                      1,
                    )}
                  </TableCell>
                  <TableCell className="text-right text-foreground tnum">—</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
        </Card>
      </section>

      {/* ===== 单场明细 ===== */}
      <section className="mt-6">
        <SectionHeader
          title="Match Details"
          enTitle="单场明细"
          description={`每场对局 ${benchData.params.players} 条目同场对抗，胜方为资源结算最高者。`}        />
        <Card>
          <div className="thin-scroll overflow-x-auto">
            <Table className="min-w-[760px] text-xs">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  {MATCH_COLUMNS.map((col) => (
                    <TableHead
                      key={col.key}
                      className={
                        col.align === "right" ? "text-right" : "text-left"
                      }
                    >
                      {col.label}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {benchData.scenarios.flatMap((scenario: BenchmarkScenario) =>
                  scenario.matches.map((match: BenchmarkMatch) => {
                    const player = match.players[id];
                    if (!player) return null;
                    const rank = match.rank[id] ?? match.rank[`${id}-s${match.seed}`] ?? "—";
                    const cells: Record<string, string> = {
                      scenario: scenario.label,
                      seed: String(match.seed),
                      rank: String(rank),
                      isWinner: player.isWinner ? "胜" : "—",
                      kills: String(player.kills),
                      damageDealt: String(player.damageDealt),
                      harvested: String(player.harvested),
                      deposited: String(player.deposited),
                      populationPeak: String(player.populationPeak),
                      aliveTicks: String(player.aliveTicks),
                    };
                    return (
                      <TableRow
                        key={`${scenario.name}-${match.seed}`}
                        className={player.isWinner ? "bg-rank-gold/[0.06]" : undefined}
                      >
                        {MATCH_COLUMNS.map((col) => (
                          <TableCell
                            key={col.key}
                            className={
                              col.align === "right" ? "text-right tnum" : "tnum"
                            }
                          >
                            {col.key === "scenario" ? (
                              <span className="font-medium text-foreground">{cells[col.key]}</span>
                            ) : col.key === "isWinner" && player.isWinner ? (
                              <span className="font-medium text-rank-gold">{cells[col.key]}</span>
                            ) : (
                              cells[col.key]
                            )}
                          </TableCell>
                        ))}
                      </TableRow>
                    );
                  }),
                )}
              </TableBody>
            </Table>
          </div>
        </Card>
      </section>
    </div>
  );
}
