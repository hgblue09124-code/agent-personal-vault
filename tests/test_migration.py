import pytest
from agent_personal_vault.migration import MigrationEngine


def test_schema_migration_handler():
    engine = MigrationEngine()

    v1_0_entity = {
        "schema_version": "1.0",
        "id": "strat-m1",
        "type": "strategy",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
        "provenance": {"source": "legacy"},
        "data": {
            "strategy_id": "strat-m1",
            "name": "Old Strategy",
            "description": "Legacy",
            "rule": "Rule",
            "applicable_context": {},
            "prerequisites": [],
            "expected_outcome": "Outcome",
            "evidence": [],
            "success_count": 5,
            "failure_count": 0,
            "confidence": 1.0,
            "status": "SUPPORTED",
            "version": 1,
            "provenance": {},
            "source_experiences": [],
            "supersedes": None,
            "superseded_by": None,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
            "metadata": {}
        }
    }

    migrated = engine.migrate(v1_0_entity, "1.1")
    assert migrated["schema_version"] == "1.1"
    assert migrated["provenance"]["metadata"]["migrated_from"] == "1.0"
    assert "inconclusive_count" in migrated["data"]
    assert migrated["data"]["inconclusive_count"] == 0
