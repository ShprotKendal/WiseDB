#include "batch.h"
#include "codec.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fault_injection.h"

#define BATCH_HEADER_SIZE 12u
#define BATCH_MUTATION_HEADER_SIZE 12u
#define BATCH_MAGIC "WDBBAT1\0"
#define BATCH_DELETE_FLAG 1u

struct batch_replay_context {
    size_t max_key_size;
    size_t max_value_size;
    wisedb_batch_replay_fn callback;
    void *callback_context;
    char *error_out;
    size_t error_capacity;
};

static void set_error(char *out, size_t capacity, const char *message) {
    if (out && capacity > 0) (void)snprintf(out, capacity, "%s", message ? message : "");
}

static int key_compare(const uint8_t *left, size_t left_size, const uint8_t *right, size_t right_size) {
    size_t common_size = left_size < right_size ? left_size : right_size;
    int compare = common_size ? memcmp(left, right, common_size) : 0;
    if (compare != 0) return compare;
    return (left_size > right_size) - (left_size < right_size);
}

static void free_mutations(wisedb_batch_mutation *mutations, size_t count) {
    (void)count;
    free(mutations);
}

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
) {
    if (error_out && error_capacity > 0) error_out[0] = '\0';
    if (sequence_out) *sequence_out = 0;
    if (outcome_out) *outcome_out = WISEDB_WAL_OUTCOME_NOT_APPENDED;
    if (!wal || !mutations || mutation_count == 0 || mutation_count > UINT32_MAX
        || mutation_count > WISEDB_BATCH_MAX_MUTATIONS
        || max_key_size == 0 || max_value_size == 0) {
        set_error(error_out, error_capacity, "invalid batch arguments");
        return -1;
    }

    size_t payload_size = BATCH_HEADER_SIZE;
    for (size_t i = 0; i < mutation_count; ++i) {
        const wisedb_batch_mutation *mutation = &mutations[i];
        if (!mutation->key || mutation->key_size == 0 || mutation->key_size > max_key_size
            || mutation->key_size > UINT32_MAX || mutation->value_size > max_value_size
            || mutation->value_size > UINT32_MAX || (!mutation->delete_key && !mutation->value && mutation->value_size)
            || (mutation->delete_key && (mutation->value || mutation->value_size))) {
            set_error(error_out, error_capacity, "invalid batch mutation or size limit exceeded");
            return -1;
        }
        if (i > 0 && key_compare(mutations[i - 1].key, mutations[i - 1].key_size,
                                 mutation->key, mutation->key_size) >= 0) {
            set_error(error_out, error_capacity, "batch keys must be strictly increasing");
            return -1;
        }
        size_t value_size = mutation->delete_key ? 0 : mutation->value_size;
        if (payload_size > SIZE_MAX - BATCH_MUTATION_HEADER_SIZE
            || mutation->key_size > SIZE_MAX - payload_size - BATCH_MUTATION_HEADER_SIZE
            || value_size > SIZE_MAX - payload_size - BATCH_MUTATION_HEADER_SIZE - mutation->key_size) {
            set_error(error_out, error_capacity, "encoded batch size overflow");
            return -1;
        }
        payload_size += BATCH_MUTATION_HEADER_SIZE + mutation->key_size + value_size;
        if (payload_size > wisedb_wal_max_record_size(wal)) {
            set_error(error_out, error_capacity, "encoded batch exceeds WAL record limit");
            return -1;
        }
    }
    if (payload_size > wisedb_wal_max_record_size(wal)) {
        set_error(error_out, error_capacity, "encoded batch exceeds WAL record limit");
        return -1;
    }

    uint8_t *payload = (uint8_t *)malloc(payload_size);
    if (!payload) {
        set_error(error_out, error_capacity, "out of memory encoding batch");
        return -1;
    }
    memcpy(payload, BATCH_MAGIC, 8);
    wisedb_store_u32_le(payload + 8, (uint32_t)mutation_count);
    size_t offset = BATCH_HEADER_SIZE;
    for (size_t i = 0; i < mutation_count; ++i) {
        const wisedb_batch_mutation *mutation = &mutations[i];
        uint32_t value_size = mutation->delete_key ? 0 : (uint32_t)mutation->value_size;
        wisedb_store_u32_le(payload + offset, (uint32_t)mutation->key_size);
        wisedb_store_u32_le(payload + offset + 4, value_size);
        wisedb_store_u32_le(payload + offset + 8, mutation->delete_key ? BATCH_DELETE_FLAG : 0u);
        offset += BATCH_MUTATION_HEADER_SIZE;
        memcpy(payload + offset, mutation->key, mutation->key_size);
        offset += mutation->key_size;
        if (value_size > 0) {
            memcpy(payload + offset, mutation->value, value_size);
            offset += value_size;
        }
    }

    int result = wisedb_wal_append(wal, payload, payload_size, sequence_out);
    if (outcome_out) *outcome_out = wisedb_wal_last_append_outcome(wal);
    free(payload);
    if (result != 0) set_error(error_out, error_capacity, wisedb_wal_last_error(wal));
    return result;
}

