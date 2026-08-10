"""Research runs and reproducible result bundles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from types import MappingProxyType

from arena_hero_research.analysis import DataQualityReport, EffectEstimate
from arena_hero_research.contracts import OutcomeRole, Preregistration, ResearchRunStatus
from arena_hero_sim.serialization import JsonValue, content_sha256, to_json_value


class ResearchBundleError(ValueError):
    pass


class AnalysisPlanMismatchError(ResearchBundleError):
    pass


def _sha(value: str, field_name: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _public_metadata(value: Mapping[str, JsonValue], field_name: str) -> Mapping[str, JsonValue]:
    converted = to_json_value(value)
    if not isinstance(converted, dict) or not converted:
        raise ValueError(f"{field_name} must be a non-empty JSON object")
    prohibited = {"secret", "token", "password", "credential"}

    def reject_sensitive_keys(item: JsonValue) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if any(term in key.casefold() for term in prohibited):
                    raise ValueError(f"{field_name} contains a prohibited sensitive key")
                reject_sensitive_keys(nested)
        elif isinstance(item, list):
            for nested in item:
                reject_sensitive_keys(nested)

    reject_sensitive_keys(converted)
    return MappingProxyType(converted)


@dataclass(frozen=True, slots=True)
class ResearchRun:
    run_id: str
    preregistration: Preregistration
    frozen_config_sha256: str
    source_build_sha256: str
    input_data_sha256: str
    environment_sha256: str
    sbom_sha256: str
    status: ResearchRunStatus

    def __post_init__(self) -> None:
        normalized_run_id = self.run_id.strip()
        if not normalized_run_id:
            raise ValueError("run_id must not be empty")
        object.__setattr__(self, "run_id", normalized_run_id)
        if not self.preregistration.verify():
            raise ValueError("preregistration digest verification failed")
        for field_name in (
            "frozen_config_sha256",
            "source_build_sha256",
            "input_data_sha256",
            "environment_sha256",
            "sbom_sha256",
        ):
            object.__setattr__(self, field_name, _sha(getattr(self, field_name), field_name))


@dataclass(frozen=True, slots=True)
class ResultBundle:
    schema_version: str
    run: ResearchRun
    preregistration_sha256: str
    analysis_plan_sha256: str
    estimates: tuple[EffectEstimate, ...]
    data_quality: Mapping[str, DataQualityReport]
    provenance: Mapping[str, JsonValue]
    environment: Mapping[str, JsonValue]
    status: ResearchRunStatus
    publishable: bool

    def __post_init__(self) -> None:
        if self.schema_version != "arena.research.result-bundle.v1":
            raise ResearchBundleError("unsupported result bundle schema")
        if self.preregistration_sha256 != self.run.preregistration.canonical_sha256:
            raise ResearchBundleError("result bundle preregistration digest mismatch")
        expected_plan = self.run.preregistration.design.analysis_plan.canonical_sha256()
        if self.analysis_plan_sha256 != expected_plan:
            raise AnalysisPlanMismatchError("result bundle analysis-plan digest mismatch")
        if self.status is not self.run.status:
            raise ResearchBundleError("result bundle status must match research run status")
        if self.status is not ResearchRunStatus.COMPLETE and self.publishable:
            raise ResearchBundleError(f"{self.status.value} result bundles cannot be publishable")
        expected_outcomes = {
            item.name
            for item in self.run.preregistration.design.outcomes
            if item.role is not OutcomeRole.EXPLORATORY
        }
        estimate_outcomes = {item.outcome_name for item in self.estimates}
        if len(estimate_outcomes) != len(self.estimates):
            raise ResearchBundleError("result bundle contains duplicate outcome estimates")
        if estimate_outcomes != expected_outcomes:
            raise ResearchBundleError(
                "result bundle must contain every preregistered confirmatory outcome exactly once"
            )
        if set(self.data_quality) != expected_outcomes:
            raise ResearchBundleError("data-quality reports must match confirmatory outcomes")
        sample_sizes = {item.outcome_name: item.sample_size for item in self.estimates}
        for outcome_name, report in self.data_quality.items():
            if report.outcome_name != outcome_name:
                raise ResearchBundleError("data-quality report outcome does not match its key")
            if report.complete_pairs != sample_sizes[outcome_name]:
                raise ResearchBundleError("estimate sample size must match complete-pair count")
        object.__setattr__(self, "estimates", tuple(self.estimates))
        object.__setattr__(self, "data_quality", MappingProxyType(dict(self.data_quality)))
        object.__setattr__(self, "provenance", _public_metadata(self.provenance, "provenance"))
        object.__setattr__(self, "environment", _public_metadata(self.environment, "environment"))

    @classmethod
    def create(
        cls,
        *,
        run: ResearchRun,
        estimates: tuple[EffectEstimate, ...],
        data_quality: Mapping[str, DataQualityReport],
        provenance: Mapping[str, JsonValue],
        environment: Mapping[str, JsonValue],
        publishable: bool,
    ) -> ResultBundle:
        return cls(
            schema_version="arena.research.result-bundle.v1",
            run=run,
            preregistration_sha256=run.preregistration.canonical_sha256,
            analysis_plan_sha256=run.preregistration.design.analysis_plan.canonical_sha256(),
            estimates=estimates,
            data_quality=data_quality,
            provenance=provenance,
            environment=environment,
            status=run.status,
            publishable=publishable,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "run": {
                "run_id": self.run.run_id,
                "preregistration_sha256": self.run.preregistration.canonical_sha256,
                "frozen_config_sha256": self.run.frozen_config_sha256,
                "source_build_sha256": self.run.source_build_sha256,
                "input_data_sha256": self.run.input_data_sha256,
                "environment_sha256": self.run.environment_sha256,
                "sbom_sha256": self.run.sbom_sha256,
                "status": self.run.status.value,
            },
            "preregistration_sha256": self.preregistration_sha256,
            "analysis_plan_sha256": self.analysis_plan_sha256,
            "estimates": [to_json_value(asdict(item)) for item in self.estimates],
            "data_quality": {
                name: to_json_value(asdict(report)) for name, report in self.data_quality.items()
            },
            "provenance": dict(self.provenance),
            "environment": dict(self.environment),
            "status": self.status.value,
            "publishable": self.publishable,
        }

    def bundle_sha256(self) -> str:
        return content_sha256(self.to_dict())
