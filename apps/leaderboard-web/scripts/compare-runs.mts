/**
 * 产物对比脚本：两个 results.json（schema arena.bench.report.v3）的榜单/胜方/参数变化。
 *
 * 用法：
 *   npx tsx scripts/compare-runs.mts <old.json> <new.json>
 *
 * 输出：
 *   - 参数对比（players/ticks/seeds/scenarios/rulesVersion）
 *   - 榜单变化表（条目 | 旧 composite | 新 composite | Δ | 名次变化）
 *   - 胜方统计变化（每场景胜场 top3）
 * 主榜 + 对照组（leaderboardControl）合并比较，与站点展示口径一致。
 */
import { readFileSync } from "node:fs";

interface ScoreRow {
  contestantId: string;
  composite: number;
  avgRank: number;
}
interface Report {
  schema: string;
  generatedAt: string;
  params: { players: number; rulesVersion: string; scenarios: string[]; seeds: number[]; ticks: number };
  leaderboard: ScoreRow[];
  leaderboardControl?: ScoreRow[];
  scenarios: { name: string; matches: { winner: string }[] }[];
}

function load(path: string): Report {
  const raw = JSON.parse(readFileSync(path, "utf8")) as Report;
  if (raw.schema !== "arena.bench.report.v3") {
    throw new Error(`Unexpected schema: ${raw.schema} (expect arena.bench.report.v3)`);
  }
  return raw;
}

function mergedRows(report: Report): (ScoreRow & { rank: number })[] {
  return [...report.leaderboard, ...(report.leaderboardControl ?? [])]
    .sort((a, b) => b.composite - a.composite)
    .map((row, index) => ({ ...row, rank: index + 1 }));
}

function winnerCounts(report: Report): Record<string, { wins: number; byScenario: Record<string, number> }> {
  const out: Record<string, { wins: number; byScenario: Record<string, number> }> = {};
  for (const scenario of report.scenarios) {
    for (const match of scenario.matches) {
      if (!match.winner) continue;
      (out[match.winner] ??= { wins: 0, byScenario: {} });
      out[match.winner].wins += 1;
      out[match.winner].byScenario[scenario.name] = (out[match.winner].byScenario[scenario.name] ?? 0) + 1;
    }
  }
  return out;
}

function main(): void {
  const [oldPath, newPath] = process.argv.slice(2);
  if (!oldPath || !newPath) {
    console.error("用法: npx tsx scripts/compare-runs.mts <old.json> <new.json>");
    process.exit(1);
  }
  const oldReport = load(oldPath);
  const newReport = load(newPath);

  console.log("=== 参数对比 ===");
  const p1 = oldReport.params;
  const p2 = newReport.params;
  console.log(`  players : ${p1.players} -> ${p2.players}${p1.players !== p2.players ? "  <== 变化" : ""}`);
  console.log(`  ticks   : ${p1.ticks} -> ${p2.ticks}${p1.ticks !== p2.ticks ? "  <== 变化" : ""}`);
  console.log(`  seeds   : ${p1.seeds.length} -> ${p2.seeds.length}${p1.seeds.length !== p2.seeds.length ? "  <== 变化" : ""}`);
  console.log(`  rules   : ${p1.rulesVersion} -> ${p2.rulesVersion}`);
  console.log(`  场景    : ${p1.scenarios.length} -> ${p2.scenarios.length}`);
  console.log(`  生成时间: ${oldReport.generatedAt} -> ${newReport.generatedAt}`);

  console.log("\n=== 榜单变化（合并对照组，按新 composite 排序）===");
  const oldRows = new Map(mergedRows(oldReport).map((r) => [r.contestantId, r]));
  const newRows = mergedRows(newReport);
  const width = Math.max(...newRows.map((r) => r.contestantId.length), 14);
  console.log(`  ${"条目".padEnd(width)} ${"旧分".padStart(7)} ${"新分".padStart(7)} ${"Δ".padStart(7)}  名次`);
  for (const row of newRows) {
    const old = oldRows.get(row.contestantId);
    const delta = old ? row.composite - old.composite : null;
    const rankShift = old ? `${old.rank}→${row.rank}` : "新进";
    console.log(
      `  ${row.contestantId.padEnd(width)} ${old ? old.composite.toFixed(3).padStart(7) : "—".padStart(7)} ${row.composite.toFixed(3).padStart(7)} ${delta === null ? "—".padStart(7) : (delta >= 0 ? "+" : "") + delta.toFixed(3).padStart(6)}  ${rankShift}`,
    );
  }
  const gone = [...oldRows.keys()].filter((id) => !newRows.some((r) => r.contestantId === id));
  if (gone.length > 0) console.log(`  退出榜单: ${gone.join(", ")}`);

  console.log("\n=== 胜方统计（场次胜场 top5）===");
  const oldWins = winnerCounts(oldReport);
  const newWins = winnerCounts(newReport);
  const allIds = [...new Set([...Object.keys(oldWins), ...Object.keys(newWins)])];
  for (const id of allIds.sort((a, b) => (newWins[b]?.wins ?? 0) - (newWins[a]?.wins ?? 0))) {
    const o = oldWins[id]?.wins ?? 0;
    const n = newWins[id]?.wins ?? 0;
    console.log(`  ${id.padEnd(width)} ${o} 场 -> ${n} 场${n !== o ? `  <== ${n - o > 0 ? "+" : ""}${n - o}` : ""}`);
  }
}

main();
