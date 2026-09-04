import pytest
from pathlib import Path
from agent_personal_vault.providers import (
    LocalFilesystemProvider,
    ICloudDriveProvider,
    StorageCorruptionError,
)
from agent_personal_vault.models import Envelope


@pytest.mark.parametrize("provider_cls", [LocalFilesystemProvider, ICloudDriveProvider])
def test_provider_crud_operations(tmp_path: Path, provider_cls):
    provider = provider_cls(tmp_path)
    env = Envelope(
        id="prov-id-1",
        type="identity",
        data={"display_name": "Provider Test User"}
    )

    # 1. Put
    saved = provider.put(env)
    assert saved is True
    assert provider.exists("identity", "prov-id-1")

    # 2. Get
    retrieved = provider.get("identity", "prov-id-1")
    assert retrieved is not None
    assert retrieved.id == "prov-id-1"
    assert retrieved.data["display_name"] == "Provider Test User"

    # 3. Idempotent Put
    saved_again = provider.put(env)
    assert saved_again is False

    # 4. Metadata
    meta = provider.get_metadata("identity", "prov-id-1")
    assert meta is not None
    assert meta["entity_id"] == "prov-id-1"
    assert meta["content_hash"] is not None

    # 5. List
    all_identities = provider.list_all("identity")
    assert len(all_identities) == 1
    assert all_identities[0].id == "prov-id-1"

    # 6. Delete
    deleted = provider.delete("identity", "prov-id-1")
    assert deleted is True
    assert not provider.exists("identity", "prov-id-1")


def test_provider_corrupt_data_handling(tmp_path: Path):
    provider = LocalFilesystemProvider(tmp_path)
    provider.put(Envelope(id="id-ok", type="identity", data={"display_name": "OK"}))

    # Write corrupt JSON file
    corrupt_file = tmp_path / "identity" / "corrupt.json"
    corrupt_file.write_text("{ invalid json ...", encoding="utf-8")

    # Default list_all raises StorageCorruptionError
    with pytest.raises(StorageCorruptionError):
        provider.list_all("identity")

    # With skip_invalid=True
    diagnostics = []
    valid_items = provider.list_all("identity", skip_invalid=True, diagnostics=diagnostics)
    assert len(valid_items) == 1
    assert valid_items[0].id == "id-ok"
    assert len(diagnostics) == 1
