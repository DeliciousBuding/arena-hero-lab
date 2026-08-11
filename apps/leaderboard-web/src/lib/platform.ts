/**
 * 平台面板静态数据层：直接 import scripts/generate_platform.py 生成的
 * platform.json（arena.platform.status.v2）。所有徽章与摘要均来自
 * 确定性验证证据，不包含任何 mock 或竞争排名。
 */
import rawPlatform from "@/data/platform.json";

export interface AgentEvidence {
  fixture_canonical_sha256: string;
  plan_sha256: string;
  rules_version: string;
  captured_on: string;
}

export interface AgentCard {
  name: string;
  repository: string;
  source_commit: string;
  source_commit_short: string;
  sdk: { name: string; version: string };
  evidence: AgentEvidence;
  status: string;
  note: string;
}

export interface SimulatorCard {
  status: string;
  backends: { reference: string; optimized: string };
  engine_versions: { reference: string; optimized: string };
  workload: { id: string; version: string; sha256: string };
  evidence: {
    case_count: number;
    episode_count: number;
    batch_size: number;
    differential_sha256: string;
    episode_order_sha256: string;
    differential_schema: string;
  };
  performance: { status: string; note: string };
}

export interface ResearchCard {
  status: string;
  fit: { schema_version: string; canonical_sha256: string };
  certificate: {
    schema_version: string;
    canonical_sha256: string;
    solver_status: string;
    boundary: boolean;
    precision_limited: boolean;
  };
  report: { schema_version: string; canonical_sha256: string; status: string; passed: boolean };
  evidence: { schema: string; round_trip_verified: boolean };
}

export interface PlatformStatus {
  schema: string;
  source_date: string;
  agent: AgentCard;
  simulator: SimulatorCard;
  research: ResearchCard;
  trust_boundary: { statement: string; competitive_rankings: string };
}

export const platformData = rawPlatform as PlatformStatus;

/** 展示用短摘要：前 10 位即可区分，完整值在详情页可见。 */
export function shortSha(full: string): string {
  return full.slice(0, 10);
}

/**
 * 平台面板渲染文案：数据源（platform.json）保留英文证据字段，展示层统一中文产品口吻。
 * 信任边界保持显式：一致性 ≠ 竞技名次；性能/耗时仅为本机诊断。
 */
export const platformCopy = {
  agentNote:
    "回合→计划适配链已通过冻结已知答案摘要一致性校验。竞技跑分需完整策略与回合循环，本卡不代表竞技名次。",
  simulatorNote: "性能与耗时仅为本机诊断，不构成生产性能声明。",
  trustStatement: "一致性与差分证据描述确定性管线的可复现性，不代表竞技比赛结果。",
  trustCompetitiveRankings:
    "仅排行榜区反映真实比赛结果；平台卡片不参与、不改变竞争排名。",
} as const;

/** 展示用短仓库名：从完整 URL 提取 owner/repo，防止移动端溢出；href 仍使用完整 URL。 */
export function shortRepo(repository: string): string {
  try {
    const short = new URL(repository).pathname.replace(/^\/+/, "").replace(/\/+$/, "");
    return short || repository;
  } catch {
    return repository;
  }
}
