import { ArrowLeft, Award, CircleAlert } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { KindBadge } from "@/components/kind-badge";
import { KillTimelinePanel } from "@/components/kill-timeline-panel";
import { MiniBars } from "@/components/mini-bars";
import { RadarChart } from "@/components/radar-chart";
import { RankBadge } from "@/components/rank-badge";
import { SectionHeader } from "@/components/section-header";
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

/** 条目详情页：v3 四维雷达 + 分项条形 + 场景×指标小图 + 分场景表现 + 单场明细 */
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

  /** 场景级 perEntry 数据（该条目在该场景的指标） */
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

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:py-10">
      <Link
        href="/"
        className="mb-6 inline-flex items-center gap-1.5 text-sm text-text-secondary transition-colors hover:text-text-primary"
      >
        <ArrowLeft className="h-4 w-4" />
        返回榜单总览
      </Link>

      {/* ===== 条目头部 ===== */}
      <header className="card flex flex-wrap items-start gap-5 p-6">
        <RankBadge rank={entry.rank} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-bold text-text-primary sm:text-2xl">{contestant.label}</h1>
            <span className="rounded-lg border border-border-primary px-2 py-0.5 text-xs text-text-tertiary tnum">
              {contestant.id}
            </span>
            <KindBadge kind={contestant.kind} />
            <span
              className={`inline-flex items-center gap-1 rounded-lg border px-2 py-0.5 text-xs font-medium ${
                contestant.kind === "builtin"
                  ? "border-rank-gold/40 bg-rank-gold/10 text-rank-gold"
                  : "border-accent-primary/30 bg-accent-soft text-accent-primary"
              }`}
            >
              <Award className="h-3 w-3" />
              {contestant.kind === "builtin" ? "内置对照（校准基线）" : "第三方 agent"}
            </span>
          </div>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-text-secondary">
            {contestant.configNote}
          </p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            {[
              ["综合分", pct(entry.composite)],
              ["平均名次", entry.avgRank.toFixed(2)],
              ["击杀/场", entry.killRate.toFixed(2)],
              ["rankScore", pct(entry.rankScore)],
              ["killScore", pct(entry.killScore)],
              ["economyScore", pct(entry.economyScore)],
              ["场景名次波动", `±${entry.rankStddev.toFixed(2)}`],
            ].map(([label, value]) => (
              <span
                key={label}
                className="rounded-lg border border-border-primary bg-surface-tertiary/50 px-2.5 py-1 text-text-secondary"
              >
                {label} <span className="font-semibold text-text-primary tnum">{value}</span>
              </span>
            ))}
            <span
              title="survivalScore 为 v2 兼容字段：v3 恒 1.0，已弃用"
              className="inline-flex items-center gap-1 rounded-lg border border-dashed border-border-primary bg-surface-tertiary/30 px-2.5 py-1 text-text-tertiary"
            >
              <CircleAlert className="h-3 w-3" />
              survivalScore <span className="font-semibold tnum line-through">{pct(entry.survivalScore)}</span>
            </span>
          </div>
        </div>
      </header>

      {/* ===== v3 四维雷达 + 分项条形 ===== */}
      <section className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="card p-6">
          <SectionHeader title="v3 四维画像" enTitle="Score Radar" description="kill / rank / economy / survival 四项 0–1 分数" />
          <RadarChart values={radarValues} />
        </div>
        <div className="card p-6">
          <SectionHeader title="排名分项" enTitle="Score Breakdown" description="综合分由各分项合成，条形为该条目占满分的比例" />
          <div className="space-y-4">
            {SCORE_DIMENSIONS.map((dim) => {
              const value = entry[dim.key] as number;
              return (
                <div key={dim.key}>
                  <div className="mb-1 flex items-baseline justify-between text-sm">
                    <span className="font-medium text-text-primary">
                      {dim.label}
                      <span className="ml-1.5 text-[11px] text-text-tertiary">{dim.enLabel}</span>
                    </span>
                    <span className="tnum text-text-secondary">{pct(value)}</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-surface-tertiary">
                    <div
                      className="h-full rounded-full bg-gradient-accent transition-all"
                      style={{ width: `${Math.max(2, value * 100)}%` }}
                    />
                  </div>
                </div>
              );
            })}
            <p className="pt-1 text-[11px] leading-relaxed text-text-tertiary">
              survivalScore 为 v2 兼容字段（v3 恒 1.0），已弃用，仅供对照。
            </p>
          </div>
        </div>
      </section>

      {/* ===== 场景 × 指标小图 ===== */}
      <section className="mt-6 card p-6">
        <SectionHeader
          title="场景 × 指标小图"
          enTitle="Scenario Mini Charts"
          description="该条目在各场景下的关键指标分布（柱高按该条目所有场景中的最大值归一化，悬浮查看数值）。"
        />
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 xl:grid-cols-4">
          {miniMetrics.map((m) => (
            <div key={m.title}>
              <div className="mb-1 text-sm font-medium text-text-primary">
                {m.title}
                <span className="ml-1.5 text-[11px] font-normal text-text-tertiary">{m.note}</span>
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
      </section>

      {/* ===== 分场景表现 ===== */}
      <section className="mt-6 card p-6">
        <SectionHeader
          title="分场景表现"
          enTitle="Per Scenario"
          description="每场景均值（场景级 perEntry 指标），最好/最差为跨种子名次极值。"
        />
        <div className="thin-scroll overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-border-primary text-xs text-text-tertiary">
                <th className="py-2 pr-3 text-left font-medium">场景</th>
                <th className="py-2 pr-3 text-right font-medium">平均名次</th>
                <th className="py-2 pr-3 text-right font-medium">最好/最差</th>
                <th className="py-2 pr-3 text-right font-medium">击杀率</th>
                <th className="py-2 pr-3 text-right font-medium">资源/刻</th>
                <th className="py-2 pr-3 text-right font-medium">人口峰值</th>
                <th className="py-2 pr-3 text-right font-medium">信标刻</th>
                <th className="py-2 text-right font-medium">首杀刻</th>
              </tr>
            </thead>
            <tbody>
              {scenarioStats.map(({ scenario, stat }) => {
                if (!stat) return null;
                const derived = benchData.entryScenarioStats[id]?.[scenario.name];
                return (
                  <tr key={scenario.name} className="border-b border-border-primary/60 last:border-b-0">
                    <td className="py-2.5 pr-3">
                      <span className="font-medium text-text-primary">{scenario.label}</span>
                      <span className="ml-2 text-xs text-text-tertiary tnum">{scenario.name}</span>
                    </td>
                    <td className="py-2.5 pr-3 text-right text-text-primary tnum">
                      {stat.avgRank.toFixed(2)}
                    </td>
                    <td className="py-2.5 pr-3 text-right text-text-secondary tnum">
                      {derived ? `${derived.bestRank} / ${derived.worstRank}` : "—"}
                    </td>
                    <td className="py-2.5 pr-3 text-right text-text-primary tnum">
                      {stat.killRate.toFixed(2)}
                    </td>
                    <td className="py-2.5 pr-3 text-right text-text-primary tnum">
                      {stat.resourcesPerTick.toFixed(3)}
                    </td>
                    <td className="py-2.5 pr-3 text-right text-text-primary tnum">
                      {stat.populationPeak.toFixed(1)}
                    </td>
                    <td className="py-2.5 pr-3 text-right text-text-primary tnum">
                      {stat.beaconTicks.toFixed(1)}
                    </td>
                    <td className="py-2.5 text-right text-text-primary tnum">
                      {stat.firstKillTick == null ? "—" : stat.firstKillTick}
                    </td>
                  </tr>
                );
              })}
              <tr className="bg-surface-tertiary/40 font-medium">
                <td className="py-2.5 pr-3 text-text-primary">跨场景汇总</td>
                <td className="py-2.5 pr-3 text-right text-text-primary tnum">
                  {entry.avgRank.toFixed(2)}
                </td>
                <td className="py-2.5 pr-3 text-right text-text-secondary tnum">
                  波动 ±{entry.rankStddev.toFixed(2)}
                </td>
                <td className="py-2.5 pr-3 text-right text-text-primary tnum">
                  {entry.killRate.toFixed(2)}/场
                </td>
                <td className="py-2.5 pr-3 text-right text-text-primary tnum">
                  {fmt(
                    scenarioStats.reduce((n, s) => n + (s.stat ? s.stat.resourcesPerTick : 0), 0) /
                      Math.max(1, scenarioStats.filter((s) => s.stat).length),
                    3,
                  )}
                </td>
                <td className="py-2.5 pr-3 text-right text-text-primary tnum">
                  {fmt(
                    scenarioStats.reduce((n, s) => n + (s.stat ? s.stat.populationPeak : 0), 0) /
                      Math.max(1, scenarioStats.filter((s) => s.stat).length),
                    1,
                  )}
                </td>
                <td className="py-2.5 pr-3 text-right text-text-primary tnum">
                  {fmt(
                    scenarioStats.reduce((n, s) => n + (s.stat ? s.stat.beaconTicks : 0), 0) /
                      Math.max(1, scenarioStats.filter((s) => s.stat).length),
                    1,
                  )}
                </td>
                <td className="py-2.5 text-right text-text-primary tnum">—</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      {/* ===== 单场明细 ===== */}
      <section className="mt-6 card p-6">
        <SectionHeader
          title="单场明细"
          enTitle="Match Details"
          description={`每场对局 ${benchData.params.players} 条目同场对抗，胜方为资源结算最高者。`}
        />
        <div className="thin-scroll overflow-x-auto">
          <table className="w-full min-w-[900px] border-collapse text-xs">
            <thead className="sticky top-0 z-10 bg-surface-primary">
              <tr className="border-b border-border-primary text-text-tertiary">
                {MATCH_COLUMNS.map((col) => (
                  <th
                    key={col.key}
                    className={`whitespace-nowrap px-2 py-2 font-medium ${
                      col.align === "right" ? "text-right" : "text-left"
                    }`}
                  >
                    {col.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {benchData.scenarios.flatMap((scenario: BenchmarkScenario) =>
                scenario.matches.map((match: BenchmarkMatch) => {
                  const player = match.players[id];
                  if (!player) return null;
                  const rank = match.rank[id] ?? match.rank[`${id}-s${match.seed}`] ?? "—";
                  const cells: Record<string, string> = {
                    scenario: scenario.label,
                    seed: String(match.seed),
                    rank: String(rank),
                    isWinner: player.isWinner ? "🏆" : "—",
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
                    <tr
                      key={`${scenario.name}-${match.seed}`}
                      className={`border-b border-border-primary/60 last:border-b-0 ${
                        player.isWinner ? "bg-rank-gold/[0.06]" : "hover:bg-surface-tertiary/50"
                      }`}
                    >
                      {MATCH_COLUMNS.map((col) => (
                        <td
                          key={col.key}
                          className={`whitespace-nowrap px-2 py-2 text-text-primary tnum ${
                            col.align === "right" ? "text-right" : "text-left"
                          } ${col.key === "scenario" ? "font-medium" : ""}`}
                        >
                          {cells[col.key]}
                        </td>
                      ))}
                    </tr>
                  );
                }),
              )}
            </tbody>
          </table>
        </div>
      </section>
      {/* ===== 击杀时序 ===== */}
      <section className="mt-6 card p-6">
        <SectionHeader
          title="击杀时序"
          enTitle="Kill Timeline"
          description="核心摧毁事件沿 tick 轴展开：每行一个玩家，标记位置 = 摧毁时刻、颜色 = 击杀者（悬浮查看击杀者 → 被击杀者）。"
        />
        <KillTimelinePanel
          scenarios={benchData.scenarios}
          roster={benchData.contestants.map((c) => ({ id: c.id, label: c.label }))}
          ticks={benchData.params.ticks}
        />
      </section>
    </div>
  );
}
