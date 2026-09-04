from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone


class StrategyStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    SUPPORTED = "SUPPORTED"
    WEAKENED = "WEAKENED"
    RETIRED = "RETIRED"
    SUPERSEDED = "SUPERSEDED"


TYPE_TO_FOLDER = {
    "identity": "identity",
    "user_context": "user_context",
    "memory": "memory",
    "knowledge": "knowledge",
    "experience": "experiences",
    "lesson": "lessons",
    "strategy": "strategies",
    "strategy_application": "strategy_applications",
    "goal": "goals",
    "task": "tasks",
    "event": "events",
    "runtime_state": "runtime",
    "audit_entry": "audit",
    "export_manifest": "export",
    "backup_manifest": "backups",
}

FOLDER_TO_TYPE = {v: k for k, v in TYPE_TO_FOLDER.items()}


@dataclass
class Provenance:
    source: str
    source_id: Optional[str] = None
    agent_version: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "agent_version": self.agent_version,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Provenance":
        return cls(
            source=data.get("source", "unknown"),
            source_id=data.get("source_id"),
            agent_version=data.get("agent_version"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Envelope:
    id: str
    type: str
    data: Dict[str, Any]
    schema_version: str = "1.0"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    provenance: Optional[Provenance] = None

    def __post_init__(self):
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if not self.created_at:
            self.created_at = now_iso
        if not self.updated_at:
            self.updated_at = now_iso
        if self.provenance is None:
            self.provenance = Provenance(source="agent-core")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "type": self.type,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "provenance": self.provenance.to_dict() if isinstance(self.provenance, Provenance) else self.provenance,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Envelope":
        prov_raw = data.get("provenance", {})
        prov = Provenance.from_dict(prov_raw) if isinstance(prov_raw, dict) else prov_raw
        return cls(
            id=data["id"],
            type=data["type"],
            data=data.get("data", {}),
            schema_version=data.get("schema_version", "1.0"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            provenance=prov,
        )
