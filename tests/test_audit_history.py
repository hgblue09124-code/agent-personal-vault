import pytest
from pathlib import Path
from agent_personal_vault.store import VaultStore
from agent_personal_vault.models import Envelope


def test_audit_history_preservation(tmp_path: Path):
    store = VaultStore(tmp_path)
    env = Envelope(
        id="goal-aud-1",
        type="goal",
        data={"title": "Audit Goal", "status": "PLANNED"}
    )

    # Create
    store.put(env)

    # Update
    env.data["status"] = "ACTIVE"
    store.put(env)

    # Verify audit entries
    audit_entries = store.list_all("audit_entry")
    assert len(audit_entries) == 2

    actions = [a.data["action_type"] for a in audit_entries]
    assert "CREATE" in actions
    assert "UPDATE" in actions

    for entry in audit_entries:
        assert entry.data["entity_id"] == "goal-aud-1"
        assert entry.data["entity_type"] == "goal"
