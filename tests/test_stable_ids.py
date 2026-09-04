import pytest
from pathlib import Path
from agent_personal_vault.store import VaultStore
from agent_personal_vault.models import Envelope


def test_stable_ids(tmp_path: Path):
    store = VaultStore(tmp_path)
    env = Envelope(
        id="mem-stable-123",
        type="memory",
        data={
            "subject": "Stable ID Test",
            "content": "This memory must preserve its exact ID across reads/writes.",
            "memory_type": "episodic",
            "importance": 0.9
        }
    )
    store.put(env)
    retrieved = store.get("memory", "mem-stable-123")
    assert retrieved.id == "mem-stable-123"

    # Save update to same ID
    env.data["content"] = "Updated content with same ID."
    store.put(env)
    retrieved_after = store.get("memory", "mem-stable-123")
    assert retrieved_after.id == "mem-stable-123"
    assert retrieved_after.data["content"] == "Updated content with same ID."
