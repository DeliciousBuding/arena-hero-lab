/**
 * 静态数据层：直接 import scripts/convert.mts 生成的 bench.json。
 * 所有展示数字均来自 arena.bench.report.v3 评测产物，不包含任何 mock 数据。
 */
import rawBench from "@/data/bench.json";

export interface Contestant {
  id: string;
  label: string;
  kind: "python" | "builtin";
  configNote: string;
  /** GitHub 仓库（社区 agent 第三方来源；v3.1，convert 侧映射）。 */
  repoUrl?: string;
  /** Linux DO 社区帖子（讨论来源；v3.1，convert 侧映射）。 */
  linuxdoUrl?: string;
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
  scenarios: BenchmarkScenario[];
  entryScenarioStats: Record<string, Record<string, EntryScenarioStat>>;
  scenarioOrder: string[];
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

/** v3 雷达四维：kill / rank / economy / survival（均为 0–1 分数） */
export const SCORE_DIMENSIONS: { key: keyof LeaderboardRow; label: string; enLabel: string }[] = [
  { key: "killScore", label: "击杀", enLabel: "Kill" },
  { key: "rankScore", label: "名次", enLabel: "Rank" },
  { key: "economyScore", label: "经济", enLabel: "Economy" },
  { key: "survivalScore", label: "生存", enLabel: "Survival" },
] as const;
