import pytest

from arena_hero_sim import MICROBENCHMARK_SCHEMA, MicrobenchmarkReport


def _report(**changes: object) -> MicrobenchmarkReport:
    values: dict[str, object] = {
        "schema_version": MICROBENCHMARK_SCHEMA,
        "benchmark": "contract-validation-and-placeholder-batch-dispatch",
        "backend_id": "reference-placeholder",
        "engine_version": "0.1.0-placeholder",
        "episodes_per_repeat": 10,
        "repeats": 3,
        "batch_size": 4,
        "durations_ns": (10, 20, 30),
        "median_ns": 20,
        "p95_ns": 30,
        "episodes_per_second": 500_000_000.0,
        "python_version": "3.12",
        "platform": "test-platform",
        "production_claim": False,
    }
    values.update(changes)
    return MicrobenchmarkReport(**values)  # type: ignore[arg-type]


def test_microbenchmark_report_accepts_only_self_consistent_raw_evidence() -> None:
    report = _report()
    assert report.durations_ns == (10, 20, 30)
    assert report.production_claim is False


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"production_claim": True}, "production"),
        ({"durations_ns": (10, 20)}, "one sample per repeat"),
        ({"durations_ns": (10, 0, 30)}, "positive integer"),
        ({"median_ns": 21}, "summaries"),
        ({"p95_ns": 20}, "summaries"),
        ({"episodes_per_second": -1.0}, "raw-duration median"),
        ({"episodes_per_second": 1.0}, "raw-duration median"),
    ],
)
def test_microbenchmark_report_rejects_forged_or_contradictory_evidence(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _report(**changes)
