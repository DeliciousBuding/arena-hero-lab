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
