import pytest
from pathlib import Path
from agent_personal_vault.store import VaultStore
from agent_personal_vault.strategy_manager import StrategyManager
from agent_personal_vault.models import StrategyStatus


def test_strategy_lifecycle_and_application(tmp_path: Path):
    store = VaultStore(tmp_path)
    sm = StrategyManager(store)

    # 1. Create candidate strategy
    strat_env = sm.create_strategy(
        strategy_id="strat-001",
        name="Atomic Write Strategy",
        description="Write tmp file and rename",
        rule="Write .tmp then replace",
        applicable_context={"fs": "posix"},
        prerequisites=["write permission"],
        expected_outcome="No partial writes",
        source_experiences=["exp-001"],
        status=StrategyStatus.CANDIDATE
    )
    assert strat_env.data["status"] == "CANDIDATE"

    # 2. Apply strategy
    app_env = sm.record_application(
        strategy_id="strat-001",
        task_id="task-001",
        applied_context={"file": "data.json"},
        outcome="SUCCESS",
        verification_result="SHA256 hash verified"
    )

    assert app_env.data["strategy_id"] == "strat-001"
    assert app_env.data["outcome"] == "SUCCESS"

    # Verify strategy status promoted to VALIDATED after success
    updated_strat = store.get("strategy", "strat-001")
    assert updated_strat.data["success_count"] == 1
    assert updated_strat.data["status"] == "VALIDATED"


def test_strategy_supersession(tmp_path: Path):
    store = VaultStore(tmp_path)
    sm = StrategyManager(store)

    # Create old strategy
    sm.create_strategy(
        strategy_id="strat-old",
        name="Old Strategy",
        description="Old way",
        rule="Old rule",
        applicable_context={},
        prerequisites=[],
        expected_outcome="Outcome",
        source_experiences=["exp-old"]
    )

    # Supersede
    sm.supersede_strategy(
        old_strategy_id="strat-old",
        new_strategy_id="strat-new",
        new_name="New Improved Strategy",
        new_description="New way",
        new_rule="New rule",
        applicable_context={},
        prerequisites=[],
        expected_outcome="Better outcome",
        source_experiences=["exp-new"]
    )

    old_strat = store.get("strategy", "strat-old")
    new_strat = store.get("strategy", "strat-new")

    assert old_strat.data["status"] == "SUPERSEDED"
    assert old_strat.data["superseded_by"] == "strat-new"

    assert new_strat.data["supersedes"] == "strat-old"
    assert new_strat.data["version"] == 2
