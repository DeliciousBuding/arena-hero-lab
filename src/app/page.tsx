import { Database, FlaskConical } from "lucide-react";
import { LeaderboardView } from "@/components/leaderboard-view";
import { ResearchSection } from "@/components/research-section";
import { benchData } from "@/lib/bench";
import { dimensions } from "@/lib/dimensions";

export default function HomePage() {
  const { params } = benchData;
  const generatedDate = new Date(benchData.generatedAt).toLocaleString("zh-CN");

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:py-10">
      {/* 页头：项目说明（Leaderboard Overview 等效区） */}
      <header className="mb-8">
        <div className="mb-3 inline-flex items-center gap-1.5 rounded-full border border-accent-primary/30 bg-accent-soft px-3 py-1 text-xs font-medium text-accent-primary">
          <FlaskConical className="h-3.5 w-3.5" />
          arena-hero 模拟器评测 v2 · Leaderboard Overview
        </div>
        <h1 className="text-2xl font-bold tracking-tight text-text-primary sm:text-3xl">
          评测榜单总览
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-text-secondary">
          10 个参赛条目在 5 个场景 × 3 个种子共 15 场对局中的综合表现：
          综合分 / 击杀 / 生存 / 场景梯度 / 五维画像 / 生态 六大维度。
          所有数字均来自评测产物 <span className="tnum text-text-tertiary">arena.bench.report.v2</span>，无任何 mock 数据。
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
            静态数据 · 本地构建
          </span>
        </div>
      </header>

      {/* 六大维度卡片 */}
      <LeaderboardView dimensions={dimensions} />

      {/* 研究报告：白底图集 + CSV 汇总表 */}
      <div className="mt-14">
        <ResearchSection />
      </div>

      {/* 关于本站 */}
      <section id="about" className="mt-14 scroll-mt-20">
        <h2 className="text-xl font-semibold text-text-primary">关于本站</h2>
        <div className="mt-4 space-y-3 rounded-2xl border border-border-primary bg-surface-primary p-5 text-sm leading-relaxed text-text-secondary">
          <p>
            <span className="font-medium text-text-primary">项目</span>
            ：arena-hero 模拟器评测 v2 的公开榜单。将评测产物（
            <span className="tnum">results.json</span>，schema{" "}
            <span className="tnum">{benchData.schema}</span>）转换为静态页面，
            前端视觉参考 arena.ai/leaderboard 并针对数据展示做了优化（排序 / 搜索 / 明暗主题 / 移动端）。
          </p>
          <p>
            <span className="font-medium text-text-primary">数据流</span>
            ：<span className="tnum">scripts/convert.mts</span> 读取{" "}
            <span className="tnum">{benchData.source}</span> 下的 results.json，
            聚合生成 <span className="tnum">src/data/bench.json</span>（构建时静态引入，无后端）。
            刷新数据：<code className="rounded bg-surface-tertiary px-1.5 py-0.5 tnum">node scripts/convert.mts</code>
          </p>
          <p>
            <span className="font-medium text-text-primary">综合分公式</span>
            ：rankScore×0.6 + killScore×0.2 + survivalScore×0.2（由本数据集最小二乘拟合验证，
            最大误差 8e-16）。
          </p>
        </div>
      </section>
    </div>
  );
}
