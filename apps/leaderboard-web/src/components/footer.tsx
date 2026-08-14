import { ExternalLink } from "lucide-react";
import { StaticLink } from "@/components/static-link";
import { benchData } from "@/lib/bench";
import { Separator } from "@/components/ui/separator";

/** 精简 footer：数据源仓库 + 版本 + schema，底部 hairline 分隔。 */
export function Footer() {
  return (
    <footer className="border-t border-border">
      <div className="container-page flex flex-col gap-3 px-4 py-6 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div className="flex items-center gap-3">
          <span>© 2026 Arena Hero</span>
          <Separator orientation="vertical" className="h-3" />
          <StaticLink
            href="https://github.com/DeliciousBuding/arena-hero-lab"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 transition-colors hover:text-foreground"
          >
            Arena Hero Lab
            <ExternalLink className="h-3 w-3" />
          </StaticLink>
          <Separator orientation="vertical" className="h-3" />
        </div>
        <span className="tnum">
          {benchData.schema} · {new Date(benchData.generatedAt).toLocaleString("zh-CN")}
        </span>
      </div>
    </footer>
  );
}


