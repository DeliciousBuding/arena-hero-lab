"use client";

import { Moon, Sun } from "lucide-react";
import { useSyncExternalStore } from "react";

/** 订阅 html 的 class 变化（.dark 切换），保持按钮状态与主题同步 */
function subscribe(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["class"],
  });
  return () => observer.disconnect();
}

function getSnapshot(): string {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

function getServerSnapshot(): string {
  return "dark";
}

/** 亮/暗主题切换：写入 documentElement.dark 类并持久化到 localStorage */
export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const dark = theme === "dark";

  function toggle() {
    const next = dark ? "light" : "dark";
    document.documentElement.classList.toggle("dark", next === "dark");
    try {
      localStorage.setItem("arena-leaderboard-theme", next);
    } catch {
      // localStorage 不可用时静默降级（仅本次会话生效）
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? "切换为亮色模式" : "切换为暗色模式"}
      className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border-primary bg-surface-primary text-text-secondary transition-colors hover:text-text-primary"
    >
      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </button>
  );
}
