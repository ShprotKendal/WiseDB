#ifndef WISEDB_WAL_H
#define WISEDB_WAL_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct wisedb_wal wisedb_wal;

typedef enum {
    WISEDB_WAL_OUTCOME_NOT_APPENDED = 0,
    WISEDB_WAL_OUTCOME_DURABLE = 1,
    WISEDB_WAL_OUTCOME_UNKNOWN = 2
} wisedb_wal_outcome;


/*
 * WAL is an append-only sequence of bounded, CRC32C-protected records. New
 * appends use version 2, whose independently checksummed fixed header makes
 * the payload length safe to use when recognizing a torn final payload. The
 * reader accepts complete version-1 records for compatibility. One process
 * owns a WAL file at a time; calls on a handle must be externally serialized.
 * A successful append has been fsync'd before return.
 *
 * Opening validates the complete log and may truncate only a final version-2
 * payload whose fixed header checksum is valid. Partial headers and incomplete
 * version-1 frames are ambiguous and fail closed. Complete malformed or
 * checksum-invalid frames cause open to fail.
 *
 * This is a low-level prototype, not yet the database transaction protocol.
 * The caller is responsible for placing it under a trusted directory and for
 * including all transaction effects in one record payload.
 */

#define WISEDB_WAL_FORMAT_VERSION 2u
#define WISEDB_WAL_LEGACY_FORMAT_VERSION 1u
#define WISEDB_WAL_DEFAULT_MAX_RECORD_SIZE (16u * 1024u * 1024u)

typedef int (*wisedb_wal_replay_fn)(
    uint64_t sequence,
    const uint8_t *payload,
    size_t payload_size,
    void *context
);

/* Returns NULL on error. max_record_size must be positive. */
wisedb_wal *wisedb_wal_open(const char *path, size_t max_record_size);

/* Variant that reports an open/validation error into a caller-owned buffer. */
wisedb_wal *wisedb_wal_open_ex(
    const char *path,
    size_t max_record_size,
    char *error_out,
    size_t error_capacity
);
void wisedb_wal_close(wisedb_wal *wal);

/* Appends one record and synchronizes it. Sequence numbers start at 1. */
int wisedb_wal_append(
    wisedb_wal *wal,
    const uint8_t *payload,
    size_t payload_size,
    uint64_t *sequence_out
);

/* Replays records in sequence order. Payload memory is valid only in callback. */
int wisedb_wal_replay(
    wisedb_wal *wal,
    wisedb_wal_replay_fn callback,
    void *context
);

/* Borrowed error string, valid until the next operation or close. */
const char *wisedb_wal_last_error(const wisedb_wal *wal);
size_t wisedb_wal_max_record_size(const wisedb_wal *wal);
wisedb_wal_outcome wisedb_wal_last_append_outcome(const wisedb_wal *wal);

#ifdef __cplusplus
}
#endif

#endif /* WISEDB_WAL_H */
