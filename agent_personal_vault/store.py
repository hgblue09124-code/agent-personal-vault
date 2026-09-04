import os
import json
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from .models import Envelope, TYPE_TO_FOLDER, FOLDER_TO_TYPE
from .serialization import canonical_dumps, canonical_hash
from .validation import SchemaValidator


class VaultStore:
    def __init__(self, root_path: Path, schema_dir: Optional[Path] = None):
        self.root_path = Path(root_path).resolve()
        self.validator = SchemaValidator(schema_dir=schema_dir)
        self._ensure_directories()

    def _ensure_directories(self):
        for folder in TYPE_TO_FOLDER.values():
            (self.root_path / folder).mkdir(parents=True, exist_ok=True)

    def _get_entity_path(self, entity_type: str, entity_id: str) -> Path:
        folder = TYPE_TO_FOLDER.get(entity_type)
        if not folder:
            raise ValueError(f"Unknown entity type: {entity_type}")
        return self.root_path / folder / f"{entity_id}.json"

    def atomic_write(self, filepath: Path, content_str: str) -> None:
        """
        Atomic write using temporary file in the same folder and os.replace.
        """
        filepath.parent.mkdir(parents=True, exist_ok=True)
        tmp_filepath = filepath.parent / f"{filepath.name}.tmp.{uuid.uuid4().hex}"
        try:
            with open(tmp_filepath, "w", encoding="utf-8") as f:
                f.write(content_str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_filepath, filepath)
        except Exception:
            if tmp_filepath.exists():
                tmp_filepath.unlink()
            raise

    def exists(self, entity_type: str, entity_id: str) -> bool:
        return self._get_entity_path(entity_type, entity_id).exists()

    def get(self, entity_type: str, entity_id: str) -> Optional[Envelope]:
        path = self._get_entity_path(entity_type, entity_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Envelope.from_dict(data)

    def list_all(self, entity_type: str) -> List[Envelope]:
        folder = TYPE_TO_FOLDER.get(entity_type)
        if not folder:
            return []
        dir_path = self.root_path / folder
        if not dir_path.exists():
            return []

        entities = []
        for file in sorted(dir_path.glob("*.json")):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                entities.append(Envelope.from_dict(data))
            except Exception:
                continue
        return entities

    def validate_relationships(self, envelope: Envelope) -> List[str]:
        """
        Verify that referenced IDs in the entity data exist in the store.
        Returns a list of missing reference warnings/errors.
        """
        missing = []
        d = envelope.data

        # Relationship checks based on domain field semantics
        ref_checks = []
        if envelope.type == "task":
            if d.get("goal_id"):
                ref_checks.append(("goal", d["goal_id"]))
            if d.get("strategy_id"):
                ref_checks.append(("strategy", d["strategy_id"]))
        elif envelope.type == "experience":
            if d.get("task_id"):
                ref_checks.append(("task", d["task_id"]))
        elif envelope.type == "lesson":
            for exp_id in d.get("source_experiences", []):
                ref_checks.append(("experience", exp_id))
        elif envelope.type == "strategy":
            for exp_id in d.get("source_experiences", []):
                ref_checks.append(("experience", exp_id))
            if d.get("supersedes"):
                ref_checks.append(("strategy", d["supersedes"]))
            if d.get("superseded_by"):
                ref_checks.append(("strategy", d["superseded_by"]))
        elif envelope.type == "strategy_application":
            if d.get("strategy_id"):
                ref_checks.append(("strategy", d["strategy_id"]))
            if d.get("task_id"):
                ref_checks.append(("task", d["task_id"]))

        for ref_type, ref_id in ref_checks:
            if not self.exists(ref_type, ref_id):
                missing.append(f"Referenced {ref_type} ID '{ref_id}' not found in store.")

        return missing

    def put(
        self,
        envelope: Envelope,
        record_audit: bool = True,
        strict_relationships: bool = False
    ) -> bool:
        """
        Save entity atomically with schema validation, duplicate detection, and audit logging.
        Returns True if saved/updated, False if idempotent duplicate skip.
        """
        env_dict = envelope.to_dict()

        # 1. Schema Validation
        self.validator.validate_entity(env_dict)

        # 2. Relationship Validation
        missing_refs = self.validate_relationships(envelope)
        if missing_refs and strict_relationships:
            raise ValueError(f"Relationship integrity error: {'; '.join(missing_refs)}")

        path = self._get_entity_path(envelope.type, envelope.id)

        # 3. Idempotency & Duplicate Check
        is_new = not path.exists()
        action_type = "CREATE" if is_new else "UPDATE"

        if not is_new:
            existing_env = self.get(envelope.type, envelope.id)
            if existing_env:
                # Compare canonical hashes of data
                if canonical_hash(existing_env.data) == canonical_hash(envelope.data):
                    # Data is identical -> No-op for idempotency
                    return False

        # 4. Atomic Write
        content_str = canonical_dumps(env_dict)
        self.atomic_write(path, content_str)

        # 5. Audit Logging
        if record_audit and envelope.type != "audit_entry":
            self._write_audit_entry(
                action_type=action_type,
                entity_id=envelope.id,
                entity_type=envelope.type,
                changes={"data_hash": canonical_hash(envelope.data)},
                reason=f"Vault {action_type} operation"
            )

        return True

    def _write_audit_entry(self, action_type: str, entity_id: str, entity_type: str, changes: Dict[str, Any], reason: str):
        audit_id = f"audit-{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        audit_env = Envelope(
            id=audit_id,
            type="audit_entry",
            created_at=now_iso,
            updated_at=now_iso,
            data={
                "action_type": action_type,
                "entity_id": entity_id,
                "entity_type": entity_type,
                "changes": changes,
                "actor": "agent-personal-vault",
                "reason": reason,
            }
        )
        self.put(audit_env, record_audit=False)
