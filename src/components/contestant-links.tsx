import { MessageSquare } from "lucide-react";
import { GitHubIcon } from "@/components/app-chrome";
import type { Contestant } from "@/lib/bench";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

function repoShorthand(repoUrl: string): string {
  try {
    const pathname = new URL(repoUrl).pathname;
    return pathname.replace(/^\//, "").replace(/\/$/, "");
  } catch {
    return "GitHub";
  }
}

/**
 * 条目外链群：GitHub 仓库 + Linux DO 帖子（均为社区来源，非官方）。
 * 用 Button ghost + icon-sm + Tooltip，键盘可访问，焦点环统一。
 * 入口：entry 头部（紧凑）、榜单行（inline）。
 */
export function ContestantLinks({
  contestant,
  variant = "compact",
  className,
}: {
  contestant: Contestant;
  variant?: "compact" | "inline";
  className?: string;
}) {
  const links: React.ReactNode[] = [];
  if (contestant.repoUrl !== undefined) {
    links.push(
      <Tooltip key="repo">
        <TooltipTrigger asChild>
          <Button asChild variant="outline" size="sm" className="h-7 gap-1.5 px-2 text-xs">
            <a
              href={contestant.repoUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              <GitHubIcon className="h-3 w-3" />
              {variant === "compact" ? repoShorthand(contestant.repoUrl) : "GitHub"}
            </a>
          </Button>
        </TooltipTrigger>
        <TooltipContent>GitHub 仓库（社区第三方实现）</TooltipContent>
      </Tooltip>,
    );
  }
  if (contestant.linuxdoUrl !== undefined) {
    links.push(
      <Tooltip key="linuxdo">
        <TooltipTrigger asChild>
          <Button asChild variant="ghost" size="icon-sm" className="h-7 w-7" aria-label="Linux DO 讨论帖">
            <a
              href={contestant.linuxdoUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              <MessageSquare className="h-3.5 w-3.5" />
            </a>
          </Button>
        </TooltipTrigger>
        <TooltipContent>Linux DO 讨论帖</TooltipContent>
      </Tooltip>,
    );
  }
  if (links.length === 0) return null;
  return (
    <div className={cn("flex items-center gap-1.5", className)}>{links}</div>
  );
}
