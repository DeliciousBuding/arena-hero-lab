/**
 * 数据转换脚本：results.json（schema arena.bench.report.v2）→ src/data/bench.json
 *
 * 用法：
 *   node scripts/convert.mts                        # 使用 scripts/input/results.json（仓库内置副本）
 *   node scripts/convert.mts --source=<path>        # 从指定路径读取（如实时 runs 目录）
 *
 * 输出 src/data/bench.json 为静态数据，前端构建时直接 import，不接后端。
 * 本脚本只做确定性变换（聚合/排序/格式化），不编造任何数字。
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const defaultSource = resolve(repoRoot, "scripts/input/results.json");
const sourceArg = process.argv.find((a) => a.startsWith("--source="));
const sourcePath = sourceArg ? resolve(sourceArg.slice("--source=".length)) : defaultSource;
const outputPath = resolve(repoRoot, "src/data/bench.json");

/** 场景中文名（展示用，纯翻译） */
const SCENARIO_LABELS: Record<string, string> = {
  "ffa-dense": "高密度冲突",
  "ffa-std": "标准地图",
  "ffa-open": "开阔地图",
  "ffa-scarce": "资源匮乏",
  "ffa-random": "随机落点",
};

/** 归一化五维的顺序与中文名 */
const PROFILE_DIMENSIONS = ["economy", "military", "survival", "beacon", "expansion"] as const;

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
    killRate: number;
    killScore: number;
    rankScore: number;
    survivalMedian: number;
    survivalScore: number;
  }[];
  profiles: Record<
    string,
    {
      normalized: Record<(typeof PROFILE_DIMENSIONS)[number], number>;
      raw: Record<(typeof PROFILE_DIMENSIONS)[number], number>;
    }
  >;
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
    }[];
  }[];
}

interface EntryScenarioStat {
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

/** 简易 CSV 解析（支持 BOM、空字段、引号） */
function parseCsv(text: string): string[][] {
  const clean = text.replace(/^\uFEFF/, "");
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < clean.length; i++) {
    const ch = clean[i];
    if (inQuotes) {
      if (ch === '"') {
        if (clean[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && clean[i + 1] === "\n") i++;
      row.push(field);
      field = "";
      if (row.some((c) => c !== "")) rows.push(row);
      row = [];
    } else {
      field += ch;
    }
  }
  row.push(field);
  if (row.some((c) => c !== "")) rows.push(row);
  return rows;
}

function main(): void {
  const raw: RawReport = JSON.parse(readFileSync(sourcePath, "utf8"));
  if (raw.schema !== "arena.bench.report.v2") {
    throw new Error(`Unexpected schema: ${raw.schema} (expect arena.bench.report.v2)`);
  }

  const scenarioIds = raw.scenarios.map((s) => s.name);

  /** 每个条目 × 每个场景的聚合指标（由全部 matches 计算） */
  const entryScenarioStats: Record<string, Record<string, EntryScenarioStat>> = {};

  const scenarios = raw.scenarios.map((scenario) => {
    const statsForEntry: Record<string, EntryScenarioStat> = {};

    const matches = scenario.matches.map((match) => {
      const players: Record<string, PerPlayerStats & { isWinner: boolean }> = {};
      for (const [key, stats] of Object.entries(match.perPlayer)) {
        // key 形如 "arena-evolve-s1" / "arena-evolve-s2" / "arena-evolve-s3" / "ts-aggressive"
        const id = key.endsWith("-s1") || key.endsWith("-s2") || key.endsWith("-s3")
          ? key.slice(0, -3)
          : key;
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
          bucket.firstKillTick = bucket.firstKillTick === null
            ? stats.firstKillTick
            : Math.min(bucket.firstKillTick, stats.firstKillTick);
        }
        const rank = match.rank[key] ?? match.rank[id] ?? 0;
        bucket.avgRank += rank;
        bucket.bestRank = Math.min(bucket.bestRank, rank);
        bucket.worstRank = Math.max(bucket.worstRank, rank);
      }
      return { seed: match.seed, winner: match.winner, rank: match.rank, players };
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

  /** 榜单：按综合分降序，附跨场景排名统计 */
  const sortedLeaderboard = [...raw.leaderboard].sort((a, b) => b.composite - a.composite);
  const leaderboard = sortedLeaderboard.map((entry, index) => {
    const perScenario = entryScenarioStats[entry.contestantId] ?? {};
    const ranks = Object.values(perScenario).map((s) => s.avgRank);
    const scenarioRanks = Object.fromEntries(
      Object.entries(perScenario).map(([name, s]) => [name, s.avgRank]),
    );
    return {
      rank: index + 1,
      contestantId: entry.contestantId,
      composite: entry.composite,
      avgRank: entry.avgRank,
      rankStddev: stddev(ranks),
      killRate: entry.killRate,
      killScore: entry.killScore,
      rankScore: entry.rankScore,
      survivalMedian: entry.survivalMedian,
      survivalScore: entry.survivalScore,
      scenarioRanks,
    };
  });

  /** CSV 汇总表 → 结构化行 */
  const csvText = readFileSync(resolve(repoRoot, "public/research/06_summary_table.csv"), "utf8");
  const csvRows = parseCsv(csvText);
  const csvHeader = csvRows[0] ?? [];
  const summaryTable = csvRows.slice(1).map((cells) =>
    Object.fromEntries(csvHeader.map((h, i) => [h, cells[i] ?? ""])),
  );

  const output = {
    schema: raw.schema,
    generatedAt: raw.generatedAt,
    convertedAt: new Date().toISOString(),
    source: "data/runs/sim/arena-bench-v2-d874a86e1931",
    params: raw.params,
    contestants: raw.contestants.map((c) => ({
      id: c.id,
      label: c.label,
      kind: c.kind,
      configNote: c.configNote,
    })),
    profileDimensions: [...PROFILE_DIMENSIONS],
    profiles: raw.profiles,
    leaderboard,
    scenarios,
    entryScenarioStats,
    summaryTable: { header: csvHeader, rows: summaryTable },
    scenarioOrder: scenarioIds,
  };

  mkdirSync(dirname(outputPath), { recursive: true });
  writeFileSync(outputPath, JSON.stringify(output, null, 2) + "\n", "utf8");
  console.log(`[convert] ${sourcePath} -> ${outputPath}`);
  console.log(
    `[convert] ${leaderboard.length} entries, ${scenarios.length} scenarios, ` +
      `${scenarios.reduce((n, s) => n + s.matches.length, 0)} matches, ${summaryTable.length} summary rows`,
  );
}

main();
