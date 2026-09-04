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
from ..models import Envelope, TYPE_TO_FOLDER, FOLDER_TO_TYPE
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
        # macOS ubiquitous container root
        return home / "Library" / "Mobile Documents" / container_id.replace(".", "~") / "Documents"
    else:
        # Fallback / simulation path on non-macOS platforms
        return home / ".icloud_vault" / container_id / "Documents"


class ICloudDriveProvider(StorageProvider):
    """
    iCloud Drive Storage Provider.
    Models Apple ubiquitous container filesystem semantics (Ubiquity / Mobile Documents directory).
    Handles iCloud ubiquitous placeholder files ('.<filename>.icloud') when files are evicted/not downloaded.
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

    def _get_entity_path(self, entity_type: str, entity_id: str) -> Path:
        folder = TYPE_TO_FOLDER.get(entity_type)
        if not folder:
            raise ValueError(f"Unknown entity type: '{entity_type}'")
        return self.icloud_root / folder / f"{entity_id}.json"

    def _get_placeholder_path(self, entity_type: str, entity_id: str) -> Path:
        """
        Returns the Apple ubiquitous container placeholder path: .<filename>.json.icloud
        """
        folder = TYPE_TO_FOLDER.get(entity_type)
        if not folder:
            raise ValueError(f"Unknown entity type: '{entity_type}'")
        return self.icloud_root / folder / f".{entity_id}.json.icloud"

    def is_evicted(self, entity_type: str, entity_id: str) -> bool:
        """
        Returns True if the file exists on iCloud Drive but is currently evicted
        (only the ubiquitous .<filename>.json.icloud placeholder file is present locally).
        """
        normal_path = self._get_entity_path(entity_type, entity_id)
        placeholder_path = self._get_placeholder_path(entity_type, entity_id)
        return (not normal_path.exists()) and placeholder_path.exists()

    def exists(self, entity_type: str, entity_id: str) -> bool:
        normal_path = self._get_entity_path(entity_type, entity_id)
        placeholder_path = self._get_placeholder_path(entity_type, entity_id)
        return normal_path.exists() or placeholder_path.exists()

    def trigger_download(self, entity_type: str, entity_id: str) -> bool:
        """
        Simulates / invokes platform ubiquitous file download request.
        On macOS/iOS,NSFileManager or startDownloadingUbiquitousItemAtURL would be called.
        In filesystem simulation mode, if placeholder contains data payload or test simulation, resolves it.
        """
        if not self.is_evicted(entity_type, entity_id):
            return True

        placeholder_path = self._get_placeholder_path(entity_type, entity_id)
        normal_path = self._get_entity_path(entity_type, entity_id)

        # macOS platform call simulation / test double resolution
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
        """Atomic write to ubiquitous directory."""
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

        path = self._get_entity_path(entity_type, entity_id)
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
        env_dict = envelope.to_dict()
        if validate_schema:
            self.validator.validate_entity(env_dict)

        path = self._get_entity_path(envelope.type, envelope.id)
        placeholder_path = self._get_placeholder_path(envelope.type, envelope.id)

        # Check if existing entity is identical
        if path.exists():
            try:
                existing = self.get(envelope.type, envelope.id, validate_schema=False)
                if existing and canonical_hash(existing.data) == canonical_hash(envelope.data):
                    return False
            except Exception:
                pass

        content_str = canonical_dumps(env_dict)
        self.atomic_write(path, content_str)

        # If a placeholder existed previously, remove it
        if placeholder_path.exists():
            try:
                placeholder_path.unlink()
            except Exception:
                pass

        return True

    def delete(self, entity_type: str, entity_id: str) -> bool:
        normal_path = self._get_entity_path(entity_type, entity_id)
        placeholder_path = self._get_placeholder_path(entity_type, entity_id)

        deleted = False
        if normal_path.exists():
            normal_path.unlink()
            deleted = True
        if placeholder_path.exists():
            placeholder_path.unlink()
            deleted = True

        return deleted

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
            p_path = self._get_placeholder_path(entity_type, entity_id)
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

        path = self._get_entity_path(entity_type, entity_id)
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
