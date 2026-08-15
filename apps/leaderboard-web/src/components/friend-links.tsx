import Image from "next/image";
import { ExternalLink } from "lucide-react";
import { StaticLink } from "@/components/static-link";
import { Card } from "@/components/ui/card";
import { SectionHeader } from "@/components/section-header";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "/arena-hero-lab";

interface FriendLink {
  name: string;
  url: string;
  logo: string;
  logoWidth: number;
  logoHeight: number;
  description: string;
}

/** 友情链接：Linux DO 社区 + 官方游戏入口。 */
const FRIEND_LINKS: FriendLink[] = [
  {
    name: "Linux DO",
    url: "https://linux.do/",
    logo: `${BASE_PATH}/linuxdo-mark.png`,
    logoWidth: 40,
    logoHeight: 40,
    description: "开放的技术交流社区，Arena Hero 智能体开源分享与玩法讨论的聚集地。",
  },
  {
    name: "Arena Hero 官网",
    url: "https://app.arenahero.io/",
    logo: `${BASE_PATH}/arenahero-mark.svg`,
    logoWidth: 28,
    logoHeight: 28,
    description: "arena-hero 官方游戏入口：实时对局、段位与赛季玩法。",
  },
];

/** 友链区：关联站点与社区，外链新窗口打开，hover 高亮。 */
export function FriendLinks() {
  return (
    <section className="mb-16 scroll-mt-20">
      <SectionHeader
        title="友情链接"
        enTitle="Friend Links"
        description="关联站点与社区交流平台，欢迎互链。"
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {FRIEND_LINKS.map((link) => (
          <StaticLink
            key={link.name}
            href={link.url}
            target="_blank"
            rel="noreferrer"
            className="group"
          >
            <Card className="flex items-center gap-4 p-5 transition-colors group-hover:border-foreground/30">
              <span className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-full bg-secondary ring-1 ring-border">
                <Image
                  src={link.logo}
                  alt=""
                  width={link.logoWidth}
                  height={link.logoHeight}
                  className="object-contain"
                />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                  {link.name}
                  <ExternalLink className="h-3.5 w-3.5 text-muted-foreground transition-colors group-hover:text-foreground" />
                </span>
                <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">
                  {link.description}
                </span>
              </span>
            </Card>
          </StaticLink>
        ))}
      </div>
    </section>
  );
}
