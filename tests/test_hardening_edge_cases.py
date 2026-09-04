import pytest
import tempfile
import tarfile
import io
import json
from pathlib import Path

from agent_personal_vault.store import VaultStore
from agent_personal_vault.strategy_manager import StrategyManager
from agent_personal_vault.backup_restore import BackupRestoreManager
from agent_personal_vault.migration import MigrationEngine
from agent_personal_vault.serialization import canonical_dumps, canonical_hash
from agent_personal_vault.models import Envelope, StrategyStatus


def test_list_all_corruption_surfacing(tmp_path: Path):
    store = VaultStore(tmp_path)
    store.put(Envelope(id="id1", type="identity", data={"display_name": "Alice"}))

    # Create corrupt file
    corrupt_path = tmp_path / "identity" / "corrupt.json"
    corrupt_path.write_text("{ corrupt json ...", encoding="utf-8")

    # Default list_all must raise ValueError on corrupt file
    with pytest.raises(ValueError, match="Corrupted or invalid entity file"):
        store.list_all("identity")

    # Diagnostic mode with skip_invalid=True
    diagnostics = []
    valid = store.list_all("identity", skip_invalid=True, diagnostics=diagnostics)
    assert len(valid) == 1
    assert valid[0].id == "id1"
    assert len(diagnostics) == 1
    assert "corrupt.json" in diagnostics[0]["file"]


def test_backup_path_traversal_protection(tmp_path: Path):
    vault_dir = tmp_path / "vault"
    store = VaultStore(vault_dir)
    mgr = BackupRestoreManager(store)

    malicious_tar = tmp_path / "malicious.tar.gz"
    with tarfile.open(malicious_tar, "w:gz") as tar:
        payload = b"evil code"
        ti = tarfile.TarInfo(name="../../evil.sh")
        ti.size = len(payload)
        tar.addfile(ti, io.BytesIO(payload))

    with pytest.raises(ValueError, match="Path traversal security violation"):
        mgr.restore_from_backup(malicious_tar)


def test_strategy_lifecycle_invalid_transitions(tmp_path: Path):
    store = VaultStore(tmp_path)
    sm = StrategyManager(store)

    sm.create_strategy(
        strategy_id="strat-1",
        name="Strategy 1",
        description="Desc",
        rule="Rule",
        applicable_context={},
        prerequisites=[],
        expected_outcome="Outcome",
        source_experiences=[]
    )

    # CANDIDATE -> SUPPORTED (Illegal)
    with pytest.raises(ValueError, match="Illegal strategy state transition"):
        sm.update_strategy_status("strat-1", StrategyStatus.SUPPORTED)

    # Retire strategy
    sm.update_strategy_status("strat-1", StrategyStatus.RETIRED)

    # RETIRED -> VALIDATED (Illegal - terminal state)
    with pytest.raises(ValueError, match="Illegal strategy state transition"):
        sm.update_strategy_status("strat-1", StrategyStatus.VALIDATED)


def test_self_reference_and_circular_supersession_rejection(tmp_path: Path):
    store = VaultStore(tmp_path)

    # Strategy superseding itself
    self_strat = Envelope(
        id="s-self",
        type="strategy",
        data={
            "strategy_id": "s-self", "name": "Self", "description": "D", "rule": "R",
            "applicable_context": {}, "prerequisites": [], "expected_outcome": "O",
            "evidence": [], "success_count": 0, "failure_count": 0, "inconclusive_count": 0,
            "confidence": 0.5, "status": "CANDIDATE", "version": 1, "provenance": {},
            "source_experiences": [], "supersedes": "s-self", "superseded_by": None,
            "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z", "metadata": {}
        }
    )
    errors = store.validate_relationships(self_strat)
    assert any("cannot supersede itself" in err for err in errors)

    # Circular supersession
    store.put(Envelope(
        id="s-a",
        type="strategy",
        data={
            "strategy_id": "s-a", "name": "A", "description": "D", "rule": "R",
            "applicable_context": {}, "prerequisites": [], "expected_outcome": "O",
            "evidence": [], "success_count": 0, "failure_count": 0, "inconclusive_count": 0,
            "confidence": 0.5, "status": "SUPERSEDED", "version": 1, "provenance": {},
            "source_experiences": [], "supersedes": "s-b", "superseded_by": "s-b",
            "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z", "metadata": {}
        }
    ))

    s_b = Envelope(
        id="s-b",
        type="strategy",
        data={
            "strategy_id": "s-b", "name": "B", "description": "D", "rule": "R",
            "applicable_context": {}, "prerequisites": [], "expected_outcome": "O",
            "evidence": [], "success_count": 0, "failure_count": 0, "inconclusive_count": 0,
            "confidence": 0.5, "status": "CANDIDATE", "version": 2, "provenance": {},
            "source_experiences": [], "supersedes": "s-a", "superseded_by": None,
            "created_at": "2025-01-01T00:00:00Z", "updated_at": "2025-01-01T00:00:00Z", "metadata": {}
        }
    )

    circ_errors = store.validate_relationships(s_b)
    assert any("Circular supersession loop detected" in err for err in circ_errors)


def test_canonical_hash_key_order_independence():
    dict1 = {
        "b": "second",
        "a": "first",
        "c": {"y": [1, 2], "x": True}
    }
    dict2 = {
        "c": {"x": True, "y": [1, 2]},
        "a": "first",
        "b": "second"
    }

    assert canonical_dumps(dict1) == canonical_dumps(dict2)
    assert canonical_hash(dict1) == canonical_hash(dict2)


def test_migration_engine_unknown_version_rejection():
    engine = MigrationEngine()
    env = {
        "schema_version": "1.0",
        "id": "entity-1",
        "type": "identity",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
        "provenance": {"source": "test"},
        "data": {"display_name": "Test"}
    }

    with pytest.raises(ValueError, match="Unsupported migration"):
        engine.migrate(env, "99.0")
