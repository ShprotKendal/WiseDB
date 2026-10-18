#define _DARWIN_C_SOURCE 1
#define _FILE_OFFSET_BITS 64
#define _POSIX_C_SOURCE 200809L

#include "storage.h"
#include "codec.h"

#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "fault_injection.h"

struct storage_item {
    uint8_t *key;
    size_t key_size;
    uint8_t *value;
    size_t value_size;
    uint64_t sequence;
    int tombstone;
};

struct wisedb_storage {
    char *directory;
    char *wal_path;
    wisedb_wal *wal;
    struct storage_item *items;
    size_t item_count;
    uint64_t sequence;
    uint64_t generation;
    uint64_t table_id;
    size_t max_record_size;
    size_t max_key_size;
    size_t max_value_size;
    int has_manifest;
    int writes_frozen;
    dev_t directory_device;
    ino_t directory_inode;
    char error[256];
};

struct recovery_context {
    struct wisedb_storage *storage;
    uint64_t checkpoint_sequence;
    uint64_t last_wal_sequence;
};

static void set_error(char *out, size_t capacity, const char *message) {
    if (out && capacity > 0) (void)snprintf(out, capacity, "%s", message ? message : "");
}

static void set_storage_error(wisedb_storage *storage, const char *message) {
    if (!storage) return;
    (void)snprintf(storage->error, sizeof(storage->error), "%s", message ? message : "");
}

static int compare_bytes(const uint8_t *left, size_t left_size, const uint8_t *right, size_t right_size) {
    size_t common = left_size < right_size ? left_size : right_size;
    int order = common ? memcmp(left, right, common) : 0;
    if (order != 0) return order;
    return (left_size > right_size) - (left_size < right_size);
}

static int validate_mutations(const wisedb_storage *storage,
                              const wisedb_batch_mutation *mutations, size_t count,
                              char *error_out, size_t error_capacity) {
    if (!storage || !mutations || count == 0 || count > WISEDB_BATCH_MAX_MUTATIONS) {
        set_error(error_out, error_capacity, "invalid or empty mutation batch");
        return -1;
    }
    for (size_t i = 0; i < count; i++) {
        const wisedb_batch_mutation *mutation = &mutations[i];
        if (!mutation->key || mutation->key_size == 0 || mutation->key_size > storage->max_key_size
            || mutation->value_size > storage->max_value_size
            || (!mutation->delete_key && mutation->value_size > 0 && !mutation->value)
            || (mutation->delete_key && (mutation->value || mutation->value_size))) {
            set_error(error_out, error_capacity, "mutation key/value is invalid or exceeds configured limits");
            return -1;
        }
        if (i > 0 && compare_bytes(mutations[i - 1].key, mutations[i - 1].key_size,
                                   mutation->key, mutation->key_size) >= 0) {
            set_error(error_out, error_capacity, "mutation keys must be strictly sorted and unique");
            return -1;
        }
    }
    return 0;
}

static int directory_is_current(const wisedb_storage *storage) {
    struct stat current;
    if (!storage || lstat(storage->directory, &current) != 0 || !S_ISDIR(current.st_mode)
        || S_ISLNK(current.st_mode)) return 0;
    return current.st_dev == storage->directory_device && current.st_ino == storage->directory_inode;
}

static void free_items(struct storage_item *items, size_t count) {
    if (!items) return;
    for (size_t i = 0; i < count; i++) {
        free(items[i].key);
        free(items[i].value);
    }
    free(items);
}

