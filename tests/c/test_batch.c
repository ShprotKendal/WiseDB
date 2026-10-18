#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L

#include "batch.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "CHECK failed at %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        goto cleanup; \
    } \
} while (0)

typedef struct {
    size_t batches;
    size_t mutations;
    uint64_t sequence;
    int saw_delete;
    int saw_large_value;
} replay_state;

static int apply_batch(
    uint64_t sequence,
    const wisedb_batch_mutation *mutations,
    size_t mutation_count,
    void *opaque
) {
    replay_state *state = (replay_state *)opaque;
    if (sequence != state->batches + 1) return -1;
    state->batches++;
    state->mutations += mutation_count;
    state->sequence = sequence;
    for (size_t i = 0; i < mutation_count; ++i) {
        if (mutations[i].delete_key) state->saw_delete = 1;
        if (mutations[i].value_size > 70000) state->saw_large_value = 1;
    }
    return 0;
}

static int reject_batch(uint64_t sequence, const wisedb_batch_mutation *mutations, size_t count, void *context) {
    (void)sequence;
    (void)mutations;
    (void)count;
    (void)context;
    return -1;
}

int main(void) {
    char path[] = "/tmp/wisedb-batch-test-XXXXXX";
    char error[256] = {0};
    wisedb_wal *wal = NULL;
    int result = 1;
    int fd = mkstemp(path);
    CHECK(fd >= 0);
    CHECK(close(fd) == 0);
    wal = wisedb_wal_open(path, 256 * 1024);
    CHECK(wal != NULL);

    const uint8_t key_a[] = {'a'};
    const uint8_t key_b[] = {'b'};
    const uint8_t key_c[] = {'c'};
    const uint8_t value_a[] = {'1'};
    const uint8_t value_b[] = {'2'};
    wisedb_batch_mutation mutations[] = {
        {key_a, sizeof(key_a), value_a, sizeof(value_a), 0},
        {key_b, sizeof(key_b), NULL, 0, 1}
    };
    uint64_t sequence = 0;
    CHECK(wisedb_wal_append_batch(wal, mutations, 2, 64, 4096, &sequence, error, sizeof(error)) == 0);
    CHECK(sequence == 1);

    wisedb_batch_mutation duplicate[] = {
        {key_a, sizeof(key_a), value_a, sizeof(value_a), 0},
        {key_a, sizeof(key_a), value_b, sizeof(value_b), 0}
    };
    CHECK(wisedb_wal_append_batch(wal, duplicate, 2, 64, 4096, &sequence, error, sizeof(error)) != 0);
    CHECK(sequence == 0 && strstr(error, "increasing") != NULL);

    wisedb_batch_mutation unsorted[] = {
        {key_b, sizeof(key_b), value_b, sizeof(value_b), 0},
        {key_a, sizeof(key_a), value_a, sizeof(value_a), 0}
    };
    CHECK(wisedb_wal_append_batch(wal, unsorted, 2, 64, 4096, &sequence, error, sizeof(error)) != 0);
    CHECK(sequence == 0 && strstr(error, "increasing") != NULL);

    wisedb_batch_mutation second_batch[] = {
        {key_b, sizeof(key_b), value_b, sizeof(value_b), 0},
        {key_c, sizeof(key_c), value_a, sizeof(value_a), 0}
    };
    CHECK(wisedb_wal_append_batch(wal, second_batch, 2, 64, 4096, &sequence, error, sizeof(error)) == 0);
    CHECK(sequence == 2);

    const size_t large_size = 90000;
    uint8_t *large_value = (uint8_t *)malloc(large_size);
    CHECK(large_value != NULL);
    memset(large_value, 0x4c, large_size);
    wisedb_batch_mutation large_mutation = {key_c, sizeof(key_c), large_value, large_size, 0};
    CHECK(wisedb_wal_append_batch(wal, &large_mutation, 1, 64, 128 * 1024, &sequence, error, sizeof(error)) == 0);
    CHECK(sequence == 3);
    free(large_value);
    large_value = NULL;

    replay_state state = {0};
    CHECK(wisedb_wal_replay_batches(wal, 64, 128 * 1024, apply_batch, &state, error, sizeof(error)) == 0);
    CHECK(state.batches == 3 && state.mutations == 5 && state.sequence == 3);
    CHECK(state.saw_delete && state.saw_large_value);
    CHECK(wisedb_wal_replay_batches(wal, 64, 128 * 1024, reject_batch, NULL, error, sizeof(error)) != 0);
    CHECK(strstr(error, "callback") != NULL);

    const uint8_t key_d[] = {'d'};
    const uint8_t key_e[] = {'e'};
    wisedb_batch_mutation sorted_many[] = {
        {key_a, sizeof(key_a), value_a, sizeof(value_a), 0},
        {key_b, sizeof(key_b), value_b, sizeof(value_b), 0},
        {key_c, sizeof(key_c), value_a, sizeof(value_a), 0},
        {key_d, sizeof(key_d), value_b, sizeof(value_b), 0},
        {key_e, sizeof(key_e), value_a, sizeof(value_a), 0}
    };
    CHECK(wisedb_wal_append_batch(wal, sorted_many, 5, 64, 4096, &sequence, error, sizeof(error)) == 0);
    CHECK(sequence == 4);
    memset(&state, 0, sizeof(state));
    CHECK(wisedb_wal_replay_batches(wal, 64, 128 * 1024, apply_batch, &state, error, sizeof(error)) == 0);
    CHECK(state.batches == 4 && state.mutations == 10);

    const uint8_t oversized_bytes[11] = {0};
    wisedb_batch_mutation oversized_value = {key_a, sizeof(key_a), oversized_bytes, sizeof(oversized_bytes), 0};
    CHECK(wisedb_wal_append_batch(wal, &oversized_value, 1, 64, 10, &sequence, error, sizeof(error)) != 0);
    CHECK(sequence == 0 && strstr(error, "limit") != NULL);

    wisedb_wal_close(wal);
    wal = NULL;
    wal = wisedb_wal_open(path, 256 * 1024);
    CHECK(wal != NULL);
    memset(&state, 0, sizeof(state));
    CHECK(wisedb_wal_replay_batches(wal, 64, 128 * 1024, apply_batch, &state, error, sizeof(error)) == 0);
    CHECK(state.batches == 4 && state.mutations == 10);

    CHECK(unlink(path) == 0);
    result = 0;

cleanup:
    if (wal) wisedb_wal_close(wal);
    unlink(path);
    return result;
}
