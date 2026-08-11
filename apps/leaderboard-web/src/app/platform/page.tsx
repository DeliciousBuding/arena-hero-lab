import Link from "next/link";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { platformCopy, platformData, shortRepo } from "@/lib/platform";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Stat, StatHint, StatLabel, StatValue } from "@/components/ui/stat";

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1 text-xs">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <code className="break-all text-right tnum text-foreground">{value}</code>
    </div>
  );
}

function DetailCard({
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
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="text-lg" aria-hidden="true">
              {icon}
            </span>
            <CardTitle asChild>
              <h2>{title}</h2>
            </CardTitle>
          </div>
          <Badge variant={badgeVariant} className="text-xs">{badge}</Badge>
        </div>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">{children}</CardContent>
    </Card>
  );
}

export default function PlatformPage() {
  const { agent, simulator, research, schema, source_date } = platformData;

  return (
    <div className="container-page px-4 py-10 sm:px-6">
      <Link
        href="/"
        className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        返回排行榜
      </Link>

      <header className="mb-8 mt-4">
        <h1 className="font-serif text-3xl font-normal leading-tight tracking-tight text-foreground sm:text-4xl">
          Python 新一代平台
          <span className="ml-3 text-brand">Platform</span>
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-muted-foreground">
          Agent · 模拟器 · 科研分析 的确定性验证成果总览。所有徽章与摘要均由
          <code className="mx-1 rounded bg-secondary px-1 py-0.5 text-xs">{schema}</code>
          生成器实时重算并核验。
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground tnum">
          <span>证据快照 {source_date}</span>
          <span className="hidden h-3 w-px bg-border-faint sm:block" />
          <span>一致性 ≠ 竞技名次</span>
        </div>
      </header>

      <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Stat>
          <StatLabel className="text-xs">Agent 一致性</StatLabel>
          <StatValue>已核验</StatValue>
          <StatHint className="text-xs">turn→plan 已知答案摘要一致</StatHint>
        </Stat>
        <Stat>
          <StatLabel className="text-xs">模拟器差分</StatLabel>
          <StatValue>{simulator.evidence.case_count} 场景</StatValue>
          <StatHint className="text-xs">参考 vs 优化语义一致</StatHint>
        </Stat>
        <Stat>
          <StatLabel className="text-xs">科研证据链</StatLabel>
          <StatValue>全链路</StatValue>
          <StatHint className="text-xs">拟合 → 证书 → 报告可复核</StatHint>
        </Stat>
      </div>

      <div className="mb-8 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <DetailCard
          icon="🤖"
          title="Python Agent"
          description="SDK 回合 → 领域观测 → 决策 → 指令计划的离线一致性校验。"
          badge="一致性已核验"
          badgeVariant="success"
        >
          <DetailRow label="状态" value={agent.status} />
          <DetailRow label="来源仓库" value={agent.name} />
          <div className="flex items-center gap-1 py-1 text-xs">
            <span className="shrink-0 text-muted-foreground">公开源码</span>
            <Link
              href={agent.repository}
              target="_blank"
              rel="noreferrer"
              className="inline-flex min-w-0 flex-1 items-center gap-1 break-all rounded-sm text-foreground underline decoration-foreground/40 underline-offset-4 transition-colors hover:bg-brand-soft hover:decoration-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              {shortRepo(agent.repository)}
              <ExternalLink className="h-3 w-3 shrink-0" />
            </Link>
          </div>
          <DetailRow label="来源提交" value={agent.source_commit} />
          <DetailRow label="SDK" value={`${agent.sdk.name} ${agent.sdk.version}`} />
          <Separator className="my-2" />
          <DetailRow label="计划摘要" value={agent.evidence.plan_sha256} />
          <DetailRow label="证据文件摘要" value={agent.evidence.fixture_canonical_sha256} />
          <DetailRow label="规则版本" value={agent.evidence.rules_version} />
          <DetailRow label="采集日期" value={agent.evidence.captured_on} />
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{platformCopy.agentNote}</p>
        </DetailCard>

        <DetailCard
          icon="🧪"
          title="Python 模拟器"
          description="参考引擎与优化后端在同一标准场景集上的语义差分。"
          badge="差分一致"
          badgeVariant="brand"
        >
          <DetailRow label="状态" value={simulator.status} />
          <DetailRow label="参考后端" value={simulator.backends.reference} />
          <DetailRow label="优化后端" value={simulator.backends.optimized} />
          <DetailRow label="工作负载" value={`${simulator.workload.id} @ ${simulator.workload.version}`} />
          <Separator className="my-2" />
          <DetailRow label="场景数" value={String(simulator.evidence.case_count)} />
          <DetailRow label="回合数" value={String(simulator.evidence.episode_count)} />
          <DetailRow label="批次大小" value={String(simulator.evidence.batch_size)} />
          <DetailRow label="差分摘要" value={simulator.evidence.differential_sha256} />
          <DetailRow label="回合顺序摘要" value={simulator.evidence.episode_order_sha256} />
          <DetailRow label="差分模式" value={simulator.evidence.differential_schema} />
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{platformCopy.simulatorNote}</p>
        </DetailCard>

        <DetailCard
          icon="📐"
          title="科研分析"
          description="分层随机效应拟合 → 求解器证书 → 交叉验证报告的可验证证据链。"
          badge="证据链已核验"
          badgeVariant="default"
        >
          <DetailRow label="状态" value={research.status} />
          <Separator className="my-2" />
          <DetailRow label="拟合模式" value={research.fit.schema_version} />
          <DetailRow label="拟合摘要" value={research.fit.canonical_sha256} />
          <DetailRow label="证书模式" value={research.certificate.schema_version} />
          <DetailRow label="求解器状态" value={research.certificate.solver_status} />
          <DetailRow label="证书摘要" value={research.certificate.canonical_sha256} />
          <DetailRow label="报告模式" value={research.report.schema_version} />
          <DetailRow label="报告状态" value={research.report.status} />
          <DetailRow label="报告摘要" value={research.report.canonical_sha256} />
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
            证据链已写入临时账本并重新加载核验（{research.evidence.schema}）。
          </p>
        </DetailCard>
      </div>

      <Card className="mb-8">
        <CardContent className="flex flex-col gap-3 p-6">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-foreground">信任边界</span>
            <Badge variant="outline">Trust Boundary</Badge>
          </div>
          <p className="text-xs leading-relaxed text-muted-foreground">{platformCopy.trustStatement}</p>
          <p className="text-xs leading-relaxed text-muted-foreground">{platformCopy.trustCompetitiveRankings}</p>
        </CardContent>
      </Card>

      <p className="text-xs leading-relaxed text-muted-foreground">
        本页数据由 <code>scripts/generate_platform.py</code> 生成，模式为
        <code className="mx-1 rounded bg-secondary px-1 py-0.5">{schema}</code>
        ，输出确定性可复现，不包含时间戳或机器相关路径。
      </p>
    </div>
  );
}
