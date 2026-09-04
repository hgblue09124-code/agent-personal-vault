import json
from pathlib import Path
from typing import Dict, Any, Optional
from jsonschema import Draft202012Validator, ValidationError


DOMAIN_SCHEMA_MAP = {
    "identity": "identity.json",
    "user_context": "user_context.json",
    "memory": "memory.json",
    "knowledge": "knowledge.json",
    "experience": "experience.json",
    "lesson": "lesson.json",
    "strategy": "strategy.json",
    "strategy_application": "strategy_application.json",
    "goal": "goal.json",
    "task": "task.json",
    "event": "event.json",
    "runtime_state": "runtime_state.json",
    "audit_entry": "audit_entry.json",
    "export_manifest": "export_manifest.json",
    "backup_manifest": "backup_manifest.json",
}


class SchemaValidator:
    def __init__(self, schema_dir: Optional[Path] = None):
        if schema_dir is None:
            schema_dir = Path(__file__).parent.parent / "schema" / "v1"
        self.schema_dir = Path(schema_dir)
        self.validators: Dict[str, Draft202012Validator] = {}
        self._load_schemas()

    def _load_schemas(self):
        envelope_path = self.schema_dir / "envelope.json"
        if envelope_path.exists():
            env_schema = json.loads(envelope_path.read_text("utf-8"))
            self.validators["envelope"] = Draft202012Validator(env_schema)

        for domain, schema_filename in DOMAIN_SCHEMA_MAP.items():
            path = self.schema_dir / schema_filename
            if path.exists():
                schema = json.loads(path.read_text("utf-8"))
                self.validators[domain] = Draft202012Validator(schema)

    def validate_envelope(self, envelope_dict: Dict[str, Any]) -> None:
        if "envelope" in self.validators:
            self.validators["envelope"].validate(envelope_dict)

    def validate_domain(self, domain_type: str, data_dict: Dict[str, Any]) -> None:
        if domain_type in self.validators:
            self.validators[domain_type].validate(data_dict)
        else:
            raise ValidationError(f"Unknown domain type: '{domain_type}' with no schema found.")

    def validate_entity(self, envelope_dict: Dict[str, Any]) -> None:
        """
        Validate both the canonical envelope and the internal domain data.
        """
        self.validate_envelope(envelope_dict)
        entity_type = envelope_dict.get("type")
        data = envelope_dict.get("data", {})
        if entity_type:
            self.validate_domain(entity_type, data)
