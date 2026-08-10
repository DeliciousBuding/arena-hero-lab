import { Badge } from "@/components/ui/badge";

/**
 * 条目类型徽章：所有参赛条目统一为第三方 agent（arena-ts 客户端与社区实现
 * 同等待遇，无内置对照特化）。颜色约束在设计系统内，不分裂。
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
