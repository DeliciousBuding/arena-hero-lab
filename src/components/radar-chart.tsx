export interface RadarValue {
  key: string;
  label: string;
  value: number;
}

/**
 * v3 四维雷达图：纯 SVG 渲染（服务端可执行）。
 * 维度为 kill / rank / economy / survival 四项 0–1 分数，
 * 网格环 25/50/75/100%，值为 0 的顶点收缩到圆心（如实反映分数）。
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
      {/* 数据多边形：渐变描边 + 半透明填充 */}
      <defs>
        <linearGradient id="radar-fill" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="var(--accent-primary)" stopOpacity={0.28} />
          <stop offset="100%" stopColor="var(--accent-secondary)" stopOpacity={0.18} />
        </linearGradient>
        <linearGradient id="radar-stroke" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="var(--accent-primary)" />
          <stop offset="100%" stopColor="var(--accent-secondary)" />
        </linearGradient>
      </defs>
      <polygon
        points={polygonPoints}
        fill="url(#radar-fill)"
        stroke="url(#radar-stroke)"
        strokeWidth={2}
        strokeLinejoin="round"
      />
      {/* 顶点 + 轴标签 */}
      {values.map((v, i) => {
        const p = pointAt(v.value, i);
        const label = pointAt(1.2, i);
        return (
          <g key={v.key}>
            <circle
              cx={p.x}
              cy={p.y}
              r={v.value < 0.02 ? 0 : 3.2}
              fill="var(--accent-primary)"
              stroke="var(--surface-primary)"
              strokeWidth={1.5}
            />
            <text
              x={label.x}
              y={label.y}
              textAnchor="middle"
              dominantBaseline="middle"
              className="fill-current"
              style={{ color: "var(--text-primary)", fontSize: 12, fontWeight: 600 }}
            >
              {v.label}
            </text>
            <text
              x={label.x}
              y={label.y + 14}
              textAnchor="middle"
              dominantBaseline="middle"
              className="tnum"
              style={{ color: "var(--text-secondary)", fontSize: 11 }}
            >
              {v.value.toFixed(2)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
