import os
import json
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from .base import (
    StorageProvider,
    StorageError,
    EntityNotFoundError,
    StorageCorruptionError,
)
from ..models import Envelope, TYPE_TO_FOLDER, FOLDER_TO_TYPE
from ..serialization import canonical_dumps, canonical_hash
from ..validation import SchemaValidator


class LocalFilesystemProvider(StorageProvider):
    """
    Local filesystem storage provider implementation.
    Offers offline-first, atomic, schema-validated JSON persistence.
    """

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
            raise ValueError(f"Unknown entity type: '{entity_type}'")
        return self.root_path / folder / f"{entity_id}.json"

    def atomic_write(self, filepath: Path, content_str: str) -> None:
        """Atomic write using temporary file in the same directory and os.replace."""
        filepath.parent.mkdir(parents=True, exist_ok=True)
        tmp_filepath = filepath.parent / f"{filepath.name}.tmp.{uuid.uuid4().hex}"
        try:
            with open(tmp_filepath, "w", encoding="utf-8") as f:
                f.write(content_str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_filepath, filepath)
        except Exception as e:
            if tmp_filepath.exists():
                tmp_filepath.unlink()
            raise StorageError(f"Atomic write failed for '{filepath}': {e}") from e

    def exists(self, entity_type: str, entity_id: str) -> bool:
        try:
            return self._get_entity_path(entity_type, entity_id).exists()
        except ValueError:
            return False

    def get(self, entity_type: str, entity_id: str, validate_schema: bool = True) -> Optional[Envelope]:
        path = self._get_entity_path(entity_type, entity_id)
        if not path.exists():
            return None
        try:
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
            if validate_schema:
                self.validator.validate_entity(data)
            return Envelope.from_dict(data)
        except Exception as err:
            raise StorageCorruptionError(
                f"Failed to read/validate entity '{entity_id}' of type '{entity_type}' from '{path}': {err}"
            ) from err

    def put(
        self,
        envelope: Envelope,
        validate_schema: bool = True,
        record_audit: bool = True
    ) -> bool:
        env_dict = envelope.to_dict()
        if validate_schema:
            self.validator.validate_entity(env_dict)

        path = self._get_entity_path(envelope.type, envelope.id)
        is_new = not path.exists()

        if not is_new:
            try:
                existing_env = self.get(envelope.type, envelope.id, validate_schema=False)
                if existing_env and canonical_hash(existing_env.data) == canonical_hash(envelope.data):
                    # Data is identical -> Idempotent no-op
                    return False
            except Exception:
                pass  # If corrupt, overwrite safely

        content_str = canonical_dumps(env_dict)
        self.atomic_write(path, content_str)
        return True

    def delete(self, entity_type: str, entity_id: str) -> bool:
        path = self._get_entity_path(entity_type, entity_id)
        if not path.exists():
            return False
        try:
            path.unlink()
            return True
        except Exception as e:
            raise StorageError(f"Failed to delete entity '{entity_id}' of type '{entity_type}': {e}") from e

    def list_all(
        self,
        entity_type: str,
        skip_invalid: bool = False,
        validate_schema: bool = True,
        diagnostics: Optional[List[Dict[str, Any]]] = None
    ) -> List[Envelope]:
        folder = TYPE_TO_FOLDER.get(entity_type)
        if not folder:
            return []
        dir_path = self.root_path / folder
        if not dir_path.exists():
            return []

        entities = []
        for file in sorted(dir_path.glob("*.json")):
            try:
                content = file.read_text(encoding="utf-8")
                data = json.loads(content)
                if validate_schema:
                    self.validator.validate_entity(data)
                env = Envelope.from_dict(data)
                entities.append(env)
            except Exception as err:
                error_info = {
                    "file": str(file),
                    "entity_type": entity_type,
                    "error": str(err),
                }
                if diagnostics is not None:
                    diagnostics.append(error_info)

                if not skip_invalid:
                    raise StorageCorruptionError(
                        f"Corrupted or invalid entity file '{file}' in local store: {err}"
                    ) from err

        return entities

    def get_metadata(self, entity_type: str, entity_id: str) -> Optional[Dict[str, Any]]:
        path = self._get_entity_path(entity_type, entity_id)
        if not path.exists():
            return None

        stat = path.stat()
        env = self.get(entity_type, entity_id, validate_schema=False)
        c_hash = canonical_hash(env.to_dict()) if env else None

        mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "path": str(path),
            "size_bytes": stat.st_size,
            "mtime": mtime_iso,
            "mtime_timestamp": stat.st_mtime,
            "content_hash": c_hash,
            "provider": "LocalFilesystemProvider",
        }
