"""
Agent Personal Vault - Reference Storage Standard v1 Implementation
"""

__version__ = "1.0.0"

from .models import Envelope, Provenance, StrategyStatus
from .serialization import canonical_dumps, canonical_hash
from .validation import SchemaValidator
from .store import VaultStore
from .strategy_manager import StrategyManager
from .migration import MigrationEngine
from .export_import import VaultExporter, VaultImporter
from .backup_restore import BackupRestoreManager

__all__ = [
    "Envelope",
    "Provenance",
    "StrategyStatus",
    "canonical_dumps",
    "canonical_hash",
    "SchemaValidator",
    "VaultStore",
    "StrategyManager",
    "MigrationEngine",
    "VaultExporter",
    "VaultImporter",
    "BackupRestoreManager",
]
