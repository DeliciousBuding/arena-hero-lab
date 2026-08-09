import { ArrowRight, Coins, Route, Swords, Trophy } from "lucide-react";
import type { ComponentType } from "react";
import Link from "next/link";
import type { Dimension } from "@/lib/dimensions";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LeaderboardTable } from "./leaderboard-table";

export const DIMENSION_ICONS: Record<string, ComponentType<{ className?: string }>> = {
  trophy: Trophy,
  swords: Swords,
  route: Route,
  coins: Coins,
};

/** 维度卡片：lucide 图标 + serif 标题 + LeaderboardTable + "查看全部"链接 */
export function DimensionCard({ dimension }: { dimension: Dimension }) {
  const Icon = DIMENSION_ICONS[dimension.icon] ?? Trophy;
  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-secondary/50 text-brand">
            <Icon className="h-4 w-4" />
          </span>
          <div className="min-w-0">
            <CardTitle className="flex items-baseline gap-2 text-base">
              {dimension.title}
              <span className="font-sans text-xs font-normal text-muted-foreground">
                {dimension.enTitle}
              </span>
            </CardTitle>
            <p className="mt-1 max-w-md text-xs leading-relaxed text-muted-foreground">
              {dimension.description}
            </p>
          </div>
        </div>
        <Button asChild variant="ghost" size="sm" className="shrink-0 gap-1 text-xs text-brand hover:bg-brand-soft">
          <Link href={`/leaderboard?dim=${dimension.id}`}>
            查看全部
            <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </Button>
      </CardHeader>
      <CardContent className="px-2 pb-3">
        <LeaderboardTable rows={dimension.rows} valueLabel={dimension.valueLabel} />
      </CardContent>
    </Card>
  );
}
