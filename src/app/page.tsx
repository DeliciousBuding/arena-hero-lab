import { Layers } from "lucide-react";
import { GitHubIcon } from "@/components/app-chrome";
import { Heatmap } from "@/components/heatmap";
import { RankBars, type RankBarRow } from "@/components/rank-bars";
import { ScenarioComparison } from "@/components/scenario-comparison";
import { ScoreBars, type ScoreBarEntry } from "@/components/score-bars";
import { SectionHeader } from "@/components/section-header";
import { Card, CardContent } from "@/components/ui/card";
import { benchData, contestantOf, SCORE_DIMENSIONS } from "@/lib/bench";

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

/** 通用维度行构建：主值 + 副指标 + 条长依据。 */
function dimensionRows(
  pick: (row: (typeof benchData.leaderboard)[number]) => {
    value: number;
    ascending: boolean;
    primary: string;
    secondary: string;
  },
): RankBarRow[] {
  return benchData.leaderboard.map((entry) => {
    const contestant = contestantOf(entry.contestantId);
    const picked = pick(entry);
    return {
      rank: entry.rank,
      id: entry.contestantId,
      label: contestant?.label ?? entry.contestantId,
      kind: contestant?.kind ?? "python",
      value: picked.value,
      ascending: picked.ascending,
      primary: picked.primary,
      secondary: picked.secondary,
      href: `/entry/${entry.contestantId}`,
      repoUrl: contestant?.repoUrl,
    };
  });
}

/** 场景 perEntry 资源/刻的跨场景均值（未参赛场景按 0 记）。 */
function economyPerTick(id: string): number {
  const values = benchData.scenarios
    .map((s) => s.perEntry[id]?.resourcesPerTick)
    .filter((v): v is number => v != null);
  if (values.length === 0) return 0;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

/** 场景梯度：跨场景平均名次（越小越好，条形反向 = 名次越好条越长）。 */
function scenarioRows(): RankBarRow[] {
  return dimensionRows((entry) => {
    const ranks = Object.values(entry.scenarioRanks).filter(
      (v): v is number => v != null,
    );
    const best = ranks.length > 0 ? Math.min(...ranks) : 0;
    const worst = ranks.length > 0 ? Math.max(...ranks) : 0;
    return {
      value: entry.avgRank,
      ascending: false,
      primary: entry.avgRank.toFixed(2),
      secondary: `± ${entry.rankStddev.toFixed(2)} · 最好 ${best} / 最差 ${worst}`,
    };
  });
}

/** 四维分数对比图数据（击杀/名次/经济/生存 0–1 分；维度定义见 lib/bench SCORE_DIMENSIONS）。 */
function scoreEntries(): ScoreBarEntry[] {
  return benchData.leaderboard.map((entry) => {
    const contestant = contestantOf(entry.contestantId);
    return {
      id: entry.contestantId,
      label: contestant?.label ?? entry.contestantId,
      scores: SCORE_DIMENSIONS.map((dim) => ({
        key: dim.key as string,
        label: dim.label,
        value: entry[dim.key] as number,
      })),
    };
  });
}

/** 维度卡片：serif 标题 + 该维度排名条形图。 */
function DimensionCard({
  title,
  enTitle,
  description,
  rows,
  valueLabel,
}: {
  title: string;
  enTitle: string;
  description: string;
  rows: RankBarRow[];
  valueLabel: string;
}) {
  return (
    <Card>
      <CardContent className="p-5">
        <h2 className="mb-1 flex items-baseline gap-2 font-serif text-lg font-normal">
          {title}
          <span className="font-sans text-xs font-normal text-muted-foreground">{enTitle}</span>
        </h2>
        <p className="mb-4 text-xs leading-relaxed text-muted-foreground">{description}</p>
        <RankBars rows={rows} valueLabel={valueLabel} />
      </CardContent>
    </Card>
  );
}

export default function HomePage() {
  const generatedDate = new Date(benchData.generatedAt).toLocaleString("zh-CN");
  const runId = benchData.source.split("/").pop() ?? "";

  const economyRows = dimensionRows((entry) => ({
    value: entry.economyScore,
    ascending: true,
    primary: `${(entry.economyScore * 100).toFixed(1)}%`,
    secondary: `资源/刻 ${(economyPerTick(entry.contestantId) * 1000).toFixed(1)}`,
  }));
  const killRows = dimensionRows((entry) => ({
    value: entry.killRate,
    ascending: true,
    primary: entry.killRate.toFixed(2),
    secondary: `killScore ${(entry.killScore * 100).toFixed(1)}%`,
  }));

  return (
    <div className="container-page px-4 py-10 sm:px-6">
      {/* ===== Hero ===== */}
      <header className="mb-14">
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
          <h1 className="font-serif text-4xl font-normal leading-tight tracking-tight text-foreground sm:text-5xl">
            Arena Hero
            <span className="ml-3 text-brand">Leaderboard</span>
          </h1>
          <div className="flex flex-col items-start gap-1.5 text-xs text-muted-foreground sm:items-end">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
                <Layers className="h-3.5 w-3.5" />
                <span className="tnum">{benchData.schema}</span>
              </span>
              <span className="text-border">·</span>
              <span className="tnum break-words" title={`run ${runId}`}>
                run {runId.slice(-12)}
              </span>
              <span className="text-border">·</span>
              <time className="tnum whitespace-nowrap">{generatedDate}</time>
            </div>
            <a
              href="https://github.com/DeliciousBuding/arena"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 whitespace-nowrap text-foreground transition-colors hover:text-brand"
            >
              <GitHubIcon className="h-3 w-3" />
              DeliciousBuding/arena
            </a>
          </div>
        </div>
      </header>

      {/* ===== 综合排名图 ===== */}
      <section className="mb-16">
        <SectionHeader
          id="rankings"
          title="Overall Rankings"
          description="综合分条形图（v3 composite 加权合成），可搜索过滤；点击条目进入详情页。"
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

      {/* ===== 维度卡（经济 / 击杀 / 场景梯度） ===== */}
      <section className="mb-16">
        <SectionHeader
          id="dimensions"
          title="Dimension Breakdown"
          enTitle="维度分解"
          description="经济 / 击杀 / 场景梯度三个独立维度排名（综合分维度见上方 Overall Rankings）。"
        />
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
          <DimensionCard
            title="经济"
            enTitle="Economy"
            description="economyScore 归一化分（0–1），副指标为跨场景场均资源采集速率。"
            rows={economyRows}
            valueLabel="经济分"
          />
          <DimensionCard
            title="击杀"
            enTitle="Kills"
            description="场均击杀 killRate，killScore 为击杀归一化分。"
            rows={killRows}
            valueLabel="击杀/场"
          />
          <DimensionCard
            title="场景梯度"
            enTitle="Scenario"
            description="跨场景平均名次（越小越好，条形反向），波动 ± 越小越稳定。"
            rows={scenarioRows()}
            valueLabel="平均名次"
          />
        </div>
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
