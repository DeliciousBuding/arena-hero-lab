import { benchData } from "@/lib/bench";
import { Badge } from "@/components/ui/badge";
import { Stat, StatHint, StatLabel, StatValue } from "@/components/ui/stat";

interface KillAggregate {
  readonly kills: number;
  readonly deaths: number;
  readonly topVictim: { readonly id: string; readonly label: string; readonly count: number } | null;
  readonly topKiller: { readonly id: string; readonly label: string; readonly count: number } | null;
  readonly victims: readonly { readonly id: string; readonly label: string; readonly count: number }[];
  readonly killers: readonly { readonly id: string; readonly label: string; readonly count: number }[];
}

function contestantLabel(id: string): string {
  return benchData.contestants.find((c) => c.id === id)?.label ?? id;
}

/** 聚合该 entry 跨全部场次的击杀/被击杀统计 */
function aggregateKillStats(contestantId: string): KillAggregate {
  let kills = 0;
  let deaths = 0;
  const victimCounts = new Map<string, number>();
  const killerCounts = new Map<string, number>();
  for (const scenario of benchData.scenarios) {
    for (const match of scenario.matches) {
      // Total kills come from the terminal player stats (always available);
      // per-victim/killer breakdown requires killEvents (may be empty if the
      // battery was run before the kill-event derivation fix).
      const playerStats = match.players?.[contestantId];
      if (playerStats) {
        kills += playerStats.kills ?? 0;
      }
      for (const event of match.killEvents ?? []) {
        if (event.destroyedBy.includes(contestantId)) {
          if (event.victim !== undefined && event.victim !== contestantId) {
            victimCounts.set(event.victim, (victimCounts.get(event.victim) ?? 0) + 1);
          }
        }
        if (event.victim === contestantId) {
          deaths += 1;
          for (const killer of event.destroyedBy) {
            if (killer !== contestantId) {
              killerCounts.set(killer, (killerCounts.get(killer) ?? 0) + 1);
            }
          }
        }
      }
    }
  }
  const victims = [...victimCounts.entries()]
    .map(([id, count]) => ({ id, label: contestantLabel(id), count }))
    .sort((a, b) => b.count - a.count);
  const killers = [...killerCounts.entries()]
    .map(([id, count]) => ({ id, label: contestantLabel(id), count }))
    .sort((a, b) => b.count - a.count);
  return {
    kills,
    deaths,
    topVictim: victims[0] ?? null,
    topKiller: killers[0] ?? null,
    victims,
    killers,
  };
}

/** 击杀贡献统计面板（server component，跨全部场次聚合 killEvents） */
export function KillStats({ contestantId }: { contestantId: string }) {
  const stats = aggregateKillStats(contestantId);
  const totalMatches = benchData.scenarios.reduce((n, s) => n + s.matches.length, 0);
  if (stats.kills === 0 && stats.deaths === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        跨 {totalMatches} 场无核心摧毁事件（纯经济/防守型，或未参与斩首）。
      </p>
    );
  }
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat>
          <StatLabel>击杀</StatLabel>
          <StatValue className="text-rank-gold">{stats.kills}</StatValue>
          <StatHint>次 Core 斩首</StatHint>
        </Stat>
        <Stat>
          <StatLabel>被击杀</StatLabel>
          <StatValue className="text-rank-bronze">{stats.deaths}</StatValue>
          <StatHint>次 Core 被毁</StatHint>
        </Stat>
        <Stat>
          <StatLabel>主要猎物</StatLabel>
          <StatValue className="text-base">
            {stats.topVictim ? stats.topVictim.label : "—"}
          </StatValue>
          <StatHint>{stats.topVictim ? `${stats.topVictim.count} 次击杀` : "无斩首"}</StatHint>
        </Stat>
        <Stat>
          <StatLabel>主要威胁</StatLabel>
          <StatValue className="text-base">
            {stats.topKiller ? stats.topKiller.label : "—"}
          </StatValue>
          <StatHint>{stats.topKiller ? `${stats.topKiller.count} 次被杀` : "从未被斩"}</StatHint>
        </Stat>
      </div>
      {stats.victims.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-medium text-muted-foreground">击杀关系（该 entry → 猎物）</div>
          <div className="flex flex-wrap gap-1.5">
            {stats.victims.map((v) => (
              <Badge key={v.id} variant="brand" className="gap-1">
                → {v.label}
                <span className="tnum font-bold">{v.count}</span>
              </Badge>
            ))}
          </div>
        </div>
      )}
      {stats.killers.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-medium text-muted-foreground">被击杀关系（猎手 → 该 entry）</div>
          <div className="flex flex-wrap gap-1.5">
            {stats.killers.map((k) => (
              <Badge key={k.id} variant="outline" className="gap-1">
                {k.label} →
                <span className="tnum font-bold text-rank-bronze">{k.count}</span>
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
