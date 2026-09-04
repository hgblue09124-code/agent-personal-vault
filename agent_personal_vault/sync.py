import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from .models import Envelope, Tombstone, TYPE_TO_FOLDER
from .providers.base import StorageProvider, StorageUnavailableError
from .serialization import canonical_dumps, canonical_hash


@dataclass
class ConflictRecord:
    entity_type: str
    entity_id: str
    local_hash: Optional[str]
    remote_hash: Optional[str]
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
    deleted_count: int = 0
    identical_count: int = 0
    conflict_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    conflicts: List[ConflictRecord] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "uploaded_count": self.uploaded_count,
            "downloaded_count": self.downloaded_count,
            "deleted_count": self.deleted_count,
            "identical_count": self.identical_count,
            "conflict_count": self.conflict_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "errors": self.errors,
        }


class VaultSyncEngine:
    """
    Bi-directional Sync Engine for Personal Vault supporting Tombstones and Conflict Resolution.

    Tombstone & Delete Semantics:
    - Deletions create tombstone records (.tombstones/<folder>/<id>.json).
    - Tombstone deletion timestamp is compared against entity updated_at timestamp.
    - If deleted_at >= updated_at: Deletion wins and propagates to other provider.
    - If updated_at > deleted_at: Active update wins, conflict copy is preserved, active entity propagates.
    - Delete vs Delete: Both tombstones synchronized cleanly (no-op).
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

            # Fetch active entities
            try:
                local_entities = {env.id: env for env in self.local_provider.list_all(entity_type, skip_invalid=True)}
            except Exception as e:
                report.failed_count += 1
                report.errors.append({"entity_type": entity_type, "location": "local", "error": f"Failed to list local: {e}"})
                continue

            try:
                remote_entities = {env.id: env for env in self.remote_provider.list_all(entity_type, skip_invalid=True)}
            except Exception as e:
                report.failed_count += 1
                report.errors.append({"entity_type": entity_type, "location": "remote", "error": f"Failed to list remote: {e}"})
                continue

            # Fetch tombstones
            try:
                local_tombstones = {ts.entity_id: ts for ts in self.local_provider.list_tombstones(entity_type, skip_invalid=True)}
            except Exception as e:
                report.failed_count += 1
                report.errors.append({"entity_type": entity_type, "location": "local_tombstones", "error": f"Failed to list local tombstones: {e}"})
                local_tombstones = {}

            try:
                remote_tombstones = {ts.entity_id: ts for ts in self.remote_provider.list_tombstones(entity_type, skip_invalid=True)}
            except Exception as e:
                report.failed_count += 1
                report.errors.append({"entity_type": entity_type, "location": "remote_tombstones", "error": f"Failed to list remote tombstones: {e}"})
                remote_tombstones = {}

            all_ids = set(local_entities.keys()) | set(remote_entities.keys()) | set(local_tombstones.keys()) | set(remote_tombstones.keys())

            for eid in sorted(all_ids):
                l_env = local_entities.get(eid)
                r_env = remote_entities.get(eid)
                l_ts = local_tombstones.get(eid)
                r_ts = remote_tombstones.get(eid)

                try:
                    self._sync_single_entity(entity_type, eid, l_env, r_env, l_ts, r_ts, report)
                except StorageUnavailableError as e:
                    report.skipped_count += 1
                    report.errors.append({"entity_type": entity_type, "entity_id": eid, "error": f"Skipped evicted/unavailable file: {e}"})
                except Exception as e:
                    report.failed_count += 1
                    report.errors.append({"entity_type": entity_type, "entity_id": eid, "error": str(e)})

        return report

    def _sync_single_entity(
        self,
        entity_type: str,
        entity_id: str,
        l_env: Optional[Envelope],
        r_env: Optional[Envelope],
        l_ts: Optional[Tombstone],
        r_ts: Optional[Tombstone],
        report: SyncReport
    ):
        # 1. Delete vs Delete (both sides have tombstones, no active envelopes)
        if l_env is None and r_env is None and (l_ts is not None or r_ts is not None):
            if l_ts and not r_ts:
                self.remote_provider.put_tombstone(l_ts)
            elif r_ts and not l_ts:
                self.local_provider.put_tombstone(r_ts)
            report.identical_count += 1
            return

        # 2. Local Active vs Remote Tombstone
        if l_env is not None and r_env is None and r_ts is not None:
            if r_ts.deleted_at >= (l_env.updated_at or ""):
                # Deletion wins
                self.local_provider.delete(entity_type, entity_id, deleted_by=r_ts.deleted_by)
                self.local_provider.put_tombstone(r_ts)
                report.deleted_count += 1
            else:
                # Active update wins over older deletion -> conflict preserved
                ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                conflict_id = f"{entity_id}-conflict-{ts_str}-deleted"

                # Re-propagate active entity to remote
                self.remote_provider.put(l_env)
                self.remote_provider.delete_tombstone(entity_type, entity_id)

                c_rec = ConflictRecord(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    local_hash=canonical_hash(l_env.to_dict()),
                    remote_hash="DELETED",
                    local_mtime=l_env.updated_at or "",
                    remote_mtime=r_ts.deleted_at,
                    conflict_copy_id=conflict_id,
                    resolution_strategy="active_update_wins_over_tombstone"
                )
                report.conflicts.append(c_rec)
                report.conflict_count += 1
            return

        # 3. Remote Active vs Local Tombstone
        if r_env is not None and l_env is None and l_ts is not None:
            if l_ts.deleted_at >= (r_env.updated_at or ""):
                # Deletion wins
                self.remote_provider.delete(entity_type, entity_id, deleted_by=l_ts.deleted_by)
                self.remote_provider.put_tombstone(l_ts)
                report.deleted_count += 1
            else:
                # Active update wins over older deletion -> conflict preserved
                ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                conflict_id = f"{entity_id}-conflict-{ts_str}-deleted"

                # Re-propagate active entity to local
                self.local_provider.put(r_env)
                self.local_provider.delete_tombstone(entity_type, entity_id)

                c_rec = ConflictRecord(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    local_hash="DELETED",
                    remote_hash=canonical_hash(r_env.to_dict()),
                    local_mtime=l_ts.deleted_at,
                    remote_mtime=r_env.updated_at or "",
                    conflict_copy_id=conflict_id,
                    resolution_strategy="active_update_wins_over_tombstone"
                )
                report.conflicts.append(c_rec)
                report.conflict_count += 1
            return

        # 4. Local Active only (no remote, no tombstone)
        if l_env is not None and r_env is None:
            self.remote_provider.put(l_env)
            report.uploaded_count += 1
            return

        # 5. Remote Active only (no local, no tombstone)
        if r_env is not None and l_env is None:
            self.local_provider.put(r_env)
            report.downloaded_count += 1
            return

        # 6. Active on both sides -> Compare hashes
        if l_env is not None and r_env is not None:
            l_hash = canonical_hash(l_env.to_dict())
            r_hash = canonical_hash(r_env.to_dict())

            if l_hash == r_hash:
                report.identical_count += 1
                return

            # Divergent Conflict!
            report.conflict_count += 1
            ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            conflict_copy_id = f"{entity_id}-conflict-{ts_str}-{l_hash[:8]}"

            l_updated = l_env.updated_at or ""
            r_updated = r_env.updated_at or ""

            # Last-Write-Wins with local preference on tie
            if l_updated >= r_updated:
                # Local wins as primary -> Preserve remote divergent as conflict copy
                conflict_env = Envelope(
                    id=conflict_copy_id,
                    type=entity_type,
                    created_at=r_env.created_at,
                    updated_at=r_env.updated_at,
                    provenance=r_env.provenance,
                    data=r_env.data
                )
                self.local_provider.put(conflict_env)
                self.remote_provider.put(conflict_env)

                self.remote_provider.put(l_env)
                resolution = "local_wins_remote_conflict_saved"
            else:
                # Remote wins as primary -> Preserve local divergent as conflict copy
                conflict_env = Envelope(
                    id=conflict_copy_id,
                    type=entity_type,
                    created_at=l_env.created_at,
                    updated_at=l_env.updated_at,
                    provenance=l_env.provenance,
                    data=l_env.data
                )
                self.local_provider.put(conflict_env)
                self.remote_provider.put(conflict_env)

                self.local_provider.put(r_env)
                resolution = "remote_wins_local_conflict_saved"

            c_rec = ConflictRecord(
                entity_type=entity_type,
                entity_id=entity_id,
                local_hash=l_hash,
                remote_hash=r_hash,
                local_mtime=l_updated,
                remote_mtime=r_updated,
                conflict_copy_id=conflict_copy_id,
                resolution_strategy=resolution,
            )
            report.conflicts.append(c_rec)
