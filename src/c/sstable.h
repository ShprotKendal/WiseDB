#ifndef WISEDB_SSTABLE_H
#define WISEDB_SSTABLE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct wisedb_sstable_writer wisedb_sstable_writer;
typedef struct wisedb_sstable_reader wisedb_sstable_reader;

typedef struct {
    const uint8_t *key;
    size_t key_size;
    const uint8_t *value;
    size_t value_size;
    uint64_t sequence;
    int tombstone;
} wisedb_sstable_entry;

#define WISEDB_SSTABLE_FORMAT_VERSION 1u
#define WISEDB_SSTABLE_DEFAULT_MAX_KEY_SIZE (1024u * 1024u)
#define WISEDB_SSTABLE_DEFAULT_MAX_VALUE_SIZE (16u * 1024u * 1024u)

/*
 * Internal immutable sorted-table prototype. Keys must be strictly increasing
 * under bytewise lexicographic ordering (prefix sorts before its extension).
 * Writer publishes with temp file, fsync, no-replace hard link, unlink, and
 * parent-directory fsync. A successful finish frees the writer.
 * The caller owns the directory namespace and serializes access to target paths.
 */
wisedb_sstable_writer *wisedb_sstable_writer_open(
    const char *path,
    uint64_t checkpoint_sequence,
    size_t max_key_size,
    size_t max_value_size,
    char *error_out,
    size_t error_capacity
);
int wisedb_sstable_writer_add(wisedb_sstable_writer *writer, const wisedb_sstable_entry *entry);
int wisedb_sstable_writer_finish(wisedb_sstable_writer *writer);
void wisedb_sstable_writer_abort(wisedb_sstable_writer *writer);
const char *wisedb_sstable_writer_last_error(const wisedb_sstable_writer *writer);

/* Open validates the complete table and then positions at its first entry. */
wisedb_sstable_reader *wisedb_sstable_reader_open(
    const char *path,
    size_t max_key_size,
    size_t max_value_size,
    char *error_out,
    size_t error_capacity
);
/* 1 = entry, 0 = end, -1 = error. Returned key/value are heap-owned. */
int wisedb_sstable_reader_next(
    wisedb_sstable_reader *reader,
    wisedb_sstable_entry *entry_out
);
uint64_t wisedb_sstable_reader_checkpoint(const wisedb_sstable_reader *reader);
uint64_t wisedb_sstable_reader_entry_count(const wisedb_sstable_reader *reader);
void wisedb_sstable_entry_free(wisedb_sstable_entry *entry);
void wisedb_sstable_reader_close(wisedb_sstable_reader *reader);
const char *wisedb_sstable_last_error(const wisedb_sstable_reader *reader);

#ifdef __cplusplus
}
#endif

#endif /* WISEDB_SSTABLE_H */
