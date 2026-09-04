import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from .models import Envelope, TYPE_TO_FOLDER
from .providers.base import StorageProvider, StorageUnavailableError
from .serialization import canonical_dumps, canonical_hash


@dataclass
class ConflictRecord:
    entity_type: str
    entity_id: str
    local_hash: str
    remote_hash: str
    local_mtime: str
    remote_mtime: str
    conflict_copy_id: str
    resolution_strategy: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "local_hash": self.local_hash,
            "remote_hash": self.remote_hash,
            "local_mtime": self.local_mtime,
            "remote_mtime": self.remote_mtime,
            "conflict_copy_id": self.conflict_copy_id,
            "resolution_strategy": self.resolution_strategy,
        }


@dataclass
class SyncReport:
    timestamp: str
    uploaded_count: int = 0
    downloaded_count: int = 0
    identical_count: int = 0
    conflict_count: int = 0
    skipped_count: int = 0
    conflicts: List[ConflictRecord] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "uploaded_count": self.uploaded_count,
            "downloaded_count": self.downloaded_count,
            "identical_count": self.identical_count,
            "conflict_count": self.conflict_count,
            "skipped_count": self.skipped_count,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "errors": self.errors,
        }


class VaultSyncEngine:
    """
    Bi-directional Sync Engine for Personal Vault between two StorageProviders
    (e.g., LocalFilesystemProvider and ICloudDriveProvider).

    Conflict Model:
    - Local-only: upload to remote.
    - Remote-only: download to local.
    - Identical: no-op.
    - Divergent (conflicting):
      * NEVER silently overwrite divergent data!
      * Saves a deterministic conflict copy: '<id>.conflict.<timestamp>.<hash[:8]>' in local/remote store.
      * Keeps the record with the newer 'updated_at' timestamp as the primary entity.
      * Logs an explicit ConflictRecord in SyncReport.
    """

    def __init__(
        self,
        local_provider: StorageProvider,
        remote_provider: StorageProvider,
        auto_unevict_remote: bool = False
    ):
        self.local_provider = local_provider
        self.remote_provider = remote_provider
        self.auto_unevict_remote = auto_unevict_remote

    def sync(self) -> SyncReport:
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        report = SyncReport(timestamp=now_iso)

        for entity_type in TYPE_TO_FOLDER.keys():
            if entity_type in ("export_manifest", "backup_manifest"):
                continue

            # List entities in local and remote
            try:
                local_entities = {env.id: env for env in self.local_provider.list_all(entity_type, skip_invalid=True)}
            except Exception as e:
                report.errors.append({"entity_type": entity_type, "location": "local", "error": str(e)})
                continue

            try:
                remote_entities = {env.id: env for env in self.remote_provider.list_all(entity_type, skip_invalid=True)}
            except Exception as e:
                report.errors.append({"entity_type": entity_type, "location": "remote", "error": str(e)})
                continue

            all_ids = set(local_entities.keys()).union(remote_entities.keys())

            for eid in sorted(all_ids):
                local_env = local_entities.get(eid)
                remote_env = remote_entities.get(eid)

                try:
                    self._sync_entity(entity_type, eid, local_env, remote_env, report)
                except StorageUnavailableError as e:
                    report.skipped_count += 1
                    report.errors.append({"entity_type": entity_type, "entity_id": eid, "error": f"Skipped evicted/unavailable file: {e}"})
                except Exception as e:
                    report.errors.append({"entity_type": entity_type, "entity_id": eid, "error": str(e)})

        return report

    def _sync_entity(
        self,
        entity_type: str,
        entity_id: str,
        local_env: Optional[Envelope],
        remote_env: Optional[Envelope],
        report: SyncReport
    ):
        # Case 1: Local only -> Upload to remote
        if local_env is not None and remote_env is None:
            self.remote_provider.put(local_env)
            report.uploaded_count += 1
            return

        # Case 2: Remote only -> Download to local
        if local_env is None and remote_env is not None:
            self.local_provider.put(remote_env)
            report.downloaded_count += 1
            return

        # Case 3: Present in both -> Compare hashes
        if local_env is not None and remote_env is not None:
            local_hash = canonical_hash(local_env.to_dict())
            remote_hash = canonical_hash(remote_env.to_dict())

            if local_hash == remote_hash:
                report.identical_count += 1
                return

            # Case 4: Divergent / Conflict!
            report.conflict_count += 1

            ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            conflict_copy_id = f"{entity_id}-conflict-{ts_str}-{local_hash[:8]}"

            # Determine LWW (Last-Write-Wins) winner based on updated_at timestamp
            local_updated = local_env.updated_at or ""
            remote_updated = remote_env.updated_at or ""

            if local_updated >= remote_updated:
                # Local wins as primary -> Preserve remote divergent as conflict copy
                conflict_env = Envelope(
                    id=conflict_copy_id,
                    type=entity_type,
                    created_at=remote_env.created_at,
                    updated_at=remote_env.updated_at,
                    provenance=remote_env.provenance,
                    data=remote_env.data
                )
                self.local_provider.put(conflict_env)
                self.remote_provider.put(conflict_env)

                # Overwrite remote with local primary
                self.remote_provider.put(local_env)
                resolution = "local_wins_remote_conflict_saved"
            else:
                # Remote wins as primary -> Preserve local divergent as conflict copy
                conflict_env = Envelope(
                    id=conflict_copy_id,
                    type=entity_type,
                    created_at=local_env.created_at,
                    updated_at=local_env.updated_at,
                    provenance=local_env.provenance,
                    data=local_env.data
                )
                self.local_provider.put(conflict_env)
                self.remote_provider.put(conflict_env)

                # Overwrite local with remote primary
                self.local_provider.put(remote_env)
                resolution = "remote_wins_local_conflict_saved"

            c_rec = ConflictRecord(
                entity_type=entity_type,
                entity_id=entity_id,
                local_hash=local_hash,
                remote_hash=remote_hash,
                local_mtime=local_updated,
                remote_mtime=remote_updated,
                conflict_copy_id=conflict_copy_id,
                resolution_strategy=resolution,
            )
            report.conflicts.append(c_rec)
