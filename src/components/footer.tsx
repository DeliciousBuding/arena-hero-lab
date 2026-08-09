import Link from "next/link";
import { benchData } from "@/lib/bench";

/** 精简 footer：数据源仓库 + 版本 + schema，无废话。 */
export function Footer() {
  return (
    <footer className="hairline">
      <div className="mx-auto flex max-w-6xl flex-col gap-2 px-4 py-6 text-xs text-text-tertiary sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div className="flex items-center gap-3">
          <span>© 2026 Arena Hero</span>
          <span>·</span>
          <Link
            href="https://github.com/DeliciousBuding/arena-hero-leaderboard"
            target="_blank"
            rel="noreferrer"
            className="link-hover text-text-secondary"
          >
            arena-hero-leaderboard
          </Link>
          <span>·</span>
          <Link
            href="https://github.com/DeliciousBuding/arena"
            target="_blank"
            rel="noreferrer"
            className="link-hover text-text-secondary"
          >
            数据源 arena
          </Link>
        </div>
        <span className="tnum">
          {benchData.schema} · {new Date(benchData.generatedAt).toLocaleString("zh-CN")}
        </span>
      </div>
    </footer>
  );
}
