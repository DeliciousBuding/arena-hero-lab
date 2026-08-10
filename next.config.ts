import type { NextConfig } from "next";

/**
 * GitHub Pages 静态导出（2026-08-09 用户裁决）：
 * - output: "export" —— 构建产出纯静态 out/，可部署任意静态托管（Pages/GH 等）
 * - basePath —— 仓库路径 /arena-hero-leaderboard（Pages 子路径部署）
 * - images.unoptimized —— export 模式禁用图片优化（静态资源直出）
 * 本地预览：pnpm build && npx serve out
 */
const nextConfig: NextConfig = {
  output: "export",
  // GitHub Pages 子路径部署；本地预览时临时注释。
  basePath: "/arena-hero-leaderboard",
  images: { unoptimized: true },
  // 把 basePath 暴露给前端（next/image 的 unoptimized 模式不会自动加前缀）
  env: { NEXT_PUBLIC_BASE_PATH: "/arena-hero-leaderboard" },
};

export default nextConfig;
