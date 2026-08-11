from __future__ import annotations

import math

import pytest

from arena_hero_research.analysis import EffectEstimate
from arena_hero_research.attribution import attribute_behavior_effects
from arena_hero_research.figures import (
    FIGURE_SCHEMA,
    FigureArtifact,
    FigureError,
    FigureKind,
    render_attribution_bar_figure,
    render_effect_forest_figure,
)


def _estimate(
    name: str,
    mean: float,
    lower: float,
    upper: float,
    adjusted_p: float = 0.03,
) -> EffectEstimate:
    return EffectEstimate(
        outcome_name=name,
        hypothesis_id=f"h-{name}",
        sample_size=30,
        mean_difference=mean,
        standardized_effect=1.0,
        confidence_lower=lower,
        confidence_upper=upper,
        confidence_level=0.95,
        raw_p_value=adjusted_p,
        adjusted_p_value=adjusted_p,
        meets_minimum_effect=True,
        estimator="paired-mean-difference",
        effect_size_method="cohen-dz",
        ci_method="paired-bootstrap-percentile",
        p_value_method="paired-normal-approximation",
    )


def _estimates() -> tuple[EffectEstimate, ...]:
    return (
        _estimate("kill_rate", 2.0, 0.8, 3.2),
        _estimate("resources_per_tick", -1.0, -2.2, 0.2, 0.12),
    )


def _attribution():
    return attribute_behavior_effects(
        estimates=_estimates(),
        outcome_dimensions={
            "kill_rate": "population_forces",
            "resources_per_tick": "resource_growth",
        },
    )


def test_forest_figure_is_deterministic_and_headless() -> None:
    first = render_effect_forest_figure(
        estimates=_estimates(), figure_id="fig-forest", title="Effect forest"
    )
    second = render_effect_forest_figure(
        estimates=_estimates(), figure_id="fig-forest", title="Effect forest"
    )
    assert first.svg == second.svg
    assert first.content_sha256 == second.content_sha256
    assert first.verify()
    assert first.svg.startswith("<svg")
    assert first.svg.rstrip().endswith("</svg>")
    assert "kill_rate" in first.svg
    assert "resources_per_tick" in first.svg


def test_forest_figure_escapes_xml_in_labels() -> None:
    estimates = (_estimate("kill<rate>&evil", 1.0, 0.5, 1.5),)
    figure = render_effect_forest_figure(estimates=estimates, figure_id="fig-escape", title="T")
    assert "<rate>" not in figure.svg
    assert "&lt;rate&gt;" in figure.svg
    assert "&amp;evil" in figure.svg


def test_forest_figure_always_draws_zero_reference_anchor() -> None:
    # All effects strictly positive: the zero reference line must still be
    # present and renderable (deterministic anchor for the figure).
    estimates = (_estimate("kill_rate", 5.0, 3.0, 7.0),)
    figure = render_effect_forest_figure(estimates=estimates, figure_id="fig-zero", title="T")
    assert "stroke-dasharray='4,3'" in figure.svg
    assert figure.verify()


def test_forest_figure_degenerate_range_is_deterministic() -> None:
    # Identical, zero-width intervals must still render without error.
    estimates = (_estimate("kill_rate", 1.0, 1.0, 1.0),)
    first = render_effect_forest_figure(estimates=estimates, figure_id="fig-degen", title="T")
    second = render_effect_forest_figure(estimates=estimates, figure_id="fig-degen", title="T")
    assert first.svg == second.svg
    assert first.verify()


def test_attribution_bar_is_deterministic_and_headless() -> None:
    attribution = _attribution()
    first = render_attribution_bar_figure(
        attribution=attribution, figure_id="fig-att", title="Behavior attribution"
    )
    second = render_attribution_bar_figure(
        attribution=attribution, figure_id="fig-att", title="Behavior attribution"
    )
    assert first.svg == second.svg
    assert first.content_sha256 == second.content_sha256
    assert first.verify()
    assert "population_forces" in first.svg
    assert "resource_growth" in first.svg
    assert "positive" in first.svg
    assert "negative" in first.svg


def test_attribution_bar_rejects_unverified_attribution() -> None:
    attribution = _attribution()
    tampered = {
        **attribution.to_dict(),
        "dimensions": [
            {**item.to_dict(), "adjusted_p_value": 0.5} if index == 0 else item.to_dict()
            for index, item in enumerate(attribution.dimensions)
        ],
    }
    from arena_hero_research.attribution import BehaviorAttribution

    restored = BehaviorAttribution.from_dict(tampered)
    assert not restored.verify()
    with pytest.raises(FigureError, match="digest verification failed"):
        render_attribution_bar_figure(attribution=restored, figure_id="fig-att", title="T")


def test_figure_id_is_normalized_before_digest() -> None:
    figure = render_effect_forest_figure(
        estimates=_estimates(), figure_id="  fig-padded  ", title="T"
    )
    assert figure.figure_id == "fig-padded"
    assert figure.verify()


def test_figure_artifact_round_trip() -> None:
    figure = render_effect_forest_figure(
        estimates=_estimates(), figure_id="fig-rt", title="Round trip"
    )
    restored = FigureArtifact.from_dict(figure.to_dict())
    assert restored == figure
    assert restored.verify()
    assert restored.kind is FigureKind.EFFECT_FOREST
    assert restored.schema_version == FIGURE_SCHEMA


def test_figure_artifact_verify_rejects_tampering() -> None:
    figure = render_effect_forest_figure(estimates=_estimates(), figure_id="fig-tamper", title="T")
    tampered = figure.to_dict()
    tampered["title"] = "changed"
    restored = FigureArtifact.from_dict(tampered)
    assert not restored.verify()


@pytest.mark.parametrize(
    ("estimates", "message"),
    [
        ((), "at least one"),
        (
            (
                _estimate("kill_rate", 1.0, 0.5, 1.5),
                _estimate("kill_rate", 2.0, 1.0, 3.0),
            ),
            "unique outcome names",
        ),
        ((_estimate("kill_rate", math.nan, 0.5, 1.5),), "finite"),
        ((_estimate("kill_rate", 1.0, 2.0, 0.5),), "ordered"),
    ],
)
def test_forest_figure_fails_closed(estimates: tuple[EffectEstimate, ...], message: str) -> None:
    with pytest.raises(FigureError, match=message):
        render_effect_forest_figure(estimates=estimates, figure_id="fig-bad", title="T")


def test_figure_from_dict_rejects_unknown_kind() -> None:
    with pytest.raises(FigureError, match="unsupported figure kind"):
        FigureArtifact.from_dict(
            {
                "schema_version": FIGURE_SCHEMA,
                "figure_id": "fig-x",
                "kind": "pie",
                "title": "T",
                "svg": "<svg/>",
                "content_sha256": "0" * 64,
            }
        )


def test_figure_artifact_is_report_consumable_json() -> None:
    figure = render_effect_forest_figure(estimates=_estimates(), figure_id="fig-json", title="JSON")
    document = figure.to_dict()
    assert set(document) == {
        "schema_version",
        "generator_version",
        "figure_id",
        "kind",
        "title",
        "svg",
        "content_sha256",
    }
    from arena_hero_sim.serialization import canonical_json_bytes

    canonical_json_bytes(document)  # must serialize without error
