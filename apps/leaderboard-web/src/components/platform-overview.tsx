import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { platformCopy, platformData, shortSha } from "@/lib/platform";
import { SectionHeader } from "@/components/section-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";

function DigestRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <code className="tnum text-foreground">{shortSha(value)}</code>
    </div>
  );
}

function PlatformCard({
  icon,
  title,
  description,
  badge,
  badgeVariant,
  children,
}: {
  icon: string;
  title: string;
  description: string;
  badge: string;
  badgeVariant: "success" | "brand" | "default";
  children: React.ReactNode;
}) {
  return (
    <Card className="flex h-full flex-col">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="text-lg" aria-hidden="true">
              {icon}
            </span>
            <CardTitle>{title}</CardTitle>
          </div>
          <Badge variant={badgeVariant} className="text-xs">{badge}</Badge>
        </div>
        <p className="text-xs leading-relaxed text-muted-foreground">{description}</p>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-2.5 text-xs">{children}</CardContent>
    </Card>
  );
}

/**
 * Python vNext 平台成果区：Agent / 模拟器 / 科研 三张产品化卡片。
 * 徽章与摘要全部来自确定性验证证据；本区不参与竞争排名。
 */
export function PlatformOverview() {
  const { agent, simulator, research } = platformData;

  return (
    <section className="mb-16 scroll-mt-20">
      <SectionHeader
        id="platform"
        title="Python vNext Platform"
        enTitle="Python 新一代平台"
        description="Agent · 模拟器 · 科研分析 的确定性验证成果。一致性 ≠ 竞技名次：本区不参与竞争排名。"
      />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <PlatformCard
          icon="🤖"
          title="Python Agent"
          description="SDK 回合 → 领域观测 → 决策 → 指令计划的离线一致性校验。"
          badge="一致性已核验"
          badgeVariant="success"
        >
          <DigestRow label="计划摘要" value={agent.evidence.plan_sha256} />
          <DigestRow label="证据来源" value={agent.evidence.fixture_canonical_sha256} />
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">来源提交</span>
            <code className="tnum text-foreground">{agent.source_commit_short}</code>
          </div>
          <Separator className="my-1" />
          <p className="leading-relaxed text-muted-foreground">{platformCopy.agentNote}</p>
        </PlatformCard>

        <PlatformCard
          icon="🧪"
          title="Python 模拟器"
          description="参考引擎与优化后端在同一标准场景集上的语义差分。"
          badge="差分一致"
          badgeVariant="brand"
        >
          <DigestRow label="差分摘要" value={simulator.evidence.differential_sha256} />
          <DigestRow label="回合顺序摘要" value={simulator.evidence.episode_order_sha256} />
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">场景 · 批次</span>
            <span className="tnum text-foreground">
              {simulator.evidence.case_count} 场景 · 每批 {simulator.evidence.batch_size}
            </span>
          </div>
          <Separator className="my-1" />
          <p className="leading-relaxed text-muted-foreground">{platformCopy.simulatorNote}</p>
        </PlatformCard>

        <PlatformCard
          icon="📐"
          title="科研分析"
          description="分层随机效应拟合 → 求解器证书 → 交叉验证报告的可验证证据链。"
          badge="证据链已核验"
          badgeVariant="default"
        >
          <DigestRow label="拟合摘要" value={research.fit.canonical_sha256} />
          <DigestRow label="证书摘要" value={research.certificate.canonical_sha256} />
          <DigestRow label="报告摘要" value={research.report.canonical_sha256} />
          <Separator className="my-1" />
          <p className="leading-relaxed text-muted-foreground">{platformCopy.trustStatement}</p>
        </PlatformCard>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-card px-4 py-3">
        <p className="max-w-2xl text-xs leading-relaxed text-muted-foreground">
          {platformCopy.trustCompetitiveRankings} {platformCopy.simulatorNote}
        </p>
        <Link
          href="/platform"
          className="inline-flex items-center gap-1 rounded-sm text-xs font-medium text-foreground underline decoration-foreground/40 underline-offset-4 transition-colors hover:bg-brand-soft hover:decoration-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          查看平台详情
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
    </section>
  );
}
