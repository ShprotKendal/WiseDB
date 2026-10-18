#ifndef WISEDB_BATCH_H
#define WISEDB_BATCH_H

#include "wal.h"

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define WISEDB_BATCH_FORMAT_VERSION 1u
#define WISEDB_BATCH_MAX_MUTATIONS (64u * 1024u)

typedef struct {
    const uint8_t *key;
    size_t key_size;
    const uint8_t *value;
    size_t value_size;
    int delete_key;
} wisedb_batch_mutation;

typedef int (*wisedb_batch_replay_fn)(
    uint64_t sequence,
    const wisedb_batch_mutation *mutations,
    size_t mutation_count,
    void *context
);

/* Encodes the complete batch into exactly one durable WAL record. */
int wisedb_wal_append_batch(
    wisedb_wal *wal,
    const wisedb_batch_mutation *mutations,
    size_t mutation_count,
    size_t max_key_size,
    size_t max_value_size,
    uint64_t *sequence_out,
    char *error_out,
    size_t error_capacity
);

/*
 * Validates an entire batch before passing mutations to the callback. Mutation
 * arrays, keys, and values are borrowed and valid only for the callback.
 */
int wisedb_wal_replay_batches(
    wisedb_wal *wal,
    size_t max_key_size,
    size_t max_value_size,
    wisedb_batch_replay_fn callback,
    void *context,
    char *error_out,
    size_t error_capacity
);

/* Variant preserving the low-level append outcome for coordinator recovery. */
int wisedb_wal_append_batch_ex(
    wisedb_wal *wal,
    const wisedb_batch_mutation *mutations,
    size_t mutation_count,
    size_t max_key_size,
    size_t max_value_size,
    uint64_t *sequence_out,
    wisedb_wal_outcome *outcome_out,
    char *error_out,
    size_t error_capacity
);

#ifdef __cplusplus
}
#endif

#endif /* WISEDB_BATCH_H */
