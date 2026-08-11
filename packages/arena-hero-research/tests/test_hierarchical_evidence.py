"""SolverCertificate and versioned cross-validation evidence tests."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

import arena_hero_research.hierarchical as hierarchical
from arena_hero_research.hierarchical import (
    ClusterMissingPolicy,
    ClusterObservation,
    CrossValidationReport,
    cross_validate_random_intercept,
    fit_random_intercept_with_certificate,
    verify_solver_certificate,
)
from arena_hero_research.hierarchical_artifacts import (
    CrossValidationReportV1,
    CrossValidationStatus,
    SolverCertificate,
    SolverEvaluation,
    SolverEvidenceError,
    SolverStatus,
    ValidationScope,
)
from arena_hero_research.hierarchical_evidence import analyze_hierarchical_evidence


def observations() -> tuple[ClusterObservation, ...]:
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


def test_legacy_cross_validation_entrypoint_is_exactly_compatible() -> None:
    report = cross_validate_random_intercept(outcome_name="score", observations=observations())

    assert type(report) is CrossValidationReport
    assert set(report.to_dict()) == {
        "outcome_name",
        "control_level",
        "treatment_level",
        "cluster_count",
        "observation_count",
        "balanced",
        "path_a_effect",
        "path_b_effect",
        "path_a_between_variance",
        "path_a_error_variance",
        "path_b_between_variance",
        "path_b_error_variance",
        "effect_absolute_difference",
        "variance_absolute_difference",
        "effect_tolerance",
        "variance_tolerance",
        "effect_passed",
        "variance_validated",
        "variance_passed",
        "passed",
        "authoritative",
    }
    assert "schema_version" not in report.to_dict()
    assert "canonical_sha256" not in report.to_dict()


def test_source_and_retained_analysis_identities_are_distinct_and_deterministic() -> None:
    data = (
        *observations(),
        ClusterObservation("score", "dropped", "c0", "control", 9.0),
    )
    first_fit, first = fit_random_intercept_with_certificate(
        outcome_name="score",
        observations=data,
        missing_policy=ClusterMissingPolicy.DROP_CLUSTER,
    )
    second_fit, second = fit_random_intercept_with_certificate(
        outcome_name="score",
        observations=tuple(reversed(data)),
        missing_policy=ClusterMissingPolicy.DROP_CLUSTER,
    )

    assert first_fit.dropped_clusters == 1
    assert first.source_input_sha256 != first.analysis_input_sha256
    assert second.source_input_sha256 == first.source_input_sha256
    assert second.analysis_input_sha256 == first.analysis_input_sha256
    assert second_fit.to_dict() == first_fit.to_dict()


def test_solver_certificate_known_answer_round_trip_and_recomputation() -> None:
    data = observations()
    fit, certificate = fit_random_intercept_with_certificate(
        outcome_name="score", observations=data
    )

    assert fit.canonical_sha256 == (
        "d8e6ab3b4ce189eee6c9d603ca54ff7bcb2a9adac890e85d3b4cdd05507bd42f"
    )
    assert certificate.schema_version == "arena.research.profile-reml-solver-certificate.v1"
    assert certificate.fit_schema_version == "arena.research.random-intercept-fit.v2"
    assert certificate.fit_sha256 == fit.canonical_sha256
    assert certificate.solver_status is SolverStatus.VERIFIED_INTERIOR
    assert certificate.profile_curvature < 0
    assert certificate.kkt_residual <= certificate.kkt_tolerance
    assert certificate.evaluation_count == certificate.iterations + 2
    assert certificate.verify()
    assert SolverCertificate.from_dict(certificate.to_dict()) == certificate
    assert verify_solver_certificate(
        certificate=certificate,
        fit=fit,
        outcome_name="score",
        observations=data,
    )


def test_analytic_score_and_curvature_match_finite_difference_test_oracle() -> None:
    data = observations()
    _, certificate = fit_random_intercept_with_certificate(outcome_name="score", observations=data)
    clusters, _ = hierarchical._prepare_clusters(
        "score", data, ClusterMissingPolicy.FAIL, "control", "treatment"
    )
    cluster_ids, sizes, sums, _ = hierarchical._cluster_sums(clusters, "control", "treatment")
    degrees = sum(sizes) - 2
    point = certificate.candidate_log_lambda
    step = 1e-3

    def objective(value: float) -> float:
        evaluation = hierarchical._profile_reml_evaluation(cluster_ids, sums, degrees, value)
        assert evaluation.valid
        assert evaluation.objective is not None
        return evaluation.objective

    lower = objective(point - step)
    center = objective(point)
    upper = objective(point + step)
    score_oracle = (upper - lower) / (2.0 * step)
    curvature_oracle = (upper - 2.0 * center + lower) / (step * step)

    assert certificate.profile_score == pytest.approx(score_oracle, abs=1e-8)
    assert certificate.profile_curvature == pytest.approx(curvature_oracle, rel=1e-6)


def test_versioned_cross_validation_report_chain_and_status() -> None:
    data = observations()
    fit, certificate = fit_random_intercept_with_certificate(
        outcome_name="score", observations=data
    )
    report = analyze_hierarchical_evidence(outcome_name="score", observations=data).report

    assert report.schema_version == "arena.research.cross-validation-report.v1"
    assert report.fit_sha256 == fit.canonical_sha256
    assert report.certificate_sha256 == certificate.canonical_sha256
    assert report.source_input_sha256 == certificate.source_input_sha256
    assert report.analysis_input_sha256 == certificate.analysis_input_sha256
    assert report.status is CrossValidationStatus.FULLY_VALIDATED
    assert report.validation_scope is ValidationScope.EFFECT_AND_VARIANCE
    assert report.passed
    assert report.verify()
    assert CrossValidationReportV1.from_dict(report.to_dict()) == report


def test_reordered_input_is_exactly_deterministic() -> None:
    data = observations()
    first_fit, first_certificate = fit_random_intercept_with_certificate(
        outcome_name="score", observations=data
    )
    first_report = analyze_hierarchical_evidence(outcome_name="score", observations=data).report
    second_fit, second_certificate = fit_random_intercept_with_certificate(
        outcome_name="score", observations=tuple(reversed(data))
    )
    second_report = analyze_hierarchical_evidence(
        outcome_name="score", observations=tuple(reversed(data))
    ).report

    assert second_fit.to_dict() == first_fit.to_dict()
    assert second_certificate.to_dict() == first_certificate.to_dict()
    assert second_report.to_dict() == first_report.to_dict()


def test_translation_offsets_preserve_numerical_status_not_artifact_identity() -> None:
    data = observations()
    _, baseline = fit_random_intercept_with_certificate(outcome_name="score", observations=data)
    digests = {baseline.canonical_sha256}
    for offset in (1e3, 1e6, 1e8):
        shifted = tuple(replace(item, value=item.value + offset) for item in data)
        _, certificate = fit_random_intercept_with_certificate(
            outcome_name="score", observations=shifted
        )
        report = analyze_hierarchical_evidence(outcome_name="score", observations=shifted).report
        assert certificate.solver_status is baseline.solver_status
        assert certificate.profile_curvature == pytest.approx(
            baseline.profile_curvature, rel=1e-7, abs=1e-8
        )
        assert report.status is CrossValidationStatus.FULLY_VALIDATED
        assert report.passed
        digests.add(certificate.canonical_sha256)
    assert len(digests) == 4


def test_boundary_fit_is_not_promoted_to_validated_evidence() -> None:
    data = tuple(
        item
        for cluster_id, control, treatments in (
            ("c1", 1.0, (3.0, 3.4, 2.6)),
            ("c2", 1.05, (3.05, 3.45, 2.65)),
            ("c3", 0.95, (2.95, 3.35, 2.55)),
            ("c4", 1.02, (3.02, 3.42, 2.62)),
        )
        for item in (
            ClusterObservation("score", cluster_id, "c0", "control", control),
            *(
                ClusterObservation("score", cluster_id, f"t{index}", "treatment", value)
                for index, value in enumerate(treatments)
            ),
        )
    )
    fit, certificate = fit_random_intercept_with_certificate(
        outcome_name="score", observations=data
    )
    report = analyze_hierarchical_evidence(outcome_name="score", observations=data).report

    assert fit.singular and fit.boundary_lambda
    assert certificate.solver_status is SolverStatus.BOUNDARY
    assert report.status is CrossValidationStatus.INDETERMINATE
    assert report.validation_scope is ValidationScope.NONE
    assert not report.passed


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update(extra=True), "keys mismatch"),
        (lambda payload: payload.pop("fit_sha256"), "keys mismatch"),
        (lambda payload: payload.update(iterations=True), "JSON integer"),
        (lambda payload: payload.update(boundary="false"), "JSON boolean"),
        (lambda payload: payload.update(profile_score=math.inf), "finite"),
        (lambda payload: payload.update(fit_sha256="0" * 64), "digest verification"),
    ],
)
def test_solver_certificate_strict_malformed_and_tamper_matrix(mutation, message: str) -> None:
    _, certificate = fit_random_intercept_with_certificate(
        outcome_name="score", observations=observations()
    )
    payload = certificate.to_dict()
    mutation(payload)
    with pytest.raises(SolverEvidenceError, match=message):
        SolverCertificate.from_dict(payload)


@pytest.mark.parametrize(
    "field",
    ["schema_version", "source_input_sha256", "certificate_sha256", "status", "passed"],
)
def test_cross_validation_report_tamper_fails_closed(field: str) -> None:
    report = analyze_hierarchical_evidence(outcome_name="score", observations=observations()).report
    payload = report.to_dict()
    payload[field] = False if field == "passed" else "tampered"
    with pytest.raises((SolverEvidenceError, ValueError)):
        CrossValidationReportV1.from_dict(payload)


def test_strict_json_float_fields_reject_integer_lexical_forms() -> None:
    fit, certificate = fit_random_intercept_with_certificate(
        outcome_name="score", observations=observations()
    )
    report = analyze_hierarchical_evidence(outcome_name="score", observations=observations()).report

    fit_payload = fit.to_dict()
    fit_payload["intercept"] = 1
    with pytest.raises(hierarchical.HierarchicalFitError, match="JSON float"):
        hierarchical.RandomInterceptFit.from_dict(fit_payload)

    observation_payload = observations()[0].to_dict()
    observation_payload["value"] = 1
    with pytest.raises(hierarchical.HierarchicalFitError, match="JSON float"):
        ClusterObservation.from_dict(observation_payload)

    certificate_payload = certificate.to_dict()
    initial_bracket = certificate_payload["initial_bracket"]
    assert isinstance(initial_bracket, list)
    initial_bracket[0] = -30
    with pytest.raises(SolverEvidenceError, match="JSON float"):
        SolverCertificate.from_dict(certificate_payload)

    certificate_payload = certificate.to_dict()
    evaluations = certificate_payload["evaluations"]
    assert isinstance(evaluations, list)
    first_evaluation = evaluations[0]
    assert isinstance(first_evaluation, dict)
    first_evaluation["objective"] = -1
    with pytest.raises(SolverEvidenceError, match="JSON float"):
        SolverCertificate.from_dict(certificate_payload)

    report_payload = report.to_dict()
    report_payload["path_a_effect"] = 1
    with pytest.raises(SolverEvidenceError, match="JSON float"):
        CrossValidationReportV1.from_dict(report_payload)


def test_solver_evaluation_accepts_only_bounded_cross_libm_roundoff() -> None:
    log_lambda = 3.238259894893683
    expected_lambda = math.exp(log_lambda)
    adjacent_lambda = math.nextafter(expected_lambda, math.inf)

    restored = SolverEvaluation.from_dict(
        {
            "log_lambda": log_lambda,
            "lambda_value": adjacent_lambda,
            "valid": True,
            "objective": -1.0,
            "invalid_reason": None,
        }
    )
    assert restored.lambda_value == adjacent_lambda

    with pytest.raises(SolverEvidenceError, match="within 4 ULP"):
        SolverEvaluation.from_dict(
            {
                "log_lambda": log_lambda,
                "lambda_value": expected_lambda * (1.0 + 1e-10),
                "valid": True,
                "objective": -1.0,
                "invalid_reason": None,
            }
        )


def test_literal_hierarchical_known_answer_artifacts() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "hierarchical-known-answers-v1.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["schema_version"] == "arena.research.hierarchical-known-answers.v1"
    expected_statuses = {
        "paired-1x1": ("verified-interior", "fully-validated", True),
        "balanced-repeated": ("verified-interior", "effect-diagnostic", False),
        "allocation-unbalanced": ("verified-interior", "effect-diagnostic", False),
    }
    certificate_numeric_fields = (
        "candidate_log_lambda",
        "candidate_lambda",
        "candidate_objective",
        "profile_score",
        "profile_curvature",
        "kkt_residual",
        "kkt_tolerance",
        "backward_error",
        "newton_correction",
    )
    report_numeric_fields = (
        "path_a_effect",
        "path_b_effect",
        "path_a_between_variance",
        "path_a_error_variance",
        "path_b_between_variance",
        "path_b_error_variance",
        "effect_absolute_difference",
        "variance_absolute_difference",
        "effect_tolerance",
        "variance_tolerance",
    )
    for case in fixture["cases"]:
        data = tuple(ClusterObservation.from_dict(item) for item in case["observations"])
        evidence = analyze_hierarchical_evidence(
            outcome_name=case["outcome_name"], observations=data
        )
        literal_certificate = SolverCertificate.from_dict(case["certificate"])
        literal_report = CrossValidationReportV1.from_dict(case["report"])
        assert literal_certificate.verify()
        assert literal_report.verify()
        assert literal_report.certificate_sha256 == literal_certificate.canonical_sha256

        # Fit v2 remains a byte-stable compatibility contract. The traced solver artifact
        # records libm-sensitive intermediate evaluations, so cross-platform recomputation
        # checks its numerical/scientific contract rather than claiming an identical digest.
        assert evidence.fit.to_dict() == case["fit"]
        assert evidence.certificate.source_input_sha256 == literal_certificate.source_input_sha256
        assert (
            evidence.certificate.analysis_input_sha256 == literal_certificate.analysis_input_sha256
        )
        assert evidence.certificate.fit_sha256 == literal_certificate.fit_sha256
        assert evidence.certificate.optimizer == literal_certificate.optimizer
        assert evidence.certificate.initial_bracket == literal_certificate.initial_bracket
        assert evidence.certificate.tolerance == literal_certificate.tolerance
        assert evidence.certificate.max_iterations == literal_certificate.max_iterations
        assert evidence.certificate.iterations == literal_certificate.iterations
        assert evidence.certificate.evaluation_count == literal_certificate.evaluation_count
        assert (
            evidence.certificate.invalid_evaluation_count
            == literal_certificate.invalid_evaluation_count
        )
        assert evidence.certificate.termination_reason == literal_certificate.termination_reason
        assert evidence.certificate.boundary is literal_certificate.boundary
        assert evidence.certificate.precision_limited is literal_certificate.precision_limited
        assert evidence.certificate.solver_status is literal_certificate.solver_status
        for field_name in certificate_numeric_fields:
            actual = getattr(evidence.certificate, field_name)
            expected = getattr(literal_certificate, field_name)
            if expected is None:
                assert actual is None
            else:
                assert actual == pytest.approx(expected, rel=1e-10, abs=1e-12)
        assert len(evidence.certificate.evaluations) == len(literal_certificate.evaluations)
        for actual, expected in zip(
            evidence.certificate.evaluations,
            literal_certificate.evaluations,
            strict=True,
        ):
            assert actual.valid is expected.valid
            assert actual.invalid_reason == expected.invalid_reason
            assert actual.log_lambda == pytest.approx(expected.log_lambda, rel=1e-10, abs=1e-12)
            assert actual.lambda_value == pytest.approx(expected.lambda_value, rel=1e-10, abs=1e-12)
            if expected.objective is None:
                assert actual.objective is None
            else:
                assert actual.objective == pytest.approx(expected.objective, rel=1e-10, abs=1e-12)

        assert evidence.report.source_input_sha256 == literal_report.source_input_sha256
        assert evidence.report.analysis_input_sha256 == literal_report.analysis_input_sha256
        assert evidence.report.fit_sha256 == literal_report.fit_sha256
        assert evidence.report.design_profile is literal_report.design_profile
        assert evidence.report.validation_scope is literal_report.validation_scope
        assert evidence.report.status is literal_report.status
        assert evidence.report.cluster_count == literal_report.cluster_count
        assert evidence.report.observation_count == literal_report.observation_count
        assert evidence.report.balanced is literal_report.balanced
        assert evidence.report.effect_passed is literal_report.effect_passed
        assert evidence.report.variance_validated is literal_report.variance_validated
        assert evidence.report.variance_passed is literal_report.variance_passed
        assert evidence.report.passed is literal_report.passed
        for field_name in report_numeric_fields:
            assert getattr(evidence.report, field_name) == pytest.approx(
                getattr(literal_report, field_name), rel=1e-10, abs=1e-12
            )

        solver_status, report_status, passed = expected_statuses[case["name"]]
        assert evidence.certificate.solver_status.value == solver_status
        assert evidence.report.status.value == report_status
        assert evidence.report.passed is passed
