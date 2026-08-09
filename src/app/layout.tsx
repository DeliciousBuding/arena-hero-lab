import type { Metadata } from "next";
import { AppChrome } from "@/components/app-chrome";
import { Footer } from "@/components/footer";
import "./globals.css";

export const metadata: Metadata = {
  title: "Arena Hero · 模拟器评测榜单",
  description:
    "arena-hero 模拟器评测 v3 Leaderboard：agent 条目 × 场景 × 种子对抗，综合分 / 名次 / 击杀 / 经济多维对比，全量图表前端渲染（React + SVG），静态导出部署于 GitHub Pages。",
};

const THEME_INIT_SCRIPT = `try{var s=localStorage.getItem('arena-leaderboard-theme');var d=window.matchMedia('(prefers-color-scheme: dark)').matches;if(s==='light'){document.documentElement.classList.remove('dark')}else if(s==='dark'){document.documentElement.classList.add('dark')}else if(d){document.documentElement.classList.add('dark')}else{document.documentElement.classList.remove('dark')}}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-screen antialiased">
        <AppChrome />
        <main className="flex-1">{children}</main>
        <Footer />
      </body>
    </html>
  );
}
