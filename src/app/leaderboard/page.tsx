import { LeaderboardView } from "@/components/leaderboard-view";
import { SectionHeader } from "@/components/section-header";
import { dimensions } from "@/lib/dimensions";

/**
 * 全量榜单页：4 张 v3 维度卡片（综合分 / 经济 / 击杀 / 场景梯度）。
 * 静态导出；LeaderboardView 客户端搜索 + 排序交互。
 */
export default function LeaderboardPage() {
  return (
    <div className="container-page px-4 py-8 sm:px-6 lg:py-10">
      <SectionHeader
        title="Leaderboard"
        enTitle="全量榜单"
        description="v3 四个维度的完整榜单（全部条目展示，不受 top10 限制）。可搜索、点击表头排序、点击条目进入详情页。"
      />
      <LeaderboardView dimensions={dimensions} />
    </div>
  );
}
