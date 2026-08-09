import type { ProfileDimensionKey } from "@/lib/bench";
import { PROFILE_DIM_LABELS } from "@/lib/bench";

export interface RadarValue {
  key: ProfileDimensionKey;
  value: number;
}

/**
 * 五维雷达图：纯 SVG 渲染（服务端可执行）。
 * 归一化 0–1 值，网格环 25/50/75/100%，多边形为五维画像。
 */
export function RadarChart({
  values,
  size = 280,
}: {
  values: RadarValue[];
  size?: number;
}) {
  const center = size / 2;
  const radius = size / 2 - 46;
  const angleStep = (Math.PI * 2) / values.length;

  const pointAt = (value: number, index: number) => {
    const angle = -Math.PI / 2 + index * angleStep;
    const r = radius * Math.max(0, Math.min(1, value));
    return { x: center + r * Math.cos(angle), y: center + r * Math.sin(angle) };
  };

  const polygonPoints = values
    .map((v, i) => {
      const p = pointAt(v.value, i);
      return `${p.x},${p.y}`;
    })
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${size} ${size}`}
      width={size}
      height={size}
      role="img"
      aria-label="五维画像雷达图"
      className="mx-auto"
    >
      {/* 网格环 */}
      {[0.25, 0.5, 0.75, 1].map((ring) => (
        <polygon
          key={ring}
          points={values
            .map((_, i) => {
              const p = pointAt(ring, i);
              return `${p.x},${p.y}`;
            })
            .join(" ")}
          fill="none"
          stroke="var(--border-primary)"
          strokeWidth={1}
        />
      ))}
      {/* 轴线 */}
      {values.map((_, i) => {
        const p = pointAt(1, i);
        return (
          <line
            key={i}
            x1={center}
            y1={center}
            x2={p.x}
            y2={p.y}
            stroke="var(--border-primary)"
            strokeWidth={1}
          />
        );
      })}
      {/* 数据多边形 */}
      <polygon
        points={polygonPoints}
        fill="var(--accent-soft)"
        stroke="var(--accent-primary)"
        strokeWidth={2}
        strokeLinejoin="round"
      />
      {/* 顶点 + 轴标签 */}
      {values.map((v, i) => {
        const p = pointAt(v.value, i);
        const label = pointAt(1.16, i);
        return (
          <g key={v.key}>
            <circle cx={p.x} cy={p.y} r={3} fill="var(--accent-primary)" />
            <text
              x={label.x}
              y={label.y}
              textAnchor="middle"
              dominantBaseline="middle"
              className="fill-current"
              style={{ color: "var(--text-secondary)", fontSize: 11 }}
            >
              {PROFILE_DIM_LABELS[v.key]}
            </text>
            <text
              x={label.x}
              y={label.y + 13}
              textAnchor="middle"
              dominantBaseline="middle"
              className="tnum"
              style={{ color: "var(--text-tertiary)", fontSize: 10 }}
            >
              {v.value.toFixed(2)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
