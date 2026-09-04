import pytest
from pathlib import Path
from agent_personal_vault.providers import LocalFilesystemProvider, ICloudDriveProvider, StorageCorruptionError
from agent_personal_vault.models import Envelope


@pytest.mark.parametrize("provider_cls", [LocalFilesystemProvider, ICloudDriveProvider])
def test_put_over_corrupted_data_refuses_silent_overwrite(tmp_path: Path, provider_cls):
    provider = provider_cls(tmp_path)

    # Manually create corrupt entity file
    corrupt_file = provider._validate_and_get_path("identity", "corrupt-id")
    corrupt_file.write_text("{ corrupt payload ...", encoding="utf-8")

    env = Envelope(
        id="corrupt-id",
        type="identity",
        data={"display_name": "Attempt Overwrite"}
    )

    # Put must NOT swallow corruption exception with 'except Exception: pass'
    with pytest.raises(StorageCorruptionError):
        provider.put(env)

    # Verify corrupt payload file content was NOT overwritten
    assert corrupt_file.read_text(encoding="utf-8") == "{ corrupt payload ..."
