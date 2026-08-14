/**
 * 静态数据层：直接 import scripts/convert.mts 生成的 bench.json。
 * 所有展示数字均来自 arena.bench.report.v4 评测产物，不包含任何 mock 数据。
 */
import rawBench from "@/data/bench.json";

export interface Contestant {
  id: string;
  label: string;
  /** python = 第三方社区 agent；control = 确定性对照 bot。 */
  kind: "python" | "control" | "ours";
  configNote: string;
  /** GitHub 仓库（社区 agent 第三方来源；v3.1，convert 侧映射）。 */
  repoUrl?: string;
  /** Linux DO 社区帖子（讨论来源；v3.1，convert 侧映射）。 */
  linuxdoUrl?: string;
  /** Linux DO 帖子标题（v3.3；convert 侧实抓存档，与 linuxdoUrl 一一对应）。 */
  linuxdoTitle?: string;
}

export interface LeaderboardRow {
  rank: number;
  contestantId: string;
  composite: number;
  avgRank: number;
  rankStddev: number;
  killRate: number;
  killScore: number;
  rankScore: number;
  economyScore: number;
  /** v2 兼容字段：v3 恒 1.0（展示时标注已弃用） */
  survivalMedian: number;
  /** v2 兼容字段：v3 恒 1.0（展示时标注已弃用） */
  survivalScore: number;
  scenarioRanks: Record<string, number | null>;
}

/** 1000 次 bootstrap 重采样得到的 95% 置信区间（2.5 / 97.5 分位）。 */
export interface BootstrapBand {
  composite: [number, number];
  rank: [number, number];
}

/** v3 场景级 perEntry 指标（与 results.json 契约一致，null = 未参赛） */
export interface ScenarioEntryStat {
  avgRank: number;
  beaconTicks: number;
  damagePerLoss: number;
  firstKillTick: number | null;
  killMatches: number;
  killRate: number;
  populationPeak: number;
  resourcesPerTick: number;
  survivalMedian: number;
}

export interface EntryScenarioStat {
  avgRank: number;
  bestRank: number;
  worstRank: number;
  kills: number;
  killRate: number;
  damageDealt: number;
  harvested: number;
  deposited: number;
  populationPeak: number;
  finalPopulation: number;
  unitsLost: number;
  aliveTicks: number;
  beaconTicks: number;
  firstKillTick: number | null;
  matchCount: number;
}

export interface MatchPlayerStats {
  aliveTicks: number;
  beaconTicks: number;
  damageDealt: number;
  deposited: number;
  finalPopulation: number;
  finalResources: number;
  firstKillTick: number | null;
  harvested: number;
  kills: number;
  populationPeak: number;
  unitsLost: number;
  isWinner: boolean;
}

export interface KillEvent {
  tick: number;
  destroyedBy: string[];
  /** 被摧毁核心的归属条目 id（v3.1；旧数据可能缺失）。 */
  victim?: string;
}

export interface BenchmarkMatch {
  seed: number;
  winner: string;
  rank: Record<string, number>;
  players: Record<string, MatchPlayerStats>;
  /** 击杀时序事件（v3.1；旧数据可能缺失）。 */
  killEvents?: KillEvent[];
  /** per-tick 资源/人口采样（v3.1 可观测性；每 50 tick 一点；旧数据可能缺失）。 */
  perTickSamples?: PerTickSample[];
}

/** 单个 per-tick 采样点（效率曲线数据源）。 */
export interface PerTickSample {
  tick: number;
  players: Record<string, { resources: number; population: number }>;
}

export interface BenchmarkScenario {
  name: string;
  label: string;
  template: { configNote: string; radius: number; randomDrop: boolean; resources: string };
  perEntry: Record<string, ScenarioEntryStat | null>;
  matches: BenchmarkMatch[];
}

export interface SubLeaderboardRow {
  rank: number;
  contestant: string;
  score: number;
  components: Record<string, number>;
  raw: Record<string, number>;
}

export interface BenchmarkData {
  schema: string;
  generatedAt: string;
  convertedAt: string;
  source: string;
  params: {
    players: number;
    rulesVersion: string;
    scenarios: string[];
    seeds: number[];
    ticks: number;
  };
  contestants: Contestant[];
  leaderboard: LeaderboardRow[];
  /** 阶段/策略小榜：early_economy / mid_game / late_game / military。 */
  subLeaderboards: Record<string, SubLeaderboardRow[]>;
  scenarios: BenchmarkScenario[];
  entryScenarioStats: Record<string, Record<string, EntryScenarioStat>>;
  scenarioOrder: string[];
  /** 综合分 / 名次的 bootstrap 95% 置信区间（旧产物可能缺失）。 */
  bootstrap?: Record<string, BootstrapBand>;
}

export const benchData = rawBench as unknown as BenchmarkData;

export function contestantOf(id: string): Contestant | undefined {
  return benchData.contestants.find((c) => c.id === id);
}

export function leaderboardRowOf(id: string): LeaderboardRow | undefined {
  return benchData.leaderboard.find((e) => e.contestantId === id);
}

export function scenarioOf(name: string): BenchmarkScenario | undefined {
  return benchData.scenarios.find((s) => s.name === name);
}

/** LeaderboardRow 中所有数值维度键（排除 id 与场景名次映射）。 */
export type NumericDimensionKey = Exclude<
  keyof LeaderboardRow,
  "contestantId" | "scenarioRanks"
>;

/** 维度分数在全体（主榜）中的排名（1-based；详情页画像参照系）。
 *  返回 null 表示该条目不在主榜（如 reference-contestant 条目）。 */
export function dimensionRankOf(id: string, key: NumericDimensionKey): number | null {
  const sorted = [...benchData.leaderboard].sort(
    (a, b) => (b[key] as number) - (a[key] as number),
  );
  const index = sorted.findIndex((row) => row.contestantId === id);
  return index === -1 ? null : index + 1;
}

/** v3 画像维度：kill / rank / economy 有区分度；survival 恒 1.0（评测规则所致）标记弃用。 */
export const SCORE_DIMENSIONS: {
  key: NumericDimensionKey;
  label: string;
  enLabel: string;
  /** v3 恒 1.0（同 tick 重生），前端画像图不展示，仅详情页分项区标注弃用。 */
  deprecated?: boolean;
}[] = [
  { key: "killScore", label: "击杀", enLabel: "Kill" },
  { key: "rankScore", label: "名次", enLabel: "Rank" },
  { key: "economyScore", label: "经济", enLabel: "Economy" },
  { key: "survivalScore", label: "生存", enLabel: "Survival", deprecated: true },
] as const;

/** 有区分度的画像维度（过滤弃用项，供雷达/分组条/首页画像使用）。 */
export const ACTIVE_SCORE_DIMENSIONS = SCORE_DIMENSIONS.filter((d) => !d.deprecated);
