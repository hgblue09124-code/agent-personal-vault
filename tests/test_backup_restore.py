import pytest
from pathlib import Path
from agent_personal_vault.store import VaultStore
from agent_personal_vault.backup_restore import BackupRestoreManager
from agent_personal_vault.models import Envelope


def test_backup_and_restore(tmp_path: Path):
    vault_dir = tmp_path / "original_vault"
    backup_dir = tmp_path / "backups"
    restore_vault_dir = tmp_path / "restored_vault"

    store = VaultStore(vault_dir)
    store.put(Envelope(id="mem-bk", type="memory", data={"subject": "Backup Test", "content": "Important data"}))

    # Backup
    mgr = BackupRestoreManager(store)
    archive_path = mgr.create_backup(backup_dir)
    assert archive_path.exists()

    manifest_files = list(backup_dir.glob("backup_manifest_*.json"))
    assert len(manifest_files) == 1

    # Restore into clean vault
    restore_store = VaultStore(restore_vault_dir)
    restore_mgr = BackupRestoreManager(restore_store)
    res = restore_mgr.restore_from_backup(archive_path, manifest_path=manifest_files[0])

    assert res["restored_count"] >= 1
    assert restore_store.exists("memory", "mem-bk")
    assert restore_store.get("memory", "mem-bk").data["content"] == "Important data"
