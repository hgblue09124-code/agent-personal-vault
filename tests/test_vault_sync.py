import pytest
from pathlib import Path
from agent_personal_vault.providers import LocalFilesystemProvider, ICloudDriveProvider
from agent_personal_vault.sync import VaultSyncEngine
from agent_personal_vault.models import Envelope


def test_vault_sync_local_and_remote(tmp_path: Path):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "icloud"

    local_p = LocalFilesystemProvider(local_dir)
    remote_p = ICloudDriveProvider(icloud_root=remote_dir)

    # Local-only entity
    local_p.put(Envelope(id="id-local", type="identity", data={"display_name": "Local Only"}))

    # Remote-only entity
    remote_p.put(Envelope(id="id-remote", type="identity", data={"display_name": "Remote Only"}))

    sync_engine = VaultSyncEngine(local_p, remote_p)
    rep1 = sync_engine.sync()

    assert rep1.uploaded_count == 1
    assert rep1.downloaded_count == 1
    assert rep1.conflict_count == 0

    assert remote_p.exists("identity", "id-local")
    assert local_p.exists("identity", "id-remote")

    # Second sync is identical and idempotent
    rep2 = sync_engine.sync()
    assert rep2.uploaded_count == 0
    assert rep2.downloaded_count == 0
    assert rep2.identical_count == 2


def test_vault_sync_divergent_conflict_resolution(tmp_path: Path):
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "icloud"

    local_p = LocalFilesystemProvider(local_dir)
    remote_p = ICloudDriveProvider(icloud_root=remote_dir)

    # Conflicting versions of same entity ID
    env_local = Envelope(
        id="goal-conflict",
        type="goal",
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-02T12:00:00Z",  # Newer
        data={"title": "Local Version Goal", "status": "ACTIVE"}
    )
    env_remote = Envelope(
        id="goal-conflict",
        type="goal",
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T12:00:00Z",  # Older
        data={"title": "Remote Version Goal", "status": "PLANNED"}
    )

    local_p.put(env_local)
    remote_p.put(env_remote)

    sync_engine = VaultSyncEngine(local_p, remote_p)
    rep = sync_engine.sync()

    assert rep.conflict_count == 1
    assert len(rep.conflicts) == 1

    c_rec = rep.conflicts[0]
    assert c_rec.entity_id == "goal-conflict"
    assert "conflict" in c_rec.conflict_copy_id

    # Primary entity in both stores has the newer local version
    ret_local = local_p.get("goal", "goal-conflict")
    ret_remote = remote_p.get("goal", "goal-conflict")
    assert ret_local.data["title"] == "Local Version Goal"
    assert ret_remote.data["title"] == "Local Version Goal"

    # Conflict copy preserved in both stores
    assert local_p.exists("goal", c_rec.conflict_copy_id)
    assert remote_p.exists("goal", c_rec.conflict_copy_id)
    conflict_env = local_p.get("goal", c_rec.conflict_copy_id)
    assert conflict_env.data["title"] == "Remote Version Goal"