static int clone_items(const struct storage_item *source, size_t count,
                       struct storage_item **items_out, size_t *capacity_out) {
    *items_out = NULL;
    *capacity_out = 0;
    if (count == 0) return 0;
    if (count > SIZE_MAX / sizeof(**items_out)) return -1;
    struct storage_item *copy = (struct storage_item *)calloc(count, sizeof(*copy));
    if (!copy) return -1;
    for (size_t i = 0; i < count; i++) {
        copy[i].key = (uint8_t *)malloc(source[i].key_size);
        if (!copy[i].key) { free_items(copy, i); return -1; }
        memcpy(copy[i].key, source[i].key, source[i].key_size);
        copy[i].key_size = source[i].key_size;
        copy[i].sequence = source[i].sequence;
        copy[i].tombstone = source[i].tombstone;
        if (source[i].value_size > 0) {
            copy[i].value = (uint8_t *)malloc(source[i].value_size);
            if (!copy[i].value) { free_items(copy, i + 1); return -1; }
            memcpy(copy[i].value, source[i].value, source[i].value_size);
        }
        copy[i].value_size = source[i].value_size;
    }
    *items_out = copy;
    *capacity_out = count;
    return 0;
}

static size_t lower_bound_items(const struct storage_item *items, size_t count,
                                const uint8_t *key, size_t key_size, int *found) {
    size_t low = 0, high = count;
    while (low < high) {
        size_t middle = low + (high - low) / 2;
        if (compare_bytes(items[middle].key, items[middle].key_size, key, key_size) < 0) low = middle + 1;
        else high = middle;
    }
    *found = low < count && compare_bytes(items[low].key, items[low].key_size, key, key_size) == 0;
    return low;
}

static int apply_mutations(struct storage_item **items_io, size_t *count_io, size_t *capacity_io,
                           uint64_t sequence, const wisedb_batch_mutation *mutations,
                           size_t mutation_count) {
    struct storage_item *items = *items_io;
    size_t count = *count_io;
    size_t capacity = *capacity_io;
    for (size_t m = 0; m < mutation_count; m++) {
        const wisedb_batch_mutation *mutation = &mutations[m];
        int found = 0;
        size_t index = lower_bound_items(items, count, mutation->key, mutation->key_size, &found);
        uint8_t *key_copy = NULL;
        uint8_t *value_copy = NULL;
        if (!found) {
            key_copy = (uint8_t *)malloc(mutation->key_size);
            if (!key_copy) goto fail;
            memcpy(key_copy, mutation->key, mutation->key_size);
        }
        if (!mutation->delete_key && mutation->value_size > 0) {
            value_copy = (uint8_t *)malloc(mutation->value_size);
            if (!value_copy) { free(key_copy); goto fail; }
            memcpy(value_copy, mutation->value, mutation->value_size);
        }
        if (!found && count == capacity) {
            size_t next = capacity ? capacity * 2 : 16;
            if (next < capacity || next > SIZE_MAX / sizeof(*items)) {
                free(key_copy); free(value_copy); goto fail;
            }
            struct storage_item *grown = (struct storage_item *)realloc(items, next * sizeof(*items));
            if (!grown) { free(key_copy); free(value_copy); goto fail; }
            items = grown;
            capacity = next;
        }
        if (!found) {
            memmove(items + index + 1, items + index, (count - index) * sizeof(*items));
            memset(&items[index], 0, sizeof(items[index]));
            items[index].key = key_copy;
            items[index].key_size = mutation->key_size;
            count++;
        } else {
            free(items[index].value);
        }
        items[index].value = value_copy;
        items[index].value_size = mutation->delete_key ? 0 : mutation->value_size;
        items[index].sequence = sequence;
        items[index].tombstone = mutation->delete_key != 0;
    }
    *items_io = items;
    *count_io = count;
    *capacity_io = capacity;
    return 0;

fail:
    *items_io = items;
    *count_io = count;
    *capacity_io = capacity;
    return -1;
}

