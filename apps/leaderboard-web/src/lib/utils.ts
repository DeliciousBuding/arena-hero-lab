import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * 类名合并：clsx 处理条件，tailwind-merge 消解冲突。
 * 设计系统约束层：所有 ui 原语经此合并，保证变体不分裂。
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
