export interface RadarValue {
  key: string;
  label: string;
  value: number;
}

/**
 * v3 四维雷达图：纯 SVG 渲染（服务端可执行）。
 * 维度为 kill / rank / economy / survival 四项 0–1 分数，
 * 网格环 25/50/75/100%，值为 0 的顶点收缩到圆心。
 * 颜色取自设计 token（brand 渐变 → rank-bronze）。
 */
export function RadarChart({
  values,
  size = 300,
}: {
  values: RadarValue[];
  size?: number;
}) {
  const center = size / 2;
  const radius = size / 2 - 52;
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
      aria-label="v3 四维分数雷达图"
      className="mx-auto"
    >
      <defs>
        <linearGradient id="radar-fill" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="var(--color-brand)" stopOpacity={0.28} />
          <stop offset="100%" stopColor="var(--color-rank-bronze)" stopOpacity={0.18} />
        </linearGradient>
        <linearGradient id="radar-stroke" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="var(--color-brand)" />
          <stop offset="100%" stopColor="var(--color-rank-bronze)" />
        </linearGradient>
      </defs>

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
          stroke="var(--color-border)"
          strokeWidth={1}
        />
      ))}
      {values.map((_, i) => {
        const p = pointAt(1, i);
        return (
          <line
            key={i}
            x1={center}
            y1={center}
            x2={p.x}
            y2={p.y}
            stroke="var(--color-border)"
            strokeWidth={1}
          />
        );
      })}
      <polygon
        points={polygonPoints}
        fill="url(#radar-fill)"
        stroke="url(#radar-stroke)"
        strokeWidth={2}
        strokeLinejoin="round"
      />
      {values.map((v, i) => {
        const p = pointAt(v.value, i);
        const label = pointAt(1.2, i);
        return (
          <g key={v.key}>
            <circle
              cx={p.x}
              cy={p.y}
              r={v.value < 0.02 ? 0 : 3.2}
              fill="var(--color-brand)"
              stroke="var(--color-background)"
              strokeWidth={1.5}
            />
            <text
              x={label.x}
              y={label.y}
              textAnchor="middle"
              dominantBaseline="middle"
              style={{ fontSize: 12, fontWeight: 600, fill: "var(--color-foreground)" }}
            >
              {v.label}
            </text>
            <text
              x={label.x}
              y={label.y + 14}
              textAnchor="middle"
              dominantBaseline="middle"
              className="tnum"
              style={{ fontSize: 11, fill: "var(--color-muted-foreground)" }}
            >
              {v.value.toFixed(2)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