static int apply_batch_to_storage(uint64_t sequence, const wisedb_batch_mutation *mutations,
                                  size_t mutation_count, void *opaque) {
    struct recovery_context *context = (struct recovery_context *)opaque;
    wisedb_storage *storage = context->storage;
    context->last_wal_sequence = sequence;
    if (sequence <= context->checkpoint_sequence) return 0;
    if (sequence != storage->sequence + 1) {
        set_storage_error(storage, "WAL sequence is not contiguous after checkpoint");
        return -1;
    }
    struct storage_item *candidate = NULL;
    size_t candidate_capacity = 0;
    if (clone_items(storage->items, storage->item_count, &candidate, &candidate_capacity) != 0) {
        set_storage_error(storage, "out of memory cloning state for WAL replay");
        return -1;
    }
    size_t candidate_count = storage->item_count;
    if (apply_mutations(&candidate, &candidate_count, &candidate_capacity,
                        sequence, mutations, mutation_count) != 0) {
        free_items(candidate, candidate_count);
        set_storage_error(storage, "out of memory applying WAL batch");
        return -1;
    }
    free_items(storage->items, storage->item_count);
    storage->items = candidate;
    storage->item_count = candidate_count;
    storage->sequence = sequence;
    return 0;
}

static int load_checkpoint(wisedb_storage *storage, uint64_t *checkpoint_sequence,
                           char *error_out, size_t error_capacity) {
    char manifest_path[4096];
    int length = snprintf(manifest_path, sizeof(manifest_path), "%s/MANIFEST", storage->directory);
    if (length < 0 || (size_t)length >= sizeof(manifest_path)) {
        set_error(error_out, error_capacity, "manifest path too long");
        return -1;
    }
    struct stat manifest_stat;
    if (lstat(manifest_path, &manifest_stat) != 0) {
        if (errno == ENOENT) {
            *checkpoint_sequence = 0;
            storage->has_manifest = 0;
            return 0;
        }
        set_error(error_out, error_capacity, "cannot inspect manifest");
        return -1;
    }
    if (!S_ISREG(manifest_stat.st_mode) || S_ISLNK(manifest_stat.st_mode)) {
        set_error(error_out, error_capacity, "manifest is not a regular file");
        return -1;
    }
    wisedb_manifest manifest;
    if (wisedb_manifest_read(storage->directory, &manifest, error_out, error_capacity) != 0) return -1;
    char table_path[4096];
    length = snprintf(table_path, sizeof(table_path), "%s/%s", storage->directory, manifest.table_name);
    if (length < 0 || (size_t)length >= sizeof(table_path)) {
        set_error(error_out, error_capacity, "checkpoint table path too long");
        return -1;
    }
    wisedb_sstable_reader *reader = wisedb_sstable_reader_open(
        table_path, storage->max_key_size, storage->max_value_size, error_out, error_capacity);
    if (!reader) return -1;
    struct storage_item *items = NULL;
    size_t count = 0, capacity = 0;
    wisedb_sstable_entry entry;
    memset(&entry, 0, sizeof(entry));
    int next;
    while ((next = wisedb_sstable_reader_next(reader, &entry)) > 0) {
        wisedb_batch_mutation mutation = {entry.key, entry.key_size, entry.value,
                                          entry.value_size, entry.tombstone};
        if (apply_mutations(&items, &count, &capacity, entry.sequence, &mutation, 1) != 0) {
            wisedb_sstable_entry_free(&entry);
            free_items(items, count);
            wisedb_sstable_reader_close(reader);
            set_error(error_out, error_capacity, "out of memory loading checkpoint table");
            return -1;
        }
        wisedb_sstable_entry_free(&entry);
        memset(&entry, 0, sizeof(entry));
    }
    if (next < 0) {
        char reader_error[256];
        (void)snprintf(reader_error, sizeof(reader_error), "%s", wisedb_sstable_last_error(reader));
        free_items(items, count);
        wisedb_sstable_reader_close(reader);
        set_error(error_out, error_capacity, reader_error);
        return -1;
    }
    wisedb_sstable_reader_close(reader);
    free_items(storage->items, storage->item_count);
    storage->items = items;
    storage->item_count = count;
    storage->sequence = manifest.checkpoint_sequence;
    storage->generation = manifest.generation;
    storage->table_id = manifest.table_id;
    storage->has_manifest = 1;
    *checkpoint_sequence = manifest.checkpoint_sequence;
    return 0;
}

