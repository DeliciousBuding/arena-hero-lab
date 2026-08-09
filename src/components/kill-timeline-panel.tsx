"use client";

import { useState } from "react";
import type { BenchmarkScenario } from "@/lib/bench";
import { KillTimeline } from "@/components/kill-timeline";

/**
 * 击杀时序面板：场景 × 种子切换（客户端交互），展示选定场次的
 * 核心摧毁时间线（tick 轴 × 玩家行，标记颜色 = 击杀者）。
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
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="flex flex-wrap gap-1.5">
          {scenarios.map((s, index) => (
            <button
              key={s.name}
              type="button"
              onClick={() => switchScenario(index)}
              className={`border px-2.5 py-1 text-xs transition-colors ${
                index === scenarioIndex
                  ? "border-text-primary bg-surface-tertiary text-text-primary"
                  : "border-border-primary text-text-tertiary hover:text-text-secondary"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5 text-xs text-text-tertiary">
          <span>种子</span>
          {seeds.map((seed, index) => (
            <button
              key={seed}
              type="button"
              onClick={() => setSeedIndex(index)}
              className={`tnum border px-2 py-0.5 transition-colors ${
                index === seedIndex
                  ? "border-accent-primary/60 text-accent-primary"
                  : "border-border-primary text-text-tertiary hover:text-text-secondary"
              }`}
            >
              {seed}
            </button>
          ))}
        </div>
        {match && (
          <span className="text-xs text-text-tertiary">
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
