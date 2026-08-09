import { dimensions } from "@/lib/dimensions";
import { DimensionCard } from "@/components/dimension-card";
import { SectionHeader } from "@/components/section-header";

/**
 * 全量榜单页：4 张 v3 维度卡片纵向排列（综合分 / 经济 / 击杀 / 场景梯度）。
 * 静态导出模式：不使用 searchParams 等动态 API。
 */
export default function LeaderboardPage() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:py-10">
      <SectionHeader
        title="全量榜单"
        enTitle="Leaderboard"
        description="v3 四个维度的完整榜单（全部条目展示，不受 top10 限制）。点击表头可排序，点击条目进入详情页。"
      />

      <div className="space-y-6">
        {dimensions.map((dimension) => (
          <div key={dimension.id} id={`dim-${dimension.id}`} className="scroll-mt-6">
            <DimensionCard dimension={dimension} />
          </div>
        ))}
      </div>
    </div>
  );
}
