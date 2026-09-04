from typing import Dict, Any, Callable
from .models import Envelope


MigrationHandler = Callable[[Dict[str, Any]], Dict[str, Any]]


class MigrationEngine:
    def __init__(self):
        self.handlers: Dict[str, MigrationHandler] = {}
        self._register_default_migrations()

    def register_migration(self, target_version: str, handler: MigrationHandler):
        self.handlers[target_version] = handler

    def _register_default_migrations(self):
        # Example v1 migration handler: v1.0 -> v1.1
        def v1_0_to_v1_1(envelope_dict: Dict[str, Any]) -> Dict[str, Any]:
            env = dict(envelope_dict)
            env["schema_version"] = "1.1"

            # Ensure provenance metadata exists
            prov = env.get("provenance", {})
            if "metadata" not in prov:
                prov["metadata"] = {}
            prov["metadata"]["migrated_from"] = envelope_dict.get("schema_version", "1.0")
            env["provenance"] = prov

            # Domain specific enhancements (non-destructive)
            d = env.get("data", {})
            if env.get("type") == "strategy":
                if "inconclusive_count" not in d:
                    d["inconclusive_count"] = 0
            env["data"] = d
            return env

        self.register_migration("1.1", v1_0_to_v1_1)

    def migrate(self, envelope_dict: Dict[str, Any], target_version: str) -> Dict[str, Any]:
        if not isinstance(envelope_dict, dict) or "id" not in envelope_dict or "type" not in envelope_dict:
            raise ValueError("Invalid canonical envelope structure provided for migration.")

        current_version = envelope_dict.get("schema_version", "1.0")
        if current_version == target_version:
            return envelope_dict

        if target_version not in self.handlers:
            raise ValueError(
                f"Unsupported migration: no handler registered to migrate schema from '{current_version}' to target version '{target_version}'."
            )

        original_id = envelope_dict["id"]
        original_created_at = envelope_dict.get("created_at")

        migrated = self.handlers[target_version](envelope_dict)

        # Ensure fundamental identity properties are preserved
        if migrated.get("id") != original_id:
            raise ValueError(f"Migration error: entity ID changed from '{original_id}' to '{migrated.get('id')}'.")
        if original_created_at and migrated.get("created_at") != original_created_at:
            migrated["created_at"] = original_created_at

        migrated["schema_version"] = target_version
        return migrated