int wisedb_wal_append_batch(
    wisedb_wal *wal,
    const wisedb_batch_mutation *mutations,
    size_t mutation_count,
    size_t max_key_size,
    size_t max_value_size,
    uint64_t *sequence_out,
    char *error_out,
    size_t error_capacity
) {
    return wisedb_wal_append_batch_ex(wal, mutations, mutation_count, max_key_size,
                                      max_value_size, sequence_out, NULL, error_out,
                                      error_capacity);
}

static int replay_one_batch(uint64_t sequence, const uint8_t *payload, size_t payload_size, void *opaque) {
    struct batch_replay_context *context = (struct batch_replay_context *)opaque;
    if (payload_size < BATCH_HEADER_SIZE || memcmp(payload, BATCH_MAGIC, 8) != 0) {
        set_error(context->error_out, context->error_capacity, "invalid WAL batch magic or header");
        return -1;
    }
    uint32_t mutation_count = wisedb_load_u32_le(payload + 8);
    if (mutation_count == 0 || mutation_count > WISEDB_BATCH_MAX_MUTATIONS
        || mutation_count > (payload_size - BATCH_HEADER_SIZE) / BATCH_MUTATION_HEADER_SIZE) {
        set_error(context->error_out, context->error_capacity, "invalid WAL batch mutation count");
        return -1;
    }

    wisedb_batch_mutation *mutations = (wisedb_batch_mutation *)calloc(mutation_count, sizeof(*mutations));
    if (!mutations) {
        set_error(context->error_out, context->error_capacity, "out of memory decoding WAL batch");
        return -1;
    }
    size_t offset = BATCH_HEADER_SIZE;
    for (uint32_t i = 0; i < mutation_count; ++i) {
        if (payload_size - offset < BATCH_MUTATION_HEADER_SIZE) {
            set_error(context->error_out, context->error_capacity, "truncated WAL batch mutation header");
            free_mutations(mutations, i);
            return -1;
        }
        uint32_t key_size = wisedb_load_u32_le(payload + offset);
        uint32_t value_size = wisedb_load_u32_le(payload + offset + 4);
        uint32_t flags = wisedb_load_u32_le(payload + offset + 8);
        offset += BATCH_MUTATION_HEADER_SIZE;
        int is_delete = (flags & BATCH_DELETE_FLAG) != 0;
        if (key_size == 0 || key_size > context->max_key_size || value_size > context->max_value_size
            || (flags & ~BATCH_DELETE_FLAG) != 0 || (is_delete && value_size != 0)
            || (uint64_t)key_size + (uint64_t)value_size > (uint64_t)(payload_size - offset)) {
            set_error(context->error_out, context->error_capacity, "invalid WAL batch mutation lengths or flags");
            free_mutations(mutations, i);
            return -1;
        }
        if (i > 0 && key_compare(mutations[i - 1].key, mutations[i - 1].key_size,
                                  payload + offset, key_size) >= 0) {
            int duplicate = key_compare(mutations[i - 1].key, mutations[i - 1].key_size,
                                        payload + offset, key_size) == 0;
            set_error(context->error_out, context->error_capacity,
                duplicate ? "WAL batch contains duplicate keys" : "WAL batch keys are not sorted");
            free_mutations(mutations, i);
            return -1;
        }
        mutations[i].key = payload + offset;
        mutations[i].key_size = key_size;
        offset += key_size;
        mutations[i].value = value_size ? payload + offset : NULL;
        mutations[i].value_size = value_size;
        mutations[i].delete_key = is_delete;
        offset += value_size;
    }
    if (offset != payload_size) {
        set_error(context->error_out, context->error_capacity, "unexpected bytes after WAL batch");
        free_mutations(mutations, mutation_count);
        return -1;
    }

    int callback_result = context->callback(sequence, mutations, mutation_count, context->callback_context);
    free_mutations(mutations, mutation_count);
    if (callback_result != 0) {
        set_error(context->error_out, context->error_capacity, "WAL batch replay callback failed");
        return -1;
    }
    return 0;
}

int wisedb_wal_replay_batches(
    wisedb_wal *wal,
    size_t max_key_size,
    size_t max_value_size,
    wisedb_batch_replay_fn callback,
    void *context,
    char *error_out,
    size_t error_capacity
) {
    if (error_out && error_capacity > 0) error_out[0] = '\0';
    if (!wal || max_key_size == 0 || max_value_size == 0 || !callback) {
        set_error(error_out, error_capacity, "invalid batch replay arguments");
        return -1;
    }
    struct batch_replay_context replay_context = {
        .max_key_size = max_key_size,
        .max_value_size = max_value_size,
        .callback = callback,
        .callback_context = context,
        .error_out = error_out,
        .error_capacity = error_capacity
    };
    if (wisedb_wal_replay(wal, replay_one_batch, &replay_context) != 0) {
        if (!error_out || error_capacity == 0 || !error_out[0]) {
            set_error(error_out, error_capacity, wisedb_wal_last_error(wal));
        }
        return -1;
    }
    return 0;
}
