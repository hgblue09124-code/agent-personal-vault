import pytest
from pathlib import Path
from agent_personal_vault.store import VaultStore
from agent_personal_vault.models import Envelope


def test_relationship_integrity_validation(tmp_path: Path):
    store = VaultStore(tmp_path)

    # Task referencing a non-existent goal and strategy
    env_task = Envelope(
        id="task-rel-1",
        type="task",
        data={
            "title": "Subtask",
            "status": "PENDING",
            "goal_id": "goal-non-existent",
            "strategy_id": "strat-non-existent"
        }
    )

    missing = store.validate_relationships(env_task)
    assert len(missing) == 2
    assert "goal-non-existent" in missing[0]
    assert "strat-non-existent" in missing[1]

    # In strict mode, put should raise ValueError
    with pytest.raises(ValueError, match="Relationship integrity error"):
        store.put(env_task, strict_relationships=True)

    # Now create goal and strategy
    store.put(Envelope(id="goal-non-existent", type="goal", data={"title": "Main Goal", "status": "ACTIVE"}))
    store.put(Envelope(
        id="strat-non-existent",
        type="strategy",
        data={
            "strategy_id": "strat-non-existent",
            "name": "Strat",
            "description": "Desc",
            "rule": "Rule",
            "applicable_context": {},
            "prerequisites": [],
            "expected_outcome": "Outcome",
            "evidence": [],
            "success_count": 0,
            "failure_count": 0,
            "inconclusive_count": 0,
            "confidence": 0.5,
            "status": "CANDIDATE",
            "version": 1,
            "provenance": {"source": "test"},
            "source_experiences": [],
            "supersedes": None,
            "superseded_by": None,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
            "metadata": {}
        }
    ))

    # Now strict save succeeds
    saved = store.put(env_task, strict_relationships=True)
    assert saved is True
