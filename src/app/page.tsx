import { ArrowRight, GitBranch, Layers } from "lucide-react";
import Link from "next/link";
import { Heatmap } from "@/components/heatmap";
import { MethodologySection } from "@/components/methodology-section";
import { OverallTable } from "@/components/overall-table";
import { ScenarioComparison } from "@/components/scenario-comparison";
import { SectionHeader } from "@/components/section-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Stat, StatHint, StatLabel, StatValue } from "@/components/ui/stat";
import { benchData, contestantOf } from "@/lib/bench";

/** 前三名领奖台行：serif 大号名次 + label + 副指标，底部 hairline */
function PodiumRow({
  rank,
  label,
  sub,
  contestantId,
}: {
  rank: number;
  label: string;
  sub: string;
  contestantId: string;
}) {
  return (
    <Link
      href={`/entry/${contestantId}`}
      className="group block border-b border-border-faint pb-4 transition-colors hover:border-foreground/30"
    >
      <div className="flex items-baseline gap-3">
        <span
          className={
            rank === 1
              ? "font-serif text-3xl font-normal text-rank-gold tnum"
              : rank === 2
                ? "font-serif text-3xl font-normal text-rank-silver tnum"
                : "font-serif text-3xl font-normal text-rank-bronze tnum"
          }
        >
          {rank}
        </span>
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-foreground group-hover:text-brand">
            {label}
          </div>
          <div className="text-xs text-muted-foreground tnum">{sub}</div>
        </div>
      </div>
    </Link>
  );
}

export default function HomePage() {
  const { params } = benchData;
  const generatedDate = new Date(benchData.generatedAt).toLocaleString("zh-CN");
  const top3 = benchData.leaderboard.slice(0, 3);
  const runId = benchData.source.split("/").pop() ?? "";
  const totalMatches = benchData.scenarios.reduce((n, s) => n + s.matches.length, 0);

  return (
    <div className="container-page px-4 py-10 sm:px-6">
      {/* ===== Hero ===== */}
      <header className="mb-12">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Layers className="h-3.5 w-3.5" />
          <span className="tnum">{benchData.schema}</span>
          <Separator orientation="vertical" className="h-3" />
          <span>run {runId}</span>
          <Separator orientation="vertical" className="h-3" />
          <time className="tnum">{generatedDate}</time>
        </div>
        <h1 className="mt-3 font-serif text-4xl font-normal leading-tight tracking-tight text-foreground sm:text-5xl">
          Arena Hero
          <span className="ml-3 text-brand">Leaderboard</span>
        </h1>
        <p className="mt-3 max-w-2xl text-base leading-relaxed text-muted-foreground">
          arena-hero 模拟器评测 v3：{params.players} 条目 × {params.scenarios.length} 场景 ×{" "}
          {params.seeds.length} 种子 · {totalMatches} 场对抗的综合榜单。
        </p>
        <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span>数据源</span>
          <a
            href="https://github.com/DeliciousBuding/arena"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-foreground transition-colors hover:text-brand"
          >
            <GitBranch className="h-3 w-3" />
            DeliciousBuding/arena
          </a>
        </div>
      </header>

      {/* ===== 数据总览 Stat 卡 ===== */}
      <section className="mb-12 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat>
          <StatLabel>条目</StatLabel>
          <StatValue>{params.players}</StatValue>
          <StatHint>agents</StatHint>
        </Stat>
        <Stat>
          <StatLabel>场景</StatLabel>
          <StatValue>{params.scenarios.length}</StatValue>
          <StatHint>scenarios</StatHint>
        </Stat>
        <Stat>
          <StatLabel>种子</StatLabel>
          <StatValue>{params.seeds.length}</StatValue>
          <StatHint>seeds/场景</StatHint>
        </Stat>
        <Stat>
          <StatLabel>总场次</StatLabel>
          <StatValue>{totalMatches}</StatValue>
          <StatHint>matches</StatHint>
        </Stat>
      </section>

      {/* ===== 前三名领奖台 ===== */}
      <section className="mb-12 grid grid-cols-1 gap-x-10 sm:grid-cols-3">
        {top3.map((row) => {
          const contestant = contestantOf(row.contestantId);
          return (
            <PodiumRow
              key={row.contestantId}
              rank={row.rank}
              label={contestant?.label ?? row.contestantId}
              sub={`综合 ${(row.composite * 100).toFixed(1)}% · 均排 ${row.avgRank.toFixed(2)}`}
              contestantId={row.contestantId}
            />
          );
        })}
      </section>

      {/* ===== 综合排名 ===== */}
      <section className="mb-16">
        <SectionHeader
          id="rankings"
          title="Overall Rankings"
          description="按综合分排序，列头可排序。点击条目进入详情页。"
          action={
            <Button asChild variant="ghost" size="sm" className="gap-1 text-xs text-brand hover:bg-brand-soft">
              <Link href="/leaderboard">
                全维度
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
          }
        />
        <Card>
          <CardContent className="thin-scroll overflow-x-auto p-2">
            <OverallTable rows={benchData.leaderboard} />
          </CardContent>
        </Card>
      </section>

      {/* ===== 评测方法 ===== */}
      <MethodologySection />

      {/* ===== 热图 ===== */}
      <section className="mb-16">
        <SectionHeader
          id="heatmap"
          title="Scenario Heatmap"
          enTitle="场景热图"
          description="场景 × 条目指标矩阵，可切换资源/刻 · 击杀率 · 存活（弃用）。"
        />
        <Heatmap />
      </section>

      {/* ===== 场景对比 ===== */}
      <section className="mb-16">
        <SectionHeader
          id="scenarios"
          title="Scenario Comparison"
          enTitle="场景对比"
          description="各场景条目表现：资源/刻横向条 + 人口峰值副条。"
        />
        <ScenarioComparison />
      </section>
    </div>
  );
}
