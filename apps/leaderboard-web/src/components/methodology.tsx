import { benchData } from "@/lib/bench";
import { Card } from "@/components/ui/card";
import { SectionHeader } from "@/components/section-header";

/**
 * 评测方法卡（arena.ai "Learn more" 的极简对应）：一卡三列，
 * 公式 / 判定 / 规模，全部数据驱动，不编造。
 */
export function Methodology() {
  const totalMatches = benchData.scenarios.reduce((n, s) => n + s.matches.length, 0);
  const subBoardCount = Object.keys(benchData.subLeaderboards ?? {}).length;
  const rows: { title: string; text: string }[] = [
    {
      title: "综合分公式",
      text: "rank 60% + kill 30% + economy 10%，同池 0-1 归一化（composite）；名次与综合分附 1000 次 bootstrap 95% 置信区间",
    },
    {
      title: "胜负判定",
      text: "每场同场对抗，判定链：存活 → 核心击杀 → 上交 → 资源 → 人口；同分同排",
    },
    {
      title: "评测规模",
      text: `${totalMatches} 场 = ${benchData.scenarios.length} 个互不相同场景 × ${benchData.params.seeds.length} 种子 × 2000 ticks；长期对抗 / 大混战 / 荒区重生为 opt-in 专场`,
    },
  ];

  return (
    <section className="mb-16 scroll-mt-20">
      <SectionHeader
        id="methodology"
        title="Methodology"
        enTitle="评测方法"
        description={`数据来源为 arena.bench.report.v4 内容寻址评测产物（确定性，无 mock）。总榜 1 张 + 阶段/策略小榜 ${subBoardCount} 张，全部同池归一化。`}
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
