import pytest
from pathlib import Path
from agent_personal_vault.providers import LocalFilesystemProvider, ICloudDriveProvider, StorageCorruptionError
from agent_personal_vault.sync import VaultSyncEngine
from agent_personal_vault.models import Envelope, Tombstone


def test_local_delete_sync_remote_deleted(tmp_path: Path):
    lp = LocalFilesystemProvider(tmp_path / "local")
    rp = ICloudDriveProvider(icloud_root=tmp_path / "icloud")

    env = Envelope(id="del-sync-1", type="goal", data={"title": "Goal to delete", "status": "ACTIVE"})
    lp.put(env)
    rp.put(env)

    # Delete locally
    lp.delete("goal", "del-sync-1")
    assert not lp.exists("goal", "del-sync-1")
    assert lp.get_tombstone("goal", "del-sync-1") is not None

    # Sync
    engine = VaultSyncEngine(lp, rp)
    report1 = engine.sync()

    assert report1.deleted_count == 1
    assert not rp.exists("goal", "del-sync-1")
    assert rp.get_tombstone("goal", "del-sync-1") is not None

    # Repeated sync does NOT resurrect
    report2 = engine.sync()
    assert report2.deleted_count == 0
    assert report2.identical_count == 1
    assert not lp.exists("goal", "del-sync-1")
    assert not rp.exists("goal", "del-sync-1")


def test_remote_delete_sync_local_deleted(tmp_path: Path):
    lp = LocalFilesystemProvider(tmp_path / "local")
    rp = ICloudDriveProvider(icloud_root=tmp_path / "icloud")

    env = Envelope(id="del-sync-2", type="task", data={"title": "Task to delete", "status": "PENDING"})
    lp.put(env)
    rp.put(env)

    # Delete remotely
    rp.delete("task", "del-sync-2")
    assert not rp.exists("task", "del-sync-2")

    engine = VaultSyncEngine(lp, rp)
    report = engine.sync()

    assert report.deleted_count == 1
    assert not lp.exists("task", "del-sync-2")
    assert lp.get_tombstone("task", "del-sync-2") is not None


def test_update_vs_delete_conflict(tmp_path: Path):
    lp = LocalFilesystemProvider(tmp_path / "local")
    rp = ICloudDriveProvider(icloud_root=tmp_path / "icloud")

    # Local puts an updated entity after remote tombstone timestamp
    env_updated = Envelope(
        id="upd-del-1",
        type="memory",
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-05T12:00:00Z",  # Newer
        data={"subject": "Updated Memory", "content": "New content"}
    )
    lp.put(env_updated)

    ts_remote = Tombstone(
        entity_type="memory",
        entity_id="upd-del-1",
        deleted_at="2025-01-02T12:00:00Z",  # Older
        deleted_by="user"
    )
    rp.put_tombstone(ts_remote)

    engine = VaultSyncEngine(lp, rp)
    report = engine.sync()

    assert report.conflict_count == 1
    assert len(report.conflicts) == 1

    # Active update wins and propagates to remote
    assert rp.exists("memory", "upd-del-1")
    assert rp.get("memory", "upd-del-1").data["subject"] == "Updated Memory"
    assert rp.get_tombstone("memory", "upd-del-1") is None


def test_corrupted_tombstone_handling(tmp_path: Path):
    lp = LocalFilesystemProvider(tmp_path / "local")

    corrupt_ts_path = tmp_path / "local" / ".tombstones" / "identity" / "corrupt-id.json"
    corrupt_ts_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_ts_path.write_text("{ corrupt tombstone ...", encoding="utf-8")

    with pytest.raises(StorageCorruptionError):
        lp.get_tombstone("identity", "corrupt-id")

    with pytest.raises(StorageCorruptionError):
        lp.list_tombstones("identity")
