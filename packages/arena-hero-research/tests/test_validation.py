from __future__ import annotations

import json

import pytest

from arena_hero_research.ledger import OperationRecord
from arena_hero_research.storage import (
    FrozenResearchRecord,
    ResearchLedgerTransaction,
    ResearchRecordKind,
)
from arena_hero_research.validation import freeze_public_metadata, require_identifier
from arena_hero_sim.serialization import content_sha256, to_json_value


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "API Key",
        "api-key",
        "api_key",
        "apikey",
        "Private Key",
        "private-key",
        "private_key",
        "privatekey",
        "Authorization Header",
        "access credential",
        "User Password",
    ],
)
def test_public_metadata_rejects_separator_and_case_variants_recursively(
    sensitive_key: str,
) -> None:
    with pytest.raises(ValueError, match="sensitive key"):
        freeze_public_metadata(
            {"outer": [{"nested": {sensitive_key: "forbidden"}}]},
            "metadata",
        )


def test_require_identifier_returns_stripped_canonical_value() -> None:
    assert require_identifier(" study ", "study_id") == "study"
    assert require_identifier(" operation-1 ", "operation_id") == "operation-1"


def test_frozen_record_identity_hashes_normalized_identifiers() -> None:
    padded = FrozenResearchRecord.create(
        study_id=" study ",
        kind=ResearchRecordKind.ANALYSIS_PLAN,
        subject_id=" analysis-plan ",
        payload={"schema_version": "test.analysis.v1"},
    )
    canonical = FrozenResearchRecord.create(
        study_id="study",
        kind=ResearchRecordKind.ANALYSIS_PLAN,
        subject_id="analysis-plan",
        payload={"schema_version": "test.analysis.v1"},
    )

    assert padded == canonical
    assert padded.verify()


def test_operation_and_transaction_hash_normalized_identifiers() -> None:
    operation = OperationRecord.create(
        operation_id=" operation-1 ",
        plan_sha256="a" * 64,
        result_sha256s=("b" * 64,),
    )
    transaction = ResearchLedgerTransaction.create(
        sequence=0,
        operation_id=" operation-1 ",
        study_id=" study ",
        record_sha256s=("c" * 64,),
        previous_transaction_sha256=None,
    )

    assert operation.operation_id == "operation-1"
    assert operation.verify()
    assert transaction.operation_id == "operation-1"
    assert transaction.study_id == "study"
    assert transaction.verify()


def test_public_metadata_is_recursively_immutable_and_json_compatible() -> None:
    metadata = freeze_public_metadata(
        {"outer": {"items": [{"value": 1}]}},
        "metadata",
    )
    assert isinstance(metadata, dict)
    outer = metadata["outer"]
    assert isinstance(outer, dict)
    items = outer["items"]
    assert isinstance(items, list)
    nested = items[0]
    assert isinstance(nested, dict)

    with pytest.raises(TypeError, match="immutable"):
        metadata["new"] = 1
    with pytest.raises(TypeError, match="immutable"):
        outer["new"] = 2
    with pytest.raises(TypeError, match="immutable"):
        items.append({"value": 2})
    with pytest.raises(TypeError, match="immutable"):
        nested["value"] = 99

    expected = {"outer": {"items": [{"value": 1}]}}
    assert json.loads(json.dumps(metadata, sort_keys=True)) == expected
    assert to_json_value(metadata) == expected
    assert content_sha256(metadata) == content_sha256(expected)
