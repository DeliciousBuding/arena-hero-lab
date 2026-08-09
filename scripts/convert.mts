/**
 * 数据转换脚本：results.json（schema arena.bench.report.v3）→ src/data/bench.json
 *
 * 用法：
 *   npx tsx scripts/convert.mts                          # 仓库内置副本 scripts/input/results.json
 *   npx tsx scripts/convert.mts <path-to-results.json>   # 指定评测产物（推荐）
 *   npx tsx scripts/convert.mts --source=<path>          # 等价写法
 *
 * 输出 src/data/bench.json 为静态数据，前端构建时直接 import，不接后端。
 * 本脚本只做确定性变换（字段裁剪/聚合/排序/中文标签映射），不编造任何数字。
 *
 * v3 契约要点（arena.bench.report.v3）：
 * - leaderboard[]：composite/avgRank/killRate/economyScore/rankScore/killScore/
 *   survivalMedian/survivalScore（survival* 为 v2 兼容字段，v3 恒 1.0，前端标注弃用）
 * - scenarios[]：perEntry[contestantId] 提供 killRate/resourcesPerTick/survivalMedian/
 *   populationPeak/avgRank/beaconTicks/firstKillTick/damagePerLoss
 * - matches[]：perPlayer 键形如 `<id>-s<seed>`，rank/winner 同键
 * - contestants[]：kind: python | builtin（builtin = 对照组）
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const defaultSource = resolve(repoRoot, "scripts/input/results.json");
const sourceArg =
  process.argv.find((a) => a.startsWith("--source=")) ??
  process.argv.find((a) => a.endsWith(".json") && !a.startsWith("-"));
const sourcePath = sourceArg
  ? resolve(sourceArg.startsWith("--source=") ? sourceArg.slice("--source=".length) : sourceArg)
  : defaultSource;
const outputPath = resolve(repoRoot, "src/data/bench.json");

const SCHEMA = "arena.bench.report.v3";

/** 场景名 → 中文标签（展示用，纯翻译，不改变任何数值） */
const SCENARIO_LABELS: Record<string, string> = {
  "ffa-dense": "高密度冲突",
  "ffa-std": "标准地图",
  "ffa-open": "开阔地图",
  "ffa-scarce": "资源匮乏",
  "ffa-random": "随机落点",
  "ffa-resource-race": "中央矿争夺",
  "ffa-defense-pressure": "资源枯竭",
};

interface PerPlayerStats {
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
}

