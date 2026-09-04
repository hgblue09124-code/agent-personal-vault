import pytest
from pathlib import Path
from agent_personal_vault.providers import LocalFilesystemProvider, ICloudDriveProvider
from agent_personal_vault.sync import VaultSyncEngine
from agent_personal_vault.models import Envelope


def test_timestamp_tie_breaking(tmp_path: Path):
    lp = LocalFilesystemProvider(tmp_path / "local")
    rp = ICloudDriveProvider(icloud_root=tmp_path / "remote")

    # Identical timestamps, different payload
    env_l = Envelope(
        id="tie-1",
        type="knowledge",
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T12:00:00Z",
        data={"domain": "test", "topic": "tie", "statement": "Local payload"}
    )
    env_r = Envelope(
        id="tie-1",
        type="knowledge",
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T12:00:00Z",
        data={"domain": "test", "topic": "tie", "statement": "Remote payload"}
    )

    lp.put(env_l)
    rp.put(env_r)

    engine = VaultSyncEngine(lp, rp)
    report = engine.sync()

    assert report.conflict_count == 1
    assert len(report.conflicts) == 1

    # Local wins primary on tie, remote saved as conflict copy
    assert lp.get("knowledge", "tie-1").data["statement"] == "Local payload"
    assert rp.get("knowledge", "tie-1").data["statement"] == "Local payload"

    c_id = report.conflicts[0].conflict_copy_id
    assert lp.exists("knowledge", c_id)
    assert lp.get("knowledge", c_id).data["statement"] == "Remote payload"


def test_sync_report_metrics_and_error_tracking(tmp_path: Path):
    lp = LocalFilesystemProvider(tmp_path / "local")
    rp = ICloudDriveProvider(icloud_root=tmp_path / "remote")

    # Put valid entity
    lp.put(Envelope(id="k1", type="knowledge", data={"domain": "d", "topic": "t", "statement": "s"}))

    engine = VaultSyncEngine(lp, rp)
    report = engine.sync()

    assert report.uploaded_count == 1
    assert report.failed_count == 0
    assert len(report.errors) == 0
