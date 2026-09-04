import pytest
from pathlib import Path
from agent_personal_vault.store import VaultStore
from agent_personal_vault.export_import import VaultExporter, VaultImporter
from agent_personal_vault.models import Envelope


def test_export_import_roundtrip(tmp_path: Path):
    src_vault_dir = tmp_path / "src_vault"
    dst_vault_dir = tmp_path / "dst_vault"
    export_dir = tmp_path / "export_data"

    src_store = VaultStore(src_vault_dir)

    # Populate source vault
    src_store.put(Envelope(id="id-1", type="identity", data={"display_name": "Alice"}))
    src_store.put(Envelope(id="goal-1", type="goal", data={"title": "Master Vault", "status": "ACTIVE"}))

    # Export
    exporter = VaultExporter(src_store)
    exported_path = exporter.export_to_directory(export_dir)
    assert (exported_path / "manifest.json").exists()

    # Import into empty destination store
    dst_store = VaultStore(dst_vault_dir)
    importer = VaultImporter(dst_store)
    res = importer.import_from_directory(exported_path)

    assert res["imported_count"] >= 2
    assert dst_store.exists("identity", "id-1")
    assert dst_store.exists("goal", "goal-1")
    assert dst_store.get("identity", "id-1").data["display_name"] == "Alice"
