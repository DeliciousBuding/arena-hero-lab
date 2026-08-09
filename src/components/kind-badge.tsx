import { Badge } from "@/components/ui/badge";

/**
 * 条目类型徽章：builtin = 内置对照组（金色），python = 第三方 agent（中性）。
 * 复用 Badge 变体，颜色约束在设计系统内，不分裂。
 */
export function KindBadge({
  kind,
  className,
}: {
  kind: "python" | "builtin";
  className?: string;
}) {
  if (kind === "builtin") {
    return (
      <Badge variant="gold" className={className} title="内置对照：arena-hero-ts 内置策略，用于校准第三方 agent 表现">
        对照组
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className={className}>
      agent
    </Badge>
  );
}
