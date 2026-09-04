import pytest
from pathlib import Path
from agent_personal_vault.store import VaultStore
from agent_personal_vault.models import Envelope
from jsonschema import ValidationError


def test_invalid_entity_rejection(tmp_path: Path):
    store = VaultStore(tmp_path)

    # Strategy missing required fields like strategy_id, rule, etc.
    invalid_env = Envelope(
        id="strat-invalid",
        type="strategy",
        data={
            "name": "Incomplete Strategy"
        }
    )
    with pytest.raises(ValidationError):
        store.put(invalid_env)

    assert not store.exists("strategy", "strat-invalid")
