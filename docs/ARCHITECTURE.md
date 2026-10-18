# WiseDB Architecture (v4 direction)

> **HTTP server (implemented, 2026-10):** one process, many logical databases under `{data_dir}/databases/`, JSON catalog in `{data_dir}/_catalog/` (users, grants, database registry), in-memory bearer sessions, WQL-only data path (`POST /databases/{name}/execute`), optional `GET .../nodes/{slug}/payload` for media bytes. No `/v1` REST, no `X-API-Key`. Do not revert to a wiki HTTP surface — see `.cursor/rules/wisedb-database-server.mdc`.

> Status: design target below, not an implementation or a claim of production guarantees.
> The v3 **storage engine** does not yet implement WAL, LSM, MVCC, or native multi-table layout; the **server catalog** is JSON, not the system DB described in §2.

> Implementation status (2026-10-03): this document remains a target plan. The v3 safety baseline includes checked JSON output, root-fd-relative POSIX file operations, exclusive process ownership, in-place staged refresh, stricter path/scan/parser checks, atomic single-file publication, media-sidecar editing, fail-closed HTTP startup, and prototype fixes. Phase A is **not yet fully qualified**: media payload+sidecar and human multi-file moves remain non-transactional; deterministic native fault hooks cover allocation, write, fsync, link, rename, unlink, and close failures, but do not model ENOSPC, short writes, process crashes, or power loss. Phase B/C now have an experimental native coordinator and integrated recovery/checkpoint tests, separate from the public v3 engine; this is not production qualification. Phases D–F remain unqualified.

> Platform status: the current filesystem-backed v3 scanner is supported/tested only on POSIX systems. The Windows branch does not scan or reopen the file-backed catalog and must not be treated as supported persistence until separately implemented and qualified.

### Current implementation traceability (2026-10-03)

| Plan area | Current implementation | Regression evidence | Gate status |
|---|---|---|---|
| A — JSON/path/media safety | `src/c/json.c`, `src/c/store.c`, `src/c/wisedb.c`, `src/wisedb/_engine.pyx` | `tests/test_memory_v3.py`, `tests/c/test_core.c`, `tests/c/test_fault_injection.c` | Root-fd path operations, owner lock, staged refresh and deterministic POSIX allocation/I/O failure tests implemented; multi-file media operations remain non-transactional and tested to expose partial outcomes |
| A — transports/auth | `src/wisedb/server/` (catalog, sessions, routes), `src/wisedb/mcp_server.py`, `src/wisedb/server/engine/layout.py` | `tests/test_server_shell.py`, `tests/test_memory_v3.py`, MCP tests | Session + grant checks on WQL; layout ops via language; MCP remains a separate single-path client |
| B — WAL/replay | `src/c/{codec,wal,batch,storage}.c` | `tests/c/test_{wal,batch,recovery}.c` | Experimental one-owner batch/WAL recovery coordinator with staged in-memory visibility, suffix replay, and deterministic allocation/WAL-fsync uncertainty tests; no idempotency/status log, WAL reclamation, or crash qualification |
| C — tables/checkpoint | `src/c/{sstable,manifest,storage}.c` | `tests/c/test_{sstable,manifest,recovery}.c` | Single-table full-state checkpoint integrated with coordinator; directory-sync-after-rename unknown outcome is tested; no memtable flush pipeline, multi-table LSM, MVCC/compaction/pins/shared budgets |
| D — native records and indexes | v3 file/RAM engine in `src/c/{store,index,retrieve}.c` | v3 reopen and search fixtures | Not implemented on v4 transactional storage |
| E/F — managed runtime and qualification | v1 HTTP/MCP and packaging | Python tests and current CI | Not implemented/qualified: no identity system DB, HTTP v2, backup/restore, migration, crash/soak or scale evidence |

This table records source/test locations, not proof that a phase gate is closed. See the phase gates below for the full acceptance criteria.

## 1. Product decisions

WiseDB is a document and graph database with full-text search. It is not a SQL database.

