/**
 * 指标配置层：把"展示哪些指标、怎么格式化、颜色/单位/反转"集中在一处。
 * 所有图表（热图 / 详情页小图 / 场景对比）从这里取配置，新增指标只改这一个文件。
 * 数据源恒为场景级 perEntry（ScenarioEntryStat）字段，键与 results.json 契约对齐。
 */
import type { ScenarioEntryStat } from "@/lib/bench";

export type ScenarioMetricKey = keyof ScenarioEntryStat;

export interface ScenarioMetricConfig {
  /** 对应 ScenarioEntryStat 字段名（数据契约键）。 */
  key: ScenarioMetricKey;
  label: string;
  unit: string;
  /** 展示小数位（0–3）。 */
  digits: number;
  note: string;
  /** 数值越小越好（如名次）→ 热图色阶反转（小值深色）。 */
  invert?: boolean;
  /** 是否出现在首页热图的指标切换器里。 */
  inHeatmap?: boolean;
}

/** 场景级指标注册表（顺序即展示顺序）。 */
export const SCENARIO_METRICS: ScenarioMetricConfig[] = [
  {
    key: "resourcesPerTick",
    label: "资源/刻",
    unit: "res/tick",
    digits: 3,
    note: "场景级平均资源采集速率",
    inHeatmap: true,
  },
  {
    key: "killRate",
    label: "击杀率",
    unit: "kill/match",
    digits: 2,
    note: "场景级场均击杀",
    inHeatmap: true,
  },
  {
    key: "avgRank",
    label: "平均名次",
    unit: "rank",
    digits: 2,
    note: "场景级平均名次（越小越好，色阶反转）",
    invert: true,
    inHeatmap: true,
  },
  {
    key: "populationPeak",
    label: "人口峰值",
    unit: "units",
    digits: 1,
    note: "场景级平均人口峰值",
  },
];

/** 热图可用指标（标记 inHeatmap 的注册表项）。 */
export const HEATMAP_METRICS = SCENARIO_METRICS.filter((m) => m.inHeatmap === true);

/** 取场景统计的该指标原始值（未参赛返回 null）。 */
export function metricValueOf(
  stat: ScenarioEntryStat | null | undefined,
  key: ScenarioMetricKey,
): number | null {
  return stat?.[key] ?? null;
}
