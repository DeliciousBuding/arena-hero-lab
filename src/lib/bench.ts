/**
 * 静态数据层：直接 import scripts/convert.mts 生成的 bench.json。
 * 所有展示数字均来自 arena.bench.report.v2 评测产物，不包含任何 mock 数据。
 */
import rawBench from "@/data/bench.json";

export type ProfileDimensionKey = "economy" | "military" | "survival" | "beacon" | "expansion";

export interface Contestant {
  id: string;
  label: string;
  kind: string;
  configNote: string;
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
  survivalMedian: number;
  survivalScore: number;
  scenarioRanks: Record<string, number>;
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

export interface BenchmarkMatch {
  seed: number;
  winner: string;
  rank: Record<string, number>;
  players: Record<string, MatchPlayerStats>;
}

export interface BenchmarkScenario {
  name: string;
  label: string;
  template: { configNote: string; radius: number; randomDrop: boolean; resources: string };
  perEntry: Record<string, Record<string, number | null>>;
  matches: BenchmarkMatch[];
}

export interface BenchmarkData {
  schema: string;
  generatedAt: string;
  convertedAt: string;
  source: string;
  params: { players: number; rulesVersion: string; scenarios: string[]; seeds: number[]; ticks: number };
  contestants: Contestant[];
  profileDimensions: ProfileDimensionKey[];
  profiles: Record<
    string,
    { normalized: Record<ProfileDimensionKey, number>; raw: Record<ProfileDimensionKey, number> }
  >;
  leaderboard: LeaderboardRow[];
  scenarios: BenchmarkScenario[];
  entryScenarioStats: Record<string, Record<string, EntryScenarioStat>>;
  summaryTable: { header: string[]; rows: Record<string, string>[] };
  scenarioOrder: string[];
}

export const benchData = rawBench as unknown as BenchmarkData;

export function contestantOf(id: string): Contestant | undefined {
  return benchData.contestants.find((c) => c.id === id);
}

export function leaderboardRowOf(id: string): LeaderboardRow | undefined {
  return benchData.leaderboard.find((e) => e.contestantId === id);
}

export function profileOf(id: string) {
  return benchData.profiles[id];
}

export function scenarioOf(name: string): BenchmarkScenario | undefined {
  return benchData.scenarios.find((s) => s.name === name);
}

export const PROFILE_DIM_LABELS: Record<ProfileDimensionKey, string> = {
  economy: "经济",
  military: "军事",
  survival: "生存",
  beacon: "信标",
  expansion: "扩张",
};
