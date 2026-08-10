"""Immutable research lifecycle with a fail-closed confirmatory freeze."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise

from arena_hero_research.assignment import AssignmentManifest
from arena_hero_research.contracts import Preregistration
from arena_hero_research.validation import require_identifier, require_sequence, require_sha256
from arena_hero_sim.serialization import JsonValue, content_sha256


class LifecycleError(ValueError):
    pass


class ResearchPhase(StrEnum):
    PILOT = "pilot"
    EXPLORATORY = "exploratory"
    CONFIRMATORY = "confirmatory"
    REPLICATION = "replication"
    COMPLETE = "complete"


_PHASE_ORDER = {
    ResearchPhase.PILOT: 0,
    ResearchPhase.EXPLORATORY: 1,
    ResearchPhase.CONFIRMATORY: 2,
    ResearchPhase.REPLICATION: 3,
    ResearchPhase.COMPLETE: 4,
}


@dataclass(frozen=True, slots=True)
class ResearchLifecycle:
    schema_version: str
    study_id: str
    phase: ResearchPhase
    preregistration_sha256: str
    analysis_plan_sha256: str
    assignment_sha256: str
    confirmatory_freeze_sha256: str | None
    history: tuple[ResearchPhase, ...]
    canonical_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != "arena.research.lifecycle.v1":
            raise LifecycleError("unsupported research lifecycle schema")
        object.__setattr__(self, "study_id", require_identifier(self.study_id, "study_id"))
        for name in (
            "preregistration_sha256",
            "analysis_plan_sha256",
            "assignment_sha256",
            "canonical_sha256",
        ):
            object.__setattr__(self, name, require_sha256(getattr(self, name), name))
        if self.confirmatory_freeze_sha256 is not None:
            object.__setattr__(
                self,
                "confirmatory_freeze_sha256",
                require_sha256(self.confirmatory_freeze_sha256, "confirmatory_freeze_sha256"),
            )
        history = tuple(self.history)
        if not history or history[-1] is not self.phase:
            raise LifecycleError("lifecycle history must end at the current phase")
        if history[0] is not ResearchPhase.PILOT:
            raise LifecycleError("research lifecycle must begin with pilot")
        if any(_PHASE_ORDER[right] != _PHASE_ORDER[left] + 1 for left, right in pairwise(history)):
            raise LifecycleError("research lifecycle phases must advance exactly once")
        if _PHASE_ORDER[self.phase] >= _PHASE_ORDER[ResearchPhase.CONFIRMATORY]:
            if self.confirmatory_freeze_sha256 is None:
                raise LifecycleError("confirmatory and later phases require a frozen commitment")
        elif self.confirmatory_freeze_sha256 is not None:
            raise LifecycleError("pre-confirmatory phases cannot carry a frozen commitment")
        object.__setattr__(self, "history", history)

    @staticmethod
    def _freeze_payload(
        preregistration: Preregistration, assignment: AssignmentManifest
    ) -> dict[str, JsonValue]:
        return {
            "schema_version": "arena.research.confirmatory-freeze.v1",
            "preregistration_sha256": preregistration.canonical_sha256,
            "analysis_plan_sha256": preregistration.design.analysis_plan.canonical_sha256(),
            "assignment_sha256": assignment.canonical_sha256,
            "hypothesis_ids": [item.hypothesis_id for item in preregistration.hypotheses],
            "outcomes": [item.name for item in preregistration.design.outcomes],
            "seeds": list(preregistration.design.replication_plan.seeds),
        }

    @classmethod
    def create(
        cls,
        *,
        study_id: str,
        preregistration: Preregistration,
        assignment: AssignmentManifest,
    ) -> ResearchLifecycle:
        cls._validate_bindings(preregistration, assignment)
        payload: dict[str, JsonValue] = {
            "schema_version": "arena.research.lifecycle.v1",
            "study_id": study_id,
            "phase": ResearchPhase.PILOT.value,
            "preregistration_sha256": preregistration.canonical_sha256,
            "analysis_plan_sha256": preregistration.design.analysis_plan.canonical_sha256(),
            "assignment_sha256": assignment.canonical_sha256,
            "confirmatory_freeze_sha256": None,
            "history": [ResearchPhase.PILOT.value],
        }
        return cls(
            schema_version="arena.research.lifecycle.v1",
            study_id=study_id,
            phase=ResearchPhase.PILOT,
            preregistration_sha256=preregistration.canonical_sha256,
            analysis_plan_sha256=preregistration.design.analysis_plan.canonical_sha256(),
            assignment_sha256=assignment.canonical_sha256,
            confirmatory_freeze_sha256=None,
            history=(ResearchPhase.PILOT,),
            canonical_sha256=content_sha256(payload),
        )

    @staticmethod
    def _validate_bindings(
        preregistration: Preregistration, assignment: AssignmentManifest
    ) -> None:
        if not preregistration.verify():
            raise LifecycleError("preregistration digest verification failed")
        if not assignment.verify():
            raise LifecycleError("assignment digest verification failed")
        if assignment.preregistration_sha256 != preregistration.canonical_sha256:
            raise LifecycleError("assignment is not bound to the preregistration")
        if assignment.design_id != preregistration.design.design_id:
            raise LifecycleError("assignment design mismatch")
        if (
            assignment.analysis_plan_sha256
            != preregistration.design.analysis_plan.canonical_sha256()
        ):
            raise LifecycleError("assignment analysis-plan mismatch")

    def transition(
        self,
        target: ResearchPhase,
        *,
        preregistration: Preregistration,
        assignment: AssignmentManifest,
    ) -> ResearchLifecycle:
        self._validate_bindings(preregistration, assignment)
        if _PHASE_ORDER[target] != _PHASE_ORDER[self.phase] + 1:
            raise LifecycleError("research phases must advance one step at a time")
        if preregistration.canonical_sha256 != self.preregistration_sha256:
            raise LifecycleError("preregistration changed after lifecycle creation")
        if preregistration.design.analysis_plan.canonical_sha256() != self.analysis_plan_sha256:
            raise LifecycleError("analysis plan changed after lifecycle creation")
        if assignment.canonical_sha256 != self.assignment_sha256:
            raise LifecycleError("assignment changed after lifecycle creation")

        freeze_sha = self.confirmatory_freeze_sha256
        expected_freeze = content_sha256(self._freeze_payload(preregistration, assignment))
        if target is ResearchPhase.CONFIRMATORY:
            freeze_sha = expected_freeze
        elif (
            _PHASE_ORDER[target] > _PHASE_ORDER[ResearchPhase.CONFIRMATORY]
            and freeze_sha != expected_freeze
        ):
            raise LifecycleError("confirmatory commitment changed after freeze")

        history = (*self.history, target)
        payload: dict[str, JsonValue] = {
            "schema_version": self.schema_version,
            "study_id": self.study_id,
            "phase": target.value,
            "preregistration_sha256": self.preregistration_sha256,
            "analysis_plan_sha256": self.analysis_plan_sha256,
            "assignment_sha256": self.assignment_sha256,
            "confirmatory_freeze_sha256": freeze_sha,
            "history": [item.value for item in history],
        }
        return ResearchLifecycle(
            schema_version=self.schema_version,
            study_id=self.study_id,
            phase=target,
            preregistration_sha256=self.preregistration_sha256,
            analysis_plan_sha256=self.analysis_plan_sha256,
            assignment_sha256=self.assignment_sha256,
            confirmatory_freeze_sha256=freeze_sha,
            history=history,
            canonical_sha256=content_sha256(payload),
        )

    def verify_against(
        self,
        *,
        preregistration: Preregistration,
        assignment: AssignmentManifest,
    ) -> bool:
        """Verify the lifecycle digest, frozen bindings, and confirmatory commitment."""

        try:
            self._validate_bindings(preregistration, assignment)
        except (TypeError, ValueError):
            return False
        if not self.verify():
            return False
        if (
            self.preregistration_sha256 != preregistration.canonical_sha256
            or self.analysis_plan_sha256 != preregistration.design.analysis_plan.canonical_sha256()
            or self.assignment_sha256 != assignment.canonical_sha256
        ):
            return False
        if _PHASE_ORDER[self.phase] < _PHASE_ORDER[ResearchPhase.CONFIRMATORY]:
            return self.confirmatory_freeze_sha256 is None
        expected = content_sha256(self._freeze_payload(preregistration, assignment))
        return self.confirmatory_freeze_sha256 == expected

    def payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "study_id": self.study_id,
            "phase": self.phase.value,
            "preregistration_sha256": self.preregistration_sha256,
            "analysis_plan_sha256": self.analysis_plan_sha256,
            "assignment_sha256": self.assignment_sha256,
            "confirmatory_freeze_sha256": self.confirmatory_freeze_sha256,
            "history": [item.value for item in self.history],
        }

    def verify(self) -> bool:
        return content_sha256(self.payload()) == self.canonical_sha256

    def to_dict(self) -> dict[str, JsonValue]:
        return {**self.payload(), "canonical_sha256": self.canonical_sha256}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ResearchLifecycle:
        history = require_sequence(value["history"], "history")
        freeze = value.get("confirmatory_freeze_sha256")
        return cls(
            schema_version=str(value["schema_version"]),
            study_id=str(value["study_id"]),
            phase=ResearchPhase(str(value["phase"])),
            preregistration_sha256=str(value["preregistration_sha256"]),
            analysis_plan_sha256=str(value["analysis_plan_sha256"]),
            assignment_sha256=str(value["assignment_sha256"]),
            confirmatory_freeze_sha256=None if freeze is None else str(freeze),
            history=tuple(ResearchPhase(str(item)) for item in history),
            canonical_sha256=str(value["canonical_sha256"]),
        )
