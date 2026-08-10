"use client";

import { TooltipProvider } from "@/components/ui/tooltip";

/**
 * 全局 Providers：客户端边界，包裹 Radix 需要的 context provider。
 * TooltipProvider 全局开启，Tooltip 组件随处可用，delay 300ms 防误触。
 */
export function Providers({ children }: { children: React.ReactNode }) {
  return <TooltipProvider delayDuration={300}>{children}</TooltipProvider>;
}