- Storage engine: WiseDB-owned C engine and on-disk format. No SQLite, RocksDB, or external persistence engine.
- Deployment target: one process owns one instance and serves multiple logical databases.
- Scale target: millions of documents and hundreds of logical databases; this is a qualification target, not a measured capacity claim.
- Markdown is an import/export format, not the live source of truth.
- Supported administrative surfaces are CLI and authenticated APIs (HTTP and MCP); no web frontend.
- Identity uses personal and service-account API tokens. Root is an application-level superuser with audited access, never an operating-system root account.
- Bounded atomic write batches may span multiple documents within one database. Cross-database transactions are out of scope.

## 2. Component boundaries

```text
HTTP / MCP / CLI / Python client
              |
     authentication and authorization
              |
       application services
              |
     database runtime manager
       /          |           \
 system DB     user DB A     user DB B ...
       \          |           /
      C storage engine: WAL, LSM, MVCC, indexes, blobs
```

- **C engine:** durability, recovery, transactions, snapshots, indexes, document/graph/search operations, resource accounting, and storage ownership.
- **Python runtime:** worker lifecycle, request admission, token and policy orchestration, bounded execution, HTTP/MCP/CLI adapters.
- **System database:** users, service principals, token verifiers, database registry, grants, quotas, schema versions, audit records, and lifecycle intents. It uses the same native storage engine and is not exposed through normal data operations.
- **Transports:** adapters only. They do not open database directories, mutate files directly, or implement independent authorization rules.

Every data request supplies an explicit opaque database ID. Physical paths are derived from server-controlled IDs; user-provided names and logical document paths never become OS paths.

## 3. Native storage model

Each database has a versioned identity and manifest, WAL segments, SSTables, and a blob area. The storage format uses explicit byte encoding, fixed endianness, bounded lengths, checksums, and format versions; native C structs are never written directly to disk.

The target engine is an LSM tree:

1. A bounded mutable memtable receives committed records.
2. Immutable memtables are flushed into checksummed SSTables.
3. A manifest records the authoritative set of tables and the recovery sequence.
4. Background compaction merges tables while respecting active snapshots and tombstones.
5. A shared, bounded block cache serves all logical databases in the process.

Authoritative records include stable document IDs, document metadata and body, a unique slug-to-ID mapping, virtual paths/folders, explicit graph edges, revisions, analyzer versions, and idempotency outcomes. Full document bodies are not loaded merely to open a database or list its metadata.

Derived indexes include incoming/outgoing graph adjacency and full-text postings/statistics. They are updated in the same transaction as the document and can be rebuilt into a new generation with a controlled atomic switch.

## 4. Commit and recovery contract

A mutation is acknowledged only after its complete transaction record is durable and visible to new snapshots:

1. Validate permissions, preconditions, unique keys, limits, and expected revisions.
2. Prepare the complete bounded batch and reserve the resources needed to publish it.
3. For media, durably publish an immutable blob before committing a reference to it.
4. Append a length-bounded, checksummed WAL batch with a commit record and sequence number.
5. Synchronize the WAL according to the documented platform durability mode.
6. Publish the batch atomically to the in-memory write view and expose its committed sequence.
7. Return success only after visibility and the requested durability level are satisfied.

The WAL is segmented and framed. Recovery replays only complete, valid committed batches after the checkpoint sequence. An incomplete tail may be discarded; corruption in a complete committed record or a required SSTable fails closed and is reported rather than silently ignored.

Checkpoint publication writes and verifies temporary SSTables, synchronizes files and directories, publishes a new manifest, then switches the current manifest pointer. WAL segments are reclaimed only when a durable checkpoint covers them. Compaction publishes new tables before old tables are deleted; old readers and backup snapshots pin required files.

A failed synchronization can leave the caller with an unknown commit outcome. The engine must stop further writes pending recovery and support an idempotency key/status lookup; it must not report a guaranteed rollback without evidence.

## 5. Transactions, snapshots, and indexes

