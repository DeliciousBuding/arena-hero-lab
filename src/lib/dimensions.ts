/**
 * 维度卡片定义：把 bench.json 组织成 4 张 arena.ai 风格分类卡片（v3 指标）。
 * 所有数值均为 results.json 的派生计算（均值/百分比/标准差），无 mock。
 */
import { benchData, contestantOf, type LeaderboardRow } from "./bench";

export interface DimensionRow {
  rank: number;
  id: string;
  label: string;
  kind: "python" | "builtin";
  /** GitHub 仓库（社区 agent 第三方来源；v3.1，convert 侧映射）。 */
  repoUrl?: string;
  /** Linux DO 社区帖子（讨论来源；v3.1，convert 侧映射）。 */
  linuxdoUrl?: string;
  primary: string;
  delta: string | null;
  secondary: string;
  sortValue: number;
}

export interface Dimension {
  id: string;
  title: string;
  enTitle: string;
  description: string;
  icon: string;
  valueLabel: string;
  rows: DimensionRow[];
}

const pct = (v: number, digits = 1): string => `${(v * 100).toFixed(digits)}%`;

/** 场景 perEntry 资源/刻的跨场景均值（未参赛场景按 0 记） */
function meanResourcesPerTick(id: string): number {
  const values = benchData.scenarios
    .map((s) => s.perEntry[id]?.resourcesPerTick)
    .filter((v): v is number => v != null);
  if (values.length === 0) return 0;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

function baseRows(entry: LeaderboardRow): {
  rank: number;
  id: string;
  label: string;
  kind: "python" | "builtin";
  repoUrl?: string;
  linuxdoUrl?: string;
} {
  const contestant = contestantOf(entry.contestantId);
  return {
    rank: entry.rank,
    id: entry.contestantId,
    label: contestant?.label ?? entry.contestantId,
    kind: contestant?.kind ?? "python",
    ...(contestant?.repoUrl !== undefined ? { repoUrl: contestant.repoUrl } : {}),
    ...(contestant?.linuxdoUrl !== undefined ? { linuxdoUrl: contestant.linuxdoUrl } : {}),
  };
}

const overallDimension: Dimension = {
  id: "composite",
  title: "综合分",
  enTitle: "Overall",
  description:
    "v3 综合分 composite（rank/kill/economy 等分项加权合成），按综合分降序排列的官方名次",
  icon: "trophy",
  valueLabel: "综合分",
  rows: benchData.leaderboard.map((entry) => ({
    ...baseRows(entry),
    primary: pct(entry.composite),
    delta: null,
    secondary: `均排 ${entry.avgRank.toFixed(2)} · rankScore ${pct(entry.rankScore)}`,
    sortValue: entry.composite,
  })),
};

const economyDimension: Dimension = {
  id: "economy",
  title: "经济",
  enTitle: "Economy",
  description: "v3 经济分 economyScore（0–1 归一化），副指标为场均资源采集速率（资源/刻）",
  icon: "coins",
  valueLabel: "经济分",
  rows: benchData.leaderboard.map((entry) => {
    const id = entry.contestantId;
    return {
      ...baseRows(entry),
      primary: pct(entry.economyScore),
      delta: null,
      secondary: `资源/刻 ${meanResourcesPerTick(id).toFixed(3)}`,
      sortValue: entry.economyScore,
    };
  }),
};

const killsDimension: Dimension = {
  id: "kills",
  title: "击杀",
  enTitle: "Kills",
  description: "场均击杀 killRate（全部对局均值），killScore 为击杀归一化分",
  icon: "swords",
  valueLabel: "击杀/场",
  rows: benchData.leaderboard.map((entry) => ({
    ...baseRows(entry),
    primary: entry.killRate.toFixed(2),
    delta: null,
    secondary: `killScore ${pct(entry.killScore)}`,
    sortValue: entry.killRate,
  })),
};

const scenarioDimension: Dimension = {
  id: "scenario",
  title: "场景梯度",
  enTitle: "Scenario",
  description: "跨场景平均名次 ± 标准差（由各场景 avgRank 派生），波动越小越稳定",
  icon: "route",
  valueLabel: "平均名次",
  rows: benchData.leaderboard.map((entry) => {
    const ranks = Object.values(entry.scenarioRanks).filter(
      (v): v is number => v != null,
    );
    const best = ranks.length > 0 ? Math.min(...ranks) : 0;
    const worst = ranks.length > 0 ? Math.max(...ranks) : 0;
    return {
      ...baseRows(entry),
      primary: entry.avgRank.toFixed(2),
      delta: `± ${entry.rankStddev.toFixed(2)}`,
      secondary: `最好 ${best} / 最差 ${worst}`,
      sortValue: entry.avgRank,
    };
  }),
};

export const dimensions: Dimension[] = [
  overallDimension,
  economyDimension,
  killsDimension,
  scenarioDimension,
];

export function dimensionOf(id: string): Dimension | undefined {
  return dimensions.find((d) => d.id === id);
}