wisedb_storage *wisedb_storage_open(const char *directory, size_t max_record_size,
                                   size_t max_key_size, size_t max_value_size,
                                   char *error_out, size_t error_capacity) {
    if (error_out && error_capacity > 0) error_out[0] = '\0';
    if (!directory || !directory[0] || max_record_size == 0 || max_key_size == 0 || max_value_size == 0) {
        set_error(error_out, error_capacity, "invalid storage open arguments");
        return NULL;
    }
    struct stat directory_stat;
    if (lstat(directory, &directory_stat) != 0 || !S_ISDIR(directory_stat.st_mode)
        || S_ISLNK(directory_stat.st_mode) || directory_stat.st_uid != geteuid()
        || (directory_stat.st_mode & 0077) != 0) {
        set_error(error_out, error_capacity, "storage directory must be owned by the current user and private");
        return NULL;
    }
    wisedb_storage *storage = (wisedb_storage *)calloc(1, sizeof(*storage));
    if (!storage) { set_error(error_out, error_capacity, "out of memory allocating storage"); return NULL; }
    storage->directory = realpath(directory, NULL);
    storage->max_record_size = max_record_size;
    storage->max_key_size = max_key_size;
    storage->max_value_size = max_value_size;
    if (!storage->directory) { set_error(error_out, error_capacity, "out of memory storing directory"); goto fail; }
    struct stat canonical_stat;
    if (lstat(storage->directory, &canonical_stat) != 0 || !S_ISDIR(canonical_stat.st_mode)
        || S_ISLNK(canonical_stat.st_mode) || canonical_stat.st_dev != directory_stat.st_dev
        || canonical_stat.st_ino != directory_stat.st_ino) {
        set_error(error_out, error_capacity, "storage directory changed during canonicalization");
        goto fail;
    }
    storage->directory_device = canonical_stat.st_dev;
    storage->directory_inode = canonical_stat.st_ino;
    size_t directory_size = strlen(storage->directory);
    static const char wal_suffix[] = "/WAL";
    if (directory_size > SIZE_MAX - sizeof(wal_suffix)) {
        set_error(error_out, error_capacity, "WAL path too long"); goto fail;
    }
    storage->wal_path = (char *)malloc(directory_size + sizeof(wal_suffix));
    if (!storage->wal_path) { set_error(error_out, error_capacity, "out of memory creating WAL path"); goto fail; }
    (void)snprintf(storage->wal_path, directory_size + sizeof(wal_suffix), "%s%s", storage->directory,
                   storage->directory[directory_size - 1] == '/' ? "WAL" : wal_suffix);
    storage->wal = wisedb_wal_open_ex(storage->wal_path, max_record_size, error_out, error_capacity);
    if (!storage->wal) goto fail;
    if (!directory_is_current(storage)) {
        set_error(error_out, error_capacity, "storage directory pathname changed while acquiring ownership");
        goto fail;
    }
    uint64_t checkpoint_sequence = 0;
    if (load_checkpoint(storage, &checkpoint_sequence, error_out, error_capacity) != 0) goto fail;
    struct recovery_context context = {storage, checkpoint_sequence, 0};
    if (wisedb_wal_replay_batches(storage->wal, max_key_size, max_value_size,
                                  apply_batch_to_storage, &context,
                                  error_out, error_capacity) != 0) {
        if (!error_out || error_capacity == 0 || !error_out[0])
            set_error(error_out, error_capacity, storage->error[0] ? storage->error : "WAL replay failed");
        goto fail;
    }
    if (context.last_wal_sequence < checkpoint_sequence) {
        set_error(error_out, error_capacity, "WAL is behind the checkpoint sequence");
        goto fail;
    }
    storage->error[0] = '\0';
    return storage;
fail:
    wisedb_storage_close(storage);
    return NULL;
}

void wisedb_storage_close(wisedb_storage *storage) {
    if (!storage) return;
    if (storage->wal) wisedb_wal_close(storage->wal);
    free_items(storage->items, storage->item_count);
    free(storage->directory);
    free(storage->wal_path);
    free(storage);
}

