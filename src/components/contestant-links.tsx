import { ExternalLink } from "lucide-react";
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

/** Linux DO 品牌圆标：橙红渐变圆 + 白色 L 字标（品牌近似色，纯 SVG 无图片依赖）。 */
export function LinuxDoLogo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      className={cn("h-3.5 w-3.5 shrink-0", className)}
      role="img"
      aria-label="Linux DO"
    >
      <defs>
        <linearGradient id="linuxdo-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#f97316" />
          <stop offset="100%" stopColor="#dc2626" />
        </linearGradient>
      </defs>
      <circle cx="8" cy="8" r="8" fill="url(#linuxdo-grad)" />
      <path
        d="M4.6 10.6V4.4h2.1v4.6h3.1v1.6z"
        fill="#fff"
      />
    </svg>
  );
}

/**
 * 条目外链群：GitHub 仓库 + Linux DO 帖子（均为社区来源，非官方）。
 * Linux DO 按钮展示"logo + 帖子标题"（compact）或"logo + Linux DO"（inline），
 * 完整标题放 Tooltip。用 Button ghost + icon-sm + Tooltip，键盘可访问，焦点环统一。
 * 入口：entry 头部（compact）、榜单行（inline）。
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
    const title = contestant.linuxdoTitle ?? "Linux DO 讨论帖";
    links.push(
      <Tooltip key="linuxdo">
        <TooltipTrigger asChild>
          {variant === "compact" ? (
            <Button
              asChild
              variant="ghost"
              size="sm"
              className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
            >
              <a
                href={contestant.linuxdoUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="max-w-[280px]"
              >
                <LinuxDoLogo />
                <span className="truncate">{title}</span>
                <ExternalLink className="h-3 w-3 shrink-0 opacity-60" />
              </a>
            </Button>
          ) : (
            <Button
              asChild
              variant="ghost"
              size="icon-sm"
              className="h-7 w-7"
              aria-label="Linux DO 讨论帖"
            >
              <a
                href={contestant.linuxdoUrl}
                target="_blank"
                rel="noopener noreferrer"
              >
                <LinuxDoLogo />
              </a>
            </Button>
          )}
        </TooltipTrigger>
        <TooltipContent>{title}</TooltipContent>
      </Tooltip>,
    );
  }
  if (links.length === 0) return null;
  return (
    <div className={cn("flex items-center gap-1.5", className)}>{links}</div>
  );
}
