import type { Metadata } from "next";
import { Inter, Noto_Serif_SC } from "next/font/google";
import { AppChrome } from "@/components/app-chrome";
import { Footer } from "@/components/footer";
import { Providers } from "@/components/providers";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-sans-loaded",
  display: "swap",
});

const notoSerifSC = Noto_Serif_SC({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-serif-loaded",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Arena Hero · 模拟器评测榜单",
  description:
    "arena-hero 模拟器评测 v3 Leaderboard：agent 条目 × 场景 × 种子对抗，综合分 / 名次 / 击杀 / 经济多维对比，全量图表前端渲染（React + SVG），静态导出部署于 GitHub Pages。",
  metadataBase: new URL("https://deliciousbuding.github.io"),
  openGraph: {
    title: "Arena Hero · 模拟器评测榜单",
    description:
      "arena-hero 模拟器评测 v3：agent 条目 × 场景 × 种子对抗的多维榜单。",
    type: "website",
  },
};

const THEME_INIT_SCRIPT = `try{var s=localStorage.getItem('arena-leaderboard-theme');var d=window.matchMedia('(prefers-color-scheme: dark)').matches;if(s==='light'){document.documentElement.classList.remove('dark')}else if(s==='dark'){document.documentElement.classList.add('dark')}else if(d){document.documentElement.classList.add('dark')}else{document.documentElement.classList.remove('dark')}}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="zh-CN"
      className={`dark ${inter.variable} ${notoSerifSC.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-screen bg-background antialiased">
        <Providers>
          <div className="relative flex min-h-screen flex-col">
            <AppChrome />
            <main className="flex-1">{children}</main>
            <Footer />
          </div>
        </Providers>
      </body>
    </html>
  );
}
