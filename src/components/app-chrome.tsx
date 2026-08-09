"use client";

import { GitBranch, Trophy } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { benchData } from "@/lib/bench";
import { ThemeToggle } from "./theme-toggle";

const NAV_ITEMS = [
  { href: "/", label: "Leaderboard" },
  { href: "/leaderboard", label: "全维度" },
  { href: "/#heatmap", label: "热图" },
  { href: "/#scenarios", label: "场景" },
];

/** arena.ai 风格顶部导航：透明底 + 0.57px 细线 + 48px 高，sticky。 */
export function AppChrome() {
  const pathname = usePathname();
  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href.split("#")[0]));

  return (
    <nav className="sticky top-0 z-40 bg-surface-secondary/95 backdrop-blur">
      <div className="mx-auto flex h-12 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
        <div className="flex items-center gap-3">
          <div className="flex h-6 w-6 items-center justify-center rounded-sm bg-accent-primary/90">
            <Trophy className="h-3.5 w-3.5 text-surface-secondary" />
          </div>
          <Link href="/" className="text-sm font-medium tracking-tight text-text-primary link-hover">
            Arena Hero
          </Link>
        </div>

        <div className="hidden items-center gap-6 sm:flex">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`text-sm transition-colors ${
                isActive(item.href) ? "font-medium text-text-primary" : "text-text-secondary hover:text-text-primary"
              }`}
            >
              {item.label}
            </Link>
          ))}
        </div>

        <div className="flex items-center gap-3">
          <span className="hidden text-xs text-text-tertiary tnum md:block">{benchData.schema}</span>
          <Link
            href="https://github.com/DeliciousBuding/arena-hero-leaderboard"
            target="_blank"
            rel="noreferrer"
            aria-label="GitHub 仓库"
            className="text-text-secondary transition-colors hover:text-text-primary"
          >
            <GitBranch className="h-4 w-4" />
          </Link>
          <ThemeToggle />
        </div>
      </div>
      <div className="hairline" />
    </nav>
  );
}
