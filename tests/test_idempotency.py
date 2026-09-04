import pytest
from pathlib import Path
from agent_personal_vault.store import VaultStore
from agent_personal_vault.models import Envelope


def test_idempotent_duplicate_writes(tmp_path: Path):
    store = VaultStore(tmp_path)
    env = Envelope(
        id="user-ctx-1",
        type="user_context",
        data={
            "preferences": {"theme": "dark"},
            "constraints": {"max_memory_gb": 16}
        }
    )

    # First write
    saved_1 = store.put(env)
    assert saved_1 is True

    # Count audit entries
    audits_after_1 = len(store.list_all("audit_entry"))

    # Repeated identical write
    saved_2 = store.put(env)
    assert saved_2 is False  # Duplicate detected, skipped

    audits_after_2 = len(store.list_all("audit_entry"))
    assert audits_after_2 == audits_after_1  # No duplicate audit entry created
