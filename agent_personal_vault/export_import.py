import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from .models import Envelope, TYPE_TO_FOLDER, FOLDER_TO_TYPE
from .serialization import canonical_dumps, canonical_hash
from .store import VaultStore


class VaultExporter:
    def __init__(self, store: VaultStore):
        self.store = store

    def export_to_directory(self, export_dir: Path) -> Path:
        export_dir = Path(export_dir).resolve()
        export_dir.mkdir(parents=True, exist_ok=True)

        entity_counts = {}
        checksums = {}

        for entity_type, folder_name in TYPE_TO_FOLDER.items():
            if entity_type in ("export_manifest", "backup_manifest"):
                continue

            entities = self.store.list_all(entity_type)
            if not entities:
                continue

            out_folder = export_dir / folder_name
            out_folder.mkdir(parents=True, exist_ok=True)

            entity_counts[entity_type] = len(entities)

            for env in entities:
                content = canonical_dumps(env.to_dict())
                c_hash = canonical_hash(env.to_dict())
                checksums[f"{folder_name}/{env.id}.json"] = c_hash

                out_path = out_folder / f"{env.id}.json"
                self.store.atomic_write(out_path, content)

        # Create export manifest
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        manifest_data = {
            "format_version": "1.0",
            "exported_at": now_iso,
            "entity_counts": entity_counts,
            "checksums": checksums,
        }

        manifest_env = Envelope(
            id="export-manifest",
            type="export_manifest",
            created_at=now_iso,
            updated_at=now_iso,
            data=manifest_data,
        )

        manifest_path = export_dir / "manifest.json"
        self.store.atomic_write(manifest_path, canonical_dumps(manifest_env.to_dict()))

        return export_dir


class VaultImporter:
    def __init__(self, target_store: VaultStore):
        self.target_store = target_store

    def import_from_directory(
        self,
        export_dir: Path,
        validate_checksums: bool = True,
        strict_relationships: bool = False
    ) -> Dict[str, Any]:
        export_dir = Path(export_dir).resolve()
        manifest_path = export_dir / "manifest.json"

        if not manifest_path.exists():
            raise FileNotFoundError(f"Export manifest missing at {manifest_path}")

        manifest_dict = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_env = Envelope.from_dict(manifest_dict)
        self.target_store.validator.validate_entity(manifest_dict)

        manifest_data = manifest_env.data
        checksums = manifest_data.get("checksums", {})

        imported_count = 0
        skipped_count = 0
        invalid_entities = []

        # First scan & validate schemas
        entities_to_import: List[Envelope] = []

        for rel_path, expected_hash in checksums.items():
            file_path = export_dir / rel_path
            if not file_path.exists():
                raise FileNotFoundError(f"Exported entity missing: {file_path}")

            entity_dict = json.loads(file_path.read_text(encoding="utf-8"))

            if validate_checksums:
                actual_hash = canonical_hash(entity_dict)
                if actual_hash != expected_hash:
                    raise ValueError(f"Checksum mismatch for {rel_path}: expected {expected_hash}, got {actual_hash}")

            # Validate schema
            try:
                self.target_store.validator.validate_entity(entity_dict)
                entities_to_import.append(Envelope.from_dict(entity_dict))
            except Exception as e:
                invalid_entities.append((rel_path, str(e)))

        if invalid_entities:
            raise ValueError(f"Import validation failed on entities: {invalid_entities}")

        # Sort entities by type dependency order so references exist when imported
        TYPE_IMPORT_PRIORITY = {
            "identity": 10,
            "user_context": 10,
            "memory": 10,
            "knowledge": 10,
            "goal": 20,
            "task": 30,
            "experience": 40,
            "lesson": 50,
            "strategy": 60,
            "strategy_application": 70,
            "event": 80,
            "runtime_state": 90,
            "audit_entry": 100,
        }

        entities_to_import.sort(key=lambda env: TYPE_IMPORT_PRIORITY.get(env.type, 999))

        # Now put entities into target store
        for env in entities_to_import:
            saved = self.target_store.put(
                env,
                record_audit=True,
                strict_relationships=strict_relationships
            )
            if saved:
                imported_count += 1
            else:
                skipped_count += 1

        return {
            "imported_count": imported_count,
            "skipped_count": skipped_count,
            "total_verified": len(entities_to_import),
        }
