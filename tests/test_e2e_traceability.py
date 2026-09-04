import pytest
from pathlib import Path
from agent_personal_vault.store import VaultStore
from agent_personal_vault.strategy_manager import StrategyManager
from agent_personal_vault.export_import import VaultExporter, VaultImporter
from agent_personal_vault.models import Envelope, StrategyStatus


def test_end_to_end_traceability_chain(tmp_path: Path):
    """
    End-to-End Test:
    create synthetic Identity
    -> create Goal
    -> create Task
    -> create Experience
    -> create Lesson
    -> create Candidate Strategy
    -> create Strategy Application
    -> record Outcome
    -> update Strategy
    -> export vault
    -> import into clean vault
    -> verify all relationships survive.
    """
    src_dir = tmp_path / "src_vault"
    clean_dir = tmp_path / "clean_vault"
    export_dir = tmp_path / "export_dir"

    store = VaultStore(src_dir)
    sm = StrategyManager(store)

    # 1. Identity
    store.put(Envelope(id="id-e2e", type="identity", data={"display_name": "E2E Agent"}))

    # 2. Goal
    store.put(Envelope(id="goal-e2e", type="goal", data={"title": "Reliable Persistence", "status": "ACTIVE"}))

    # 3. Task
    store.put(Envelope(
        id="task-e2e",
        type="task",
        data={
            "title": "Perform Atomic Sync",
            "status": "COMPLETED",
            "goal_id": "goal-e2e",
            "actions": ["write tmp", "replace"]
        }
    ))

    # 4. Experience
    store.put(Envelope(
        id="exp-e2e",
        type="experience",
        data={
            "task_id": "task-e2e",
            "action": "Atomic replace",
            "observation": "Zero byte file prevented",
            "verification": "Checksum verified",
            "outcome_status": "SUCCESS"
        }
    ))

    # 5. Lesson
    store.put(Envelope(
        id="les-e2e",
        type="lesson",
        data={
            "title": "Atomic File Renames",
            "insight": "Use temporary file + atomic rename",
            "source_experiences": ["exp-e2e"]
        }
    ))

    # 6. Candidate Strategy
    strat_env = sm.create_strategy(
        strategy_id="strat-e2e",
        name="Atomic Storage Strategy",
        description="Atomic temp write and rename",
        rule="IF writing THEN write .tmp AND rename",
        applicable_context={"type": "fs"},
        prerequisites=[],
        expected_outcome="No corrupt files",
        source_experiences=["exp-e2e"],
        status=StrategyStatus.CANDIDATE
    )

    # 7. Strategy Application & Outcome
    app_env = sm.record_application(
        strategy_id="strat-e2e",
        task_id="task-e2e",
        applied_context={"file": "test.json"},
        outcome="SUCCESS",
        verification_result="Integrity verified",
        resulting_experience_id="exp-e2e"
    )

    # Verify Strategy promoted to VALIDATED
    strat_after = store.get("strategy", "strat-e2e")
    assert strat_after.data["status"] == "VALIDATED"

    # 8. Export Vault
    exporter = VaultExporter(store)
    exporter.export_to_directory(export_dir)

    # 9. Import into Clean Vault
    clean_store = VaultStore(clean_dir)
    importer = VaultImporter(clean_store)
    import_res = importer.import_from_directory(export_dir, strict_relationships=True)

    assert import_res["imported_count"] >= 7

    # 10. Verify all relationships survive in clean vault
    c_identity = clean_store.get("identity", "id-e2e")
    c_goal = clean_store.get("goal", "goal-e2e")
    c_task = clean_store.get("task", "task-e2e")
    c_exp = clean_store.get("experience", "exp-e2e")
    c_les = clean_store.get("lesson", "les-e2e")
    c_strat = clean_store.get("strategy", "strat-e2e")
    c_app = clean_store.get("strategy_application", app_env.id)

    assert c_identity is not None
    assert c_goal is not None
    assert c_task.data["goal_id"] == c_goal.id
    assert c_exp.data["task_id"] == c_task.id
    assert c_les.data["source_experiences"] == [c_exp.id]
    assert c_strat.data["source_experiences"] == [c_exp.id]
    assert c_app.data["strategy_id"] == c_strat.id
    assert c_app.data["task_id"] == c_task.id
    assert c_app.data["resulting_experience_id"] == c_exp.id

    # Validate relationships in clean store pass strict check
    missing_in_clean = clean_store.validate_relationships(c_task)
    assert len(missing_in_clean) == 0