- One serialized writer per database; writes to distinct databases may run concurrently.
- Bounded batches are atomic within one database. Cross-database transactions are not supported.
- Readers use MVCC snapshots. A document, its graph view, and its search statistics are read from the same committed sequence.
- Revisions and conditional writes prevent lost updates. Snapshot age, batch bytes, operation count, and execution time are bounded.
- Deletion writes tombstones for a document and its indexes. Tombstones are removed only after compaction proves older copies cannot reappear and no active snapshot needs them.
- Search uses a persistent inverted index, a versioned Unicode analyzer, snapshot-consistent statistics, and bounded top-k selection. Context budgets are distinct from response-serialization overhead.
- Media uploads are streamed to temporary immutable blobs, checksummed, synchronized, and published before the reference transaction. Crashes may leave collectible orphan blobs, never acknowledged dangling references.

## 6. Users, tokens, root, and audit

- Root can manage users, service accounts, databases, grants, quotas, and all database content. Root data access is audited.
- Per-database roles are owner, editor, and reader. Service accounts are distinct identities and receive only explicit grants.
- Effective access is the intersection of principal grants, token scopes, and token-allowed database IDs. Authorization is checked again when queued work executes.
- Tokens contain a high-entropy secret, are shown only once, and are stored as verifiers, not plaintext. Tokens have expiry, revocation, and rotation. Use established cryptographic libraries; do not implement cryptography.
- Root bootstrap is an explicit offline CLI operation under exclusive instance ownership. No default credential, anonymous production mode, or automatic root recreation after catalog corruption is permitted.
- Revocation has a defined barrier: after successful revocation no new operation using the old token may commit or continue server-side response delivery. Bytes already sent cannot be recalled.
- Audit records contain actor, action, database, request/operation IDs, and outcome, but never tokens or document contents. The audit design must state the limits of cross-database atomicity and trust in the OS owner.

## 7. Single-worker runtime and resource limits

One process holds an exclusive instance lock for its lifetime. A second process, embedded client, or MCP process cannot independently open the same data root for writes. MCP is a client of the owning worker, not another storage owner.

The runtime keeps lightweight descriptors for all databases and lazily opens native handles. Leases pin a handle while a request, snapshot, or stream is active; idle eviction is allowed only after safe close/checkpoint conditions. Do not create one permanent thread or load one full index per database.

Global and per-database limits cover memory, cache, handles/file descriptors, queues, batch and request size, search work, snapshots, WAL backlog, document/blob storage, and concurrent maintenance. Admission is bounded and fair across databases and principals. Exhausted budgets cause backpressure rather than unbounded allocation.

The HTTP server explicitly runs one worker. Native work uses a bounded executor. Releasing the Python GIL is allowed only after native synchronization, reentrancy, and handle lifetime have been verified; it is not a substitute for those guarantees.

Startup acquires ownership, recovers and validates the system database, then reports readiness. User databases can recover lazily and fail independently. Shutdown stops admission, drains native operations, flushes/closes handles, and releases ownership last.

## 8. External API and compatibility

HTTP v2 uses bearer authentication, explicit database-scoped routes, stable error codes, bounded uploads, cursor pagination, conditional revisions, and idempotency keys. CLI provides offline bootstrap and administrative operations. MCP and the Python client call the same application services and policy layer.

The existing v1 API may remain as a compatibility adapter bound to one explicitly configured database and a restricted credential. A legacy shared API key is never silently promoted to root; empty-key anonymous access is not enabled in production. Swagger/ReDoc can be disabled while preserving a machine-readable OpenAPI artifact.

The v4 storage format is a major-version boundary. Existing v3 directories are not silently converted or dual-written. Migration freezes writers, inventories raw files, reports collisions and unsafe paths, imports into a new database, verifies content/counts/links/search/reopen behavior, then switches the registry. The original data remains read-only until rollback is no longer required.

## 9. Delivery phases and acceptance gates

### Phase A — contracts and legacy safety

