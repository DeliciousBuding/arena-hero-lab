import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Stat 原语：数值展示单元（榜单/详情页指标卡片用）。
 * 三段式：label（小写说明）/ value（大号数字）/ hint（辅助说明）。
 */
const Stat = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      "flex flex-col gap-0.5 rounded-md border border-border bg-card p-3 transition-colors hover:border-foreground/20",
      className,
    )}
    {...props}
  />
));
Stat.displayName = "Stat";

const StatLabel = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("text-[11px] font-medium uppercase tracking-wide text-muted-foreground", className)}
    {...props}
  />
));
StatLabel.displayName = "StatLabel";

const StatValue = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("font-serif text-2xl font-normal leading-none text-foreground tnum", className)}
    {...props}
  />
));
StatValue.displayName = "StatValue";

const StatHint = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn("text-[11px] leading-tight text-muted-foreground tnum", className)}
    {...props}
  />
));
StatHint.displayName = "StatHint";

export { Stat, StatLabel, StatValue, StatHint };
