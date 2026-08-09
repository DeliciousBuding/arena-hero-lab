import { Database, Scale, Trophy, Users } from "lucide-react";
import { benchData } from "@/lib/bench";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Stat, StatHint, StatLabel, StatValue } from "@/components/ui/stat";

const METHODOLOGY_ITEMS = [
  {
    icon: Trophy,
    label: "判定规则",
    enLabel: "Tie-break",
    description:
      "每场按 存活 → 击杀数 → 累计存款 deposited → 资源 → 人口 依次判定（并列同分同排）。胜方 = 排名第 1。",
  },
  {
    icon: Scale,
    label: "综合分公式",
    enLabel: "Composite",
    description:
      "composite = avgRank(反向 min-max) × 60% + killRate × 30% + resourcesPerTick × 10%。survivalMedian 因同 tick 重生恒 1.0 已移除（字段保留兼容）。",
  },
  {
    icon: Users,
    label: "阵容与对照",
    enLabel: "Roster",
    description:
      "8 条目同场 FFA（含 builtin 对照组，不参与主榜 composite 排名，单独展示）。每场景 × 每种子独立对局。",
  },
  {
    icon: Database,
    label: "数据来源",
    enLabel: "Source",
    description:
      "全部数值来自 arena.bench.report.v3 评测产物（results.json），前端只做确定性变换（裁剪/聚合/排序/中文标签），不编造数字。",
  },
];

/**
 * 评测方法说明：解释评测公平性 + 判定规则 + 综合分公式 + 数据来源。
 * arena.ai 风格编辑性 section——透明化方法论，建立可信度。
 */
export function MethodologySection() {
  const { params } = benchData;
  const totalMatches = benchData.scenarios.reduce((n, s) => n + s.matches.length, 0);
  const controlCount = benchData.contestants.filter((c) => c.kind === "builtin").length;
  const agentCount = benchData.contestants.filter((c) => c.kind === "python").length;

  return (
    <section className="mb-16">
      <div className="mb-6">
        <h2 className="font-serif text-xl font-normal leading-tight text-foreground">
          Methodology
          <span className="ml-2 font-sans text-xs font-normal text-muted-foreground">
            评测方法
          </span>
        </h2>
        <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted-foreground">
          评测公平性与判定规则的透明化说明。所有 agent 同场 FFA 对抗，对照组校准基线。
        </p>
        <Separator className="mt-4" />
      </div>

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat>
          <StatLabel>场景模板</StatLabel>
          <StatValue>{params.scenarios.length}</StatValue>
          <StatHint>ffa-* 场景</StatHint>
        </Stat>
        <Stat>
          <StatLabel>种子/场景</StatLabel>
          <StatValue>{params.seeds.length}</StatValue>
          <StatHint>seeds × scenarios</StatHint>
        </Stat>
        <Stat>
          <StatLabel>对抗条目</StatLabel>
          <StatValue>{agentCount}</StatValue>
          <StatHint>+ {controlCount} 对照组</StatHint>
        </Stat>
        <Stat>
          <StatLabel>总对局</StatLabel>
          <StatValue>{totalMatches}</StatValue>
          <StatHint>matches</StatHint>
        </Stat>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {METHODOLOGY_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <Card key={item.label}>
              <CardContent className="flex items-start gap-3 p-5">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-secondary/50 text-brand">
                  <Icon className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <div className="flex items-baseline gap-2">
                    <h3 className="font-medium text-foreground">{item.label}</h3>
                    <span className="text-[11px] text-muted-foreground">{item.enLabel}</span>
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                    {item.description}
                  </p>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </section>
  );
}
