import type { KillEvent } from "@/lib/bench";

const ENTRY_COLORS = [
  "#e8b941",
  "#f4f0eb",
  "#9c9a94",
  "#c9c4ba",
  "#7f7d76",
  "#e0b56e",
  "#b9b4a8",
  "#8f8b82",
];

function colorOf(id: string, roster: string[]): string {
  const index = roster.indexOf(id);
  if (index < 0) return ENTRY_COLORS[ENTRY_COLORS.length - 1];
  return ENTRY_COLORS[index % ENTRY_COLORS.length];
}

/**
 * 击杀时序图（v3.1）：横向时间轴（tick 0 → ticks），每行一个玩家，
 * 每击杀事件一个标记——位置 = 事件 tick，颜色 = 击杀者，悬浮显示
 * 击杀者 → 被击杀者。纯 SVG 服务端渲染（无客户端交互依赖）。
 */
export function KillTimeline({
  events,
  roster,
  ticks,
  maxTicks = 2000,
  height = 240,
}: {
  events: KillEvent[];
  roster: { id: string; label: string }[];
  ticks: number;
  maxTicks?: number;
  height?: number;
}) {
  const totalTicks = Math.max(1, Math.min(maxTicks, ticks));
  const rowHeight = 28;
  const leftPad = 110;
  const rightPad = 16;
  const topPad = 14;
  const bottomPad = 26;
  const width = 720;
  const innerWidth = width - leftPad - rightPad;

  const x = (tick: number) => leftPad + (tick / totalTicks) * innerWidth;
  const sorted = [...events].sort((a, b) => a.tick - b.tick);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} role="img" aria-label="击杀时序图">
      {/* 时间轴 */}
      <line
        x1={leftPad}
        y1={topPad + roster.length * rowHeight}
        x2={width - rightPad}
        y2={topPad + roster.length * rowHeight}
        stroke="var(--color-border-primary, #413d39)"
        strokeWidth={1}
      />
      {/* tick 刻度 */}
      {[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
        const tickLabel = Math.round(totalTicks * fraction);
        const px = leftPad + fraction * innerWidth;
        return (
          <g key={fraction}>
            <line
              x1={px}
              y1={topPad}
              x2={px}
              y2={topPad + roster.length * rowHeight}
              stroke="var(--color-border-faint, #2c2a27)"
              strokeWidth={0.5}
              strokeDasharray="2 4"
            />
            <text
              x={px}
              y={topPad + roster.length * rowHeight + 16}
              textAnchor="middle"
              fontSize={10}
              fill="var(--color-text-tertiary, #8f8b82)"
            >
              {tickLabel}
            </text>
          </g>
        );
      })}

      {/* 玩家行 */}
      {roster.map((player, index) => {
        const y = topPad + index * rowHeight + rowHeight / 2;
        return (
          <g key={player.id}>
            <text
              x={leftPad - 10}
              y={y + 3}
              textAnchor="end"
              fontSize={11}
              fill="var(--color-text-secondary, #9c9a94)"
            >
              {player.label}
            </text>
            <circle cx={leftPad} cy={y} r={2.5} fill={colorOf(player.id, roster.map((r) => r.id))} />
            <line
              x1={leftPad + 5}
              y1={y}
              x2={width - rightPad}
              y2={y}
              stroke="var(--color-border-faint, #2c2a27)"
              strokeWidth={0.5}
            />
          </g>
        );
      })}

      {/* 击杀事件 */}
      {sorted.map((event, index) => {
        const px = x(event.tick);
        const rowIndex = event.victim ? roster.findIndex((r) => r.id === event.victim) : -1;
        const y = rowIndex >= 0 ? topPad + rowIndex * rowHeight + rowHeight / 2 : topPad + 4;
        const killerColor = colorOf(event.destroyedBy[0] ?? "", roster.map((r) => r.id));
        const killerLabel = event.destroyedBy.length === 0
          ? "未知"
          : roster.find((r) => r.id === event.destroyedBy[0])?.label ?? event.destroyedBy[0];
        const victimLabel = event.victim
          ? roster.find((r) => r.id === event.victim)?.label ?? event.victim
          : null;
        return (
          <g key={index}>
            <line x1={px} y1={y - 6} x2={px} y2={y + 6} stroke={killerColor} strokeWidth={1.5} />
            <circle cx={px} cy={y} r={3} fill={killerColor} />
            <title>{`tick ${event.tick}：${killerLabel}${victimLabel ? ` → ${victimLabel}` : ""}（${event.destroyedBy.length} 方参与）`}</title>
          </g>
        );
      })}

      {/* 空态 */}
      {sorted.length === 0 && (
        <text
          x={leftPad + innerWidth / 2}
          y={topPad + (roster.length * rowHeight) / 2}
          textAnchor="middle"
          fontSize={12}
          fill="var(--color-text-tertiary, #8f8b82)"
        >
          本场无核心摧毁
        </text>
      )}
    </svg>
  );
}
