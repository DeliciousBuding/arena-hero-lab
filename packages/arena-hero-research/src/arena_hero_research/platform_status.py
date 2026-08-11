"""Deterministic platform status evidence for the public Arena Hero Lab site.

The platform panel aggregates reproducible conformance evidence produced by the
Python agent, simulator, and research packages. It never contains competitive
rankings: conformance and differential evidence describe reproducibility of
deterministic pipelines, not race results.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

PLATFORM_STATUS_SCHEMA = "arena.platform.status.v1"
EXTERNAL_EVIDENCE_SCHEMA = "arena.platform.external-evidence.v1"

AGENT_FIXTURE_FILENAME = "turn_to_plan_known_answers_v1.json"
AGENT_PROVENANCE_FILENAME = "provenance.json"
AGENT_SOURCE_COMMIT = "c7b5ce290baeee7c68377ff8af38ea5f6a40a5d1"
AGENT_SOURCE_COMMIT_SHORT = "c7b5ce2"
AGENT_SDK_NAME = "arena-hero"
AGENT_SDK_VERSION = "0.2.9"
AGENT_PLAN_SHA256 = "6704ea79a7711a01e66bf3534f4d032591ffad41040b92c8431c12c5a76a7a68"
FIT_PAIRED_KNOWN_ANSWER_DIGEST = "d8e6ab3b4ce189eee6c9d603ca54ff7bcb2a9adac890e85d3b4cdd05507bd42f"

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_AGENT_FIXTURE_DIR = (
    _REPO_ROOT
    / "packages"
    / "arena-hero-bench"
    / "tests"
    / "fixtures"
    / "external"
    / "arena-hero-agent"
)


class PlatformStatusError(RuntimeError):
    """Raised when platform status evidence cannot be verified."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlatformStatusError(f"cannot read platform evidence JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise PlatformStatusError(f"platform evidence JSON must be an object: {path.name}")
    return value


