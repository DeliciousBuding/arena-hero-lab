/**
 * 维度卡片定义：把 bench.json 数据组织成 6 张 arena.ai 风格分类卡片。
 * 所有数值均为 results.json 的派生计算（均值/百分比/标准差），无 mock。
 */
import {
  benchData,
  contestantOf,
  PROFILE_DIM_LABELS,
  type EntryScenarioStat,
  type ProfileDimensionKey,
} from "./bench";

export interface DimensionRow {
  rank: number;
  id: string;
  label: string;
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

/** 跨场景加权均值（以场次为权重） */
function overallOf(id: string, pick: (s: EntryScenarioStat) => number): number {
  const stats = Object.values(benchData.entryScenarioStats[id] ?? {});
  const total = stats.reduce((n, s) => n + s.matchCount, 0);
  if (total === 0) return 0;
  return stats.reduce((sum, s) => sum + pick(s) * s.matchCount, 0) / total;
}

function profileMean(id: string): number {
  const profile = benchData.profiles[id];
  if (!profile) return 0;
  const dims = Object.values(profile.normalized);
  return dims.reduce((a, b) => a + b, 0) / dims.length;
}

function strongestDim(id: string): ProfileDimensionKey {
  const profile = benchData.profiles[id];
  let best: ProfileDimensionKey = "economy";
  let bestValue = -Infinity;
  for (const dim of benchData.profileDimensions) {
    if (profile && profile.normalized[dim] > bestValue) {
      bestValue = profile.normalized[dim];
      best = dim;
    }
  }
  return best;
}

function scenarioRankRange(id: string): { best: number; worst: number } {
  const row = benchData.leaderboard.find((e) => e.contestantId === id);
  const ranks = Object.values(row?.scenarioRanks ?? {});
  if (ranks.length === 0) return { best: 0, worst: 0 };
  return { best: Math.min(...ranks), worst: Math.max(...ranks) };
}

function baseRows(id: string): { rank: number; id: string; label: string } {
  const index = benchData.leaderboard.findIndex((e) => e.contestantId === id);
  return { rank: index + 1, id, label: contestantOf(id)?.label ?? id };
}

const overallDimension: Dimension = {
  id: "composite",
  title: "综合分",
  enTitle: "Overall",
  description: "综合分 = rankScore×0.6 + killScore×0.2 + survivalScore×0.2（由本数据最小二乘拟合精确验证）",
  icon: "trophy",
  valueLabel: "综合分",
  rows: benchData.leaderboard.map((entry) => ({
    ...baseRows(entry.contestantId),
    primary: pct(entry.composite),
    delta: null,
    secondary: `均排 ${entry.avgRank.toFixed(2)} · rankScore ${pct(entry.rankScore)}`,
    sortValue: entry.composite,
  })),
};

const killsDimension: Dimension = {
  id: "kills",
  title: "击杀",
  enTitle: "Kills",
  description: "场均击杀数（15 场全部对局均值），killScore 为击杀归一化分",
  icon: "swords",
  valueLabel: "击杀/场",
  rows: benchData.leaderboard.map((entry) => ({
    ...baseRows(entry.contestantId),
    primary: entry.killRate.toFixed(2),
    delta: null,
    secondary: `killScore ${pct(entry.killScore)}`,
    sortValue: entry.killRate,
  })),
};

const survivalDimension: Dimension = {
  id: "survival",
  title: "生存规模",
  enTitle: "Survival",
  description: "终局人口（撑到 1000 ticks 时的平均兵力），兵损为场均损失单位",
  icon: "shield",
  valueLabel: "终局人口",
  rows: benchData.leaderboard.map((entry) => {
    const id = entry.contestantId;
    return {
      ...baseRows(id),
      primary: overallOf(id, (s) => s.finalPopulation).toFixed(2),
      delta: null,
      secondary: `兵损 ${overallOf(id, (s) => s.unitsLost).toFixed(2)}/场`,
      sortValue: overallOf(id, (s) => s.finalPopulation),
    };
  }),
};

const scenarioDimension: Dimension = {
  id: "scenario",
  title: "场景梯度",
  enTitle: "Scenario",
  description: "5 个场景（高密度/标准/开阔/匮乏/随机）平均名次 ± 标准差，波动越小越稳定",
  icon: "route",
  valueLabel: "平均名次",
  rows: benchData.leaderboard.map((entry) => {
    const { best, worst } = scenarioRankRange(entry.contestantId);
    return {
      ...baseRows(entry.contestantId),
      primary: entry.avgRank.toFixed(2),
      delta: `± ${entry.rankStddev.toFixed(2)}`,
      secondary: `最好 ${best} / 最差 ${worst}`,
      sortValue: entry.avgRank,
    };
  }),
};

const profileDimension: Dimension = {
  id: "profile",
  title: "五维画像",
  enTitle: "Profile",
  description: "经济 / 军事 / 生存 / 信标 / 扩张 五维归一化画像的均值，副指标为最强维度",
  icon: "radar",
  valueLabel: "画像均值",
  rows: benchData.leaderboard.map((entry) => {
    const id = entry.contestantId;
    return {
      ...baseRows(id),
      primary: pct(profileMean(id)),
      delta: null,
      secondary: `最强 · ${PROFILE_DIM_LABELS[strongestDim(id)]}`,
      sortValue: profileMean(id),
    };
  }),
};

const economyDimension: Dimension = {
  id: "economy",
  title: "生态",
  enTitle: "Economy",
  description: "经济维度归一化分（采集与上交效率），副指标为扩张维度分",
  icon: "coins",
  valueLabel: "经济分",
  rows: benchData.leaderboard.map((entry) => {
    const profile = benchData.profiles[entry.contestantId];
    const normalized = profile?.normalized ?? { economy: 0, expansion: 0 };
    return {
      ...baseRows(entry.contestantId),
      primary: pct(normalized.economy),
      delta: null,
      secondary: `扩张 ${pct(normalized.expansion)}`,
      sortValue: normalized.economy,
    };
  }),
};

export const dimensions: Dimension[] = [
  overallDimension,
  killsDimension,
  survivalDimension,
  scenarioDimension,
  profileDimension,
  economyDimension,
];

export function dimensionOf(id: string): Dimension | undefined {
  return dimensions.find((d) => d.id === id);
}
