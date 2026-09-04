from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
import uuid

from .models import Envelope, StrategyStatus
from .store import VaultStore


class StrategyManager:
    def __init__(self, store: VaultStore):
        self.store = store

    def create_strategy(
        self,
        strategy_id: str,
        name: str,
        description: str,
        rule: str,
        applicable_context: Dict[str, Any],
        prerequisites: List[str],
        expected_outcome: str,
        source_experiences: List[str],
        status: StrategyStatus = StrategyStatus.CANDIDATE,
        confidence: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Envelope:
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        data = {
            "strategy_id": strategy_id,
            "name": name,
            "description": description,
            "rule": rule,
            "applicable_context": applicable_context,
            "prerequisites": prerequisites,
            "expected_outcome": expected_outcome,
            "evidence": source_experiences.copy(),
            "success_count": 0,
            "failure_count": 0,
            "inconclusive_count": 0,
            "confidence": confidence,
            "status": status.value if isinstance(status, StrategyStatus) else status,
            "version": 1,
            "provenance": {"source": "strategy_manager"},
            "source_experiences": source_experiences,
            "supersedes": None,
            "superseded_by": None,
            "created_at": now_iso,
            "updated_at": now_iso,
            "metadata": metadata or {},
        }

        envelope = Envelope(
            id=strategy_id,
            type="strategy",
            created_at=now_iso,
            updated_at=now_iso,
            data=data,
        )
        self.store.put(envelope)
        return envelope

    def record_application(
        self,
        strategy_id: str,
        task_id: str,
        applied_context: Dict[str, Any],
        outcome: str,  # "SUCCESS", "FAILURE", "INCONCLUSIVE"
        verification_result: str,
        notes: Optional[str] = None,
        resulting_experience_id: Optional[str] = None,
    ) -> Envelope:
        app_id = f"app-{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # 1. Create Strategy Application entity
        app_data = {
            "strategy_id": strategy_id,
            "task_id": task_id,
            "applied_context": applied_context,
            "outcome": outcome,
            "verification_result": verification_result,
            "notes": notes,
            "resulting_experience_id": resulting_experience_id,
            "resulting_strategy_update": None,
        }

        app_env = Envelope(
            id=app_id,
            type="strategy_application",
            created_at=now_iso,
            updated_at=now_iso,
            data=app_data,
        )
        self.store.put(app_env)

        # 2. Update Strategy counters and status
        strat_env = self.store.get("strategy", strategy_id)
        if strat_env:
            sd = strat_env.data
            if outcome == "SUCCESS":
                sd["success_count"] = sd.get("success_count", 0) + 1
            elif outcome == "FAILURE":
                sd["failure_count"] = sd.get("failure_count", 0) + 1
            else:
                sd["inconclusive_count"] = sd.get("inconclusive_count", 0) + 1

            # Recalculate confidence & status
            total = sd["success_count"] + sd["failure_count"] + sd["inconclusive_count"]
            if total > 0:
                sd["confidence"] = round(sd["success_count"] / total, 2)

            if sd["status"] == StrategyStatus.CANDIDATE.value and sd["success_count"] >= 1:
                sd["status"] = StrategyStatus.VALIDATED.value
            elif sd["status"] == StrategyStatus.VALIDATED.value and sd["success_count"] >= 3:
                sd["status"] = StrategyStatus.SUPPORTED.value
            elif sd["failure_count"] >= 3 and sd["confidence"] < 0.5 and sd["status"] not in (StrategyStatus.RETIRED.value, StrategyStatus.SUPERSEDED.value):
                sd["status"] = StrategyStatus.WEAKENED.value

            sd["updated_at"] = now_iso
            strat_env.updated_at = now_iso

            # Save updated strategy
            self.store.put(strat_env)

            # Link strategy update in app data
            app_env.data["resulting_strategy_update"] = f"Updated counts: S={sd['success_count']}, F={sd['failure_count']}, status={sd['status']}"
            self.store.put(app_env)

        return app_env

    def supersede_strategy(
        self,
        old_strategy_id: str,
        new_strategy_id: str,
        new_name: str,
        new_description: str,
        new_rule: str,
        applicable_context: Dict[str, Any],
        prerequisites: List[str],
        expected_outcome: str,
        source_experiences: List[str],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Envelope:
        """
        Supersedes old_strategy with new_strategy.
        Preserves old strategy in SUPERSEDED state without destruction.
        """
        old_env = self.store.get("strategy", old_strategy_id)
        if not old_env:
            raise ValueError(f"Old strategy '{old_strategy_id}' not found.")

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Create new strategy envelope
        new_data = {
            "strategy_id": new_strategy_id,
            "name": new_name,
            "description": new_description,
            "rule": new_rule,
            "applicable_context": applicable_context,
            "prerequisites": prerequisites,
            "expected_outcome": expected_outcome,
            "evidence": source_experiences.copy(),
            "success_count": 0,
            "failure_count": 0,
            "inconclusive_count": 0,
            "confidence": 0.5,
            "status": StrategyStatus.CANDIDATE.value,
            "version": old_env.data.get("version", 1) + 1,
            "provenance": {"source": "strategy_manager", "superseded_from": old_strategy_id},
            "source_experiences": source_experiences,
            "supersedes": old_strategy_id,
            "superseded_by": None,
            "created_at": now_iso,
            "updated_at": now_iso,
            "metadata": metadata or {},
        }

        new_env = Envelope(
            id=new_strategy_id,
            type="strategy",
            created_at=now_iso,
            updated_at=now_iso,
            data=new_data,
        )
        self.store.put(new_env)

        # Update old strategy
        old_env.data["status"] = StrategyStatus.SUPERSEDED.value
        old_env.data["superseded_by"] = new_strategy_id
        old_env.data["updated_at"] = now_iso
        old_env.updated_at = now_iso
        self.store.put(old_env)

        # Log audit entry for supersession
        self.store._write_audit_entry(
            action_type="SUPERSEDE",
            entity_id=old_strategy_id,
            entity_type="strategy",
            changes={"superseded_by": new_strategy_id, "new_status": StrategyStatus.SUPERSEDED.value},
            reason=f"Strategy superseded by new strategy '{new_strategy_id}'"
        )

        return new_env
