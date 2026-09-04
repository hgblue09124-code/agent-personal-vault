import pytest
from pathlib import Path
from agent_personal_vault.providers import LocalFilesystemProvider, ICloudDriveProvider


@pytest.mark.parametrize("provider_cls", [LocalFilesystemProvider, ICloudDriveProvider])
def test_path_security_rejections(tmp_path: Path, provider_cls):
    provider = provider_cls(tmp_path)

    unsafe_ids = [
        "../evil",
        "../../evil",
        "/etc/passwd",
        "C:\\windows\\system32",
        "nested/path/id",
        ".hidden_file",
        "",
    ]

    for bad_id in unsafe_ids:
        # Get
        with pytest.raises(ValueError, match="Invalid or unsafe entity_id"):
            provider.get("identity", bad_id)

        # Delete
        with pytest.raises(ValueError, match="Invalid or unsafe entity_id"):
            provider.delete("identity", bad_id)

        # Exists
        with pytest.raises(ValueError, match="Invalid or unsafe entity_id"):
            provider.exists("identity", bad_id)

        # Get Metadata
        with pytest.raises(ValueError, match="Invalid or unsafe entity_id"):
            provider.get_metadata("identity", bad_id)
