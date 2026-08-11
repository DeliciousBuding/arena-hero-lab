"""Deterministic headless figure generation for research results (P3-12).

Figures are emitted as hand-built SVG (stdlib only): text output needs no
display server, font resolution, or rendering backend, so identical inputs
produce byte-identical files with a stable content digest on every platform.
Layout derives only from the supplied data plus fixed geometry constants,
which makes the figures reproducible anchors for reports and the leaderboard.

Two figure kinds are provided:

- ``effect-forest``: per-outcome paired mean difference with bootstrap
  confidence interval whiskers around a zero reference line;
- ``attribution-bar``: per-dimension contribution weights from a verified
  :class:`~arena_hero_research.attribution.BehaviorAttribution` document.

All text is XML-escaped before it is embedded, and every coordinate is derived
deterministically from the data (no timestamps, randomness, or environment
dependence).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from arena_hero_research.analysis import EffectEstimate
from arena_hero_research.attribution import BehaviorAttribution
from arena_hero_research.validation import require_identifier, require_sha256, require_text
from arena_hero_sim.serialization import JsonValue, quantized_content_sha256

FIGURE_SCHEMA: Final = "arena.research.figure.v1"
FIGURE_GENERATOR_VERSION: Final = "0.1.0"

_WIDTH: Final = 720
_ROW_HEIGHT: Final = 28
_MARGIN_TOP: Final = 56
_MARGIN_BOTTOM: Final = 24
_FOREST_LABEL_WIDTH: Final = 220
_FOREST_VALUE_WIDTH: Final = 150
_PLOT_LEFT: Final = 24
_PLOT_RIGHT: Final = 24
_FONT: Final = "font-family='monospace' font-size='12'"


class FigureError(ValueError):
    """Raised when a figure cannot be rendered deterministically."""


class FigureKind(StrEnum):
    """Supported deterministic figure kinds."""

    EFFECT_FOREST = "effect-forest"
    ATTRIBUTION_BAR = "attribution-bar"


def _escape_xml(text: str) -> str:
    """Escape text for safe embedding inside SVG markup."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _format(value: float, decimals: int = 3) -> str:
    """Deterministic short decimal formatting for figure labels."""
    if value == 0.0:
        return "0"
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _require_estimates(estimates: Sequence[EffectEstimate]) -> tuple[EffectEstimate, ...]:
    if not estimates:
        raise FigureError("at least one effect estimate is required")
    rows = tuple(estimates)
    if len({item.outcome_name for item in rows}) != len(rows):
        raise FigureError("effect estimates must have unique outcome names")
    for item in rows:
        for name in ("mean_difference", "confidence_lower", "confidence_upper"):
            if not math.isfinite(getattr(item, name)):
                raise FigureError(f"effect {name} must be finite")
        if item.confidence_lower > item.confidence_upper:
            raise FigureError("effect confidence interval must be ordered")
    return rows


def _svg_header(title: str, height: int) -> str:
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{_WIDTH}' height='{height}' "
        f"viewBox='0 0 {_WIDTH} {height}' {_FONT}>\n"
        f"<rect x='0' y='0' width='{_WIDTH}' height='{height}' fill='white'/>\n"
        f"<text x='{_WIDTH // 2}' y='28' text-anchor='middle' font-weight='bold' "
        f"font-size='14'>{_escape_xml(title)}</text>\n"
    )


def _x_scale(
    rows: Sequence[EffectEstimate],
) -> tuple[float, float, float, float]:
    """Return (low, high, plot_width, zero_x) derived deterministically."""
    # The zero reference line must always be visible, so the range always
    # includes zero; a fully degenerate range gets a deterministic pad.
    low = min(min(item.confidence_lower for item in rows), 0.0)
    high = max(max(item.confidence_upper for item in rows), 0.0)
    span = high - low
    pad = span * 0.05 if span > 0 else 0.5
    low -= pad
    high += pad
    plot_width = _WIDTH - _FOREST_LABEL_WIDTH - _FOREST_VALUE_WIDTH - _PLOT_LEFT - _PLOT_RIGHT
    if high <= low:
        raise FigureError("effect range is not renderable")
    zero_x = _PLOT_LEFT + _FOREST_LABEL_WIDTH + (0.0 - low) / (high - low) * plot_width
    return low, high, plot_width, zero_x


