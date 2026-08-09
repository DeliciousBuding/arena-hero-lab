import Link from "next/link";
import { benchData } from "@/lib/bench";

/** 复刻 arena.ai 的五行 footer（USE CASES / RANKINGS / COMPANY / LEGAL / FOLLOW） */
const FOOTER_COLUMNS: { title: string; links: { label: string; href: string }[] }[] = [
  {
    title: "用例",
    links: [
      { label: "模拟器对抗评测", href: "/#about" },
      { label: "场景 × 条目热图", href: "/#heatmap" },
      { label: "场景对比分析", href: "/#scenarios" },
      { label: "条目详情", href: "/entry/waaiging-agg" },
    ],
  },
  {
    title: "榜单",
    links: [
      { label: "综合分", href: "/leaderboard#dim-composite" },
      { label: "经济", href: "/leaderboard#dim-economy" },
      { label: "击杀", href: "/leaderboard#dim-kills" },
      { label: "场景梯度", href: "/leaderboard#dim-scenario" },
    ],
  },
  {
    title: "项目",
    links: [
      { label: "关于本站", href: "/#about" },
      { label: "热图分析", href: "/#heatmap" },
      { label: "数据转换脚本", href: "/#about" },
      { label: "评测数据源", href: "/#about" },
    ],
  },
  {
    title: "法律",
    links: [
      { label: "使用条款", href: "#" },
      { label: "隐私政策", href: "#" },
      { label: "Cookie 设置", href: "#" },
    ],
  },
  {
    title: "关注",
    links: [
      { label: "GitHub", href: "#" },
      { label: "数据仓库", href: "#" },
      { label: "内部文档", href: "#" },
    ],
  },
];

export function Footer() {
  return (
    <footer className="border-t border-border-primary bg-surface-primary">
      <div className="mx-auto max-w-6xl px-6 py-12">
        <div className="grid grid-cols-2 gap-8 sm:grid-cols-3 lg:grid-cols-5">
          {FOOTER_COLUMNS.map((column) => (
            <div key={column.title}>
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-text-tertiary">
                {column.title}
              </h3>
              <ul className="space-y-2">
                {column.links.map((link) => (
                  <li key={link.label}>
                    <Link
                      href={link.href}
                      className="text-sm text-text-secondary transition-colors hover:text-text-primary"
                    >
                      {link.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="mt-10 flex flex-col gap-2 border-t border-border-primary pt-6 text-xs text-text-tertiary sm:flex-row sm:items-center sm:justify-between">
          <span>© 2026 Arena Hero · 模拟器评测 Leaderboard（本地静态数据）</span>
          <span className="tnum">
            {benchData.schema} · {new Date(benchData.generatedAt).toLocaleString("zh-CN")}
          </span>
        </div>
      </div>
    </footer>
  );
}
