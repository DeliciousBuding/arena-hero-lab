import { RankBars, type RankBarRow } from "@/components/rank-bars";
import { SectionHeader } from "@/components/section-header";
import { benchData, contestantOf } from "@/lib/bench";

/** 小榜展示元信息（顺序即页面展示顺序）。 */
const SUBBOARD_META: {
  id: string;
  title: string;
  enTitle: string;
  description: string;
  componentLabels: Record<string, string>;
}[] = [
  {
    id: "early_economy",
    title: "Early Economy",
    enTitle: "前期经济发育",
    description:
      "25% 时点的采集 / 上交 / 存量 / 工人投入，衡量开局经济爬坡速度。",
    componentLabels: {
      early_harvested: "采集",
      early_deposited: "上交",
      early_resources: "存量",
      early_workers: "工人",
    },
  },
  {
    id: "mid_game",
    title: "Mid Game",
    enTitle: "中期运营",
    description:
      "50% 时点的人口 / 战斗单位 / 存量 / 上交，衡量中期经济与军事姿态。",
    componentLabels: {
      mid_population: "人口",
      mid_combat: "战斗单位",
      mid_resources: "存量",
      mid_deposited: "上交",
    },
  },
  {
    id: "late_game",
    title: "Late Game",
    enTitle: "后期决胜",
    description:
      "75% 时点的存量 / 人口 / 上交 + 存活，衡量后期续航与收官能力。",
    componentLabels: {
      late_resources: "存量",
      late_population: "人口",
      late_deposited: "上交",
      late_alive: "存活",
    },
  },
  {
    id: "military",
    title: "Military",
    enTitle: "军事",
    description:
      "全场累计伤害 / 核心击杀 / 战斗单位峰值，衡量进攻输出与兵力上限。",
    componentLabels: {
      military_damage: "伤害",
      military_core_kills: "核心击杀",
      military_peak_combat: "兵力峰值",
    },
  },
];

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

function rowsFor(boardId: string): RankBarRow[] {
  const board = benchData.subLeaderboards[boardId];
  if (!board) return [];
  const meta = SUBBOARD_META.find((m) => m.id === boardId);
  return board.map((row) => {
    const contestant = contestantOf(row.contestant);
    const topComponents = Object.entries(row.components)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 2)
      .map(([key, value]) => `${meta?.componentLabels[key] ?? key} ${pct(value)}`)
      .join(" · ");
    return {
      rank: row.rank,
      id: row.contestant,
      label: contestant?.label ?? row.contestant,
      kind: (contestant?.kind ?? "python") as "python" | "control",
      value: row.score,
      ascending: true,
      primary: pct(row.score),
      secondary: topComponents,
      href: `/entry/${row.contestant}`,
      repoUrl: contestant?.repoUrl,
    };
  });
}

/**
 * 阶段 / 策略小榜：前期经济 / 中期运营 / 后期决胜 / 军事。
 * 每张小榜独立归一化（同池 0-1），分数为分量加权和，与总榜 composite 同一套公正原则。
 */
export function SubLeaderboards() {
  const boards = SUBBOARD_META.filter((m) => benchData.subLeaderboards[m.id]);
  if (boards.length === 0) return null;

  return (
    <section className="mb-16">
      <SectionHeader
        id="subleaderboards"
        title="Stage & Strategy Boards"
        enTitle="阶段策略小榜"
        description="前期 / 中期 / 后期 / 军事 四张独立小榜，与综合总榜并列；每张同池 0-1 归一化，分数为分量加权和。"
      />
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        {boards.map((meta) => (
          <div key={meta.id} className="rounded-lg border border-border bg-card p-4">
            <div className="mb-3">
              <div className="flex items-baseline gap-2">
                <h3 className="font-serif text-base font-normal leading-tight text-foreground">
                  {meta.title}
                </h3>
                <span className="font-sans text-xs font-normal text-muted-foreground">
                  {meta.enTitle}
                </span>
              </div>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {meta.description}
              </p>
            </div>
            <RankBars rows={rowsFor(meta.id)} valueLabel="小榜分" />
          </div>
        ))}
      </div>
    </section>
  );
}
