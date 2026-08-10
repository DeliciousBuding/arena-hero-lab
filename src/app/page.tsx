import { ArrowRight, GitBranch, Layers } from "lucide-react";
import Link from "next/link";
import { Heatmap } from "@/components/heatmap";
import { RankBars, type RankBarRow } from "@/components/rank-bars";
import { ScenarioComparison } from "@/components/scenario-comparison";
import { ScoreBars, type ScoreBarEntry } from "@/components/score-bars";
import { SectionHeader } from "@/components/section-header";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { benchData, contestantOf, scenarioBarsOf } from "@/lib/bench";

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
      bars: scenarioBarsOf(entry.contestantId),
      href: `/entry/${entry.contestantId}`,
    };
  });
}

/** 四维分数对比图数据（击杀/名次/经济/生存 0–1 分）。 */
const SCORE_KEYS = [
  { key: "killScore" as const, label: "击杀" },
  { key: "rankScore" as const, label: "名次" },
  { key: "economyScore" as const, label: "经济" },
  { key: "survivalScore" as const, label: "生存" },
];

function scoreEntries(): ScoreBarEntry[] {
  return benchData.leaderboard.map((entry) => {
    const contestant = contestantOf(entry.contestantId);
    return {
      id: entry.contestantId,
      label: contestant?.label ?? entry.contestantId,
      scores: SCORE_KEYS.map((d) => ({
        key: d.key,
        label: d.label,
        value: entry[d.key],
      })),
    };
  });
}

export default function HomePage() {
  const { params } = benchData;
  const generatedDate = new Date(benchData.generatedAt).toLocaleString("zh-CN");
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

      {/* ===== 综合排名图 ===== */}
      <section className="mb-16">
        <SectionHeader
          id="rankings"
          title="Overall Rankings"
          description="综合分条形图（v3 composite 加权合成），可搜索过滤；点击条目进入详情页。"
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
          <CardContent className="p-5">
            <RankBars rows={rankRows()} valueLabel="综合分" />
          </CardContent>
        </Card>
      </section>

      {/* ===== 四维分数对比 ===== */}
      <section className="mb-16">
        <SectionHeader
          id="scores"
          title="Score Profile"
          enTitle="四维对比"
          description="各条目击杀 / 名次 / 经济 / 生存 四项归一化分数（0–100%），直观对比强弱项。"
        />
        <ScoreBars entries={scoreEntries()} />
      </section>

      {/* ===== 热图 ===== */}
      <section className="mb-16">
        <SectionHeader
          id="heatmap"
          title="Scenario Heatmap"
          enTitle="场景热图"
          description="场景 × 条目指标矩阵，可切换资源/刻 · 击杀率 · 平均名次。"
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
