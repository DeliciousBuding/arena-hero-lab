"use client";

import { Trophy } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
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
  { href: "/", label: "Leaderboard", sectionId: "rankings" },
  { href: "/#scenarios", label: "场景", sectionId: "scenarios" },
  { href: "/#scores", label: "画像", sectionId: "scores" },
  { href: "/#heatmap", label: "热图", sectionId: "heatmap" },
] as const;

/** GitHub 经典黑猫头像（官方 mark-github octicon，fill 风格）。 */
export function GitHubIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="currentColor"
      className={className}
      aria-hidden="true"
    >
      <path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z" />
    </svg>
  );
}

/**
 * 顶部导航：sticky + 半透明背景模糊，48px 高，底部 hairline。
 * 右侧 = Arena Hero 官网（美术 logo）+ GitHub + Linux DO 官方帖 + 主题切换；
 * 锚点项按滚动位置 scroll spy 高亮。
 */
export function AppChrome() {
  const pathname = usePathname();
  /** scroll spy：当前视口顶部附近的区块 id（无区块可见 = null，如 hero 区）。 */
  const [activeSection, setActiveSection] = useState<string | null>(null);

  useEffect(() => {
    const sections = NAV_ITEMS.map((item) => item.sectionId)
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el !== null);
    if (sections.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length > 0) {
          setActiveSection(visible[0].target.id);
        }
      },
      // 视口上部 15%–30% 的窄带作为"当前区块"判定区
      { rootMargin: "-15% 0px -70% 0px", threshold: 0 },
    );
    sections.forEach((section) => observer.observe(section));
    return () => observer.disconnect();
  }, [pathname]);

  /** 锚点链接按 scroll spy 高亮；首页 Leaderboard 在 hero/榜单区亮。 */
  const isActive = (href: string): boolean => {
    if (href.includes("#")) {
      return activeSection === href.slice(2);
    }
    return pathname === "/" && (activeSection === null || activeSection === "rankings");
  };

  return (
    <nav className="sticky top-0 z-40 border-b border-border bg-background/80 backdrop-blur-md">
      <div className="container-page flex h-14 items-center justify-between gap-4 px-4 sm:px-6">
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="flex items-center gap-2 text-sm font-medium tracking-tight text-foreground transition-colors hover:text-brand"
          >
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-foreground text-background">
              <Trophy className="h-3.5 w-3.5" />
            </span>
            <span className="font-serif text-base leading-none">Arena Hero</span>
            <span className="font-serif text-base leading-none text-brand">Leaderboard</span>
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
              <Button asChild variant="ghost" size="icon-sm" aria-label="Arena Hero 官网">
                <Link
                  href="https://app.arenahero.io/"
                  target="_blank"
                  rel="noreferrer"
                >
                  <Image
                    src={`${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/arenahero-mark.svg`}
                    alt=""
                    width={16}
                    height={16}
                    className="h-4 w-4 rounded"
                  />
                </Link>
              </Button>
            </TooltipTrigger>
            <TooltipContent>Arena Hero 官网</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button asChild variant="ghost" size="icon-sm" aria-label="arena-hero-agent-ts 仓库（评测与模拟器）">
                <Link
                  href="https://github.com/DeliciousBuding/arena-hero-agent-ts"
                  target="_blank"
                  rel="noreferrer"
                >
                  <GitHubIcon className="h-4 w-4" />
                </Link>
              </Button>
            </TooltipTrigger>
            <TooltipContent>arena-hero-agent-ts 仓库（评测与模拟器）</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button asChild variant="ghost" size="icon-sm" aria-label="Linux DO 官方帖（Arena Hero 介绍）">
                <Link
                  href="https://linux.do/t/topic/2703804"
                  target="_blank"
                  rel="noreferrer"
                >
                  <Image
                    src={`${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/linuxdo-mark.png`}
                    alt=""
                    width={20}
                    height={20}
                    className="h-5 w-5 rounded-full ring-1 ring-border"
                  />
                </Link>
              </Button>
            </TooltipTrigger>
            <TooltipContent>Linux DO 官方帖</TooltipContent>
          </Tooltip>
          <ThemeToggle />
        </div>
      </div>
    </nav>
  );
}
