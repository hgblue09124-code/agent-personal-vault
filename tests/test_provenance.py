import pytest
from pathlib import Path
from agent_personal_vault.store import VaultStore
from agent_personal_vault.models import Envelope, Provenance


def test_provenance_preservation(tmp_path: Path):
    store = VaultStore(tmp_path)
    prov = Provenance(
        source="unit_test_runner",
        source_id="run-777",
        agent_version="2.1.0",
        metadata={"environment": "CI", "experimental": False}
    )
    env = Envelope(
        id="know-prov-001",
        type="knowledge",
        provenance=prov,
        data={
            "domain": "provenance",
            "topic": "tracking",
            "statement": "Provenance captures system context."
        }
    )
    store.put(env)

    retrieved = store.get("knowledge", "know-prov-001")
    assert retrieved.provenance is not None
    assert retrieved.provenance.source == "unit_test_runner"
    assert retrieved.provenance.source_id == "run-777"
    assert retrieved.provenance.agent_version == "2.1.0"
    assert retrieved.provenance.metadata["environment"] == "CI"
