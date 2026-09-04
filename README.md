# Personal Storage Standard v1 (`agent-personal-vault`)

The **Personal Vault** is the portable personal-data format and storage standard for **Agent-Core**.

> **Core Boundary Principle:**
> *"Agent-Core owns behavior and intelligence. Personal Vault owns persistent personal data."*

This repository defines the canonical schemas, domain separations, relationship models, and reference Python implementation for personal vault stores. It is intentionally decoupled from runtime agent logic, AI models, vector databases, and provider implementations.

---

## Architecture & Provider Independence

The architecture maintains a strict conceptual boundary:

```
Agent-Core (Behavior & Intelligence)
       ↓
Storage Contract (Provider Abstract Interface)
       ↓
StorageProvider Abstraction (agent_personal_vault.providers)
├── LocalFilesystemProvider (Offline-first Local Store)
├── ICloudDriveProvider (Apple iCloud Drive Ubiquitous Container)
└── Future Providers (SQLite, CloudKit, S3, etc.)
```

### StorageProvider Abstraction
`StorageProvider` is an abstract interface (`agent_personal_vault.providers.StorageProvider`) that decouples Agent-Core domain reasoning from underlying storage backends:

* **LocalFilesystemProvider:** Primary offline-first storage provider using atomic write semantics on local disk.
* **ICloudDriveProvider:** iCloud Drive-compatible storage provider operating on Apple ubiquitous container paths (`~/Library/Mobile Documents/iCloud~.../Documents`). It detects `.icloud` ubiquitous placeholder files when files are evicted/not downloaded locally, raising `StorageUnavailableError` or triggering download on demand.
* **VaultSyncEngine:** Bi-directional sync engine (`agent_personal_vault.sync.VaultSyncEngine`) supporting local-only upload, remote-only download, identical no-op, and divergent conflict resolution. Conflicts generate deterministic conflict preservation copies (`<id>.conflict.<timestamp>.<hash>.json`) without silently overwriting divergent data.

The vault is **DATA, not business logic**. Data structures are provider-independent and stored in deterministic JSON formats.

---

## Folder Structure

The repository structure reflects domain-separated persistent folders:

```
agent-personal-vault/
├── README.md
├── .gitignore
├── agent_personal_vault/          # Reference Python Vault Library
│   ├── __init__.py
│   ├── models.py                  # Canonical Envelope and Provenance models
│   ├── serialization.py           # Deterministic JSON serialization and SHA-256 hashing
│   ├── validation.py              # JSON Schema Draft 2020-12 validator
│   ├── store.py                   # Atomic filesystem store with idempotency
│   ├── strategy_manager.py        # Strategy lifecycle and application manager
│   ├── migration.py               # Schema migration engine
│   ├── export_import.py           # Export/Import processor with manifest checksums
│   └── backup_restore.py          # Portable backup and restore manager
├── schema/
│   └── v1/                        # JSON Schema Draft 2020-12 definitions
├── identity/                      # Persona, display info, public keys
├── user_context/                  # User preferences, working hours, environment constraints
├── memory/                        # Episodic, semantic, working memories
├── knowledge/                     # Structured domain facts and verified statements
├── experiences/                   # Task execution attempts, actions, observations, verifications
├── lessons/                       # Generalized insights extracted from experiences
├── strategies/                    # Rules, methods, prerequisites, confidence, candidate status
├── strategy_applications/         # Logs of strategy execution context and outcomes
├── goals/                         # Objectives, targets, and metrics
├── tasks/                         # Discrete executable tasks and steps
├── events/                        # Discrete system runtime events
├── runtime/                       # Active session state and transient context
├── audit/                         # Append-only history and entity lifecycle trace log
├── backups/                       # Backup archives and manifest templates
└── tests/                         # Complete unit, integration, and E2E test suite
```

---

## Canonical Data Envelope

All vault entities are wrapped in a standard JSON envelope:

```json
{
  "schema_version": "1.0",
  "id": "entity-unique-id",
  "type": "strategy",
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z",
  "provenance": {
    "source": "agent-core",
    "source_id": "run-12345",
    "agent_version": "1.0.0",
    "metadata": {}
  },
  "data": { ... }
}
```

### Envelope Principles
* **Stable Unique IDs:** IDs remain unchanged throughout entity life.
* **Explicit Schema Version:** Versioning allows forward-compatible schema migrations.
* **ISO-8601 Timestamps:** Standard UTC timestamp formatting (`YYYY-MM-DDTHH:MM:SSZ`).
* **Provenance:** Tracks source, source run ID, agent version, and custom metadata.
* **Deterministic Serialization:** Canonical sorting of keys ensures repeatable content hashing.

---

## Domain Separation

To prevent knowledge degradation and preserve conceptual clarity, entities are separated into domain buckets:

