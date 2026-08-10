"use client";

import { useState } from "react";
import type { BenchmarkScenario } from "@/lib/bench";
import { cn } from "@/lib/utils";
import { KillTimeline } from "@/components/kill-timeline";

/**
 * 击杀时序面板：场景 × 种子切换（Button group），展示选定场次的击杀时间线。
 * 切换按钮用 ghost/brand 变体，键盘可访问。
 */
export function KillTimelinePanel({
  scenarios,
  roster,
  ticks,
}: {
  scenarios: BenchmarkScenario[];
  roster: { id: string; label: string }[];
  ticks: number;
}) {
  const [scenarioIndex, setScenarioIndex] = useState(0);
  const [seedIndex, setSeedIndex] = useState(0);

  const scenario = scenarios[scenarioIndex] ?? scenarios[0];
  const match = scenario?.matches[seedIndex] ?? scenario?.matches[0];
  const seeds = scenario?.matches.map((m) => m.seed) ?? [];

  const switchScenario = (index: number) => {
    setScenarioIndex(index);
    setSeedIndex(0);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="inline-flex rounded-md border border-border bg-secondary/50 p-1">
          {scenarios.map((s, index) => (
            <button
              key={s.name}
              type="button"
              onClick={() => switchScenario(index)}
              className={cn(
                "rounded-sm px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                index === scenarioIndex
                  ? "bg-background text-foreground shadow-xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {s.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span>种子</span>
          <div className="inline-flex gap-1">
            {seeds.map((seed, index) => (
              <button
                key={seed}
                type="button"
                onClick={() => setSeedIndex(index)}
                className={cn(
                  "tnum rounded-sm border px-2 py-0.5 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                  index === seedIndex
                    ? "border-brand/60 bg-brand-soft text-brand"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                {seed}
              </button>
            ))}
          </div>
        </div>
        {match && (
          <span className="text-xs text-muted-foreground">
            {match.winner ? `胜方 ${roster.find((r) => r.id === match.winner)?.label ?? match.winner}` : "平局"}
          </span>
        )}
      </div>
      {match && (
        <KillTimeline events={match.killEvents ?? []} roster={roster} ticks={ticks} />
      )}
    </div>
  );
}
