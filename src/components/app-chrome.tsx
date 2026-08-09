"use client";

import { BarChart3, FileSearch, LayoutGrid, Menu, ScrollText, Trophy, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { benchData } from "@/lib/bench";
import { ThemeToggle } from "./theme-toggle";

const NAV_ITEMS = [
  { href: "/", label: "榜单概览", icon: LayoutGrid },
  { href: "/leaderboard", label: "全量榜单", icon: BarChart3 },
  { href: "/#heatmap", label: "热图分析", icon: FileSearch },
  { href: "/#about", label: "关于本站", icon: ScrollText },
];

function SidebarContent() {
  const pathname = usePathname();
  const generatedDate = new Date(benchData.generatedAt).toLocaleDateString("zh-CN");

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 px-5 pb-6 pt-6">
        <div className="bg-gradient-accent flex h-9 w-9 items-center justify-center rounded-xl">
          <Trophy className="h-4.5 w-4.5 text-white" />
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold text-text-primary">Arena Hero</div>
          <div className="text-xs text-text-tertiary">模拟器评测榜单</div>
        </div>
      </div>

      <nav className="flex-1 space-y-1 px-3">
        {NAV_ITEMS.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href.split("#")[0]);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition-colors ${
                active
                  ? "bg-accent-soft font-medium text-accent-primary"
                  : "text-text-secondary hover:bg-surface-tertiary/60 hover:text-text-primary"
              }`}
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="space-y-3 border-t border-border-primary px-5 py-4">
        <div className="rounded-xl bg-surface-tertiary/50 px-3 py-2.5">
          <div className="flex items-center justify-between text-xs text-text-secondary">
            <span>数据版本</span>
            <span className="text-text-tertiary tnum">{benchData.schema}</span>
          </div>
          <div className="mt-1 flex items-center justify-between text-xs text-text-secondary">
            <span>生成时间</span>
            <span className="text-text-tertiary tnum">{generatedDate}</span>
          </div>
        </div>
        <div className="flex items-center justify-between">
          <ThemeToggle />
          <Link href="/#about" className="text-xs text-text-tertiary transition-colors hover:text-text-primary">
            关于 · 数据来源
          </Link>
        </div>
      </div>
    </div>
  );
}

/**
 * 应用骨架：左侧固定 sidebar（桌面）+ 移动端抽屉。
 * 复刻 arena.ai/leaderboard 的左侧导航布局。
 */
export function AppChrome() {
  const [open, setOpen] = useState(false);

  return (
    <>
      {/* 桌面端固定 sidebar */}
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[17rem] border-r border-border-primary bg-surface-primary lg:block">
        <SidebarContent />
      </aside>

      {/* 移动端抽屉 */}
      {open ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-72 border-r border-border-primary bg-surface-primary shadow-2xl">
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="关闭菜单"
              className="absolute right-3 top-4 rounded-lg p-1.5 text-text-secondary hover:bg-surface-tertiary"
            >
              <X className="h-5 w-5" />
            </button>
            <SidebarContent />
          </aside>
        </div>
      ) : null}

      {/* 移动端顶栏 */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-border-primary bg-surface-secondary/90 px-4 py-3 backdrop-blur lg:hidden">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="打开菜单"
          className="rounded-lg p-2 text-text-secondary hover:bg-surface-tertiary"
        >
          <Menu className="h-5 w-5" />
        </button>
        <div className="text-sm font-semibold text-text-primary">Arena Hero 评测榜单</div>
        <ThemeToggle />
      </header>
    </>
  );
}
