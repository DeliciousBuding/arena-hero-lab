"use client";

import { useMemo, useState } from "react";
import type { BenchmarkMatch, BenchmarkScenario, PerTickSample } from "@/lib/bench";
import { cn } from "@/lib/utils";

const CHART_WIDTH = 760;
const CHART_HEIGHT = 180;
const PAD = { top: 14, right: 12, bottom: 22, left: 42 };

const PLAYER_COLORS = [
  "var(--color-brand)",
  "var(--color-rank-gold)",
  "var(--color-rank-silver)",
  "var(--color-rank-bronze)",
  "var(--color-muted-foreground)",
  "var(--color-border)",
  "#8a7a5a",
  "#5a6a8a",
];

function buildSeries(
  match: BenchmarkMatch,
  roster: readonly { id: string; label: string }[],
  pick: (sample: PerTickSample, playerId: string) => number | null,
): { series: { playerId: string; label: string; color: string; points: [number, number][] }[]; maxY: number } {
  const samples = match.perTickSamples ?? [];
  if (samples.length === 0) return { series: [], maxY: 0 };
  const ticks = samples[samples.length - 1].tick;
  const xOf = (tick: number) => PAD.left + (tick / ticks) * (CHART_WIDTH - PAD.left - PAD.right);
  const series: { playerId: string; label: string; color: string; points: [number, number][] }[] = [];
  let maxY = 1;
  for (const player of roster) {
    const values = samples
      .map((sample) => ({ tick: sample.tick, value: pick(sample, player.id) }))
      .filter((entry): entry is { tick: number; value: number } => entry.value !== null);
    if (values.length === 0) continue;
    for (const entry of values) maxY = Math.max(maxY, entry.value);
    const yOf = (value: number) => CHART_HEIGHT - PAD.bottom - (value / maxY) * (CHART_HEIGHT - PAD.top - PAD.bottom);
    series.push({
      playerId: player.id,
      label: player.label,
      color: PLAYER_COLORS[roster.findIndex((r) => r.id === player.id) % PLAYER_COLORS.length],
      points: values.map((entry) => [xOf(entry.tick), yOf(entry.value)] as [number, number]),
    });
  }
  return { series, maxY };
}

function LineChart({
  title,
  unit,
  samples,
  match,
  roster,
  pick,
  digits,
}: {
  title: string;
  unit: string;
  samples: PerTickSample[] | undefined;
  match: BenchmarkMatch;
  roster: readonly { id: string; label: string }[];
  pick: (sample: PerTickSample, playerId: string) => number | null;
  digits: number;
}) {
  const { series, maxY } = useMemo(
    () => buildSeries(match, roster, pick),
    [match, roster, pick],
  );
  if (!samples || samples.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">该场暂无 per-tick 采样数据（旧产物）。</p>
    );
  }
  const gridLines = 4;
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-sm">
        <span className="font-medium text-foreground">{title}</span>
        <span className="text-[11px] text-muted-foreground tnum">max {maxY.toFixed(digits)} {unit}</span>
      </div>
      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
          className="h-auto w-full min-w-[560px]"
          role="img"
          aria-label={`${title}曲线（每 50 tick 采样）`}
        >
          {Array.from({ length: gridLines + 1 }, (_, i) => {
            const y = PAD.top + (i / gridLines) * (CHART_HEIGHT - PAD.top - PAD.bottom);
            const value = maxY - (i / gridLines) * maxY;
            return (
              <g key={i}>
                <line
                  x1={PAD.left}
                  y1={y}
                  x2={CHART_WIDTH - PAD.right}
                  y2={y}
                  stroke="var(--color-border-faint)"
                  strokeWidth={1}
                />
                <text
                  x={PAD.left - 6}
                  y={y + 3}
                  textAnchor="end"
                  className="fill-muted-foreground"
                  fontSize={10}
                >
                  {value.toFixed(digits)}
                </text>
              </g>
            );
          })}
          {series.map((s) => (
            <polyline
              key={s.playerId}
              points={s.points.map((p) => p.join(",")).join(" ")}
              fill="none"
              stroke={s.color}
              strokeWidth={1.5}
              opacity={0.9}
            />
          ))}
          <line
            x1={PAD.left}
            y1={CHART_HEIGHT - PAD.bottom}
            x2={CHART_WIDTH - PAD.right}
            y2={CHART_HEIGHT - PAD.bottom}
            stroke="var(--color-border)"
            strokeWidth={1}
          />
          <text
            x={(PAD.left + CHART_WIDTH - PAD.right) / 2}
            y={CHART_HEIGHT - 5}
            textAnchor="middle"
            className="fill-muted-foreground"
            fontSize={10}
          >
            tick（每 50 采样）
          </text>
        </svg>
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
        {series.map((s) => (
          <span key={s.playerId} className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
            <span className="h-1.5 w-3 rounded-full" style={{ backgroundColor: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}

/** 资源/人口时序曲线面板（client component；场景 × 种子切换，SVG 折线） */
export function ResourceTimelinePanel({
  scenarios,
  roster,
  ticks,
}: {
  scenarios: readonly BenchmarkScenario[];
  roster: readonly { id: string; label: string }[];
  ticks: number;
}) {
  const [scenarioName, setScenarioName] = useState(scenarios[0]?.name ?? "");
  const scenario = scenarios.find((s) => s.name === scenarioName) ?? scenarios[0];
  const seeds = scenario?.matches.map((m) => m.seed) ?? [];
  const [seedIndex, setSeedIndex] = useState(0);
  const match: BenchmarkMatch | undefined = scenario?.matches[seedIndex] ?? scenario?.matches[0];
  const pickResources = (sample: PerTickSample, playerId: string) =>
    sample.players[playerId]?.resources ?? null;
  const pickPopulation = (sample: PerTickSample, playerId: string) =>
    sample.players[playerId]?.population ?? null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex flex-wrap gap-1.5">
          {scenarios.map((s) => (
            <button
              key={s.name}
              type="button"
              onClick={() => {
                setScenarioName(s.name);
                setSeedIndex(0);
              }}
              className={cn(
                "rounded-full border px-3 py-1 text-xs transition-colors",
                s.name === scenario?.name
                  ? "border-foreground/40 bg-secondary text-foreground"
                  : "border-border bg-transparent text-muted-foreground hover:border-foreground/25 hover:text-foreground",
              )}
            >
              {s.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {seeds.map((seed, index) => (
            <button
              key={seed}
              type="button"
              onClick={() => setSeedIndex(index)}
              className={cn(
                "h-7 min-w-7 rounded-full border px-2 text-xs tnum transition-colors",
                index === seedIndex
                  ? "border-brand/60 bg-brand-soft text-brand"
                  : "border-border bg-transparent text-muted-foreground hover:border-foreground/25 hover:text-foreground",
              )}
            >
              {seed}
            </button>
          ))}
        </div>
        <span className="text-xs text-muted-foreground tnum">
          {scenario?.label} · seed {match?.seed} · {ticks} ticks
        </span>
      </div>

      {match === undefined || match.perTickSamples === undefined || match.perTickSamples.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          该场暂无 per-tick 采样数据（旧产物未含 v3.1 时序；需 v3.1 起重跑评测）。
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
          <LineChart
            title="资源曲线"
            unit="res"
            samples={match.perTickSamples}
            match={match}
            roster={roster}
            pick={pickResources}
            digits={1}
          />
          <LineChart
            title="人口曲线"
            unit="units"
            samples={match.perTickSamples}
            match={match}
            roster={roster}
            pick={pickPopulation}
            digits={0}
          />
        </div>
      )}
    </div>
  );
}
