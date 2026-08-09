import { ArrowRight, Database } from "lucide-react";
import Link from "next/link";
import { Heatmap } from "@/components/heatmap";
import { OverallTable } from "@/components/overall-table";
import { ScenarioComparison } from "@/components/scenario-comparison";
import { SectionHeader } from "@/components/section-header";
import { benchData, contestantOf } from "@/lib/bench";

/** 前三名领奖台卡片（金/银/铜） */
function PodiumCard({
  rank,
  label,
  sub,
  medal,
  glow,
}: {
  rank: number;
  label: string;
  sub: string;
  medal: string;
  glow?: boolean;
}) {
  return (
    <div
      className={`card relative flex items-center gap-3 p-4 transition-shadow hover:shadow-card-hover ${
        glow ? "shadow-glow" : ""
      }`}
    >
      <div
        className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-base font-bold ${
          medal === "gold"
            ? "bg-rank-gold/15 text-rank-gold"
            : medal === "silver"
              ? "bg-rank-silver/15 text-rank-silver"
              : "bg-rank-bronze/15 text-rank-bronze"
        }`}
      >
        {rank}
      </div>
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-text-primary">{label}</div>
        <div className="text-xs text-text-secondary tnum">{sub}</div>
      </div>
      <div className="ml-auto shrink-0">
        <div
          className={`h-1.5 w-16 rounded-full bg-gradient-accent ${
            medal === "gold" ? "opacity-100" : "opacity-40"
          }`}
        />
      </div>
    </div>
  );
}

export default function HomePage() {
  const { params } = benchData;
  const generatedDate = new Date(benchData.generatedAt).toLocaleString("zh-CN");
  const top3 = benchData.leaderboard.slice(0, 3);

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:py-10">
      {/* ===== Hero（对齐 arena.ai/leaderboard：细体标题 + 简洁副标） ===== */}
      <header className="mb-10">
        <h1 className="text-3xl font-light tracking-tight text-text-primary sm:text-4xl">
          Leaderboard Overview
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-text-secondary">
          {params.players} 个参赛条目在 {params.scenarios.length} 个场景 × {params.seeds.length} 个种子共{" "}
          {benchData.scenarios.reduce((n, s) => n + s.matches.length, 0)} 场对局中的综合表现。
          所有数字均来自评测产物 <span className="tnum text-text-tertiary">{benchData.schema}</span>，
          图表全部由前端 React + SVG 渲染，无后端、无 mock 数据。
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          {[
            `${params.players} 参赛者`,
            `${params.scenarios.length} 场景 × ${params.seeds.length} 种子`,
            `${params.ticks} ticks/场`,
            `rules ${params.rulesVersion}`,
            `生成于 ${generatedDate}`,
          ].map((chip) => (
            <span
              key={chip}
              className="rounded-lg border border-border-primary bg-surface-primary px-2.5 py-1 text-text-secondary tnum"
            >
              {chip}
            </span>
          ))}
          <span className="inline-flex items-center gap-1 rounded-lg border border-border-primary bg-surface-primary px-2.5 py-1 text-text-secondary">
            <Database className="h-3 w-3" />
            静态数据 · GitHub Pages
          </span>
        </div>
      </header>

      {/* ===== 前三名领奖台 ===== */}
      <section className="mb-10 grid grid-cols-1 gap-4 sm:grid-cols-3">
        {top3.map((row) => {
          const medal = row.rank === 1 ? "gold" : row.rank === 2 ? "silver" : "bronze";
          return (
            <Link key={row.contestantId} href={`/entry/${row.contestantId}`}>
              <PodiumCard
                rank={row.rank}
                label={contestantOf(row.contestantId)?.label ?? row.contestantId}
                sub={`综合分 ${(row.composite * 100).toFixed(1)}% · 均排 ${row.avgRank.toFixed(2)}`}
                medal={medal}
                glow={row.rank === 1}
              />
            </Link>
          );
        })}
      </section>

      {/* ===== 综合排名（增强榜单表） ===== */}
      <section className="mb-12">
        <SectionHeader
          id="rankings"
          title="综合排名"
          enTitle="Overall Rankings"
          description="按综合分排名的全量榜单。点击列头可按 综合分 / 平均名次 / 击杀 / 经济 排序；琥珀色底纹行与“对照组”标签为内置对照策略。"
          action={
            <Link
              href="/leaderboard"
              className="inline-flex items-center gap-1 rounded-lg border border-border-primary px-3 py-1.5 text-xs font-medium text-text-secondary transition-colors hover:bg-surface-tertiary hover:text-text-primary"
            >
              全部维度榜单
              <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          }
        />
        <div className="card p-4 sm:p-5">
          <OverallTable rows={benchData.leaderboard} limit={10} />
        </div>
      </section>

      {/* ===== 场景 × 条目热图 ===== */}
      <section className="mb-12">
        <SectionHeader
          id="heatmap"
          title="场景 × 条目热图"
          enTitle="Scenario × Entry Heatmap"
          description="每个单元格为该条目在该场景下的指标值（相对色阶）。可在 资源/刻、击杀率、存活 之间切换指标。"
        />
        <Heatmap />
      </section>

      {/* ===== 场景对比 ===== */}
      <section className="mb-12">
        <SectionHeader
          id="scenarios"
          title="场景对比"
          enTitle="Scenario Comparison"
          description="每个场景单独成卡：条目按该场景平均名次排序，资源/刻与人口峰值以横向条对比。"
        />
        <ScenarioComparison />
      </section>

      {/* ===== 关于本站 ===== */}
      <section id="about" className="mt-14 scroll-mt-20">
        <SectionHeader title="关于本站" enTitle="About" />
        <div className="card space-y-3 p-5 text-sm leading-relaxed text-text-secondary">
          <p>
            <span className="font-medium text-text-primary">项目</span>
            ：arena-hero 模拟器评测 v3 的公开榜单，静态导出部署于 GitHub Pages。评测产物（
            <span className="tnum">results.json</span>，schema{" "}
            <span className="tnum">{benchData.schema}</span>）经转换脚本生成{" "}
            <span className="tnum">bench.json</span> 后全部在浏览器端渲染——热图 / 雷达 /
            榜单 / 对比条均为 React + SVG，不再依赖任何后端与 Python 出图。
          </p>
          <p>
            <span className="font-medium text-text-primary">数据流</span>
            ：<span className="tnum">npx tsx scripts/convert.mts &lt;results.json&gt;</span>{" "}
            读取评测产物（当前样例：<span className="tnum">{benchData.source}</span>），
            确定性裁剪字段并生成 <span className="tnum">src/data/bench.json</span>（构建时静态引入，无后端）。
            全量评测完成后用同一命令重跑即可覆盖刷新数据，随后 <span className="tnum">pnpm build</span>。
          </p>
          <p>
            <span className="font-medium text-text-primary">字段说明</span>
            ：v3 榜单分项为 composite / rankScore / killScore / economyScore；
            survivalScore 与 survivalMedian 为 v2 兼容字段（v3 恒 1.0，已弃用，前端不再展示）。
            内置对照组（builtin，如 ts-aggressive / ts-safety）以琥珀色标注。
          </p>
        </div>
      </section>
    </div>
  );
}
