import os
import json
import platform
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


# Default iCloud ubiquitous container path on macOS/iOS
DEFAULT_ICLOUD_CONTAINER_ID = "iCloud.com.agentpersonalvault.Vault"


def get_default_icloud_root(container_id: str = DEFAULT_ICLOUD_CONTAINER_ID) -> Path:
    """
    Returns the standard macOS iCloud Drive ubiquitous Documents container directory path.
    """
    home = Path.home()
    if platform.system() == "Darwin":
        return home / "Library" / "Mobile Documents" / container_id.replace(".", "~") / "Documents"
    else:
        return home / ".icloud_vault" / container_id / "Documents"


class ICloudDriveProvider(StorageProvider):
    """
    iCloud Drive Storage Provider.
    Models Apple ubiquitous container filesystem semantics (Ubiquity / Mobile Documents directory).
    Handles iCloud ubiquitous placeholder files ('.<filename>.icloud') when files are evicted/not downloaded.

    ARCHITECTURE & PLATFORM LIMITATION NOTICE:
    In standard Python cross-platform environments (and CI linux environments) without Objective-C /
    PyObjC bindings to NSFileManager's startDownloadingUbiquitousItemAtURL:, this provider
    operates via standard filesystem semantics on Apple ubiquitous container paths.
    It detects and manages Apple ubiquitous placeholder files ('.<id>.json.icloud') when files
    are evicted by macOS/iOS, raising StorageUnavailableError or handling test-double payloads.
    It does NOT invoke native Apple Cocoa NSFileManager APIs directly.
    """

    def __init__(
        self,
        icloud_root: Optional[Path] = None,
        container_id: str = DEFAULT_ICLOUD_CONTAINER_ID,
        schema_dir: Optional[Path] = None,
        auto_download_placeholder: bool = False
    ):
        if icloud_root is None:
            self.icloud_root = get_default_icloud_root(container_id).resolve()
        else:
            self.icloud_root = Path(icloud_root).resolve()

        self.container_id = container_id
        self.auto_download_placeholder = auto_download_placeholder
        self.validator = SchemaValidator(schema_dir=schema_dir)
        self._ensure_directories()

    def _ensure_directories(self):
        for folder in TYPE_TO_FOLDER.values():
            (self.icloud_root / folder).mkdir(parents=True, exist_ok=True)
            (self.icloud_root / ".tombstones" / folder).mkdir(parents=True, exist_ok=True)

    def _validate_and_get_path(self, entity_type: str, entity_id: str, is_tombstone: bool = False, is_placeholder: bool = False) -> Path:
        if not isinstance(entity_type, str) or not isinstance(entity_id, str):
            raise ValueError("entity_type and entity_id must be strings.")

        folder = TYPE_TO_FOLDER.get(entity_type)
        if not folder:
            raise ValueError(f"Unknown entity type: '{entity_type}'")

        if not entity_id or "/" in entity_id or "\\" in entity_id or ".." in entity_id or entity_id.startswith("."):
            raise ValueError(f"Invalid or unsafe entity_id: '{entity_id}'")

        base_dir = (self.icloud_root / ".tombstones" / folder) if is_tombstone else (self.icloud_root / folder)
        base_dir.mkdir(parents=True, exist_ok=True)

        fname = f".{entity_id}.json.icloud" if is_placeholder else f"{entity_id}.json"
        target_path = (base_dir / fname).resolve()
        resolved_base = base_dir.resolve()

        try:
            target_path.relative_to(resolved_base)
        except ValueError:
            raise ValueError(f"Path traversal detected: entity_id '{entity_id}' escapes base directory '{resolved_base}'")

        return target_path

    def is_evicted(self, entity_type: str, entity_id: str) -> bool:
        normal_path = self._validate_and_get_path(entity_type, entity_id)
        placeholder_path = self._validate_and_get_path(entity_type, entity_id, is_placeholder=True)
        return (not normal_path.exists()) and placeholder_path.exists()

    def exists(self, entity_type: str, entity_id: str) -> bool:
        normal_path = self._validate_and_get_path(entity_type, entity_id)
        placeholder_path = self._validate_and_get_path(entity_type, entity_id, is_placeholder=True)
        return normal_path.exists() or placeholder_path.exists()

    def trigger_download(self, entity_type: str, entity_id: str) -> bool:
        """
        Triggers download / un-evict for an iCloud Drive placeholder file.
        In filesystem adapter mode, if placeholder contains valid payload, restores the downloaded file.
        Note: Native Apple NSFileManager.startDownloadingUbiquitousItemAtURL is not invoked in this Python layer.
        """
        if not self.is_evicted(entity_type, entity_id):
            return True

        placeholder_path = self._validate_and_get_path(entity_type, entity_id, is_placeholder=True)
        normal_path = self._validate_and_get_path(entity_type, entity_id)

        try:
            if placeholder_path.stat().st_size > 0:
                content = placeholder_path.read_text(encoding="utf-8")
                normal_path.write_text(content, encoding="utf-8")
                placeholder_path.unlink()
                return True
        except Exception as e:
            raise StorageUnavailableError(f"Failed to download/unevict iCloud file for '{entity_id}': {e}") from e

        return False

    def atomic_write(self, filepath: Path, content_str: str) -> None:
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
            raise StorageError(f"Atomic write to iCloud Drive path failed for '{filepath}': {e}") from e

    def get(self, entity_type: str, entity_id: str, validate_schema: bool = True) -> Optional[Envelope]:
        if self.is_evicted(entity_type, entity_id):
            if self.auto_download_placeholder:
                self.trigger_download(entity_type, entity_id)
            else:
                raise StorageUnavailableError(
                    f"Entity '{entity_id}' of type '{entity_type}' is on iCloud Drive but evicted/not yet downloaded "
                    f"locally (ubiquitous placeholder '.{entity_id}.json.icloud' present)."
                )

        path = self._validate_and_get_path(entity_type, entity_id)
        if not path.exists():
            return None

        try:
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
            if validate_schema:
                self.validator.validate_entity(data)
            return Envelope.from_dict(data)
        except StorageUnavailableError:
            raise
        except Exception as err:
            raise StorageCorruptionError(
                f"Failed to read/validate iCloud entity '{entity_id}' of type '{entity_type}' from '{path}': {err}"
            ) from err

    def put(
        self,
        envelope: Envelope,
        validate_schema: bool = True
    ) -> bool:
        path = self._validate_and_get_path(envelope.type, envelope.id)
        placeholder_path = self._validate_and_get_path(envelope.type, envelope.id, is_placeholder=True)

        env_dict = envelope.to_dict()
        if validate_schema:
            self.validator.validate_entity(env_dict)

        if path.exists():
            # Check existing data. If existing is corrupt, get() raises StorageCorruptionError (NOT swallowed!)
            existing = self.get(envelope.type, envelope.id, validate_schema=False)
            if existing and canonical_hash(existing.data) == canonical_hash(envelope.data):
                return False

        content_str = canonical_dumps(env_dict)
        self.atomic_write(path, content_str)

        if placeholder_path.exists():
            try:
                placeholder_path.unlink()
            except Exception as e:
                raise StorageError(f"Failed to remove evicted placeholder file '{placeholder_path}': {e}") from e

        # Remove tombstone if it exists
        self.delete_tombstone(envelope.type, envelope.id)

        return True

    def delete(self, entity_type: str, entity_id: str, deleted_by: str = "agent-core") -> bool:
        normal_path = self._validate_and_get_path(entity_type, entity_id)
        placeholder_path = self._validate_and_get_path(entity_type, entity_id, is_placeholder=True)

        existed = normal_path.exists() or placeholder_path.exists()
        if normal_path.exists():
            normal_path.unlink()
        if placeholder_path.exists():
            placeholder_path.unlink()

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
        dir_path = self.icloud_root / folder
        if not dir_path.exists():
            return []

        entities = []
        for file in sorted(dir_path.glob("*.json")):
            if file.name.startswith("."):
                continue  # Skip hidden/tmp/placeholder files in normal list
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
                        f"Corrupted or invalid entity file '{file}' in iCloud store: {err}"
                    ) from err

        return entities

    def get_metadata(self, entity_type: str, entity_id: str) -> Optional[Dict[str, Any]]:
        if not self.exists(entity_type, entity_id):
            return None

        is_ev = self.is_evicted(entity_type, entity_id)
        if is_ev:
            p_path = self._validate_and_get_path(entity_type, entity_id, is_placeholder=True)
            stat = p_path.stat()
            mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            return {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "path": str(p_path),
                "size_bytes": stat.st_size,
                "mtime": mtime_iso,
                "mtime_timestamp": stat.st_mtime,
                "content_hash": None,
                "provider": "ICloudDriveProvider",
                "icloud_status": "evicted_placeholder",
                "is_downloaded": False,
            }

        path = self._validate_and_get_path(entity_type, entity_id)
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
            "provider": "ICloudDriveProvider",
            "icloud_status": "downloaded_local",
            "is_downloaded": True,
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
        tombstone_root = self.icloud_root / ".tombstones"
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
