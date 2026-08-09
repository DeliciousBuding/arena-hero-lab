import { ArrowLeft, Award, FlaskConical } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { RadarChart } from "@/components/radar-chart";
import { RankBadge } from "@/components/rank-badge";
import { benchData, PROFILE_DIM_LABELS, type ProfileDimensionKey } from "@/lib/bench";

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

/** 条目详情页：五维雷达 + 分场景表现 + 单场明细表 */
export default async function EntryPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const entry = benchData.leaderboard.find((e) => e.contestantId === id);
  const contestant = benchData.contestants.find((c) => c.id === id);
  const profile = benchData.profiles[id];
  if (!entry || !contestant || !profile) {
    notFound();
  }

  const stats = benchData.entryScenarioStats[id] ?? {};
  const scenarioRanks = Object.values(entry.scenarioRanks);

  const radarValues = benchData.profileDimensions.map((key) => ({
    key: key as ProfileDimensionKey,
    value: profile.normalized[key],
  }));

  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:py-10">
      <Link
        href="/"
        className="mb-6 inline-flex items-center gap-1.5 text-sm text-text-secondary transition-colors hover:text-text-primary"
      >
        <ArrowLeft className="h-4 w-4" />
        返回榜单总览
      </Link>

      {/* 条目头部 */}
      <header className="flex flex-wrap items-start gap-5 rounded-2xl border border-border-primary bg-surface-primary p-6">
        <RankBadge rank={entry.rank} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-bold text-text-primary sm:text-2xl">{contestant.label}</h1>
            <span className="rounded-lg border border-border-primary px-2 py-0.5 text-xs text-text-tertiary tnum">
              {contestant.id}
            </span>
            <span className="inline-flex items-center gap-1 rounded-lg border border-accent-primary/30 bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent-primary">
              <Award className="h-3 w-3" />
              {contestant.kind === "builtin" ? "内置对照" : "第三方 agent"}
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
              ["survivalScore", pct(entry.survivalScore)],
              ["场景名次波动", `±${entry.rankStddev.toFixed(2)}`],
            ].map(([label, value]) => (
              <span
                key={label}
                className="rounded-lg border border-border-primary bg-surface-tertiary/50 px-2.5 py-1 text-text-secondary"
              >
                {label} <span className="font-semibold text-text-primary tnum">{value}</span>
              </span>
            ))}
          </div>
        </div>
      </header>

      {/* 五维画像 + 原始值 */}
      <section className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="rounded-2xl border border-border-primary bg-surface-primary p-6">
          <h2 className="mb-4 text-base font-semibold text-text-primary">五维画像（归一化）</h2>
          <RadarChart values={radarValues} />
        </div>
        <div className="rounded-2xl border border-border-primary bg-surface-primary p-6">
          <h2 className="mb-4 text-base font-semibold text-text-primary">画像原始值</h2>
          <div className="space-y-2.5">
            {benchData.profileDimensions.map((key) => (
              <div key={key} className="flex items-center gap-3">
                <span className="w-14 text-sm text-text-secondary">
                  {PROFILE_DIM_LABELS[key as ProfileDimensionKey]}
                </span>
                <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface-tertiary">
                  <div
                    className="h-full rounded-full bg-accent-primary"
                    style={{ width: `${Math.max(2, profile.normalized[key] * 100)}%` }}
                  />
                </div>
                <span className="w-24 text-right text-xs text-text-tertiary tnum">
                  {profile.normalized[key].toFixed(3)}{" "}
                  <span className="text-text-tertiary">/ raw {fmt(profile.raw[key], 3)}</span>
                </span>
              </div>
            ))}
          </div>
          <p className="mt-4 text-xs leading-relaxed text-text-tertiary">
            归一化：同维度 10 条目内相对得分（0–1，该维度最强条目为 1）。
          </p>
        </div>
      </section>

      {/* 分场景表现 */}
      <section className="mt-6 rounded-2xl border border-border-primary bg-surface-primary p-6">
        <h2 className="mb-4 text-base font-semibold text-text-primary">分场景表现（3 场均值）</h2>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-sm">
            <thead>
              <tr className="border-b border-border-primary text-xs text-text-tertiary">
                <th className="py-2 pr-3 text-left font-medium">场景</th>
                <th className="py-2 pr-3 text-right font-medium">平均名次</th>
                <th className="py-2 pr-3 text-right font-medium">最好/最差</th>
                <th className="py-2 pr-3 text-right font-medium">击杀</th>
                <th className="py-2 pr-3 text-right font-medium">采集</th>
                <th className="py-2 pr-3 text-right font-medium">上交</th>
                <th className="py-2 pr-3 text-right font-medium">人口峰值</th>
                <th className="py-2 text-right font-medium">兵损/场</th>
              </tr>
            </thead>
            <tbody>
              {benchData.scenarios.map((scenario) => {
                const s = stats[scenario.name];
                if (!s) return null;
                return (
                  <tr key={scenario.name} className="border-b border-border-primary/60 last:border-b-0">
                    <td className="py-2.5 pr-3">
                      <span className="font-medium text-text-primary">{scenario.label}</span>
                      <span className="ml-2 text-xs text-text-tertiary tnum">{scenario.name}</span>
                    </td>
                    <td className="py-2.5 pr-3 text-right text-text-primary tnum">
                      {s.avgRank.toFixed(2)}
                    </td>
                    <td className="py-2.5 pr-3 text-right text-text-secondary tnum">
                      {s.bestRank} / {s.worstRank}
                    </td>
                    <td className="py-2.5 pr-3 text-right text-text-primary tnum">{s.kills}</td>
                    <td className="py-2.5 pr-3 text-right text-text-primary tnum">{s.harvested}</td>
                    <td className="py-2.5 pr-3 text-right text-text-primary tnum">{s.deposited}</td>
                    <td className="py-2.5 pr-3 text-right text-text-primary tnum">
                      {s.populationPeak.toFixed(1)}
                    </td>
                    <td className="py-2.5 text-right text-text-primary tnum">
                      {s.unitsLost.toFixed(2)}
                    </td>
                  </tr>
                );
              })}
              {scenarioRanks.length > 0 ? (
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
                    {fmt(Object.values(stats).reduce((n, s) => n + s.harvested, 0) / Math.max(1, Object.values(stats).length), 1)}
                  </td>
                  <td className="py-2.5 pr-3 text-right text-text-primary tnum">
                    {fmt(Object.values(stats).reduce((n, s) => n + s.deposited, 0) / Math.max(1, Object.values(stats).length), 1)}
                  </td>
                  <td className="py-2.5 pr-3 text-right text-text-primary tnum">
                    {fmt(
                      Object.values(stats).reduce((n, s) => n + s.populationPeak, 0) /
                        Math.max(1, Object.values(stats).length),
                      1,
                    )}
                  </td>
                  <td className="py-2.5 text-right text-text-primary tnum">
                    {fmt(Object.values(stats).reduce((n, s) => n + s.unitsLost, 0) / Math.max(1, Object.values(stats).length), 2)}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      {/* 单场明细 */}
      <section className="mt-6 rounded-2xl border border-border-primary bg-surface-primary p-6">
        <h2 className="mb-1 text-base font-semibold text-text-primary">单场明细（15 场）</h2>
        <p className="mb-4 text-xs text-text-tertiary">
          每场对局 {benchData.params.players} 条目同场对抗，胜方为资源结算最高者。
        </p>
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
              {benchData.scenarios.flatMap((scenario) =>
                scenario.matches.map((match) => {
                  const player = match.players[id];
                  if (!player) return null;
                  const cells: Record<string, string> = {
                    scenario: scenario.label,
                    seed: String(match.seed),
                    rank: String(match.rank[id] ?? match.rank[`${id}-s${match.seed}`] ?? "—"),
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
                      className={`border-b border-border-primary/60 last:border-b-0 hover:bg-surface-tertiary/50 ${
                        player.isWinner ? "bg-accent-soft/40" : ""
                      }`}
                    >
                      {MATCH_COLUMNS.map((col) => (
                        <td
                          key={col.key}
                          className={`whitespace-nowrap px-2 py-1.5 text-text-primary tnum ${
                            col.align === "right" ? "text-right" : "text-left"
                          } ${col.key === "scenario" ? "font-medium" : ""} ${
                            col.key === "isWinner" ? "text-center" : ""
                          }`}
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

      <p className="mt-6 flex items-center gap-1.5 text-xs text-text-tertiary">
        <FlaskConical className="h-3.5 w-3.5" />
        数据源：{benchData.source} · {benchData.schema} · 生成于{" "}
        {new Date(benchData.generatedAt).toLocaleString("zh-CN")}
      </p>
    </div>
  );
}
