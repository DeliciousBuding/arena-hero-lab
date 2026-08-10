import { Heatmap } from "@/components/heatmap";
import { RankBars, type RankBarRow } from "@/components/rank-bars";
import { ScenarioComparison } from "@/components/scenario-comparison";
import { ScoreBars, type ScoreBarEntry } from "@/components/score-bars";
import { SectionHeader } from "@/components/section-header";
import { Card, CardContent } from "@/components/ui/card";
import { ACTIVE_SCORE_DIMENSIONS, benchData, contestantOf } from "@/lib/bench";

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

export default function HomePage() {
  const totalMatches = benchData.scenarios.reduce((n, s) => n + s.matches.length, 0);
  const generatedAt = new Date(benchData.generatedAt).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <div className="container-page px-4 py-10 sm:px-6">
      {/* ===== Hero ===== */}
      <header className="mb-10">
        <h1 className="font-serif text-4xl font-normal leading-tight tracking-tight text-foreground sm:text-5xl">
          Arena Hero
          <span className="ml-3 text-brand">Leaderboard</span>
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-muted-foreground">
          社区智能体 × 场景 × 种子对抗评测，多维指标可视化
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground tnum">
          <span>
            {benchData.leaderboard.length} 条目同场 · {benchData.params.seeds.length} 种子 ×{" "}
            {benchData.params.ticks.toLocaleString("zh-CN")} ticks · {totalMatches} 场
          </span>
          <span className="hidden h-3 w-px bg-border-faint sm:block" />
          <span>schema {benchData.schema}</span>
          <span className="hidden h-3 w-px bg-border-faint sm:block" />
          <span>评测时间 {generatedAt}</span>
        </div>
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