def _require(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PlatformStatusError(f"missing or invalid {label}")
    return value


def agent_fixture_dir(fixture_dir: Path | None) -> Path:
    """Resolve the external agent fixture directory (defaults to the frozen copy)."""
    return (fixture_dir or _DEFAULT_AGENT_FIXTURE_DIR).resolve()


def load_agent_conformance(fixture_dir: Path | None = None) -> dict[str, Any]:
    """Load and strictly verify the frozen external agent known-answer evidence.

    Fail-closed on any mismatch: content digest drift, provenance tampering,
    SDK version drift, or a plan digest that no longer matches the fixture.
    """
    directory = agent_fixture_dir(fixture_dir)
    fixture_path = directory / AGENT_FIXTURE_FILENAME
    provenance_path = directory / AGENT_PROVENANCE_FILENAME

    fixture_bytes = fixture_path.read_bytes()
    fixture_sha = _sha256_bytes(fixture_bytes)
    provenance = _load_json(provenance_path)

    if provenance.get("schema") != EXTERNAL_EVIDENCE_SCHEMA:
        raise PlatformStatusError("external agent evidence schema mismatch")

    source = provenance.get("source")
    if not isinstance(source, dict):
        raise PlatformStatusError("external agent evidence missing source metadata")
    source_repo = _require(source.get("repository"), "source repository")
    source_commit = _require(source.get("commit"), "source commit")
    sdk = source.get("sdk")
    if not isinstance(sdk, dict):
        raise PlatformStatusError("external agent evidence missing SDK metadata")
    sdk_name = _require(sdk.get("name"), "SDK name")
    sdk_version = _require(sdk.get("version"), "SDK version")

    artifact = provenance.get("artifact")
    if not isinstance(artifact, dict):
        raise PlatformStatusError("external agent evidence missing artifact metadata")
    declared_sha = _require(artifact.get("sha256"), "artifact sha256")
    declared_plan_sha = _require(artifact.get("plan_sha256"), "plan sha256")

    if source_repo != "DeliciousBuding/arena-hero-agent":
        raise PlatformStatusError("external agent evidence repository mismatch")
    if source_commit != AGENT_SOURCE_COMMIT:
        raise PlatformStatusError("external agent evidence source commit mismatch")
    if sdk_name != AGENT_SDK_NAME or sdk_version != AGENT_SDK_VERSION:
        raise PlatformStatusError("external agent evidence SDK version mismatch")
    if declared_sha != fixture_sha:
        raise PlatformStatusError("external agent fixture content digest mismatch")
    if declared_plan_sha != AGENT_PLAN_SHA256:
        raise PlatformStatusError("external agent plan digest mismatch")

    fixture = _load_json(fixture_path)
    embedded_plan_sha = _require(fixture.get("plan_sha256"), "fixture plan sha256")
    embedded_rules = _require(fixture.get("rules_version"), "fixture rules version")
    if embedded_plan_sha != AGENT_PLAN_SHA256:
        raise PlatformStatusError("external agent fixture plan digest mismatch")

    return {
        "name": "arena-hero-agent",
        "repository": "https://github.com/DeliciousBuding/arena-hero-agent",
        "source_commit": source_commit,
        "source_commit_short": _require(source.get("commit_short"), "source commit short"),
        "sdk": {"name": sdk_name, "version": sdk_version},
        "evidence": {
            "fixture_sha256": fixture_sha,
            "plan_sha256": AGENT_PLAN_SHA256,
            "rules_version": embedded_rules,
            "captured_on": _require(artifact.get("captured_on"), "captured on"),
        },
        "status": "verified-conformance",
        "note": (
            "Turn-to-plan adaptation chain passes the frozen known-answer digest. "
            "Competitive race results require the complete policy and tick loop; "
            "this card is not a ranking entry."
        ),
    }


def _episode_rows(run: Any) -> list[dict[str, Any]]:
    from arena_hero_sim.reference_contracts import ReplayArtifactIdentity

    rows: list[dict[str, Any]] = []
    for episode in run.episodes:
        semantic_sha = ReplayArtifactIdentity.from_artifact_refs(
            episode.artifact_refs
        ).semantic_sha256
        rows.append(
            {
                "episode_id": episode.episode_id,
                "final_world_sha256": episode.final_world_sha256,
                "semantic_sha256": semantic_sha,
            }
        )
    return rows


def compute_simulator_differential(*, batch_size: int = 9) -> dict[str, Any]:
    """Run the canonical reference vs optimized differential on real code.

    The report is recomputed on every generation and fails closed if the two
    backends disagree. No timing or throughput is recorded here; performance
    claims are local-diagnostic only and never published.
    """
    from arena_hero_sim.optimized import OptimizedEngineBackend
    from arena_hero_sim.reference_workload import (
        CANONICAL_REFERENCE_WORKLOAD_SHA256,
        BackendWorkloadRunner,
        ReferenceWorkloadRunner,
        canonical_reference_scenario_registry,
        canonical_reference_workload_manifest,
        compare_workload_runs,
    )

    scenarios = canonical_reference_scenario_registry()
    manifest = canonical_reference_workload_manifest()
    reference = ReferenceWorkloadRunner(scenarios).run(manifest, batch_size=batch_size)
    candidate = BackendWorkloadRunner(scenarios, OptimizedEngineBackend(scenarios.scenarios)).run(
        manifest, batch_size=batch_size
    )
    report = compare_workload_runs(reference, candidate)

    if not report.passed or not report.publishable:
        raise PlatformStatusError("simulator differential evidence failed")
    if report.workload_sha256 != CANONICAL_REFERENCE_WORKLOAD_SHA256:
        raise PlatformStatusError("simulator canonical workload identity drifted")

    reference_rows = _episode_rows(reference)
    candidate_rows = _episode_rows(candidate)
    if reference_rows != candidate_rows:
        raise PlatformStatusError("simulator episode order or semantic identity drifted")

    episode_order_sha = _sha256_text(
        json.dumps(reference_rows, sort_keys=True, separators=(",", ":"))
    )

    reference_backend = reference.backend
    candidate_backend = candidate.backend
    case_ids = sorted({episode.case_id for episode in candidate.episodes})

    return {
        "status": "verified-differential",
        "backends": {
            "reference": reference_backend.backend_id,
            "optimized": candidate_backend.backend_id,
        },
        "engine_versions": {
            "reference": reference_backend.engine_version,
            "optimized": candidate_backend.engine_version,
        },
        "workload": {
            "id": manifest.workload_id,
            "version": manifest.workload_version,
            "sha256": report.workload_sha256,
        },
        "evidence": {
            "case_count": len(case_ids),
            "episode_count": len(candidate.episodes),
            "batch_size": batch_size,
            "differential_sha256": report.sha256,
            "episode_order_sha256": episode_order_sha,
            "differential_schema": report.schema_version,
        },
        "performance": {
            "status": "local-diagnostic-only",
            "note": "Speed measurements are machine-local diagnostics and are not published as production claims.",
        },
    }


def _paired_cluster_observations() -> tuple[Any, ...]:
    from arena_hero_research.hierarchical import ClusterObservation

    values = (
        ("c1", 1.0, 2.2),
        ("c2", 2.0, 3.6),
        ("c3", 1.5, 2.8),
        ("c4", 2.5, 4.1),
    )
    return tuple(
        item
        for cluster_id, control, treatment in values
        for item in (
            ClusterObservation("score", cluster_id, "c0", "control", control),
            ClusterObservation("score", cluster_id, "t0", "treatment", treatment),
        )
    )


def compute_research_evidence() -> dict[str, Any]:
    """Recompute the hierarchical evidence chain and verify it end to end.

    The chain is recomputed from the canonical paired observations, verified in
    memory, committed to a scratch ledger, and restored through the
    authoritative loader. Digests and statuses are recorded only after every
    check passes; a known-answer digest pins Fit v2 identity.
    """
    from arena_hero_research.hierarchical import SolverStatus
    from arena_hero_research.hierarchical_artifacts import (
        CROSS_VALIDATION_REPORT_SCHEMA,
        FIT_SCHEMA,
        SOLVER_CERTIFICATE_SCHEMA,
        CrossValidationStatus,
        ValidationScope,
    )
    from arena_hero_research.hierarchical_evidence import (
        analyze_hierarchical_evidence,
        commit_hierarchical_analysis_evidence,
        load_hierarchical_analysis_evidence,
    )
    from arena_hero_research.storage import FilesystemResearchLedgerStorage

    observations = _paired_cluster_observations()
    evidence = analyze_hierarchical_evidence(
        outcome_name="score",
        observations=observations,
        control_level="control",
        treatment_level="treatment",
    )
    if not evidence.verify():
        raise PlatformStatusError("research evidence chain verification failed")

    fit = evidence.fit
    certificate = evidence.certificate
    report = evidence.report

    if fit.schema_version != FIT_SCHEMA:
        raise PlatformStatusError("research fit schema drift")
    if certificate.schema_version != SOLVER_CERTIFICATE_SCHEMA:
        raise PlatformStatusError("research certificate schema drift")
    if report.schema_version != CROSS_VALIDATION_REPORT_SCHEMA:
        raise PlatformStatusError("research report schema drift")
    if fit.canonical_sha256 != FIT_PAIRED_KNOWN_ANSWER_DIGEST:
        raise PlatformStatusError("research fit known-answer digest mismatch")
    if certificate.solver_status is not SolverStatus.VERIFIED_INTERIOR:
        raise PlatformStatusError("research solver certificate is not verified interior")
    if not certificate.has_verified_root_conditions():
        raise PlatformStatusError("research solver root conditions are not verified")
    if report.status is not CrossValidationStatus.FULLY_VALIDATED:
        raise PlatformStatusError("research cross-validation report is not fully validated")
    if report.validation_scope is not ValidationScope.EFFECT_AND_VARIANCE:
        raise PlatformStatusError("research validation scope drift")

    with tempfile.TemporaryDirectory() as tmp:
        storage = FilesystemResearchLedgerStorage(Path(tmp))
        commit_hierarchical_analysis_evidence(
            storage,
            operation_id="platform-status-known-answer-paired-1x1",
            study_id="platform-status-known-answer",
            analysis_id="paired-1x1",
            evidence=evidence,
            expected_head_sha256=None,
        )
        restored = load_hierarchical_analysis_evidence(
            storage,
            study_id="platform-status-known-answer",
            analysis_id="paired-1x1",
        )
        if not restored.verify():
            raise PlatformStatusError("research evidence ledger round-trip failed")

    return {
        "status": "verified-evidence-chain",
        "fit": {
            "schema_version": fit.schema_version,
            "canonical_sha256": fit.canonical_sha256,
        },
        "certificate": {
            "schema_version": certificate.schema_version,
            "canonical_sha256": certificate.canonical_sha256,
            "solver_status": certificate.solver_status.value,
            "boundary": bool(certificate.boundary),
            "precision_limited": bool(certificate.precision_limited),
        },
        "report": {
            "schema_version": report.schema_version,
            "canonical_sha256": report.canonical_sha256,
            "status": report.status.value,
            "passed": bool(report.passed),
        },
        "evidence": {
            "schema": "arena.research.hierarchical-evidence.v1",
            "round_trip_verified": True,
        },
    }


def build_platform_status(
    *,
    agent: dict[str, Any] | None = None,
    simulator: dict[str, Any] | None = None,
    research: dict[str, Any] | None = None,
    source_date: str = "2026-08-11",
) -> dict[str, Any]:
    """Assemble the deterministic platform status document."""
    if agent is None:
        agent = load_agent_conformance()
    if simulator is None:
        simulator = compute_simulator_differential()
    if research is None:
        research = compute_research_evidence()
    if agent.get("status") != "verified-conformance":
        raise PlatformStatusError("agent conformance is not verified")
    if simulator.get("status") != "verified-differential":
        raise PlatformStatusError("simulator differential is not verified")
    if research.get("status") != "verified-evidence-chain":
        raise PlatformStatusError("research evidence chain is not verified")

    return {
        "schema": PLATFORM_STATUS_SCHEMA,
        "source_date": source_date,
        "agent": agent,
        "simulator": simulator,
        "research": research,
        "trust_boundary": {
            "statement": (
                "Conformance and differential evidence describe reproducibility of "
                "deterministic pipelines, not competitive match results."
            ),
            "competitive_rankings": (
                "Only the leaderboard section reflects real match outcomes; platform "
                "cards never alter or replace competitive ranks."
            ),
        },
    }


def write_platform_status(platform: dict[str, Any], output: Path) -> None:
    """Write platform status deterministically (stable key order, no timestamps)."""
    text = json.dumps(platform, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")


def generate_platform_status(
    output: Path,
    *,
    agent_fixture_dir: Path | None = None,
    batch_size: int = 9,
) -> dict[str, Any]:
    """Generate and write the platform status document."""
    platform = build_platform_status(
        agent=load_agent_conformance(agent_fixture_dir),
        simulator=compute_simulator_differential(batch_size=batch_size),
        research=compute_research_evidence(),
    )
    write_platform_status(platform, output)
    return platform
