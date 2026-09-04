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


def test_active_update_vs_older_tombstone(tmp_path: Path):
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
    report1 = engine.sync()

    assert report1.conflict_count == 1
    assert len(report1.conflicts) == 1

    c_id = report1.conflicts[0].conflict_copy_id

    # Active update wins and propagates to remote
    assert rp.exists("memory", "upd-del-1")
    assert rp.get("memory", "upd-del-1").data["subject"] == "Updated Memory"

    # Divergent conflict copy is persisted on disk in both stores
    assert lp.exists("memory", c_id)
    assert rp.exists("memory", c_id)

    # Repeated sync is 100% idempotent and generates 0 new conflict copies
    report2 = engine.sync()
    assert report2.conflict_count == 0
    assert report2.uploaded_count == 0
    assert report2.downloaded_count == 0

    report3 = engine.sync()
    assert report3.conflict_count == 0


def test_active_update_vs_newer_tombstone(tmp_path: Path):
    lp = LocalFilesystemProvider(tmp_path / "local")
    rp = ICloudDriveProvider(icloud_root=tmp_path / "icloud")

    # Local entity updated at earlier date
    env_old = Envelope(
        id="upd-del-2",
        type="memory",
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-02T12:00:00Z",  # Older
        data={"subject": "Old Memory", "content": "Old content"}
    )
    lp.put(env_old)

    # Remote tombstone deleted at later date
    ts_newer = Tombstone(
        entity_type="memory",
        entity_id="upd-del-2",
        deleted_at="2025-01-05T12:00:00Z",  # Newer
        deleted_by="user"
    )
    rp.put_tombstone(ts_newer)

    engine = VaultSyncEngine(lp, rp)
    report = engine.sync()

    assert report.deleted_count == 1
    assert not lp.exists("memory", "upd-del-2")
    assert not rp.exists("memory", "upd-del-2")
    assert lp.get_tombstone("memory", "upd-del-2") is not None


def test_delete_vs_delete(tmp_path: Path):
    lp = LocalFilesystemProvider(tmp_path / "local")
    rp = ICloudDriveProvider(icloud_root=tmp_path / "icloud")

    ts_l = Tombstone(entity_type="goal", entity_id="g-dvd", deleted_at="2025-01-01T10:00:00Z")
    ts_r = Tombstone(entity_type="goal", entity_id="g-dvd", deleted_at="2025-01-01T10:00:00Z")

    lp.put_tombstone(ts_l)
    rp.put_tombstone(ts_r)

    engine = VaultSyncEngine(lp, rp)
    report = engine.sync()

    assert report.identical_count == 1
    assert report.conflict_count == 0