def render_effect_forest_figure(
    *,
    estimates: Sequence[EffectEstimate],
    figure_id: str,
    title: str,
) -> FigureArtifact:
    """Render a deterministic forest plot of paired effects with bootstrap CIs."""
    rows = _require_estimates(estimates)
    low, high, plot_width, zero_x = _x_scale(rows)
    height = _MARGIN_TOP + len(rows) * _ROW_HEIGHT + _MARGIN_BOTTOM
    parts = [_svg_header(title, height)]

    def value_x(value: float) -> float:
        return _PLOT_LEFT + _FOREST_LABEL_WIDTH + (value - low) / (high - low) * plot_width

    parts.append(
        f"<line x1='{_format(zero_x, 2)}' y1='{_format(_MARGIN_TOP - 8, 2)}' x2='{_format(zero_x, 2)}' y2='{_format(height - _MARGIN_BOTTOM + 8, 2)}' stroke='#999999' stroke-width='1' "
        "stroke-dasharray='4,3'/>\n"
    )
    for index, item in enumerate(rows):
        y = _MARGIN_TOP + _ROW_HEIGHT * index + _ROW_HEIGHT // 2
        x_low = value_x(item.confidence_lower)
        x_high = value_x(item.confidence_upper)
        x_mean = value_x(item.mean_difference)
        parts.append(
            f"<text x='{_format(_FOREST_LABEL_WIDTH - 10, 2)}' y='{_format(y + 4, 2)}' text-anchor='end'>{_escape_xml(item.outcome_name)}</text>\n"
        )
        parts.append(
            "<line x1='{x1}' y1='{y}' x2='{x2}' y2='{y}' stroke='black' stroke-width='2'/>\n".format(
                x1=_format(x_low, 2), y=_format(y, 2), x2=_format(x_high, 2)
            )
        )
        parts.append(
            "<line x1='{x}' y1='{y1}' x2='{x}' y2='{y2}' stroke='black' stroke-width='1'/>\n".format(
                x=_format(x_low, 2), y1=_format(y - 5, 2), y2=_format(y + 5, 2)
            )
        )
        parts.append(
            "<line x1='{x}' y1='{y1}' x2='{x}' y2='{y2}' stroke='black' stroke-width='1'/>\n".format(
                x=_format(x_high, 2), y1=_format(y - 5, 2), y2=_format(y + 5, 2)
            )
        )
        parts.append(
            f"<circle cx='{_format(x_mean, 2)}' cy='{_format(y, 2)}' r='4' fill='black'/>\n"
        )
        label = f"{_format(item.mean_difference)} [{_format(item.confidence_lower)}, {_format(item.confidence_upper)}]"
        parts.append(
            f"<text x='{_format(_PLOT_LEFT + _FOREST_LABEL_WIDTH + plot_width + 10, 2)}' y='{_format(y + 4, 2)}' text-anchor='start'>{_escape_xml(label)}</text>\n"
        )
    parts.append(
        f"<text x='{_format(zero_x, 2)}' y='{_format(_MARGIN_TOP - 12, 2)}' text-anchor='middle' font-size='11' fill='#666666'>0</text>\n"
    )
    parts.append("</svg>\n")
    return _build_artifact(
        figure_id=figure_id,
        kind=FigureKind.EFFECT_FOREST,
        title=title,
        svg="".join(parts),
    )