int wisedb_storage_commit(wisedb_storage *storage, const wisedb_batch_mutation *mutations,
                          size_t mutation_count, uint64_t *sequence_out,
                          wisedb_storage_outcome *outcome_out,
                          char *error_out, size_t error_capacity) {
    if (error_out && error_capacity > 0) error_out[0] = '\0';
    if (sequence_out) *sequence_out = 0;
    if (outcome_out) *outcome_out = WISEDB_STORAGE_OUTCOME_NOT_COMMITTED;
    if (!storage || !mutations || mutation_count == 0) {
        set_error(error_out, error_capacity, "invalid storage commit arguments");
        return -1;
    }
    if (!directory_is_current(storage)) {
        storage->writes_frozen = 1;
        set_error(error_out, error_capacity, "storage directory pathname changed; reopen for recovery");
        if (outcome_out) *outcome_out = WISEDB_STORAGE_OUTCOME_UNKNOWN;
        return -1;
    }
    if (storage->writes_frozen) {
        set_error(error_out, error_capacity, "storage writes are frozen; close and reopen for recovery");
        if (outcome_out) *outcome_out = WISEDB_STORAGE_OUTCOME_UNKNOWN;
        return -1;
    }
    if (validate_mutations(storage, mutations, mutation_count, error_out, error_capacity) != 0) return -1;
    if (storage->sequence == UINT64_MAX) {
        set_error(error_out, error_capacity, "storage sequence exhausted");
        return -1;
    }
    struct storage_item *candidate = NULL;
    size_t candidate_capacity = 0;
    if (clone_items(storage->items, storage->item_count, &candidate, &candidate_capacity) != 0) {
        set_error(error_out, error_capacity, "out of memory preparing atomic write view");
        return -1;
    }
    size_t candidate_count = storage->item_count;
    uint64_t sequence = storage->sequence + 1;
    if (apply_mutations(&candidate, &candidate_count, &candidate_capacity,
                        sequence, mutations, mutation_count) != 0) {
        free_items(candidate, candidate_count);
        set_error(error_out, error_capacity, "out of memory preparing atomic write view");
        return -1;
    }
    wisedb_wal_outcome wal_outcome = WISEDB_WAL_OUTCOME_NOT_APPENDED;
    uint64_t wal_sequence = 0;
    int append_result = wisedb_wal_append_batch_ex(storage->wal, mutations, mutation_count,
        storage->max_key_size, storage->max_value_size, &wal_sequence, &wal_outcome,
        error_out, error_capacity);
    if (append_result != 0) {
        free_items(candidate, candidate_count);
        if (wal_outcome == WISEDB_WAL_OUTCOME_UNKNOWN) {
            storage->writes_frozen = 1;
            if (outcome_out) *outcome_out = WISEDB_STORAGE_OUTCOME_UNKNOWN;
        }
        return -1;
    }
    if (wal_sequence != sequence || wal_outcome != WISEDB_WAL_OUTCOME_DURABLE) {
        free_items(candidate, candidate_count);
        storage->writes_frozen = 1;
        if (outcome_out) *outcome_out = WISEDB_STORAGE_OUTCOME_UNKNOWN;
        set_error(error_out, error_capacity, "WAL sequence/outcome mismatch after append; writes frozen");
        return -1;
    }
    free_items(storage->items, storage->item_count);
    storage->items = candidate;
    storage->item_count = candidate_count;
    storage->sequence = sequence;
    if (sequence_out) *sequence_out = sequence;
    if (outcome_out) *outcome_out = WISEDB_STORAGE_OUTCOME_COMMITTED;
    return 0;
}

int wisedb_storage_get(const wisedb_storage *storage, const uint8_t *key,
                       size_t key_size, wisedb_storage_entry *entry_out) {
    if (!storage || !key || key_size == 0 || !entry_out) return -1;
    memset(entry_out, 0, sizeof(*entry_out));
    int found = 0;
    size_t index = lower_bound_items(storage->items, storage->item_count, key, key_size, &found);
    if (!found || storage->items[index].tombstone) return 0;
    entry_out->key = storage->items[index].key;
    entry_out->key_size = storage->items[index].key_size;
    entry_out->value = storage->items[index].value;
    entry_out->value_size = storage->items[index].value_size;
    entry_out->sequence = storage->items[index].sequence;
    entry_out->tombstone = 0;
    return 1;
}