* **Identity:** *WHO I AM*
* **User Context:** *WHAT I LIKE / MY CONSTRAINTS*
* **Knowledge / Memory:** *WHAT I KNOW*
* **Experience:** *WHAT I EXPERIENCED*
* **Lesson:** *WHAT I LEARNED*
* **Strategy:** *WHAT I BELIEVE WORKS*
* **Strategy Application:** *HOW A STRATEGY WAS USED*
* **Goal:** *WHAT I WANT TO ACHIEVE*
* **Task:** *WHAT I AM DOING*
* **Event:** *WHAT HAPPENED IN THE SYSTEM*
* **Runtime State:** *CURRENT EXECUTION STATE*
* **Audit:** *WHAT HAPPENED FOR AUDIT / TRACEABILITY*

---

## Traceability Chain & Strategy Model

### Traceability Chain
Relationships between entities use stable IDs to maintain complete traceability across system execution:

$$\text{TASK} \rightarrow \text{ACTION} \rightarrow \text{OBSERVATION} \rightarrow \text{VERIFICATION} \rightarrow \text{EXPERIENCE} \rightarrow \text{LESSON} \rightarrow \text{STRATEGY} \rightarrow \text{STRATEGY APPLICATION} \rightarrow \text{OUTCOME} \rightarrow \text{STRATEGY UPDATE}$$

### Strategy Model & Lifecycle Statuses
Strategies represent reusable procedural rules developed by Agent-Core.

Supported Strategy statuses:
* `CANDIDATE`: Newly generated strategy from lessons.
* `VALIDATED`: Applied successfully at least once.
* `SUPPORTED`: Proven effective over multiple applications.
* `WEAKENED`: Encountered repeated failures or degraded confidence.
* `RETIRED`: Explicitly deactivated.
* `SUPERSEDED`: Replaced by a newer strategy version.

State transitions are strictly validated. Direct jumps from `CANDIDATE` to `SUPPORTED` or transitions out of terminal states (`RETIRED`, `SUPERSEDED`) are rejected with `ValueError`.

### Strategy Supersession & Non-Destructive Mutation
When strategy B supersedes strategy A:
* Old strategy A status becomes `SUPERSEDED` and `superseded_by` points to B.
* New strategy B `supersedes` points to A and version increments.
* Historical evidence and audit logs are preserved without destructive mutations.

---

## Data Integrity, Idempotency, & Atomic Writes

* **Atomic Writes:** Files are written to `.tmp` files in the same directory, flushed/fsynced, and atomically replaced (`os.replace`).
* **Idempotency:** Re-ingesting an entity with identical data generates no duplicates or redundant audit entries.
* **Duplicate Detection:** SHA-256 canonical hashing detects duplicate content.
* **Corruption Protection:** `list_all()` and `get()` surface corrupted records by default (raising `ValueError`) instead of silently ignoring data loss. Diagnostic mode (`skip_invalid=True`) captures corrupt record details without silent failure.
* **Relationship Integrity:** Relationship validation verifies that referenced entity IDs exist, types match, self-references are rejected where invalid, and circular supersession loops are detected.
* **Path Traversal Security:** Archive extraction in `BackupRestoreManager` enforces path containment to prevent path traversal security vulnerabilities.

---

## Versioning & Migration Strategy

All entities carry `schema_version`. When schemas evolve (e.g., `1.0` to `1.1`), `MigrationEngine` applies forward-compatible migration handlers to transform older entity envelopes without losing historical data.

---

## Export / Import & Backup / Restore

### Portable Export
Exports dump vault contents along with `manifest.json`:
```json
{
  "format_version": "1.0",
  "exported_at": "2025-01-01T00:00:00Z",
  "entity_counts": { "identity": 1, "strategy": 5 },
  "checksums": { "identity/user.json": "a1b2c3..." }
}
```
Importing validates schema versions, checksums, and topological relationship order before committing data.

### Backup / Restore
Backups generate tar.gz archives with `backup_manifest.json` containing archive checksums and total entity counts for verified restoration.

---

## Privacy Boundary

This repository is **PUBLIC** as a specification and reference standard.
* **NEVER** commit real personal information, API keys, credentials, tokens, or private conversations.
* All reference files in domain folders contain **synthetic test data**.
* `.gitignore` excludes `.env*`, `*.key`, `*.pem`, `*.db`, `*.sqlite`, local transient runtime files, and real backup archives.

---

## How Agent-Core Connects to Personal Vault

Agent-Core interacts with Personal Vault using a **Storage Contract**:

```python
class StorageContract(Protocol):
    def get(self, entity_type: str, entity_id: str) -> Envelope: ...
    def put(self, envelope: Envelope) -> bool: ...
    def list_all(self, entity_type: str) -> List[Envelope]: ...
    def record_strategy_application(self, ...) -> Envelope: ...
```

Agent-Core executes business and reasoning logic, then persists resulting identity updates, tasks, experiences, lessons, strategies, and audit logs into Personal Vault through `VaultStore`.

---

## Testing

Run the full test suite with:

```bash
python3 -m pytest -v
```

Tests cover schema validation, valid entity creation, invalid entity rejection, stable IDs, provenance, relationship integrity, duplicate/idempotent import, export/import roundtrip, backup/restore, schema migration, strategy lifecycle/applications, audit preservation, and end-to-end traceability.