def render_attribution_bar_figure(
    *,
    attribution: BehaviorAttribution,
    figure_id: str,
    title: str,
) -> FigureArtifact:
    """Render a deterministic horizontal bar chart of behavior attribution weights."""
    if not attribution.verify():
        raise FigureError("behavior attribution digest verification failed")
    dimensions = attribution.dimensions
    plot_left = 240
    plot_right = 140
    plot_width = _WIDTH - plot_left - plot_right - _PLOT_LEFT - _PLOT_RIGHT
    height = _MARGIN_TOP + len(dimensions) * _ROW_HEIGHT + _MARGIN_BOTTOM
    parts = [_svg_header(title, height)]
    for index, item in enumerate(dimensions):
        y = _MARGIN_TOP + _ROW_HEIGHT * index + _ROW_HEIGHT // 2
        bar_x = _PLOT_LEFT + plot_left
        bar_width = item.weight * plot_width
        parts.append(
            f"<text x='{_format(plot_left - 10, 2)}' y='{_format(y + 4, 2)}' text-anchor='end'>{_escape_xml(item.dimension.value)}</text>\n"
        )
        parts.append(
            f"<rect x='{_format(bar_x, 2)}' y='{_format(y - 7, 2)}' width='{_format(bar_width, 2)}' height='14' fill='#4a90d9'/>\n"
        )
        label = f"{_format(item.weight * 100, 1)}% ({item.direction})"
        parts.append(
            f"<text x='{_format(bar_x + plot_width + 10, 2)}' y='{_format(y + 4, 2)}' text-anchor='start'>{_escape_xml(label)}</text>\n"
        )
    parts.append("</svg>\n")
    return _build_artifact(
        figure_id=figure_id,
        kind=FigureKind.ATTRIBUTION_BAR,
        title=title,
        svg="".join(parts),
    )


def _build_artifact(
    *,
    figure_id: str,
    kind: FigureKind,
    title: str,
    svg: str,
) -> FigureArtifact:
    # Validation normalizes text by stripping; the content address must be
    # computed over the same normalized payload the artifact will verify.
    normalized_figure_id = figure_id.strip()
    normalized_title = title.strip()
    normalized_svg = svg.strip()
    return FigureArtifact(
        schema_version=FIGURE_SCHEMA,
        figure_id=normalized_figure_id,
        kind=kind,
        title=normalized_title,
        svg=normalized_svg,
        content_sha256=quantized_content_sha256(
            {
                "schema_version": FIGURE_SCHEMA,
                "generator_version": FIGURE_GENERATOR_VERSION,
                "figure_id": normalized_figure_id,
                "kind": kind.value,
                "title": normalized_title,
                "svg": normalized_svg,
            }
        ),
    )


@dataclass(frozen=True, slots=True)
class FigureArtifact:
    """Content-addressed deterministic SVG figure artifact."""

    schema_version: str
    figure_id: str
    kind: FigureKind
    title: str
    svg: str
    content_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != FIGURE_SCHEMA:
            raise FigureError("unsupported figure schema")
        object.__setattr__(self, "figure_id", require_identifier(self.figure_id, "figure_id"))
        if not isinstance(self.kind, FigureKind):
            raise FigureError("figure kind must be a supported FigureKind")
        object.__setattr__(self, "title", require_text(self.title, "title"))
        object.__setattr__(self, "svg", require_text(self.svg, "svg"))
        object.__setattr__(
            self, "content_sha256", require_sha256(self.content_sha256, "content_sha256")
        )

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "generator_version": FIGURE_GENERATOR_VERSION,
            "figure_id": self.figure_id,
            "kind": self.kind.value,
            "title": self.title,
            "svg": self.svg,
        }

    def verify(self) -> bool:
        return quantized_content_sha256(self.payload()) == self.content_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "content_sha256": self.content_sha256}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> FigureArtifact:
        kind_value = value["kind"]
        if not isinstance(kind_value, str):
            raise FigureError("figure kind must be a string")
        try:
            kind = FigureKind(kind_value)
        except ValueError as exc:
            raise FigureError(f"unsupported figure kind: {kind_value!r}") from exc
        return cls(
            schema_version=str(value["schema_version"]),
            figure_id=str(value["figure_id"]),
            kind=kind,
            title=str(value["title"]),
            svg=str(value["svg"]),
            content_sha256=str(value["content_sha256"]),
        )
