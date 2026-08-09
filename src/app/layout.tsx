import type { Metadata } from "next";
import { AppChrome } from "@/components/app-chrome";
import { Footer } from "@/components/footer";
import "./globals.css";

export const metadata: Metadata = {
  title: "Arena Hero · 模拟器评测榜单",
  description:
    "arena-hero 模拟器评测 v2 Leaderboard：10 个 agent 条目 × 5 场景 × 3 种子，综合分 / 击杀 / 生存 / 场景梯度 / 五维画像 / 生态。",
};

const THEME_INIT_SCRIPT = `try{var t=localStorage.getItem('arena-leaderboard-theme');if(t==='light'){document.documentElement.classList.remove('dark')}else{document.documentElement.classList.add('dark')}}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-screen antialiased">
        <AppChrome />
        <div className="flex min-h-screen flex-col lg:pl-[17rem]">
          <main className="flex-1">{children}</main>
          <Footer />
        </div>
      </body>
    </html>
  );
}
