import { Download, ExternalLink } from "lucide-react";
import { benchData } from "@/lib/bench";

/** 白底科研图集元信息（文件名 → 中文说明） */
const PLOT_META: { file: string; title: string; note: string }[] = [
  {
    file: "01_leaderboard.png",
    title: "综合榜单",
    note: "10 条目综合分排名（0–1 归一化）",
  },
  {
    file: "02_rank_heatmap.png",
    title: "排名热力图",
    note: "5 场景 × 3 种子的名次分布",
  },
  {
    file: "03_kill_chart.png",
    title: "击杀统计",
    note: "场均击杀与 killScore 对照",
  },
  {
    file: "04_radar.png",
    title: "五维画像",
    note: "经济/军事/生存/信标/扩张 雷达图",
  },
  {
    file: "05_boxplots.png",
    title: "指标分布",
    note: "关键指标箱线图（异常值可视化）",
  },
  {
    file: "06_summary_table.png",
    title: "汇总统计表",
    note: "场景 × 条目聚合统计（见下方表格）",
  },
  {
    file: "07_overview.png",
    title: "全景总览",
    note: "多指标综合 Overview 图",
  },
];

const CSV_HEADER_LABELS: Record<string, string> = {
  scenario: "场景",
  entry: "条目",
  resources_per_tick: "资源/刻",
  population_peak: "人口峰值",
  survival_median: "存活中位",
  kill_rate: "击杀率",
  first_kill_tick: "首杀刻",
  avg_rank: "平均名次",
};

/** 研究报告区块：白底图集 + CSV 汇总表 */
export function ResearchSection() {
  const { header, rows } = benchData.summaryTable;

  return (
    <section id="research" className="scroll-mt-20">
      <div className="mb-5 flex items-end justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-text-primary">研究报告 · 白底科研图集</h2>
          <p className="mt-1 text-sm text-text-secondary">
            由评测流程自动生成的 7 张科研图 + 汇总 CSV（点击图片可查看原图）
          </p>
        </div>
        <a
          href="/research/06_summary_table.csv"
          download
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border-primary px-3 py-1.5 text-xs font-medium text-text-secondary transition-colors hover:bg-surface-tertiary hover:text-text-primary"
        >
          <Download className="h-3.5 w-3.5" />
          下载 CSV
        </a>
      </div>

      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-3">
        {PLOT_META.map((plot) => (
          <a
            key={plot.file}
            href={`/research/${plot.file}`}
            target="_blank"
            rel="noreferrer"
            className="group overflow-hidden rounded-2xl border border-border-primary bg-surface-primary transition-colors hover:border-accent-primary/50"
          >
            <div className="relative aspect-[4/3] overflow-hidden bg-white">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={`/research/${plot.file}`}
                alt={plot.title}
                className="h-full w-full object-contain transition-transform duration-300 group-hover:scale-[1.03]"
              />
              <span className="absolute right-2 top-2 rounded-lg bg-black/60 p-1.5 text-white opacity-0 transition-opacity group-hover:opacity-100">
                <ExternalLink className="h-3.5 w-3.5" />
              </span>
            </div>
            <div className="flex items-baseline justify-between gap-2 px-4 py-3">
              <span className="text-sm font-medium text-text-primary">{plot.title}</span>
              <span className="text-right text-[11px] text-text-tertiary tnum">{plot.file}</span>
            </div>
            <p className="px-4 pb-3 text-xs text-text-secondary">{plot.note}</p>
          </a>
        ))}
      </div>

      <div className="mt-6 overflow-hidden rounded-2xl border border-border-primary bg-surface-primary">
        <div className="border-b border-border-primary px-4 py-3">
          <h3 className="text-sm font-semibold text-text-primary">06 汇总统计表（场景 × 条目）</h3>
        </div>
        <div className="thin-scroll max-h-96 overflow-auto">
          <table className="w-full border-collapse text-xs">
            <thead className="sticky top-0 z-10 bg-surface-primary">
              <tr className="border-b border-border-primary text-text-tertiary">
                {header.map((col) => (
                  <th key={col} className="whitespace-nowrap px-3 py-2 text-left font-medium">
                    {CSV_HEADER_LABELS[col] ?? col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr
                  key={i}
                  className="border-b border-border-primary/60 last:border-b-0 hover:bg-surface-tertiary/50"
                >
                  {header.map((col) => {
                    const raw = row[col];
                    const empty = raw === "" || raw == null;
                    const numeric = !empty && !Number.isNaN(Number(raw));
                    return (
                      <td
                        key={col}
                        className={`whitespace-nowrap px-3 py-1.5 text-text-primary tnum ${
                          numeric ? "text-right" : ""
                        } ${col === "entry" ? "font-medium" : ""}`}
                      >
                        {empty ? "—" : numeric ? Number(raw).toLocaleString("zh-CN", { maximumFractionDigits: 3 }) : raw}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