int wisedb_storage_checkpoint(wisedb_storage *storage, char *error_out, size_t error_capacity) {
    if (error_out && error_capacity > 0) error_out[0] = '\0';
    if (!storage) { set_error(error_out, error_capacity, "invalid storage checkpoint argument"); return -1; }
    if (!directory_is_current(storage)) {
        storage->writes_frozen = 1;
        set_error(error_out, error_capacity, "storage directory pathname changed; writes frozen");
        return -1;
    }
    if (storage->writes_frozen) { set_error(error_out, error_capacity, "storage writes are frozen; close and reopen for recovery"); return -1; }
    if (storage->has_manifest && storage->generation == UINT64_MAX) {
        set_error(error_out, error_capacity, "manifest generation exhausted"); return -1;
    }
    uint64_t generation = storage->has_manifest ? storage->generation + 1 : 1;
    uint64_t table_id = generation;
    char table_name[128];
    char table_path[4096];
    int length = -1;
    for (unsigned attempt = 0; attempt < 1024; attempt++, table_id++) {
        if (table_id == 0 || wisedb_manifest_table_name(table_id, table_name, sizeof(table_name)) != 0) break;
        length = snprintf(table_path, sizeof(table_path), "%s/%s", storage->directory, table_name);
        if (length < 0 || (size_t)length >= sizeof(table_path)) break;
        if (access(table_path, F_OK) != 0 && errno == ENOENT) break;
        table_id++;
    }
    if (length < 0 || (size_t)length >= sizeof(table_path) || access(table_path, F_OK) == 0) {
        set_error(error_out, error_capacity, "could not allocate an unused checkpoint table id");
        return -1;
    }
    wisedb_sstable_writer *writer = wisedb_sstable_writer_open(table_path, storage->sequence,
        storage->max_key_size, storage->max_value_size, error_out, error_capacity);
    if (!writer) return -1;
    for (size_t i = 0; i < storage->item_count; i++) {
        struct storage_item *item = &storage->items[i];
        wisedb_sstable_entry entry = {item->key, item->key_size, item->value,
                                      item->value_size, item->sequence, item->tombstone};
        if (wisedb_sstable_writer_add(writer, &entry) != 0) {
            set_error(error_out, error_capacity, wisedb_sstable_writer_last_error(writer));
            wisedb_sstable_writer_abort(writer);
            return -1;
        }
    }
    if (wisedb_sstable_writer_finish(writer) != 0) {
        set_error(error_out, error_capacity, wisedb_sstable_writer_last_error(writer));
        wisedb_sstable_writer_abort(writer);
        storage->writes_frozen = 1;
        return -1;
    }
    wisedb_manifest manifest;
    memset(&manifest, 0, sizeof(manifest));
    manifest.generation = generation;
    manifest.checkpoint_sequence = storage->sequence;
    manifest.table_id = table_id;
    manifest.entry_count = storage->item_count;
    (void)snprintf(manifest.table_name, sizeof(manifest.table_name), "%s", table_name);
    if (wisedb_manifest_publish(storage->directory, &manifest, error_out, error_capacity) != 0) {
        storage->writes_frozen = 1;
        return -1;
    }
    storage->generation = generation;
    storage->table_id = table_id;
    storage->has_manifest = 1;
    return 0;
}

int wisedb_storage_get_status(const wisedb_storage *storage, wisedb_storage_status *status_out) {
    if (!storage || !status_out) return -1;
    status_out->sequence = storage->sequence;
    status_out->generation = storage->generation;
    status_out->live_key_count = 0;
    for (size_t i = 0; i < storage->item_count; i++)
        if (!storage->items[i].tombstone) status_out->live_key_count++;
    status_out->writes_frozen = storage->writes_frozen;
    return 0;
}
