/**
 * 条目类型徽章：builtin = 内置对照组（琥珀色描边），python = 第三方 agent（常规描边）。
 */
export function KindBadge({ kind }: { kind: "python" | "builtin" }) {
  if (kind === "builtin") {
    return (
      <span
        className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-rank-gold/40 bg-rank-gold/10 px-1.5 py-px text-[10px] font-medium text-rank-gold"
        title="内置对照组：arena-hero-ts 内置策略，用于校准第三方 agent 表现"
      >
        对照组
      </span>
    );
  }
  return (
    <span className="inline-flex items-center whitespace-nowrap rounded-md border border-border-primary bg-surface-tertiary/50 px-1.5 py-px text-[10px] font-medium text-text-tertiary">
      agent
    </span>
  );
}
