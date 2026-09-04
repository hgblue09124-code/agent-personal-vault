import pytest
from pathlib import Path
from agent_personal_vault.providers import (
    ICloudDriveProvider,
    StorageUnavailableError,
)
from agent_personal_vault.models import Envelope


def test_icloud_evicted_placeholder_handling(tmp_path: Path):
    provider = ICloudDriveProvider(icloud_root=tmp_path)

    # Simulate an evicted iCloud ubiquitous placeholder file (.mem-1.json.icloud)
    placeholder_path = tmp_path / "memory" / ".mem-1.json.icloud"
    env_content = Envelope(
        id="mem-1",
        type="memory",
        data={"subject": "Evicted Memory", "content": "Ubiquitous content"}
    ).to_dict()

    import json
    placeholder_path.write_text(json.dumps(env_content), encoding="utf-8")

    # Check is_evicted
    assert provider.is_evicted("memory", "mem-1") is True
    assert provider.exists("memory", "mem-1") is True

    # Metadata reflects evicted state
    meta = provider.get_metadata("memory", "mem-1")
    assert meta["icloud_status"] == "evicted_placeholder"
    assert meta["is_downloaded"] is False

    # Get without auto_download raises StorageUnavailableError
    with pytest.raises(StorageUnavailableError, match="evicted/not yet downloaded"):
        provider.get("memory", "mem-1")

    # Trigger download
    downloaded = provider.trigger_download("memory", "mem-1")
    assert downloaded is True

    # Now normal file exists and placeholder is unlinked
    assert provider.is_evicted("memory", "mem-1") is False
    retrieved = provider.get("memory", "mem-1")
    assert retrieved is not None
    assert retrieved.data["subject"] == "Evicted Memory"
