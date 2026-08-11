"""Platform status generator: determinism, schema, and fail-closed evidence."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from arena_hero_research.platform_status import (
    _DEFAULT_AGENT_FIXTURE_DIR,
    AGENT_PLAN_SHA256,
    AGENT_SDK_VERSION,
    AGENT_SOURCE_COMMIT,
    FIT_PAIRED_KNOWN_ANSWER_DIGEST,
    PLATFORM_STATUS_SCHEMA,
    PlatformStatusError,
    build_platform_status,
    compute_simulator_differential,
    generate_platform_status,
    load_agent_conformance,
    write_platform_status,
)

KNOWN_DIFFERENTIAL_DIGEST = "1b65d7c39a5175f67a9319336746f5e15a2a5279c23163d24d82ca2a00c1ea7e"
KNOWN_FIXTURE_CANONICAL_SHA256 = "6e076a91fa4bc06f3f52ec82c34aa35afb4bea9485c1bf918170c3c42afff080"
# Research certificate/report content addresses are computed over quantized
# floats, so they are reproducible on Windows and Linux (one-ULP libm drift is
# absorbed; semantic tamper still changes the digest).
KNOWN_RESEARCH_CERT_DIGEST = "906ed164c045531a43ec84bdc6badc7873bc8995fd273cea75456b3c050f8f40"
KNOWN_RESEARCH_REPORT_DIGEST = "60fcb5d9b768e142185a6a81311f89fc6e1c73765bf7f1996d2b177f4e0d0754"


@pytest.fixture()
def tmp_output(tmp_path: Path) -> Path:
    return tmp_path / "platform.json"


def _copy_fixture_dir(target: Path) -> Path:
    shutil.copytree(_DEFAULT_AGENT_FIXTURE_DIR, target)
    return target


def test_generator_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    generate_platform_status(first)
    generate_platform_status(second)
    assert first.read_bytes() == second.read_bytes()


def test_platform_schema_and_known_values(tmp_output: Path) -> None:
    platform = generate_platform_status(tmp_output)
    assert platform["schema"] == PLATFORM_STATUS_SCHEMA
    assert platform["source_date"] == "2026-08-11"

    agent = platform["agent"]
    assert agent["status"] == "verified-conformance"
    assert agent["sdk"]["version"] == AGENT_SDK_VERSION
    assert agent["source_commit"] == AGENT_SOURCE_COMMIT
    assert agent["evidence"]["plan_sha256"] == AGENT_PLAN_SHA256
    assert agent["evidence"]["fixture_canonical_sha256"] == KNOWN_FIXTURE_CANONICAL_SHA256

    simulator = platform["simulator"]
    assert simulator["status"] == "verified-differential"
    assert simulator["backends"] == {
        "reference": "reference-engine",
        "optimized": "optimized-python-v1",
    }
    assert simulator["evidence"]["case_count"] == 9
    assert simulator["evidence"]["differential_sha256"] == KNOWN_DIFFERENTIAL_DIGEST
    assert simulator["performance"]["status"] == "local-diagnostic-only"

    research = platform["research"]
    assert research["status"] == "verified-evidence-chain"
    assert research["fit"]["canonical_sha256"] == FIT_PAIRED_KNOWN_ANSWER_DIGEST
    assert research["certificate"]["canonical_sha256"] == KNOWN_RESEARCH_CERT_DIGEST
    assert research["report"]["canonical_sha256"] == KNOWN_RESEARCH_REPORT_DIGEST
    assert research["certificate"]["solver_status"] == "verified-interior"
    assert research["report"]["status"] == "fully-validated"
    assert research["report"]["passed"] is True

    assert "rank" not in platform["trust_boundary"]


def test_write_is_stable_and_readable(tmp_path: Path) -> None:
    platform = build_platform_status()
    output = tmp_path / "out" / "platform.json"
    write_platform_status(platform, output)
    reloaded = json.loads(output.read_text(encoding="utf-8"))
    assert reloaded == platform


def test_checked_in_platform_artifact_matches_generator(tmp_path: Path) -> None:
    """The committed web artifact must equal a fresh generator run (CI freshness)."""
    from arena_hero_research.platform_status import _REPO_ROOT

    checked_in = _REPO_ROOT / "apps" / "leaderboard-web" / "src" / "data" / "platform.json"
    assert checked_in.is_file(), f"missing checked-in platform artifact: {checked_in}"
    generated = generate_platform_status(tmp_path / "fresh.json")
    assert json.loads(checked_in.read_text(encoding="utf-8")) == generated


def test_agent_fixture_tamper_fails_closed(tmp_path: Path) -> None:
    fixture_dir = _copy_fixture_dir(tmp_path / "fixture")
    (fixture_dir / "turn_to_plan_known_answers_v1.json").write_text(
        '{"plan_sha256": "tampered"}', encoding="utf-8"
    )
    with pytest.raises(PlatformStatusError, match="canonical digest mismatch"):
        load_agent_conformance(fixture_dir)


def test_agent_provenance_tamper_fails_closed(tmp_path: Path) -> None:
    fixture_dir = _copy_fixture_dir(tmp_path / "fixture")
    provenance_path = fixture_dir / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["artifact"]["canonical_sha256"] = "0" * 64
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    with pytest.raises(PlatformStatusError, match="canonical digest mismatch"):
        load_agent_conformance(fixture_dir)


def test_agent_provenance_sdk_drift_fails_closed(tmp_path: Path) -> None:
    fixture_dir = _copy_fixture_dir(tmp_path / "fixture")
    provenance_path = fixture_dir / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["source"]["sdk"]["version"] = "0.3.0"
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    with pytest.raises(PlatformStatusError, match="SDK version mismatch"):
        load_agent_conformance(fixture_dir)


def test_simulator_differential_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FailingReport:
        passed = False
        publishable = False
        schema_version = "arena.sim.differential-report.v1"
        sha256 = "0" * 64
        workload_sha256 = "0" * 64

    import arena_hero_sim.reference_workload as reference_workload

    monkeypatch.setattr(
        reference_workload, "compare_workload_runs", lambda *a, **k: _FailingReport()
    )
    with pytest.raises(PlatformStatusError, match="differential evidence failed"):
        compute_simulator_differential()


def test_research_evidence_invalid_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeEvidence:
        def verify(self) -> bool:
            return False

    import arena_hero_research.hierarchical_evidence as hierarchical_evidence

    monkeypatch.setattr(
        hierarchical_evidence,
        "analyze_hierarchical_evidence",
        lambda **kwargs: _FakeEvidence(),
    )
    with pytest.raises(PlatformStatusError, match="verification failed"):
        from arena_hero_research.platform_status import compute_research_evidence

        compute_research_evidence()


def test_build_platform_status_rejects_unverified_cards() -> None:
    with pytest.raises(PlatformStatusError, match="agent conformance"):
        build_platform_status(agent={"status": "failed"}, simulator=None, research=None)


def test_agent_fixture_identity_is_line_ending_insensitive(tmp_path: Path) -> None:
    fixture_dir = _copy_fixture_dir(tmp_path / "fixture")
    fixture_path = fixture_dir / "turn_to_plan_known_answers_v1.json"
    lf_text = fixture_path.read_text(encoding="utf-8")
    assert "\r\n" not in lf_text
    fixture_path.write_text(lf_text.replace("\n", "\r\n"), encoding="utf-8", newline="")
    agent = load_agent_conformance(fixture_dir)
    assert agent["evidence"]["fixture_canonical_sha256"] == KNOWN_FIXTURE_CANONICAL_SHA256


def test_agent_fixture_identity_is_whitespace_insensitive(tmp_path: Path) -> None:
    fixture_dir = _copy_fixture_dir(tmp_path / "fixture")
    fixture_path = fixture_dir / "turn_to_plan_known_answers_v1.json"
    value = json.loads(fixture_path.read_text(encoding="utf-8"))
    compact = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    fixture_path.write_text(compact, encoding="utf-8", newline="")
    agent = load_agent_conformance(fixture_dir)
    assert agent["evidence"]["fixture_canonical_sha256"] == KNOWN_FIXTURE_CANONICAL_SHA256


def test_agent_fixture_semantic_tamper_fails_closed(tmp_path: Path) -> None:
    fixture_dir = _copy_fixture_dir(tmp_path / "fixture")
    fixture_path = fixture_dir / "turn_to_plan_known_answers_v1.json"
    value = json.loads(fixture_path.read_text(encoding="utf-8"))
    value["rules_version"] = "v9.9"
    fixture_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8", newline="")
    with pytest.raises(PlatformStatusError, match="canonical digest mismatch"):
        load_agent_conformance(fixture_dir)


def test_agent_provenance_plan_digest_tamper_fails_closed(tmp_path: Path) -> None:
    fixture_dir = _copy_fixture_dir(tmp_path / "fixture")
    provenance_path = fixture_dir / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["artifact"]["plan_sha256"] = "0" * 64
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
    with pytest.raises(PlatformStatusError, match="plan digest mismatch"):
        load_agent_conformance(fixture_dir)