interface RawReport {
  schema: string;
  generatedAt: string;
  params: {
    players: number;
    rulesVersion: string;
    scenarios: string[];
    seeds: number[];
    ticks: number;
  };
  contestants: { id: string; label: string; kind: string; configNote: string }[];
  errors: unknown[];
  leaderboard: {
    contestantId: string;
    avgRank: number;
    composite: number;
    economyScore: number;
    killRate: number;
    killScore: number;
    rankScore: number;
    survivalMedian: number;
    survivalScore: number;
  }[];
  scenarios: {
    name: string;
    seedCount: number;
    template: { configNote: string; radius: number; randomDrop: boolean; resources: string };
    perEntry: Record<string, Record<string, number | null>>;
    matches: {
      seed: number;
      winner: string;
      rank: Record<string, number>;
      perPlayer: Record<string, PerPlayerStats>;
      /** v3.1 击杀时序事件（旧产物缺失）。 */
      killEvents?: { tick: number; destroyedBy: string[] }[];
    }[];
  }[];
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

function mean(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

function stddev(values: number[]): number {
  if (values.length < 2) return 0;
  const m = mean(values);
  return Math.sqrt(mean(values.map((v) => (v - m) ** 2)));
}

/** perPlayer 键（`<id>-s<seed>` 或裸 id）→ 条目 id */
function resolvePlayerId(key: string, seed: number, contestantIds: string[]): string | null {
  if (contestantIds.includes(key)) return key;
  const suffix = `-s${seed}`;
  if (key.endsWith(suffix) && contestantIds.includes(key.slice(0, -suffix.length))) {
    return key.slice(0, -suffix.length);
  }
  return null;
}

function main(): void {
  const raw: RawReport = JSON.parse(readFileSync(sourcePath, "utf8"));
  if (raw.schema !== SCHEMA) {
    throw new Error(`Unexpected schema: ${raw.schema} (expect ${SCHEMA})`);
  }

  const contestantIds = raw.contestants.map((c) => c.id);
  const scenarioIds = raw.scenarios.map((s) => s.name);

  /** 每条目 × 每场景的聚合指标（由全部 matches 派生，供分场景表/详情页使用） */
  const entryScenarioStats: Record<string, Record<string, EntryScenarioStat>> = {};

  const scenarios = raw.scenarios.map((scenario) => {
    const statsForEntry: Record<string, EntryScenarioStat> = {};

    const matches = scenario.matches.map((match) => {
      const players: Record<string, PerPlayerStats & { isWinner: boolean }> = {};
      for (const [key, stats] of Object.entries(match.perPlayer)) {
        const id = resolvePlayerId(key, match.seed, contestantIds);
        if (!id) continue;
        players[id] = { ...stats, isWinner: match.winner === key };
        const bucket = (statsForEntry[id] ??= {
          avgRank: 0,
          bestRank: Infinity,
          worstRank: -Infinity,
          kills: 0,
          killRate: 0,
          damageDealt: 0,
          harvested: 0,
          deposited: 0,
          populationPeak: 0,
          finalPopulation: 0,
          unitsLost: 0,
          aliveTicks: 0,
          beaconTicks: 0,
          firstKillTick: null,
          matchCount: 0,
        });
        bucket.kills += stats.kills;
        bucket.damageDealt += stats.damageDealt;
        bucket.harvested += stats.harvested;
        bucket.deposited += stats.deposited;
        bucket.populationPeak += stats.populationPeak;
        bucket.finalPopulation += stats.finalPopulation;
        bucket.unitsLost += stats.unitsLost;
        bucket.aliveTicks += stats.aliveTicks;
        bucket.beaconTicks += stats.beaconTicks;
        bucket.matchCount += 1;
        if (stats.firstKillTick !== null) {
          bucket.firstKillTick =
            bucket.firstKillTick === null
              ? stats.firstKillTick
              : Math.min(bucket.firstKillTick, stats.firstKillTick);
        }
        const rank = match.rank[key] ?? match.rank[id] ?? 0;
        bucket.avgRank += rank;
        bucket.bestRank = Math.min(bucket.bestRank, rank);
        bucket.worstRank = Math.max(bucket.worstRank, rank);
      }
      return {
        seed: match.seed,
        winner: match.winner,
        rank: match.rank,
        players,
        killEvents: (match.killEvents ?? []).map((event) => ({
          tick: event.tick,
          destroyedBy: event.destroyedBy
            .map((rawId) => resolvePlayerId(rawId, match.seed, contestantIds))
            .filter((id): id is string => id !== null),
        })),
      };
    });

    for (const bucket of Object.values(statsForEntry)) {
      bucket.avgRank /= bucket.matchCount;
      bucket.killRate = bucket.kills / bucket.matchCount;
      bucket.bestRank = bucket.bestRank === Infinity ? 0 : bucket.bestRank;
      bucket.worstRank = bucket.worstRank === -Infinity ? 0 : bucket.worstRank;
      bucket.kills = Math.round(bucket.kills);
      bucket.damageDealt = Math.round(bucket.damageDealt);
      bucket.harvested = Math.round(bucket.harvested);
      bucket.deposited = Math.round(bucket.deposited);
      bucket.populationPeak = bucket.populationPeak / bucket.matchCount;
      bucket.finalPopulation = bucket.finalPopulation / bucket.matchCount;
      bucket.unitsLost = bucket.unitsLost / bucket.matchCount;
      bucket.aliveTicks = bucket.aliveTicks / bucket.matchCount;
      bucket.beaconTicks = bucket.beaconTicks / bucket.matchCount;
    }
    for (const [id, bucket] of Object.entries(statsForEntry)) {
      (entryScenarioStats[id] ??= {})[scenario.name] = bucket;
    }

    return {
      name: scenario.name,
      label: SCENARIO_LABELS[scenario.name] ?? scenario.name,
      template: scenario.template,
      perEntry: scenario.perEntry,
      matches,
    };
  });

  /** 榜单：按综合分降序，附每场景平均名次与跨场景波动（由 perEntry.avgRank 派生） */
  const sortedLeaderboard = [...raw.leaderboard].sort((a, b) => b.composite - a.composite);
  const leaderboard = sortedLeaderboard.map((entry, index) => {
    const scenarioRanks = Object.fromEntries(
      scenarios.map((s) => [s.name, s.perEntry[entry.contestantId]?.avgRank ?? null]),
    );
    const rankValues = Object.values(scenarioRanks).filter((v): v is number => v != null);
    return {
      rank: index + 1,
      contestantId: entry.contestantId,
      composite: entry.composite,
      avgRank: entry.avgRank,
      rankStddev: stddev(rankValues),
      killRate: entry.killRate,
      killScore: entry.killScore,
      rankScore: entry.rankScore,
      economyScore: entry.economyScore,
      survivalMedian: entry.survivalMedian,
      survivalScore: entry.survivalScore,
      scenarioRanks,
    };
  });

  const output = {
    schema: raw.schema,
    generatedAt: raw.generatedAt,
    convertedAt: new Date().toISOString(),
    source: dirname(sourcePath).replace(/\\/g, "/"),
    params: raw.params,
    contestants: raw.contestants.map((c) => ({
      id: c.id,
      label: c.label,
      kind: c.kind,
      configNote: c.configNote,
    })),
    leaderboard,
    scenarios,
    entryScenarioStats,
    scenarioOrder: scenarioIds,
  };

  mkdirSync(dirname(outputPath), { recursive: true });
  writeFileSync(outputPath, JSON.stringify(output, null, 2) + "\n", "utf8");
  console.log(`[convert] ${sourcePath} -> ${outputPath}`);
  console.log(
    `[convert] ${leaderboard.length} entries, ${scenarios.length} scenarios, ` +
      `${scenarios.reduce((n, s) => n + s.matches.length, 0)} matches`,
  );
}

main();
