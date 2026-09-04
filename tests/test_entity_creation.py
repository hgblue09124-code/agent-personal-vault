import pytest
from pathlib import Path
from agent_personal_vault.store import VaultStore
from agent_personal_vault.models import Envelope


def test_valid_entity_creation(tmp_path: Path):
    store = VaultStore(tmp_path)
    env = Envelope(
        id="task-100",
        type="task",
        data={
            "title": "Clean Vault Database",
            "status": "PENDING"
        }
    )
    saved = store.put(env)
    assert saved is True
    assert store.exists("task", "task-100")

    retrieved = store.get("task", "task-100")
    assert retrieved is not None
    assert retrieved.id == "task-100"
    assert retrieved.data["title"] == "Clean Vault Database"
