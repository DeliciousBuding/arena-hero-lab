import { RankBars, type RankBarRow } from "@/components/rank-bars";
import { SectionHeader } from "@/components/section-header";
import { Card, CardContent } from "@/components/ui/card";
import { benchData, contestantOf, scenarioBarsOf } from "@/lib/bench";

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
      bars: scenarioBarsOf(entry.contestantId),
      href: `/entry/${entry.contestantId}`,
    };
  });
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

/**
 * 全维度榜单页：4 张图表卡（综合分 / 经济 / 击杀 / 场景梯度），
 * 全部为条形图（arena.ai 风格），支持搜索过滤，点击条目进入详情页。
 * 静态导出；RankBars 客户端搜索交互。
 */
export default function LeaderboardPage() {
  const overallRows = dimensionRows((entry) => ({
    value: entry.composite,
    ascending: true,
    primary: `${(entry.composite * 100).toFixed(1)}%`,
    secondary: `rankScore ${(entry.rankScore * 100).toFixed(1)}%`,
  }));
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
    <div className="container-page px-4 py-8 sm:px-6 lg:py-10">
      <SectionHeader
        title="Leaderboard"
        enTitle="全量榜单"
        description="v3 四个维度的完整榜单（全部条目展示）。全部图表化——综合分 / 经济 / 击杀 / 场景梯度条形图，可搜索过滤、点击进入详情页。"
      />
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Card>
          <CardContent className="p-5">
            <h2 className="mb-4 flex items-baseline gap-2 font-serif text-lg font-normal">
              综合分
              <span className="font-sans text-xs font-normal text-muted-foreground">Overall</span>
            </h2>
            <RankBars rows={overallRows} valueLabel="综合分" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-5">
            <h2 className="mb-4 flex items-baseline gap-2 font-serif text-lg font-normal">
              经济
              <span className="font-sans text-xs font-normal text-muted-foreground">Economy</span>
            </h2>
            <RankBars rows={economyRows} valueLabel="经济分" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-5">
            <h2 className="mb-4 flex items-baseline gap-2 font-serif text-lg font-normal">
              击杀
              <span className="font-sans text-xs font-normal text-muted-foreground">Kills</span>
            </h2>
            <RankBars rows={killRows} valueLabel="击杀/场" />
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-5">
            <h2 className="mb-4 flex items-baseline gap-2 font-serif text-lg font-normal">
              场景梯度
              <span className="font-sans text-xs font-normal text-muted-foreground">Scenario</span>
            </h2>
            <RankBars rows={scenarioRows()} valueLabel="平均名次" />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

/** 场景 perEntry 资源/刻的跨场景均值（未参赛场景按 0 记）。 */
function economyPerTick(id: string): number {
  const values = benchData.scenarios
    .map((s) => s.perEntry[id]?.resourcesPerTick)
    .filter((v): v is number => v != null);
  if (values.length === 0) return 0;
  return values.reduce((a, b) => a + b, 0) / values.length;
}
