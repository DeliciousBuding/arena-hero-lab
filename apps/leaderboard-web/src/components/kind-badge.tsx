import { Badge } from "@/components/ui/badge";

/**
 * 条目类型徽章：第三方社区 agent 与确定性对照 bot 统一展示为 agent。
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
