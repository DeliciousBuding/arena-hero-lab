"use client";

import { useState } from "react";
import { benchData, contestantOf, type BenchmarkScenario, type ScenarioEntryStat } from "@/lib/bench";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";

type MetricKey = "resourcesPerTick" | "killRate" | "avgRank";

const METRICS: {
  key: MetricKey;
  label: string;
  unit: string;
  digits: number;
  note: string;
  /** 数值越小越好（如名次）→ 色阶反转（小值深色）。 */
  invert?: boolean;
}[] = [
  { key: "resourcesPerTick", label: "资源/刻", unit: "res/tick", digits: 3, note: "场景级平均资源采集速率" },
  { key: "killRate", label: "击杀率", unit: "kill/match", digits: 2, note: "场景级场均击杀" },
  { key: "avgRank", label: "平均名次", unit: "rank", digits: 2, note: "场景级平均名次（越小越好，色阶反转）", invert: true },
];

const CELL_W = 66;
const CELL_H = 44;
const HEADER_H = 56;
const ROW_LABEL_W = 132;
const PAD = 12;
const LEGEND_H = 30;

/** 场景 × 条目热图：资源/杀率/存活 三选切换，纯 SVG 渲染。颜色全取设计 token。 */
export function Heatmap() {
  const [metricKey, setMetricKey] = useState<MetricKey>("resourcesPerTick");
  const metric = METRICS.find((m) => m.key === metricKey)!;

  const entries = [...benchData.leaderboard].sort((a, b) => a.rank - b.rank);
  const scenarios = benchData.scenarioOrder
    .map((name) => benchData.scenarios.find((s) => s.name === name))
    .filter((s): s is BenchmarkScenario => Boolean(s));

  const values: (number | null)[][] = scenarios.map((scenario) =>
    entries.map((entry) => {
      const stat: ScenarioEntryStat | null | undefined = scenario.perEntry[entry.contestantId];
      return stat?.[metricKey] ?? null;
    }),
  );
  const flat = values.flat().filter((v): v is number => v != null);
  const min = flat.length ? Math.min(...flat) : 0;
  const max = flat.length ? Math.max(...flat) : 0;
  const degenerate = flat.length > 0 && max - min < 1e-9;

  const stepOf = (v: number | null): number => {
    if (v == null) return 0;
    if (degenerate) return 3;
    const t = max === min ? 0 : (v - min) / (max - min);
    const normalized = metric.invert === true ? 1 - t : t;
    return Math.min(6, Math.max(1, 1 + Math.round(normalized * 5)));
  };

  const fmt = (v: number | null): string => {
    if (v == null) return "—";
    return v.toFixed(metric.digits);
  };

  const width = ROW_LABEL_W + entries.length * CELL_W + PAD * 2;
  const height = HEADER_H + scenarios.length * CELL_H + LEGEND_H + PAD * 2;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-md border border-border bg-secondary/50 p-1">
          {METRICS.map((m) => (
            <button
              key={m.key}
              type="button"
              onClick={() => setMetricKey(m.key)}
              title={m.note}
              className={cn(
                "rounded-sm px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                metricKey === m.key
                  ? "bg-background text-foreground shadow-xs"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {m.label}
            </button>
          ))}
        </div>
        <span className="text-xs text-muted-foreground">
          {metric.note}
          {degenerate ? "（当前所有数值相同，色块为恒定值）" : ` · 色阶范围 ${fmt(min)} ~ ${fmt(max)}`}
        </span>
      </div>

      <Card className="p-2">
        <CardContent className="thin-scroll overflow-x-auto p-0">
          <svg
            viewBox={`0 0 ${width} ${height}`}
            width="100%"
            role="img"
            aria-label={`场景 × 条目${metric.label}热图`}
            className="min-w-[760px]"
          >
            {entries.map((entry, col) => {
              const contestant = contestantOf(entry.contestantId);
              const x = ROW_LABEL_W + PAD + col * CELL_W + CELL_W / 2;
              return (
                <g key={entry.contestantId}>
                  <text
                    x={x}
                    y={HEADER_H - 34}
                    textAnchor="middle"
                    className="tnum"
                    style={{ fontSize: 11, fill: "var(--color-muted-foreground)" }}
                  >
                    {entry.rank}. {entry.contestantId}
                  </text>
                  <text
                    x={x}
                    y={HEADER_H - 18}
                    textAnchor="middle"
                    style={{ fontSize: 10, fill: "var(--color-muted-foreground)" }}
                  >
                    {contestant?.kind === "builtin" ? "对照组" : "agent"}
                  </text>
                  {contestant?.kind === "builtin" ? (
                    <circle cx={x} cy={HEADER_H - 4} r={2.5} fill="var(--color-rank-gold)" />
                  ) : null}
                </g>
              );
            })}

            {scenarios.map((scenario, row) => {
              const y = HEADER_H + row * CELL_H;
              return (
                <g key={scenario.name}>
                  <text
                    x={ROW_LABEL_W - 10}
                    y={y + CELL_H / 2 + 4}
                    textAnchor="end"
                    style={{ fontSize: 12, fill: "var(--color-foreground)" }}
                  >
                    {scenario.label}
                  </text>
                  {entries.map((entry, col) => {
                    const v = values[row][col];
                    const step = stepOf(v);
                    const x = ROW_LABEL_W + PAD + col * CELL_W;
                    const cx = x + CELL_W / 2;
                    const cy = y + CELL_H / 2;
                    return (
                      <g key={entry.contestantId}>
                        <rect
                          x={x + 3}
                          y={y + 3}
                          width={CELL_W - 6}
                          height={CELL_H - 6}
                          rx={7}
                          fill={`var(--color-heat-${step})`}
                          stroke="transparent"
                          strokeWidth={1.5}
                          className="heat-cell"
                        >
                          <title>
                            {`${scenario.label} · ${contestantOf(entry.contestantId)?.label ?? entry.contestantId}\n${metric.label}: ${fmt(v)} ${metric.unit}`}
                          </title>
                        </rect>
                        {v != null ? (
                          <text
                            x={cx}
                            y={cy - 2}
                            textAnchor="middle"
                            className="tnum"
                            style={{ fontSize: 12.5, fontWeight: 600, fill: `var(--color-heat-text-${step})` }}
                          >
                            {fmt(v)}
                          </text>
                        ) : null}
                        <text
                          x={cx}
                          y={cy + 13}
                          textAnchor="middle"
                          style={{ fontSize: 9.5, fill: `var(--color-heat-text-${step})`, opacity: 0.75 }}
                        >
                          {v == null ? "未参赛" : metric.unit}
                        </text>
                      </g>
                    );
                  })}
                </g>
              );
            })}

            <g>
              <defs>
                <linearGradient id="heat-legend" x1="0" y1="0" x2="1" y2="0">
                  {[1, 2, 3, 4, 5, 6].map((s) => (
                    <stop key={s} offset={`${((s - 1) / 5) * 100}%`} stopColor={`var(--color-heat-${s})`} />
                  ))}
                  <stop offset="100%" stopColor="var(--color-heat-6)" />
                </linearGradient>
              </defs>
              <text x={ROW_LABEL_W + PAD} y={height - LEGEND_H + 6} style={{ fontSize: 10, fill: "var(--color-muted-foreground)" }}>
                低
              </text>
              <rect
                x={ROW_LABEL_W + PAD + 20}
                y={height - LEGEND_H}
                width={Math.max(80, entries.length * CELL_W - 40)}
                height={8}
                rx={4}
                fill="url(#heat-legend)"
              />
              <text
                x={ROW_LABEL_W + PAD + 20 + Math.max(80, entries.length * CELL_W - 40) + 6}
                y={height - LEGEND_H + 6}
                style={{ fontSize: 10, fill: "var(--color-muted-foreground)" }}
              >
                高
              </text>
              <text x={width - PAD} y={height - LEGEND_H + 6} textAnchor="end" style={{ fontSize: 10, fill: "var(--color-muted-foreground)" }}>
                单元格悬浮查看详情 · 列头为总体名次
              </text>
            </g>
          </svg>
        </CardContent>
      </Card>
    </div>
  );
}
