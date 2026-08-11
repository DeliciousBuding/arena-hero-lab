from dataclasses import replace

import pytest

from arena_hero_sim import DifferentialMismatch, DifferentialReport


def _mismatch() -> DifferentialMismatch:
    return DifferentialMismatch(field="metrics", reference={"score": 1}, candidate={"score": 2})


def test_differential_report_cannot_publish_or_pass_with_mismatches() -> None:
    with pytest.raises(ValueError, match="fail-closed"):
        DifferentialReport(
            workload_sha256="a" * 64,
            reference_run_sha256="b" * 64,
            candidate_run_sha256="c" * 64,
            mismatches=(_mismatch(),),
            publishable=True,
        )


def test_differential_passed_is_derived_from_strict_publishability() -> None:
    report = DifferentialReport(
        workload_sha256="a" * 64,
        reference_run_sha256="b" * 64,
        candidate_run_sha256="c" * 64,
        mismatches=(),
        publishable=True,
    )
    assert report.passed is True
    with pytest.raises(ValueError, match="boolean"):
        replace(report, publishable=1)  # type: ignore[arg-type]
