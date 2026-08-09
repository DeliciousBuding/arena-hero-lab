import { ArrowRight, Coins, Radar, Route, Shield, Swords, Trophy } from "lucide-react";
import Link from "next/link";
import type { ComponentType } from "react";
import type { Dimension } from "@/lib/dimensions";
import { LeaderboardTable } from "./leaderboard-table";

export const DIMENSION_ICONS: Record<string, ComponentType<{ className?: string }>> = {
  trophy: Trophy,
  swords: Swords,
  shield: Shield,
  route: Route,
  radar: Radar,
  coins: Coins,
};

/** 单个维度卡片：标题 + 说明 + 榜单表 + “查看全部”链接 */
export function DimensionCard({ dimension }: { dimension: Dimension }) {
  const Icon = DIMENSION_ICONS[dimension.icon] ?? Trophy;
  return (
    <section className="flex flex-col border-b border-border-primary">
      <header className="flex items-start justify-between gap-3 border-b border-border-faint px-1 py-4">
        <div className="flex items-start gap-3">
          <span className="pt-0.5 text-accent-primary">
            <Icon className="h-4 w-4" />
          </span>
          <div>
            <h2 className="flex items-baseline gap-2 text-base font-semibold text-text-primary">
              {dimension.title}
              <span className="text-xs font-normal text-text-tertiary">{dimension.enTitle}</span>
            </h2>
            <p className="mt-0.5 max-w-md text-xs leading-relaxed text-text-secondary">
              {dimension.description}
            </p>
          </div>
        </div>
        <Link
          href={`/leaderboard?dim=${dimension.id}`}
          className="inline-flex shrink-0 items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium text-accent-primary transition-colors hover:bg-accent-soft"
        >
          查看全部
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </header>
      <div className="flex-1 px-5 py-3">
        <LeaderboardTable rows={dimension.rows} valueLabel={dimension.valueLabel} />
      </div>
    </section>
  );
}
