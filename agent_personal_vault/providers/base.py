from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from ..models import Envelope, Tombstone


class StorageError(ValueError):
    """Base exception for storage provider operations, inheriting from ValueError for compatibility."""
    pass


class EntityNotFoundError(StorageError):
    """Raised when an entity is not found in the storage provider."""
    pass


class StorageCorruptionError(StorageError):
    """Raised when entity data or metadata is corrupted."""
    pass


class StorageUnavailableError(StorageError):
    """Raised when the storage provider or specific file/container is unavailable (e.g. offline/evicted)."""
    pass


class ConflictError(StorageError):
    """Raised when a storage write or sync conflict occurs."""
    pass


class StorageProvider(ABC):
    """
    Abstract StorageProvider interface.
    Decouples Agent-Core and Personal Vault domain logic from underlying storage mechanisms
    (Local Filesystem, iCloud Drive, SQLite, S3, etc.).
    """

    @abstractmethod
    def get(self, entity_type: str, entity_id: str) -> Optional[Envelope]:
        """Retrieve envelope by entity_type and entity_id."""
        pass

    @abstractmethod
    def put(self, envelope: Envelope) -> bool:
        """Store envelope. Returns True if created/updated, False if idempotent skip."""
        pass

    @abstractmethod
    def delete(self, entity_type: str, entity_id: str) -> bool:
        """Delete entity by entity_type and entity_id."""
        pass

    @abstractmethod
    def exists(self, entity_type: str, entity_id: str) -> bool:
        """Check if entity exists."""
        pass

    @abstractmethod
    def list_all(
        self,
        entity_type: str,
        skip_invalid: bool = False,
        diagnostics: Optional[List[Dict[str, Any]]] = None
    ) -> List[Envelope]:
        """List all envelopes for entity_type."""
        pass

    @abstractmethod
    def get_metadata(self, entity_type: str, entity_id: str) -> Optional[Dict[str, Any]]:
        """
        Get provider-level metadata for an entity (mtime, size, content_hash, cloud_status, etc.).
        Returns None if entity does not exist.
        """
        pass

    @abstractmethod
    def get_tombstone(self, entity_type: str, entity_id: str) -> Optional[Tombstone]:
        """Get tombstone for entity if it was deleted."""
        pass

    @abstractmethod
    def put_tombstone(self, tombstone: Tombstone) -> bool:
        """Record tombstone for deleted entity."""
        pass

    @abstractmethod
    def list_tombstones(self, entity_type: Optional[str] = None) -> List[Tombstone]:
        """List tombstones, optionally filtered by entity_type."""
        pass

    @abstractmethod
    def delete_tombstone(self, entity_type: str, entity_id: str) -> bool:
        """Remove a tombstone."""
        pass
