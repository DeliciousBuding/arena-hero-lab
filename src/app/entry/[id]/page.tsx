import { ArrowLeft, CircleAlert, Trophy } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ContestantLinks } from "@/components/contestant-links";
import { KillStats } from "@/components/kill-stats";
import { KillTimelinePanel } from "@/components/kill-timeline-panel";
import { MiniBars } from "@/components/mini-bars";
import { RadarChart } from "@/components/radar-chart";
import { RankBadge } from "@/components/rank-badge";
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
  benchData,
  contestantOf,
  SCORE_DIMENSIONS,
  type BenchmarkMatch,
  type BenchmarkScenario,
  type ScenarioEntryStat,
} from "@/lib/bench";

/** 静态导出：预渲染所有条目页 */
export function generateStaticParams() {
  return benchData.leaderboard.map((row) => ({ id: row.contestantId }));
}

export const dynamicParams = false;

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
  { key: "finalPopulation", label: "终局人口", align: "right" },
  { key: "unitsLost", label: "兵损", align: "right" },
  { key: "aliveTicks", label: "存活刻", align: "right" },
  { key: "beaconTicks", label: "信标刻", align: "right" },
  { key: "firstKillTick", label: "首杀刻", align: "right" },
  { key: "finalResources", label: "终局资源", align: "right" },
];

const fmt = (v: number | null | undefined, digits = 2): string =>
  v == null ? "—" : Number(v.toFixed(digits)).toLocaleString("zh-CN");

/** 条目详情页：头部 + 雷达 + 条形 + 场景小图 + 分场景表 + 单场明细 + 击杀时序 */
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

  const radarValues = SCORE_DIMENSIONS.map((dim) => ({
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

  const miniMetrics: { title: string; note: string; digits: number; unit: string; pick: (s: ScenarioEntryStat) => number }[] = [
    { title: "资源/刻", note: "场景级平均资源采集速率", digits: 3, unit: "res/tick", pick: (s) => s.resourcesPerTick },
    { title: "人口峰值", note: "场景级平均人口峰值", digits: 1, unit: "units", pick: (s) => s.populationPeak },
    { title: "场均击杀", note: "场景级击杀率", digits: 2, unit: "kills", pick: (s) => s.killRate },
    { title: "平均名次", note: "场景级平均名次（越低越好）", digits: 1, unit: "rank", pick: (s) => s.avgRank },
  ];

  const stats: [string, string][] = [
    ["综合分", pct(entry.composite)],
    ["平均名次", entry.avgRank.toFixed(2)],
    ["击杀/场", entry.killRate.toFixed(2)],
    ["rankScore", pct(entry.rankScore)],
    ["killScore", pct(entry.killScore)],
    ["economyScore", pct(entry.economyScore)],
    ["场景名次波动", `±${entry.rankStddev.toFixed(2)}`],
  ];

  return (
    <div className="container-page px-4 py-8 sm:px-6 lg:py-10">
      <Button asChild variant="ghost" size="sm" className="mb-6 gap-1.5 text-muted-foreground hover:text-foreground">
        <Link href="/">
          <ArrowLeft className="h-4 w-4" />
          返回榜单总览
        </Link>
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
              <Badge
                variant={contestant.kind === "builtin" ? "gold" : "brand"}
                className="gap-1"
              >
                <Trophy className="h-3 w-3" />
                {contestant.kind === "builtin" ? "内置对照（校准基线）" : "社区第三方 agent"}
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

      {/* ===== v3 四维雷达 + 分项条形 ===== */}
      <section className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card className="p-6">
          <SectionHeader title="Score Radar" enTitle="四维画像" description="kill / rank / economy / survival 四项 0–1 分数" />
          <RadarChart values={radarValues} />
        </Card>
        <Card className="p-6">
          <SectionHeader title="Score Breakdown" enTitle="排名分项" description="综合分由各分项合成，条形为该条目占满分的比例" />
          <div className="space-y-4">
            {SCORE_DIMENSIONS.map((dim) => {
              const value = entry[dim.key] as number;
              return (
                <div key={dim.key}>
                  <div className="mb-1 flex items-baseline justify-between text-sm">
                    <span className="font-medium text-foreground">
                      {dim.label}
                      <span className="ml-1.5 text-[11px] text-muted-foreground">{dim.enLabel}</span>
                    </span>
                    <span className="tnum text-muted-foreground">{pct(value)}</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full rounded-full bg-brand-gradient transition-all"
                      style={{ width: `${Math.max(2, value * 100)}%` }}
                    />
                  </div>
                </div>
              );
            })}
            <p className="pt-1 text-[11px] leading-relaxed text-muted-foreground">
              survivalScore 为 v2 兼容字段（v3 恒 1.0），已弃用，仅供对照。
            </p>
          </div>
        </Card>
      </section>

      {/* ===== 场景 × 指标小图 ===== */}
      <section className="mt-6">
        <SectionHeader
          title="Scenario Mini Charts"
          enTitle="场景指标小图"
          description="该条目在各场景下的关键指标分布（柱高按该条目所有场景中的最大值归一化，悬浮查看数值）。"
        />
        <Card className="p-6">
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 xl:grid-cols-4">
            {miniMetrics.map((m) => (
              <div key={m.title}>
                <div className="mb-1 text-sm font-medium text-foreground">
                  {m.title}
                  <span className="ml-1.5 text-[11px] font-normal text-muted-foreground">{m.note}</span>
                </div>
                <MiniBars
                  items={scenarioStats.map(({ scenario, stat }) => ({
                    key: scenario.name,
                    label: scenario.label,
                    value: stat == null ? null : m.pick(stat),
                  }))}
                  unit={m.unit}
                  digits={m.digits}
                />
              </div>
            ))}
          </div>
        </Card>
      </section>

      {/* ===== 分场景表现 ===== */}
      <section className="mt-6">
        <SectionHeader
          title="Per Scenario"
          enTitle="分场景表现"
          description="每场景均值（场景级 perEntry 指标），最好/最差为跨种子名次极值。"
        />
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
          description={`每场对局 ${benchData.params.players} 条目同场对抗，胜方为资源结算最高者。`}
        />
        <Card>
          <div className="thin-scroll overflow-x-auto">
            <Table className="min-w-[900px] text-xs">
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
                      finalPopulation: String(player.finalPopulation),
                      unitsLost: String(player.unitsLost),
                      aliveTicks: String(player.aliveTicks),
                      beaconTicks: String(player.beaconTicks),
                      firstKillTick: player.firstKillTick == null ? "—" : String(player.firstKillTick),
                      finalResources: String(player.finalResources),
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
    </div>
  );
}
