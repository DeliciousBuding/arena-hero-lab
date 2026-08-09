import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { Heatmap } from "@/components/heatmap";
import { OverallTable } from "@/components/overall-table";
import { ScenarioComparison } from "@/components/scenario-comparison";
import { SectionHeader } from "@/components/section-header";
import { benchData, contestantOf } from "@/lib/bench";

/** 扁平领奖台行（arena.ai 风格：无卡片圆角，细线分隔） */
function PodiumRow({
  rank,
  label,
  sub,
  medal,
}: {
  rank: number;
  label: string;
  sub: string;
  medal: "gold" | "silver" | "bronze";
}) {
  const colors = {
    gold: "text-rank-gold",
    silver: "text-rank-silver",
    bronze: "text-rank-bronze",
  } as const;
  return (
    <div className="flex items-baseline gap-3 border-b border-border-primary py-4">
      <span className={`text-2xl font-light tnum ${colors[medal]}`}>{rank}</span>
      <div className="min-w-0">
        <div className="truncate text-sm font-medium text-text-primary">{label}</div>
        <div className="text-xs text-text-tertiary tnum">{sub}</div>
      </div>
    </div>
  );
}

export default function HomePage() {
  const { params } = benchData;
  const generatedDate = new Date(benchData.generatedAt).toLocaleString("zh-CN");
  const top3 = benchData.leaderboard.slice(0, 3);
  const runId = benchData.source.split("/").pop() ?? "";

  return (
    <div className="mx-auto max-w-6xl px-4 py-10 sm:px-6">
      {/* ===== Hero（arena.ai：细体标题 + 一行副标 + 数据版本行） ===== */}
      <header className="mb-10">
        <h1 className="text-3xl font-light tracking-tight text-text-primary sm:text-4xl">
          Leaderboard Overview
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-text-secondary">
          {params.players} 条目 × {params.scenarios.length} 场景 × {params.seeds.length} 种子 ·{" "}
          {benchData.scenarios.reduce((n, s) => n + s.matches.length, 0)} 场
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-tertiary tnum">
          <span>
            数据源{" "}
            <a
              href="https://github.com/DeliciousBuding/arena"
              target="_blank"
              rel="noreferrer"
              className="link-hover text-text-secondary"
            >
              DeliciousBuding/arena
            </a>
          </span>
          <span>· run {runId}</span>
          <span>· {benchData.schema}</span>
          <span>· {generatedDate}</span>
        </div>
      </header>

      {/* ===== 前三名 ===== */}
      <section className="mb-12 grid grid-cols-1 gap-x-10 sm:grid-cols-3">
        {top3.map((row) => {
          const medal = row.rank === 1 ? "gold" : row.rank === 2 ? "silver" : "bronze";
          return (
            <Link key={row.contestantId} href={`/entry/${row.contestantId}`} className="link-hover">
              <PodiumRow
                rank={row.rank}
                label={contestantOf(row.contestantId)?.label ?? row.contestantId}
                sub={`综合 ${(row.composite * 100).toFixed(1)}% · 均排 ${row.avgRank.toFixed(2)}`}
                medal={medal}
              />
            </Link>
          );
        })}
      </section>

      {/* ===== 综合排名 ===== */}
      <section className="mb-14">
        <SectionHeader
          id="rankings"
          title="Overall Rankings"
          description="按综合分排序，列头可排序。"
          action={
            <Link
              href="/leaderboard"
              className="link-hover inline-flex items-center gap-1 text-xs font-medium text-text-secondary"
            >
              全维度
              <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          }
        />
        <OverallTable rows={benchData.leaderboard} />
      </section>

      {/* ===== 热图 ===== */}
      <section className="mb-14">
        <SectionHeader
          id="heatmap"
          title="Scenario Heatmap"
          description="场景 × 条目指标，可切换指标。"
        />
        <Heatmap />
      </section>

      {/* ===== 场景对比 ===== */}
      <section className="mb-14">
        <SectionHeader id="scenarios" title="Scenario Comparison" description="各场景条目表现。" />
        <ScenarioComparison />
      </section>
    </div>
  );
}
