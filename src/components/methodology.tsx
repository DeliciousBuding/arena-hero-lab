import { benchData } from "@/lib/bench";
import { Card } from "@/components/ui/card";
import { SectionHeader } from "@/components/section-header";

/**
 * 评测方法卡（arena.ai "Learn more" 的极简对应）：一卡三列，
 * 公式 / 判定 / 规模，全部数据驱动，不编造。
 */
export function Methodology() {
  const totalMatches = benchData.scenarios.reduce((n, s) => n + s.matches.length, 0);
  const rows: { title: string; text: string }[] = [
    {
      title: "综合分公式",
      text: "rank 60% + kill 30% + economy 10%（v3 composite）",
    },
    {
      title: "胜负判定",
      text: `每场 ${benchData.params.players} 条目同场对抗，资源结算最高者胜`,
    },
    {
      title: "评测规模",
      text: `${totalMatches} 场 = ${benchData.scenarios.length} 场景 × ${benchData.params.seeds.length} 种子 × ${benchData.params.ticks.toLocaleString("zh-CN")} ticks`,
    },
  ];

  return (
    <section className="mb-16 scroll-mt-20">
      <SectionHeader
        id="methodology"
        title="Methodology"
        enTitle="评测方法"
        description="数据来源为 arena.bench.report.v3 评测产物（确定性转换，无 mock）。"
      />
      <Card className="p-6">
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-3">
          {rows.map((row) => (
            <div key={row.title}>
              <div className="text-xs font-semibold text-foreground">{row.title}</div>
              <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{row.text}</p>
            </div>
          ))}
        </div>
      </Card>
    </section>
  );
}
