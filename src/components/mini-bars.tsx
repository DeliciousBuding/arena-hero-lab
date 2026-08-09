/**
 * 迷你柱状图：单指标跨场景对比（纯 SVG，无图表库）。
 * 柱高按该条目各场景中的最大值归一化；柱顶标注数值。
 * 颜色取自设计 token（brand / muted），不硬编码。
 */
export function MiniBars({
  items,
  height = 64,
  unit,
  digits = 2,
}: {
  items: { key: string; label: string; value: number | null }[];
  height?: number;
  unit?: string;
  digits?: number;
}) {
  const width = 300;
  const values = items.map((i) => i.value).filter((v): v is number => v != null);
  const max = values.length ? Math.max(...values, 1e-9) : 1;
  const slot = width / Math.max(1, items.length);
  const barW = Math.min(30, slot * 0.55);

  return (
    <svg
      viewBox={`0 0 ${width} ${height + 18}`}
      width="100%"
      role="img"
      aria-label="单指标跨场景迷你柱状图"
    >
      {items.map((item, i) => {
        const cx = slot * i + slot / 2;
        const barH = item.value == null ? 0 : Math.max(2, (item.value / max) * height);
        const y = height - barH;
        return (
          <g key={item.key}>
            <title>
              {`${item.label}: ${item.value == null ? "未参赛" : item.value.toFixed(digits) + (unit ? ` ${unit}` : "")}`}
            </title>
            <rect
              x={cx - barW / 2}
              y={y}
              width={barW}
              height={barH}
              rx={3}
              fill={item.value == null ? "var(--color-muted)" : "var(--color-brand)"}
              opacity={item.value == null ? 0.5 : 0.85}
            />
            <text
              x={cx}
              y={height + 12}
              textAnchor="middle"
              style={{ fontSize: 9, fill: "var(--color-muted-foreground)" }}
            >
              {item.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
