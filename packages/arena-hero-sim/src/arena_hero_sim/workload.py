"""Backend-neutral, content-addressed simulator workload contracts."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Self

from arena_hero_sim.contracts import RulesetRef, SimulationRequest, SimulatorConfig
from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value

WORKLOAD_MANIFEST_SCHEMA = "arena.sim.workload-manifest.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def _text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _identifier(value: str, field_name: str) -> str:
    normalized = _text(value, field_name)
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"{field_name} must be a lowercase portable identifier")
    return normalized


def _sha256(value: str, field_name: str) -> str:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _int_field(value: object, field_name: str, default: int) -> int:
    candidate = default if value is None else value
    if isinstance(candidate, bool) or not isinstance(candidate, int | str):
        raise ValueError(f"{field_name} must be an integer")
    try:
        return int(candidate)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an integer") from exc


def _frozen_str_map(value: Mapping[str, str], field_name: str) -> Mapping[str, str]:
    normalized = {
        _identifier(str(key), f"{field_name} key"): _text(str(item), f"{field_name} value")
        for key, item in value.items()
    }
    return MappingProxyType(dict(sorted(normalized.items())))


@dataclass(frozen=True, slots=True)
class WorkloadCase:
    """One backend-neutral deterministic scenario in a workload manifest."""

    case_id: str
    scenario_sha256: str
    seed: int
    max_ticks: int
    contestant_ids: tuple[str, ...]
    repetitions: int = 1
    requested_features: frozenset[str] = field(default_factory=frozenset)
    parameters: Mapping[str, str] = field(default_factory=dict)
    labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", _identifier(self.case_id, "case_id"))
        object.__setattr__(
            self, "scenario_sha256", _sha256(self.scenario_sha256, "scenario_sha256")
        )
        if self.seed < 0:
            raise ValueError("seed must be non-negative")
        if self.max_ticks < 1:
            raise ValueError("max_ticks must be positive")
        contestants = tuple(_identifier(item, "contestant_id") for item in self.contestant_ids)
        if not contestants:
            raise ValueError("at least one contestant is required")
        if len(contestants) != len(set(contestants)):
            raise ValueError("contestant_ids must be unique")
        object.__setattr__(self, "contestant_ids", contestants)
        if self.repetitions < 1:
            raise ValueError("repetitions must be positive")
        features = frozenset(
            _identifier(item, "requested_feature") for item in self.requested_features
        )
        object.__setattr__(self, "requested_features", features)
        object.__setattr__(self, "parameters", _frozen_str_map(self.parameters, "parameters"))
        object.__setattr__(self, "labels", _frozen_str_map(self.labels, "labels"))

    def to_dict(self) -> dict[str, JsonValue]:
        value = to_json_value(
            {
                "case_id": self.case_id,
                "scenario_sha256": self.scenario_sha256,
                "seed": self.seed,
                "max_ticks": self.max_ticks,
                "contestant_ids": self.contestant_ids,
                "repetitions": self.repetitions,
                "requested_features": sorted(self.requested_features),
                "parameters": self.parameters,
                "labels": self.labels,
            }
        )
        if not isinstance(value, dict):
            raise TypeError("workload case serialization must produce an object")
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        contestants = value.get("contestant_ids")
        features = value.get("requested_features", ())
        parameters = value.get("parameters", {})
        labels = value.get("labels", {})
        if not isinstance(contestants, (list, tuple)):
            raise ValueError("contestant_ids must be an array")
        if not isinstance(features, (list, tuple, set, frozenset)):
            raise ValueError("requested_features must be an array")
        if not isinstance(parameters, Mapping):
            raise ValueError("parameters must be an object")
        if not isinstance(labels, Mapping):
            raise ValueError("labels must be an object")
        return cls(
            case_id=str(value.get("case_id", "")),
            scenario_sha256=str(value.get("scenario_sha256", "")),
            seed=_int_field(value.get("seed"), "seed", -1),
            max_ticks=_int_field(value.get("max_ticks"), "max_ticks", 0),
            contestant_ids=tuple(str(item) for item in contestants),
            repetitions=_int_field(value.get("repetitions"), "repetitions", 1),
            requested_features=frozenset(str(item) for item in features),
            parameters={str(key): str(item) for key, item in parameters.items()},
            labels={str(key): str(item) for key, item in labels.items()},
        )


@dataclass(frozen=True, slots=True)
class WorkloadManifest:
    """Frozen workload identity shared by reference and optimized backends."""

    workload_id: str
    workload_version: str
    ruleset: RulesetRef
    cases: tuple[WorkloadCase, ...]
    schema_version: str = WORKLOAD_MANIFEST_SCHEMA
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workload_id", _identifier(self.workload_id, "workload_id"))
        object.__setattr__(
            self, "workload_version", _text(self.workload_version, "workload_version")
        )
        object.__setattr__(self, "schema_version", _text(self.schema_version, "schema_version"))
        cases = tuple(self.cases)
        if not cases:
            raise ValueError("workload manifest must contain at least one case")
        case_ids = tuple(item.case_id for item in cases)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("workload case_id values must be unique")
        object.__setattr__(self, "cases", cases)
        object.__setattr__(self, "metadata", _frozen_str_map(self.metadata, "metadata"))

    @property
    def episode_count(self) -> int:
        return sum(case.repetitions for case in self.cases)

    @property
    def sha256(self) -> str:
        return content_sha256(self.to_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        value = to_json_value(
            {
                "schema_version": self.schema_version,
                "workload_id": self.workload_id,
                "workload_version": self.workload_version,
                "ruleset": {
                    "name": self.ruleset.name,
                    "version": self.ruleset.version,
                    "rules_sha256": self.ruleset.rules_sha256,
                },
                "cases": [case.to_dict() for case in self.cases],
                "metadata": self.metadata,
            }
        )
        if not isinstance(value, dict):
            raise TypeError("workload manifest serialization must produce an object")
        return value

    def iter_requests(
        self,
        *,
        backend_id: str,
        engine_version: str,
        protocol_version: str,
        deterministic: bool = True,
    ) -> Iterator[SimulationRequest]:
        """Expand cases deterministically while preserving backend-neutral episode ids."""

        normalized_backend = _identifier(backend_id, "backend_id")
        normalized_engine = _text(engine_version, "engine_version")
        normalized_protocol = _text(protocol_version, "protocol_version")
        workload_sha256 = self.sha256
        for case in self.cases:
            for repetition in range(case.repetitions):
                episode_digest = content_sha256(
                    {
                        "workload_sha256": workload_sha256,
                        "case_id": case.case_id,
                        "repetition": repetition,
                    }
                )
                episode_id = f"episode-{episode_digest}"
                request_id = "request-" + content_sha256(
                    {"episode_id": episode_id, "backend_id": normalized_backend}
                )
                labels = {
                    **dict(case.labels),
                    "workload_id": self.workload_id,
                    "workload_version": self.workload_version,
                    "workload_sha256": workload_sha256,
                    "workload_case_id": case.case_id,
                    "workload_repetition": str(repetition),
                }
                yield SimulationRequest(
                    request_id=request_id,
                    episode_id=episode_id,
                    config=SimulatorConfig(
                        backend_id=normalized_backend,
                        engine_version=normalized_engine,
                        ruleset=self.ruleset,
                        seed=case.seed,
                        max_ticks=case.max_ticks,
                        protocol_version=normalized_protocol,
                        deterministic=deterministic,
                        requested_features=case.requested_features,
                        parameters=case.parameters,
                    ),
                    initial_state_sha256=case.scenario_sha256,
                    input_artifact_sha256=case.scenario_sha256,
                    contestant_ids=case.contestant_ids,
                    labels=labels,
                )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> Self:
        ruleset = value.get("ruleset")
        cases = value.get("cases")
        metadata = value.get("metadata", {})
        if not isinstance(ruleset, Mapping):
            raise ValueError("ruleset must be an object")
        if not isinstance(cases, (list, tuple)):
            raise ValueError("cases must be an array")
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be an object")
        if not all(isinstance(item, Mapping) for item in cases):
            raise ValueError("every workload case must be an object")
        return cls(
            schema_version=str(value.get("schema_version", "")),
            workload_id=str(value.get("workload_id", "")),
            workload_version=str(value.get("workload_version", "")),
            ruleset=RulesetRef(
                name=str(ruleset.get("name", "")),
                version=str(ruleset.get("version", "")),
                rules_sha256=str(ruleset.get("rules_sha256", "")),
            ),
            cases=tuple(WorkloadCase.from_dict(item) for item in cases),
            metadata={str(key): str(item) for key, item in metadata.items()},
        )
