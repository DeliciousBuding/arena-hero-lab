from dataclasses import replace

import pytest

from arena_hero_research.analysis import analyze_preregistered_paired_outcomes
from arena_hero_research.contracts import ResearchRunStatus
from arena_hero_research.results import (
    AnalysisPlanMismatchError,
    ResearchBundleError,
    ResearchRun,
    ResultBundle,
)

from .research_fixtures import make_preregistration

_DIGEST = "a" * 64
_OBSERVATIONS = {
    "score": ((1.0, 2.0, 3.0, 4.0), (1.5, 2.25, 4.0, 5.25)),
    "latency": ((10.0, 11.0, 12.0, 13.0), (9.0, 10.0, 10.5, 12.0)),
}


def _run(status: ResearchRunStatus = ResearchRunStatus.COMPLETE) -> ResearchRun:
    return ResearchRun(
        run_id="research-run-1",
        preregistration=make_preregistration(),
        frozen_config_sha256=_DIGEST,
        source_build_sha256="b" * 64,
        input_data_sha256="c" * 64,
        environment_sha256="d" * 64,
        sbom_sha256="e" * 64,
        status=status,
    )


def _analysis(run: ResearchRun):
    return analyze_preregistered_paired_outcomes(
        run.preregistration, _OBSERVATIONS, bootstrap_seed=44
    )


def _bundle(
    status: ResearchRunStatus = ResearchRunStatus.COMPLETE,
    *,
    publishable: bool | None = None,
) -> ResultBundle:
    run = _run(status)
    estimates, quality = _analysis(run)
    return ResultBundle.create(
        run=run,
        estimates=estimates,
        data_quality=quality,
        provenance={"source": "immutable-benchmark-artifact", "generator": "arena-hero-research"},
        environment={"python": "3.12", "platform_class": "local-baseline"},
        publishable=(status is ResearchRunStatus.COMPLETE if publishable is None else publishable),
    )


def test_result_bundle_is_reproducible_and_complete() -> None:
    first = _bundle()
    second = _bundle()
    assert first.bundle_sha256() == second.bundle_sha256()
    assert first.publishable
    assert first.to_dict()["status"] == "complete"


def test_result_bundle_rejects_analysis_plan_hash_mismatch() -> None:
    bundle = _bundle()
    with pytest.raises(AnalysisPlanMismatchError, match="analysis-plan"):
        replace(bundle, analysis_plan_sha256="f" * 64)


def test_result_bundle_rejects_preregistration_hash_mismatch() -> None:
    bundle = _bundle()
    with pytest.raises(ResearchBundleError, match="preregistration"):
        replace(bundle, preregistration_sha256="f" * 64)


def test_result_bundle_requires_all_confirmatory_estimates_and_quality() -> None:
    bundle = _bundle()
    with pytest.raises(ResearchBundleError, match="every preregistered"):
        replace(bundle, estimates=bundle.estimates[:1])
    with pytest.raises(ResearchBundleError, match="data-quality"):
        replace(bundle, data_quality={"score": bundle.data_quality["score"]})


def test_result_bundle_rejects_duplicate_estimate() -> None:
    bundle = _bundle()
    with pytest.raises(ResearchBundleError, match="duplicate"):
        replace(bundle, estimates=(bundle.estimates[0], bundle.estimates[0]))


def test_partial_and_failed_bundles_cannot_be_publishable() -> None:
    for status in (ResearchRunStatus.PARTIAL, ResearchRunStatus.FAILED):
        assert not _bundle(status, publishable=False).publishable
        with pytest.raises(ResearchBundleError, match="cannot be publishable"):
            _bundle(status, publishable=True)


def test_result_bundle_rejects_sensitive_metadata_recursively() -> None:
    run = _run()
    estimates, quality = _analysis(run)
    with pytest.raises(ValueError, match="sensitive key"):
        ResultBundle.create(
            run=run,
            estimates=estimates,
            data_quality=quality,
            provenance={"source": "artifact", "auth": {"api_token": "not-allowed"}},
            environment={"python": "3.12"},
            publishable=True,
        )


def test_research_run_rejects_invalid_digest() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        replace(_run(), sbom_sha256="invalid")


def test_data_quality_sample_count_must_match_estimate() -> None:
    bundle = _bundle()
    mismatched = replace(bundle.data_quality["score"], complete_pairs=99)
    with pytest.raises(ResearchBundleError, match="sample size"):
        replace(bundle, data_quality={**bundle.data_quality, "score": mismatched})
