import { Badge } from "@/components/ui/badge";

/**
 * 条目类型徽章：所有参赛条目统一为第三方 agent（legacy TypeScript contestant 与社区实现
 * 同等待遇，无 reference contestant 特化）。颜色约束在设计系统内，不分裂。
 */
export function KindBadge({
  className,
}: {
  kind?: string;
  className?: string;
}) {
  return (
    <Badge variant="outline" className={className}>
      agent
    </Badge>
  );
}
