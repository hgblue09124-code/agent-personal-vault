import shutil
import tarfile
import json
import hashlib
import uuid
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from .models import Envelope, TYPE_TO_FOLDER
from .serialization import canonical_dumps, canonical_hash
from .store import VaultStore


class BackupRestoreManager:
    def __init__(self, store: VaultStore):
        self.store = store

    def create_backup(self, backup_dir: Path) -> Path:
        """
        Creates a portable backup tar.gz archive with a backup_manifest.
        """
        backup_dir = Path(backup_dir).resolve()
        backup_dir.mkdir(parents=True, exist_ok=True)

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_name = f"vault_backup_{timestamp_str}.tar.gz"
        archive_path = backup_dir / archive_name

        total_entities = 0
        entity_counts = {}

        # Collect entities to temp directory
        temp_export_dir = backup_dir / f"_tmp_backup_{timestamp_str}"
        temp_export_dir.mkdir(parents=True, exist_ok=True)

        try:
            for entity_type, folder_name in TYPE_TO_FOLDER.items():
                if entity_type in ("export_manifest", "backup_manifest"):
                    continue
                entities = self.store.list_all(entity_type)
                if not entities:
                    continue

                folder_path = temp_export_dir / folder_name
                folder_path.mkdir(parents=True, exist_ok=True)
                entity_counts[entity_type] = len(entities)
                total_entities += len(entities)

                for env in entities:
                    content = canonical_dumps(env.to_dict())
                    out_p = folder_path / f"{env.id}.json"
                    self.store.atomic_write(out_p, content)

            # Create archive
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(temp_export_dir, arcname="vault_data")

            # Calculate archive checksum
            archive_bytes = archive_path.read_bytes()
            archive_checksum = hashlib.sha256(archive_bytes).hexdigest()

            # Create manifest
            manifest_data = {
                "vault_version": "1.0",
                "backup_timestamp": now_iso,
                "total_entities": total_entities,
                "archive_checksum": archive_checksum,
                "entity_counts": entity_counts,
            }

            manifest_env = Envelope(
                id=f"backup-{timestamp_str}",
                type="backup_manifest",
                created_at=now_iso,
                updated_at=now_iso,
                data=manifest_data,
            )

            manifest_file = backup_dir / f"backup_manifest_{timestamp_str}.json"
            self.store.atomic_write(manifest_file, canonical_dumps(manifest_env.to_dict()))

            return archive_path

        finally:
            if temp_export_dir.exists():
                shutil.rmtree(temp_export_dir)

    def restore_from_backup(self, archive_path: Path, manifest_path: Optional[Path] = None) -> Dict[str, Any]:
        """
        Restores vault data from backup archive into store.
        """
        archive_path = Path(archive_path).resolve()
        if not archive_path.exists():
            raise FileNotFoundError(f"Backup archive not found: {archive_path}")

        # Validate archive checksum if manifest is provided
        if manifest_path and Path(manifest_path).exists():
            m_dict = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            expected_checksum = m_dict.get("data", {}).get("archive_checksum")
            if expected_checksum:
                actual_checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
                if actual_checksum != expected_checksum:
                    raise ValueError("Backup archive checksum verification failed!")

        # Extract to temporary directory
        extract_dir = archive_path.parent / f"_tmp_restore_{uuid.uuid4().hex[:8]}"
        extract_dir.mkdir(parents=True, exist_ok=True)

        restored_count = 0
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(path=extract_dir, filter="data")
                else:
                    tar.extractall(path=extract_dir)

            vault_data_dir = extract_dir / "vault_data"
            if not vault_data_dir.exists():
                raise ValueError("Corrupt backup format: missing 'vault_data' folder.")

            for folder in vault_data_dir.iterdir():
                if folder.is_dir():
                    for json_file in folder.glob("*.json"):
                        e_dict = json.loads(json_file.read_text(encoding="utf-8"))
                        env = Envelope.from_dict(e_dict)
                        self.store.put(env, record_audit=True)
                        restored_count += 1

            return {"restored_count": restored_count}

        finally:
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
