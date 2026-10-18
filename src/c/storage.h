#ifndef WISEDB_STORAGE_H
#define WISEDB_STORAGE_H

#include "batch.h"
#include "manifest.h"
#include "sstable.h"
#include "wal.h"

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct wisedb_storage wisedb_storage;

typedef enum {
    WISEDB_STORAGE_OUTCOME_NOT_COMMITTED = 0,
    WISEDB_STORAGE_OUTCOME_COMMITTED = 1,
    WISEDB_STORAGE_OUTCOME_UNKNOWN = 2
} wisedb_storage_outcome;

typedef struct {
    const uint8_t *key;
    size_t key_size;
    const uint8_t *value;
    size_t value_size;
    uint64_t sequence;
    int tombstone;
} wisedb_storage_entry;

typedef struct {
    uint64_t sequence;
    uint64_t generation;
    size_t live_key_count;
    int writes_frozen;
} wisedb_storage_status;

/*
 * Experimental POSIX single-owner coordinator. This is not wired into the
 * public v3 Wiki API. Directory must already exist, be owned by the effective
 * user, have no group/other permissions, and be exclusively trusted. Ownership
 * is an advisory WAL-file lock; all participants must cooperate.
 */
wisedb_storage *wisedb_storage_open(
    const char *directory,
    size_t max_record_size,
    size_t max_key_size,
    size_t max_value_size,
    char *error_out,
    size_t error_capacity
);
void wisedb_storage_close(wisedb_storage *storage);

/*
 * One sorted unique-key batch is atomically visible after WAL fsync succeeds.
 * outcome_out distinguishes definitely-not-committed, committed, and unknown.
 * Unknown freezes writes; close and reopen to resolve by recovery before retry.
 */
int wisedb_storage_commit(
    wisedb_storage *storage,
    const wisedb_batch_mutation *mutations,
    size_t mutation_count,
    uint64_t *sequence_out,
    wisedb_storage_outcome *outcome_out,
    char *error_out,
    size_t error_capacity
);

/* Borrowed view; valid until next storage call or close. Tombstones appear absent. */
int wisedb_storage_get(
    const wisedb_storage *storage,
    const uint8_t *key,
    size_t key_size,
    wisedb_storage_entry *entry_out
);

/*
 * Full-state single-table checkpoint. WAL is deliberately retained. An error
 * after manifest rename may have published the checkpoint; it freezes writes.
 * Close and reopen to resolve the current generation before further writes.
 */
int wisedb_storage_checkpoint(
    wisedb_storage *storage,
    char *error_out,
    size_t error_capacity
);
int wisedb_storage_get_status(const wisedb_storage *storage, wisedb_storage_status *status_out);

#ifdef __cplusplus
}
#endif

#endif /* WISEDB_STORAGE_H */
