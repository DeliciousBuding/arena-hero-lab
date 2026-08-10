"use client";

import { GitBranch, Trophy } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { benchData } from "@/lib/bench";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { ThemeToggle } from "./theme-toggle";

const NAV_ITEMS = [
  { href: "/", label: "Leaderboard" },
  { href: "/leaderboard", label: "全维度" },
  { href: "/#heatmap", label: "热图" },
  { href: "/#scenarios", label: "场景" },
] as const;

/**
 * 顶部导航：sticky + 半透明背景模糊，48px 高，底部 hairline。
 * 用 Button asChild 组合 Link，焦点环统一。
 */
export function AppChrome() {
  const pathname = usePathname();
  /** 锚点链接（#hash）不做 active 高亮（否则首页会恒亮"热图/场景"）。 */
  const isActive = (href: string) => {
    if (href.includes("#")) return false;
    return href === "/" ? pathname === "/" : pathname.startsWith(href);
  };

  return (
    <nav className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur-md">
      <div className="container-page flex h-14 items-center justify-between gap-4 px-4 sm:px-6">
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="flex items-center gap-2.5 text-sm font-medium tracking-tight text-foreground transition-colors hover:text-brand"
          >
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-foreground text-background">
              <Trophy className="h-3.5 w-3.5" />
            </span>
            <span className="font-serif text-base">Arena Hero</span>
          </Link>
          <Separator orientation="vertical" className="hidden h-5 sm:block" />
          <Badge variant="outline" className="hidden sm:inline-flex">
            {benchData.schema}
          </Badge>
        </div>

        <div className="hidden items-center gap-1 sm:flex">
          {NAV_ITEMS.map((item) => (
            <Button
              key={item.href}
              asChild
              variant="ghost"
              size="sm"
              className={cn(
                "h-8",
                isActive(item.href)
                  ? "bg-secondary text-foreground"
                  : "text-muted-foreground",
              )}
            >
              <Link href={item.href}>{item.label}</Link>
            </Button>
          ))}
        </div>

        <div className="flex items-center gap-1.5">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button asChild variant="ghost" size="icon-sm" aria-label="GitHub 仓库">
                <Link
                  href="https://github.com/DeliciousBuding/arena-hero-leaderboard"
                  target="_blank"
                  rel="noreferrer"
                >
                  <GitBranch className="h-4 w-4" />
                </Link>
              </Button>
            </TooltipTrigger>
            <TooltipContent>源码仓库</TooltipContent>
          </Tooltip>
          <ThemeToggle />
        </div>
      </div>
    </nav>
  );
}
