"""Fail-closed import of offline agent runtime records into lab artifacts."""

from __future__ import annotations

import contextlib
import io
import json
import re
from pathlib import Path

import pytest

from arena_hero_bench.agent_runtime import (
    AGENT_RUN_EVIDENCE_SCHEMA,
    AGENT_RUN_IMPORT_REPORT_SCHEMA,
    AgentRuntimeImportError,
    import_agent_run,
)
from arena_hero_bench.cli import main as cli_main
from arena_hero_bench.manifest import ArtifactStatus
from arena_hero_bench.storage import FilesystemArtifactStore
from arena_hero_sim.serialization import canonical_json_bytes, content_sha256

FIXTURE = Path(__file__).parent / "fixtures" / "agent_run_records_v1.jsonl"
FIXTURE_TEXT = FIXTURE.read_text(encoding="utf-8")
TENANT = "lab-e2e-a"


def _write(tmp_path: Path, text: str, name: str = "records.jsonl") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _run_cli(*argv: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli_main(list(argv))
    return code, stdout.getvalue(), stderr.getvalue()


def test_import_happy_path_matches_public_fixture() -> None:
    evidence = import_agent_run(FIXTURE, tenant_id=TENANT)

    assert evidence.tenant_id == TENANT
    assert len(evidence.ticks) == 3
    assert [tick.tick for tick in evidence.ticks] == [1, 2, 3]
    assert evidence.ticks[2].submit_result == "rejected"
    assert evidence.ticks[2].submit_error == "local submit refused"
    assert evidence.loop is not None
    assert evidence.loop.last_tick == 3
    assert evidence.loop.stopped_reason == "stream_ended"
    assert evidence.health is None
    assert dict(evidence.provenance) == {
        "agent_commit": "568ebf2",
        "sdk_tag": "v0.3.0a1",
        "schema_version": "1",
    }
    assert evidence.content["schema_version"] == AGENT_RUN_EVIDENCE_SCHEMA
    assert evidence.report["schema_version"] == AGENT_RUN_IMPORT_REPORT_SCHEMA
    assert evidence.content_sha256 == content_sha256(evidence.content)
    assert evidence.report["artifact_sha256"] == evidence.content_sha256


def test_import_is_deterministic() -> None:
    first = import_agent_run(FIXTURE, tenant_id=TENANT)
    second = import_agent_run(FIXTURE, tenant_id=TENANT)

    assert first.content_sha256 == second.content_sha256
    assert canonical_json_bytes(first.content) == canonical_json_bytes(second.content)
    assert json.dumps(first.report, sort_keys=True) == json.dumps(second.report, sort_keys=True)


def test_cli_smoke_is_deterministic() -> None:
    first_code, first_out, _ = _run_cli(
        "import-agent-run", "--records", str(FIXTURE), "--tenant", TENANT
    )
    second_code, second_out, _ = _run_cli(
        "import-agent-run", "--records", str(FIXTURE), "--tenant", TENANT
    )

    assert first_code == 0
    assert second_code == 0
    assert first_out == second_out
    first_report = json.loads(first_out)
    assert first_report["tenant_id"] == TENANT
    assert first_report["tick_count"] == 3
    assert re.fullmatch(r"[0-9a-f]{64}", first_report["artifact_sha256"])


def test_torn_tail_fails_closed(tmp_path: Path) -> None:
    path = _write(tmp_path, FIXTURE_TEXT.rstrip("\n"))

    with pytest.raises(AgentRuntimeImportError, match="torn tail"):
        import_agent_run(path, tenant_id=TENANT)


def test_unknown_schema_version_fails_closed(tmp_path: Path) -> None:
    mutated = FIXTURE_TEXT.replace('"schemaVersion":1', '"schemaVersion":2', 1)
    path = _write(tmp_path, mutated)

    with pytest.raises(AgentRuntimeImportError, match="schemaVersion"):
        import_agent_run(path, tenant_id=TENANT)


def test_unknown_record_type_fails_closed(tmp_path: Path) -> None:
    mutated = FIXTURE_TEXT.replace('"recordType":"tick"', '"recordType":"bogus"', 1)
    path = _write(tmp_path, mutated)

    with pytest.raises(AgentRuntimeImportError, match="recordType"):
        import_agent_run(path, tenant_id=TENANT)


def test_duplicate_tick_fails_closed(tmp_path: Path) -> None:
    lines = FIXTURE_TEXT.splitlines()
    duplicate = lines[0] + "\n"
    mutated = duplicate + "\n".join(lines) + "\n"
    path = _write(tmp_path, mutated)

    with pytest.raises(AgentRuntimeImportError, match="duplicate agent tick"):
        import_agent_run(path, tenant_id=TENANT)


def test_duplicate_loop_fails_closed(tmp_path: Path) -> None:
    mutated = FIXTURE_TEXT + FIXTURE_TEXT.splitlines()[-1] + "\n"
    path = _write(tmp_path, mutated)

    with pytest.raises(AgentRuntimeImportError, match="more than one loop"):
        import_agent_run(path, tenant_id=TENANT)


def test_tenant_mismatch_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(AgentRuntimeImportError, match="tenantId"):
        import_agent_run(FIXTURE, tenant_id="another-tenant")

    mismatched = FIXTURE_TEXT.replace(TENANT, "another-tenant", 1)
    path = _write(tmp_path, mismatched)
    with pytest.raises(AgentRuntimeImportError, match="tenantId"):
        import_agent_run(path, tenant_id=TENANT)


def test_corrupt_line_fails_closed(tmp_path: Path) -> None:
    lines = FIXTURE_TEXT.splitlines()
    mutated = "\n".join(lines[:2]) + "\n{not-json\n" + "\n".join(lines[2:]) + "\n"
    path = _write(tmp_path, mutated)

    with pytest.raises(AgentRuntimeImportError, match="corrupt agent run record"):
        import_agent_run(path, tenant_id=TENANT)


def test_empty_or_recordless_input_fails_closed(tmp_path: Path) -> None:
    empty = _write(tmp_path, "")
    with pytest.raises(AgentRuntimeImportError, match="empty"):
        import_agent_run(empty, tenant_id=TENANT)

    blank = _write(tmp_path, "\n", name="blank.jsonl")
    with pytest.raises(AgentRuntimeImportError, match="no records"):
        import_agent_run(blank, tenant_id=TENANT)


def test_health_happy_path_folds_into_artifact(tmp_path: Path) -> None:
    health = {
        "schemaVersion": 1,
        "ready": True,
        "status": "stopped",
        "tenantId": TENANT,
        "processRunId": "pr-e2e",
        "runId": "run-e2e",
        "startedAtNs": 1720000000000000000,
        "updatedAtNs": 1720000000000000004,
        "lastTick": 3,
        "ticksProcessed": 3,
        "duplicateTicks": 0,
        "outOfOrderTicks": 0,
        "gapTicks": 0,
        "reconnectCount": 0,
        "stoppedReason": "stream_ended",
        "components": [{"name": "recorder", "healthy": True, "message": "ok"}],
        "lastError": None,
        "completed": True,
    }
    health_path = _write(tmp_path, json.dumps(health), name="health.json")
    evidence = import_agent_run(FIXTURE, tenant_id=TENANT, health_path=health_path)

    assert evidence.health is not None
    assert evidence.health["ready"] is True
    assert evidence.health["completed"] is True
    content_health = evidence.content.get("health")
    report_health = evidence.report.get("health")
    assert isinstance(content_health, dict)
    assert isinstance(report_health, dict)
    assert content_health["status"] == "stopped"
    assert report_health["ready"] is True
    assert evidence.content_sha256 == content_sha256(evidence.content)


def test_health_mismatch_and_bad_schema_fail_closed(tmp_path: Path) -> None:
    wrong_tenant = {
        "schemaVersion": 1,
        "ready": True,
        "status": "stopped",
        "tenantId": "another-tenant",
        "completed": True,
    }
    path = _write(tmp_path, json.dumps(wrong_tenant), name="health.json")
    with pytest.raises(AgentRuntimeImportError, match="tenantId"):
        import_agent_run(FIXTURE, tenant_id=TENANT, health_path=path)

    bad_schema = {
        "schemaVersion": 2,
        "ready": True,
        "status": "stopped",
        "tenantId": TENANT,
        "completed": True,
    }
    path2 = _write(tmp_path, json.dumps(bad_schema), name="health2.json")
    with pytest.raises(AgentRuntimeImportError, match="schemaVersion"):
        import_agent_run(FIXTURE, tenant_id=TENANT, health_path=path2)

    missing_field = {
        "schemaVersion": 1,
        "tenantId": TENANT,
        "completed": True,
    }
    path3 = _write(tmp_path, json.dumps(missing_field), name="health3.json")
    with pytest.raises(AgentRuntimeImportError, match="ready"):
        import_agent_run(FIXTURE, tenant_id=TENANT, health_path=path3)


def test_public_output_has_no_paths_or_secrets() -> None:
    evidence = import_agent_run(FIXTURE, tenant_id=TENANT)
    serialized = json.dumps(
        {"content": evidence.content, "report": evidence.report}, sort_keys=True
    )

    assert re.search(r"[A-Za-z]:[\\/]", serialized) is None
    alternate_drive_prefix = chr(67) + chr(58) + chr(92)
    assert alternate_drive_prefix not in serialized
    windows_drive_prefix = chr(68) + chr(58) + chr(92)
    assert windows_drive_prefix not in serialized
    assert "/home" not in serialized
    assert "Users" not in serialized
    assert "sk-" not in serialized
    assert "ghp_" not in serialized


def test_store_writes_content_addressed_artifact(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    code, out, err = _run_cli(
        "import-agent-run",
        "--records",
        str(FIXTURE),
        "--tenant",
        TENANT,
        "--store",
        str(store_root),
    )
    assert code == 0, err
    report = json.loads(out)
    digest = report["artifact_sha256"]

    store = FilesystemArtifactStore(store_root)
    assert store.get(digest) == canonical_json_bytes(
        import_agent_run(FIXTURE, tenant_id=TENANT).content
    )
    manifests = list(store.manifest_records())
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest.content_sha256 == digest
    assert manifest.schema_version == AGENT_RUN_EVIDENCE_SCHEMA
    assert manifest.status is ArtifactStatus.COMPLETE
    assert manifest.publishable is True
    assert dict(manifest.provenance) == {
        "agent_commit": "568ebf2",
        "sdk_tag": "v0.3.0a1",
        "schema_version": "1",
    }


def test_cli_rejects_invalid_tenant() -> None:
    code, out, err = _run_cli(
        "import-agent-run", "--records", str(FIXTURE), "--tenant", "Bad Tenant"
    )

    assert code == 2
    assert out == ""
    assert "error" in err
