#ifndef WISEDB_MANIFEST_H
#define WISEDB_MANIFEST_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint64_t generation;
    uint64_t checkpoint_sequence;
    uint64_t table_id;
    uint64_t entry_count;
    char table_name[128];
} wisedb_manifest;

/* Derives the canonical immutable table filename from a nonzero table ID. */
int wisedb_manifest_table_name(uint64_t table_id, char *out, size_t capacity);

/*
 * Reads and validates MANIFEST; referenced SST is verified before success.
 * manifest_out is modified only on success. Callers must serialize access to
 * the directory; this prototype does not provide a directory ownership lock.
 */
int wisedb_manifest_read(
    const char *directory,
    wisedb_manifest *manifest_out,
    char *error_out,
    size_t error_capacity
);

/*
 * Publishes the next generation without replacing its immutable table. The
 * directory must be non-empty and exclusively serialized by the caller. A
 * failure after rename may have an unknown publication outcome; it is not a
 * rollback guarantee. Do not reclaim WAL based only on a failed return.
 */
int wisedb_manifest_publish(
    const char *directory,
    const wisedb_manifest *manifest,
    char *error_out,
    size_t error_capacity
);

#ifdef __cplusplus
}
#endif

#endif /* WISEDB_MANIFEST_H */
