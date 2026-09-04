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
    StorageUnavailableError,
)
from ..models import Envelope, Tombstone, TYPE_TO_FOLDER, FOLDER_TO_TYPE
from ..serialization import canonical_dumps, canonical_hash
from ..validation import SchemaValidator


class LocalFilesystemProvider(StorageProvider):
    """
    Local filesystem storage provider implementation.
    Offers offline-first, atomic, schema-validated JSON persistence with tombstone support and path security.
    """

    def __init__(self, root_path: Path, schema_dir: Optional[Path] = None):
        self.root_path = Path(root_path).resolve()
        self.validator = SchemaValidator(schema_dir=schema_dir)
        self._ensure_directories()

    def _ensure_directories(self):
        for folder in TYPE_TO_FOLDER.values():
            (self.root_path / folder).mkdir(parents=True, exist_ok=True)
            (self.root_path / ".tombstones" / folder).mkdir(parents=True, exist_ok=True)

    def _validate_and_get_path(self, entity_type: str, entity_id: str, is_tombstone: bool = False) -> Path:
        if not isinstance(entity_type, str) or not isinstance(entity_id, str):
            raise ValueError("entity_type and entity_id must be strings.")

        folder = TYPE_TO_FOLDER.get(entity_type)
        if not folder:
            raise ValueError(f"Unknown entity type: '{entity_type}'")

        if not entity_id or "/" in entity_id or "\\" in entity_id or ".." in entity_id or entity_id.startswith("."):
            raise ValueError(f"Invalid or unsafe entity_id: '{entity_id}'")

        base_dir = (self.root_path / ".tombstones" / folder) if is_tombstone else (self.root_path / folder)
        base_dir.mkdir(parents=True, exist_ok=True)

        target_path = (base_dir / f"{entity_id}.json").resolve()
        resolved_base = base_dir.resolve()

        try:
            target_path.relative_to(resolved_base)
        except ValueError:
            raise ValueError(f"Path traversal detected: entity_id '{entity_id}' escapes base directory '{resolved_base}'")

        return target_path

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
        path = self._validate_and_get_path(entity_type, entity_id)
        return path.exists()

    def get(self, entity_type: str, entity_id: str, validate_schema: bool = True) -> Optional[Envelope]:
        path = self._validate_and_get_path(entity_type, entity_id)
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
        validate_schema: bool = True
    ) -> bool:
        path = self._validate_and_get_path(envelope.type, envelope.id)
        env_dict = envelope.to_dict()
        if validate_schema:
            self.validator.validate_entity(env_dict)

        is_new = not path.exists()

        if not is_new:
            # Check for existing data. If existing data is corrupt, get() raises StorageCorruptionError (NOT swallowed!)
            existing_env = self.get(envelope.type, envelope.id, validate_schema=False)
            if existing_env and canonical_hash(existing_env.data) == canonical_hash(envelope.data):
                # Identical content -> idempotent no-op
                return False

        content_str = canonical_dumps(env_dict)
        self.atomic_write(path, content_str)

        # If a tombstone existed for this entity, delete it upon update/re-creation
        self.delete_tombstone(envelope.type, envelope.id)

        return True

    def delete(self, entity_type: str, entity_id: str, deleted_by: str = "agent-core") -> bool:
        path = self._validate_and_get_path(entity_type, entity_id)
        existed = path.exists()

        if existed:
            try:
                path.unlink()
            except Exception as e:
                raise StorageError(f"Failed to delete entity file '{path}': {e}") from e

        # Create tombstone
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        ts = Tombstone(
            entity_type=entity_type,
            entity_id=entity_id,
            deleted_at=now_iso,
            deleted_by=deleted_by
        )
        self.put_tombstone(ts)
        return existed

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
        path = self._validate_and_get_path(entity_type, entity_id)
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

    def get_tombstone(self, entity_type: str, entity_id: str) -> Optional[Tombstone]:
        path = self._validate_and_get_path(entity_type, entity_id, is_tombstone=True)
        if not path.exists():
            return None
        try:
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
            return Tombstone.from_dict(data)
        except Exception as e:
            raise StorageCorruptionError(f"Corrupted tombstone file '{path}': {e}") from e

    def put_tombstone(self, tombstone: Tombstone) -> bool:
        path = self._validate_and_get_path(tombstone.entity_type, tombstone.entity_id, is_tombstone=True)
        content_str = canonical_dumps(tombstone.to_dict())
        self.atomic_write(path, content_str)
        return True

    def list_tombstones(self, entity_type: Optional[str] = None, skip_invalid: bool = False) -> List[Tombstone]:
        tombstone_root = self.root_path / ".tombstones"
        if not tombstone_root.exists():
            return []

        folders = [TYPE_TO_FOLDER[entity_type]] if entity_type and entity_type in TYPE_TO_FOLDER else TYPE_TO_FOLDER.values()
        tombstones = []

        for folder in folders:
            folder_path = tombstone_root / folder
            if not folder_path.exists():
                continue
            for file in sorted(folder_path.glob("*.json")):
                try:
                    content = file.read_text(encoding="utf-8")
                    data = json.loads(content)
                    tombstones.append(Tombstone.from_dict(data))
                except Exception as e:
                    if not skip_invalid:
                        raise StorageCorruptionError(f"Corrupted tombstone file '{file}': {e}") from e

        return tombstones

    def delete_tombstone(self, entity_type: str, entity_id: str) -> bool:
        path = self._validate_and_get_path(entity_type, entity_id, is_tombstone=True)
        if path.exists():
            try:
                path.unlink()
                return True
            except Exception as e:
                raise StorageError(f"Failed to delete tombstone file '{path}': {e}") from e
        return False
