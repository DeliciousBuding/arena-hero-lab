import { dimensions, dimensionOf } from "@/lib/dimensions";
import { DimensionCard } from "@/components/dimension-card";

/**
 * 全量榜单页：6 张维度卡片纵向排列，支持 ?dim=<id> 定位到指定维度。
 * （Next 16：searchParams 为 Promise）
 */
export default async function LeaderboardPage({
  searchParams,
}: {
  searchParams: Promise<{ dim?: string }>;
}) {
  const { dim } = await searchParams;
  const focused = dim ? dimensionOf(dim)?.id : undefined;

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-bold tracking-tight text-text-primary sm:text-3xl">
          全量榜单
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-text-secondary">
          六大维度完整榜单（每个维度 10 条全部展示），点击表头可排序，点击条目进入详情页。
          {focused ? ` 当前定位：${dimensionOf(focused)?.title}` : ""}
        </p>
      </header>

      <div className="space-y-6">
        {dimensions.map((dimension) => (
          <div
            key={dimension.id}
            id={`dim-${dimension.id}`}
            className={`scroll-mt-6 rounded-2xl transition-shadow ${
              focused === dimension.id
                ? "shadow-[0_0_0_2px_var(--accent-primary)]"
                : ""
            }`}
          >
            <DimensionCard dimension={dimension} />
          </div>
        ))}
      </div>
    </div>
  );
}
