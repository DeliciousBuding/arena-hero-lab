"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { ScenarioComparison } from "@/components/scenario-comparison";
import { SectionHeader } from "@/components/section-header";
import { benchData } from "@/lib/bench";
import { Button } from "@/components/ui/button";

/** 首页场景榜收敛：默认前 4 场景（2×2），一键展开全部。 */
export function ScenarioSection() {
  const [expanded, setExpanded] = useState(false);
  const total = benchData.scenarios.length;

  return (
    <section className="mb-16">
      <SectionHeader
        id="scenarios"
        title="Scenario Leaderboards"
        enTitle="场景榜"
        description="每个场景一场独立擂台：按平均名次排序，金银铜徽章 + 资源/刻条。"
      />
      <ScenarioComparison limit={expanded ? undefined : 4} />
      {total > 4 && (
        <div className="mt-6 text-center">
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={() => setExpanded((v) => !v)}
          >
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-180" : ""}`} />
            {expanded ? "收起场景" : `展开全部 ${total} 场景`}
          </Button>
        </div>
      )}
    </section>
  );
}
