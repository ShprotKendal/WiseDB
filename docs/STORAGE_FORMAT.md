# Native WAL prototype: format and current guarantees

> This document specifies only the standalone WAL prototype in `src/c/wal.c` and `src/c/wal.h`.
> It is not the v4 database format and does not provide database transactions, manifests, checkpoints, replication, or full ACID guarantees.

> Status (2026-10-03): the standalone v4-direction prototypes remain separate from the public v3 library. WAL v2 adds a header checksum and retains complete v1 reads; `storage.c` now provides an experimental one-directory coordinator for batch WAL, single-table checkpoint, reopen, and suffix replay. This initial coordinator deliberately retains the entire WAL and is not yet a production recovery protocol. The legacy v3 fault injector is separate; WAL/SST/manifest I/O fault injection, idempotency/status records, platform crash qualification, and power-loss tests remain outstanding.

The coordinator test target additionally injects allocation failures while preparing commits, a WAL `fsync` error with outcome reconciliation after reopen, and a manifest directory-sync error after rename. These are deterministic process-level syscall outcomes on the test filesystem; they do not simulate power loss, delayed writes, device caches, or ENOSPC/short writes.

## Scope

The prototype appends one opaque payload per record to one POSIX regular file. The caller must encode a complete transaction into a single payload, serialize operations on a handle, and place the file under a trusted, already-created directory. A successful append calls `fsync()` on the WAL file before returning. Opening uses a non-blocking exclusive `flock`; only one live owner is supported.

A failed write or `fsync` poisons the handle. The caller must close it and reopen/recover before another append. A failed `fsync` has an uncertain outcome: bytes may or may not be durable. This API does not claim rollback.

## Version 2 frame layout

New appends use unsigned little-endian fields and a 32-byte fixed header:

| Offset | Width | Field |
|---:|---:|---|
| 0 | 8 | Magic bytes `WDBWAL1\0` |
| 8 | 4 | Format version, `2` |
| 12 | 4 | Payload length in bytes |
| 16 | 8 | Record sequence, starting at `1` and increasing by one |
| 24 | 4 | CRC32C checksum |
| 28 | 4 | Header CRC32C over bytes 0–27 |
| 32 | length | Opaque payload bytes |

The record CRC32C uses the Castagnoli polynomial (`0x82f63b78` in reflected form), initial/final XOR of `0xffffffff`, and covers header bytes 8–23 followed by the payload. The header CRC covers bytes 0–27, including the record CRC. Open validates the bounded header before trusting its payload length; payload checksums are validated in fixed-size chunks. The standard check value is `CRC32C("123456789") = 0xe3069283`.

The reader also accepts complete version-1 records with their 28-byte header. A version-1 log may be appended to; newly appended records use v2, so a file can contain a v1 prefix followed by v2 records. A truncated v1 frame is ambiguous because v1 has no independently protected length; it fails closed and is never repaired automatically.

The configured maximum payload length is checked before allocation; the default constant is 16 MiB. The sequence is local to this single file. The format has no database UUID, transaction boundaries spanning records, segment identity, commit marker separate from the frame, or checkpoint sequence.

## Open and replay

Open validates frames from byte zero:

- Valid complete frames must have the expected magic, supported version, a bounded payload, contiguous nonzero sequence, and valid checksums. V2 header CRC is validated before using payload length. Because v1 has no header checksum, its record CRC is validated only after the complete payload is present; an incomplete legacy payload fails closed and its length is never used to truncate the file.
- An incomplete v2 payload is truncated only when its complete header CRC is valid; the file is then synchronized. Any incomplete header, non-magic suffix, or incomplete v1 frame fails closed without modifying the file. A complete zero-length payload frame is valid.
- An invalid complete frame, unsupported version, sequence gap, oversized declared length, or checksum mismatch fails open. The implementation does not skip corruption.
- `wisedb_wal_open_ex()` reports an error in the supplied buffer. `wisedb_wal_open()` is the convenience form without open diagnostics.
- Replay calls the supplied callback in sequence order. Payload memory is borrowed and valid only during the callback. A nonzero callback result stops replay and returns failure.

## Platform and security limits

This implementation is POSIX-only and currently uses `flock`, `pread`, `ftruncate`, `fsync`, `O_NOFOLLOW` where available, and directory `fsync` on open. It is intended for a trusted local filesystem. It does not claim equivalent durability on NFS, cloud-sync folders, or untested filesystems. The containing directory and all ancestors must be controlled by the instance owner; `O_NOFOLLOW` only protects the final path component.

Opening sets the WAL file mode to owner read/write (`0600`). The prototype does not safely create missing parent directories, validate the ownership/mode of those directories, or defend against a hostile directory being replaced during path resolution. Those checks belong in the future platform/storage layer.

The handle is not internally thread-safe. Callers must serialize append and replay, keep the handle alive during callbacks, and must not mutate the file through another descriptor while it is open. The process lock is advisory and does not stop non-cooperating programs.

## Test coverage

`tests/c/test_wal.c` covers record append/replay, empty records, sequence progression, exclusive live ownership, private file mode, reopen, v2 torn-payload repair, fail-closed partial/header-corruption cases, v1-to-v2 mixed replay, size-limit rejection, payload checksum corruption, symlink refusal, callback failure, the CRC32C standard check value, and a multi-chunk 200 KiB payload. Tests do not simulate power loss, storage-controller behavior, all possible torn writes, process crashes at every instruction, or recovery of a full database. CTest registration is POSIX-conditional.

The shared endian and CRC32C primitives live in `src/c/codec.c` and `src/c/codec.h`. `src/c/sstable.c` and `src/c/sstable.h` provide a separate immutable sorted-table prototype: 28-byte checksummed file header, sorted records with per-record CRC32C, and a checksummed footer. Records contain key length, value length, sequence, flags/tombstone, checksum, key bytes, and value bytes. Keys use strict bytewise lexicographic order (prefix first); sequences are nonzero and cannot exceed the table checkpoint sequence. Writer publication is temp-file + file `fsync` + no-replace hard link + temp unlink + parent-directory `fsync`; reader validates the entire table before iteration and rechecks entries while reading.

`src/c/manifest.c` and `src/c/manifest.h` add a fixed 48-byte checksummed manifest with a monotonic generation, checkpoint sequence, table ID, and entry count. The table filename is derived from the table ID rather than accepted as a path from manifest bytes. Publication validates the referenced SSTable, writes and syncs a private temporary manifest, renames it over `MANIFEST`, then syncs the directory. Read validates both manifest and referenced SSTable. This is a single-table checkpoint manifest prototype; it does not yet coordinate WAL replay, retain prior generations, or recover from a crash whose directory-sync outcome is uncertain.

`src/c/batch.c` and `src/c/batch.h` encode an entire sorted unique-key mutation batch into exactly one WAL record. The batch decoder validates all lengths, flags, uniqueness, and trailing bytes before invoking its callback, so malformed batches are never partially applied by that callback. A caller must still apply the complete callback batch atomically to its state; the prototype does not itself provide memtable visibility or transaction rollback.

The WAL, batch codec, SSTable, and manifest remain intentionally separate from the v3 public library. CTest has independent POSIX targets. Before promotion, add deterministic I/O fault injection, WAL+manifest recovery coordination, memtable atomic publication, multi-table merge/compaction, bounded-memory iterators, fuzzing, and crash tests. Production durability claims require platform-specific crash testing.
