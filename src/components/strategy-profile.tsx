import { Award, Crosshair, Gem, Shield, TrendingDown, TrendingUp } from "lucide-react";
import type { LeaderboardRow, BenchmarkScenario, ScenarioEntryStat } from "@/lib/bench";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface ProfileInsight {
  icon: typeof Award;
  label: string;
  enLabel: string;
  value: string;
  note: string;
  tone: "brand" | "gold" | "outline" | "success";
}

function deriveStyle(killRate: number, resourcesPerTick: number): string {
  if (killRate > 0.6 && resourcesPerTick < 8) return "进攻型";
  if (killRate < 0.3 && resourcesPerTick > 10) return "经济型";
  if (killRate > 0.4 && resourcesPerTick > 9) return "均衡型";
  return "防守型";
}

function deriveStability(rankStddev: number): { value: string; tone: ProfileInsight["tone"] } {
  if (rankStddev < 0.8) return { value: "高", tone: "success" };
  if (rankStddev < 1.5) return { value: "中", tone: "outline" };
  return { value: "低", tone: "gold" };
}

/**
 * 策略画像：从评测数据派生可读洞察（强项维度 / 风格分类 / 最佳场景 / 稳定性）。
 * 可观测性增强——把原始数字转成自然语言，降低解读门槛。
 */
export function StrategyProfile({
  entry,
  scenarioStats,
}: {
  entry: LeaderboardRow;
  scenarioStats: { scenario: BenchmarkScenario; stat: ScenarioEntryStat | null }[];
}) {
  const scores: { key: string; label: string; value: number }[] = [
    { key: "killScore", label: "击杀", value: entry.killScore },
    { key: "rankScore", label: "名次", value: entry.rankScore },
    { key: "economyScore", label: "经济", value: entry.economyScore },
  ];
  const strongest = scores.reduce((a, b) => (b.value > a.value ? b : a));

  const participated = scenarioStats.filter(
    (s): s is { scenario: BenchmarkScenario; stat: ScenarioEntryStat } => s.stat != null,
  );
  const bestScenario = participated.length
    ? participated.reduce((a, b) => (b.stat.avgRank < a.stat.avgRank ? b : a))
    : null;
  const worstScenario = participated.length
    ? participated.reduce((a, b) => (b.stat.avgRank > a.stat.avgRank ? b : a))
    : null;

  const meanResources =
    participated.length > 0
      ? participated.reduce((n, s) => n + s.stat.resourcesPerTick, 0) / participated.length
      : 0;
  const style = deriveStyle(entry.killRate, meanResources);
  const stability = deriveStability(entry.rankStddev);

  const insights: ProfileInsight[] = [
    {
      icon: Award,
      label: "强项维度",
      enLabel: "Strongest",
      value: strongest.label,
      note: `${(strongest.value * 100).toFixed(0)}% 分`,
      tone: "brand",
    },
    {
      icon: Crosshair,
      label: "战术风格",
      enLabel: "Style",
      value: style,
      note: `杀率 ${entry.killRate.toFixed(2)} · 资源/刻 ${meanResources.toFixed(2)}`,
      tone: "outline",
    },
    {
      icon: TrendingUp,
      label: "最佳场景",
      enLabel: "Best",
      value: bestScenario ? bestScenario.scenario.label : "—",
      note: bestScenario ? `均排 ${bestScenario.stat.avgRank.toFixed(1)}` : "未参赛",
      tone: "success",
    },
    {
      icon: TrendingDown,
      label: "最弱场景",
      enLabel: "Worst",
      value: worstScenario ? worstScenario.scenario.label : "—",
      note: worstScenario ? `均排 ${worstScenario.stat.avgRank.toFixed(1)}` : "未参赛",
      tone: "gold",
    },
    {
      icon: Shield,
      label: "跨场景稳定",
      enLabel: "Stability",
      value: stability.value,
      note: `σ = ${entry.rankStddev.toFixed(2)}`,
      tone: stability.tone,
    },
    {
      icon: Gem,
      label: "综合排名",
      enLabel: "Rank",
      value: `#${entry.rank}`,
      note: `综合分 ${(entry.composite * 100).toFixed(1)}%`,
      tone: "gold",
    },
  ];

  return (
    <Card>
      <CardContent className="grid grid-cols-2 gap-3 p-5 sm:grid-cols-3 lg:grid-cols-6">
        {insights.map((insight) => {
          const Icon = insight.icon;
          return (
            <div
              key={insight.label}
              className="flex flex-col gap-1.5 rounded-md border border-border-faint bg-secondary/30 p-3"
            >
              <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                <Icon className="h-3 w-3" />
                {insight.label}
                <span className="text-[10px] opacity-70">{insight.enLabel}</span>
              </div>
              <Badge variant={insight.tone} className="w-fit text-xs">
                {insight.value}
              </Badge>
              <span className="text-[11px] leading-tight text-muted-foreground tnum">
                {insight.note}
              </span>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