Document format/API/ABI, threat model, ownership rules, supported platforms, error semantics, and migration policy. Add regression tests for reopen, media, JSON boundaries, path containment, and MCP startup. Fix critical v3 defects before using v3 as an import source.

**Gate:** reproducible baseline, regression suite, and native sanitizer runs. This does not establish production durability.

### Phase B — WAL and recovery

Implement platform I/O/locking, explicit codecs, WAL framing, commit outcomes, and recovery. The initial native slice versions WAL appends to independently checksummed v2 headers, validates header integrity before trusting payload length, retains reading of complete v1 prefixes, and refuses ambiguous v1/partial-header repair. `storage.c` stages sorted state before WAL append, publishes it only after durable append, reopens from a validated single-table checkpoint and replays the retained WAL suffix; unknown WAL append outcomes freeze writes. This is experimental and is not part of the public v3 ABI. Phase B still needs WAL/SST/manifest deterministic fault injection, ENOSPC/short-I/O coverage, idempotency/status tracking, an independently specified crash model and platform crash qualification.

**Gate:** no partial visibility; acknowledged commits survive the documented crash model; corruption is detected; uncertain outcomes are queryable.

### Phase C — LSM, MVCC, and resources

Implement memtables, SSTables, manifests, checkpointing, compaction, transactions, snapshots, iterators, and shared resource budgets. An initial `storage.c` vertical slice currently checkpoints the complete sorted key state into one SSTable and replays retained WAL records on reopen; it is a correctness prototype, not an LSM/MVCC implementation.

**Gate:** reference-model tests, stable snapshots through compaction/deletion, no tombstone resurrection, and bounded memory/handles.

### Phase D — documents, graph, blobs, and search

Implement native document/virtual-path APIs, graph indexes, media blob lifecycle, persistent search, and Markdown import/export. Search and graph updates commit atomically with document changes.

**Gate:** correctness across reopen/recovery and consistent snapshots; search fixtures define ranking behavior.

### Phase E — identity and managed multi-database runtime

Implement system records, root bootstrap, tokens, grants, audit, runtime leases, scheduling, quotas, and safe Cython execution. Identity/catalog work may proceed in parallel with search only after the native record/transaction contracts are stable.

**Gate:** authorization matrix, revocation barrier, root audit, per-database isolation, single-owner enforcement, and cross-transport parity.

### Phase F — APIs and production qualification

Add HTTP v2, CLI, MCP/client adapters, backup/restore, operational metrics/readiness, non-root packaging, and migration tooling. Measure scale and recovery before publishing capacity or durability claims.

**Gate:** verified restore, crash/soak tests, clean package consumers, and reproducible benchmarks on recorded hardware and filesystems.

## 10. Verification matrix

- Reopen after each document, media, graph, batch, and deletion operation.
- Crash/fault injection before and after WAL append, commit, sync, publication, SSTable/manifest switch, blob GC, and backup pin changes. Distinguish process kill from simulated power-loss/reordered non-durable writes.
- ASan/UBSan/LSan, TSan, fuzzing for WAL/SST/codec/analyzer inputs, and randomized reference-model tests.
- Test token scopes and grants, expired/disabled/revoked credentials, queued work after revocation, root auditing, last-root recovery, traversal and symlink attacks, and same slug in many databases.
- Benchmark 1M/5M documents and 100/500 databases with recorded hardware, filesystem, dataset generator, body sizes, link density, and query distributions. Measure p50/p95/p99, throughput, RSS, queues, fairness, cold open, WAL recovery, compaction debt, and read/write/space amplification. These sizes are test cases, not performance promises.
- Validate backup restore in an isolated staging root, including checksums, document counts, graph/search fixtures, schema compatibility, and credential invalidation.

## 11. Explicit non-goals for the first production release

SQL, distributed replication/HA, cross-database transactions or queries, live Markdown edits, vector search, password/SSO flows, remote MCP authentication, web UI, custom cryptography, and unverified Windows/NFS durability guarantees.
