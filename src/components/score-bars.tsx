import { Card, CardContent } from "@/components/ui/card";

/** 单条目四维分数（0–1 归一化）。 */
export interface ScoreBarEntry {
  id: string;
  label: string;
  scores: { key: string; label: string; value: number }[];
}

const CHART_WIDTH = 920;
const CHART_HEIGHT = 360;
const PAD = { top: 24, right: 16, bottom: 56, left: 46 };
const GROUP_GAP = 26;
const BAR_GAP = 5;

const SCORE_COLORS = [
  "var(--color-destructive)",
  "var(--color-brand)",
  "var(--color-success)",
  "var(--color-muted-foreground)",
];

/**
 * 四维分数分组条形图（纯 SVG 手绘，服务端可执行）：
 * x 轴 = 条目，每组 4 根 bar（击杀/名次/经济/生存，0–100% 归一化）。
 * 颜色取自语义 token（destructive 红 / brand 品牌黄 / success 绿 / muted 灰）。
 */
export function ScoreBars({ entries }: { entries: ScoreBarEntry[] }) {
  if (entries.length === 0) return null;
  const dims = entries[0].scores;

  const groupWidth =
    (CHART_WIDTH - PAD.left - PAD.right - GROUP_GAP * (entries.length - 1)) / entries.length;
  const barWidth = (groupWidth - BAR_GAP * (dims.length - 1)) / dims.length;
  const plotHeight = CHART_HEIGHT - PAD.top - PAD.bottom;

  const yOf = (value: number) => PAD.top + plotHeight * (1 - Math.max(0, Math.min(1, value)));

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-1">
        {dims.map((d, i) => (
          <span key={d.key} className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <span
              className="h-2.5 w-2.5 rounded-sm"
              style={{ backgroundColor: SCORE_COLORS[i % SCORE_COLORS.length] }}
            />
            {d.label}
          </span>
        ))}
        <span className="text-xs text-muted-foreground">
          分数为 0–1 归一化（v3 composite 分项）· 悬浮查看精确值
        </span>
      </div>

      <Card className="p-2">
        <CardContent className="thin-scroll overflow-x-auto p-0">
          <svg
            viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
            width="100%"
            className="min-w-[720px]"
            role="img"
            aria-label="条目四维分数对比条形图"
          >
            {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
              <g key={tick}>
                <line
                  x1={PAD.left}
                  x2={CHART_WIDTH - PAD.right}
                  y1={yOf(tick)}
                  y2={yOf(tick)}
                  stroke="var(--color-border-faint)"
                  strokeWidth={1}
                />
                <text
                  x={PAD.left - 8}
                  y={yOf(tick) + 3}
                  textAnchor="end"
                  style={{ fontSize: 10, fill: "var(--color-muted-foreground)" }}
                >
                  {Math.round(tick * 100)}%
                </text>
              </g>
            ))}

            {entries.map((entry, gi) => {
              const gx = PAD.left + gi * (groupWidth + GROUP_GAP);
              const cx = gx + groupWidth / 2;
              return (
                <g key={entry.id}>
                  {entry.scores.map((score, si) => {
                    const x = gx + si * (barWidth + BAR_GAP);
                    const y = yOf(score.value);
                    const h = PAD.top + plotHeight - y;
                    return (
                      <g key={score.key}>
                        <rect
                          x={x}
                          y={y}
                          width={barWidth}
                          height={Math.max(h, 1)}
                          rx={3}
                          fill={SCORE_COLORS[si % SCORE_COLORS.length]}
                          fillOpacity={0.85}
                        >
                          <title>
                            {`${entry.label} · ${score.label}: ${(score.value * 100).toFixed(1)}%`}
                          </title>
                        </rect>
                      </g>
                    );
                  })}
                  <text
                    x={cx}
                    y={CHART_HEIGHT - PAD.bottom + 20}
                    textAnchor="middle"
                    style={{ fontSize: 11, fill: "var(--color-foreground)" }}
                  >
                    {entry.label}
                  </text>
                  <text
                    x={cx}
                    y={CHART_HEIGHT - PAD.bottom + 34}
                    textAnchor="middle"
                    style={{ fontSize: 9.5, fill: "var(--color-muted-foreground)" }}
                  >
                    {entry.id}
                  </text>
                </g>
              );
            })}
          </svg>
        </CardContent>
      </Card>
    </div>
  );
}
