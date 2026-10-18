#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L
#define WISEDB_FAULT_IMPLEMENTATION

#include "storage.h"
#include "fault_injection.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "recovery check failed at %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        result = 1; \
        goto cleanup; \
    } \
} while (0)

static int remove_tree(const char *path) {
    DIR *dir = opendir(path);
    if (!dir) return unlink(path);
    struct dirent *entry;
    int result = 0;
    while ((entry = readdir(dir)) != NULL) {
        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) continue;
        char child[4096];
        int length = snprintf(child, sizeof(child), "%s/%s", path, entry->d_name);
        if (length < 0 || (size_t)length >= sizeof(child)) { result = -1; break; }
        struct stat st;
        if (lstat(child, &st) != 0) { result = -1; break; }
        if (S_ISDIR(st.st_mode)) {
            if (remove_tree(child) != 0) { result = -1; break; }
        } else if (unlink(child) != 0) {
            result = -1;
            break;
        }
    }
    closedir(dir);
    if (result == 0) result = rmdir(path);
    return result;
}

static int expect_value(const wisedb_storage *storage, const char *key,
                        const char *value, uint64_t sequence) {
    wisedb_storage_entry entry;
    int found = wisedb_storage_get(storage, (const uint8_t *)key, strlen(key), &entry);
    if (found != 1 || entry.sequence != sequence || entry.value_size != strlen(value)
        || memcmp(entry.value, value, entry.value_size) != 0) return -1;
    return 0;
}

static int expect_absent(const wisedb_storage *storage, const char *key) {
    wisedb_storage_entry entry;
    return wisedb_storage_get(storage, (const uint8_t *)key, strlen(key), &entry) == 0 ? 0 : -1;
}

static int commit_one(wisedb_storage *storage, const char *key, const char *value,
                      wisedb_storage_outcome *outcome) {
    wisedb_batch_mutation mutation = {
        (const uint8_t *)key, strlen(key), (const uint8_t *)value, strlen(value), 0
    };
    uint64_t sequence = 0;
    char error[256];
    return wisedb_storage_commit(storage, &mutation, 1, &sequence, outcome, error, sizeof(error));
}

int main(void) {
    int result = 0;
    char directory_template[] = "/tmp/wisedb-recovery-test-XXXXXX";
    char *directory = mkdtemp(directory_template);
    wisedb_storage *storage = NULL;
    char error[256] = {0};
    CHECK(directory != NULL);
    CHECK(chmod(directory, 0700) == 0);

    storage = wisedb_storage_open(directory, 1024 * 1024, 4096, 256 * 1024, error, sizeof(error));
    CHECK(storage != NULL);
    wisedb_storage *second_owner = wisedb_storage_open(directory, 1024 * 1024, 4096,
        256 * 1024, error, sizeof(error));
    if (second_owner) wisedb_storage_close(second_owner);
    CHECK(second_owner == NULL);

    const uint8_t key_a[] = "alpha";
    const uint8_t key_b[] = "beta";
    const uint8_t key_c[] = "gamma";
    const uint8_t value_a[] = "one";
    const uint8_t value_b[] = "two";
    const uint8_t value_c[] = "three";
    wisedb_batch_mutation first_batch[] = {
        {key_a, sizeof(key_a) - 1, value_a, sizeof(value_a) - 1, 0},
        {key_b, sizeof(key_b) - 1, value_b, sizeof(value_b) - 1, 0}
    };
    wisedb_storage_status status;
    uint64_t sequence = 0;
    wisedb_storage_outcome outcome = WISEDB_STORAGE_OUTCOME_UNKNOWN;
    CHECK(wisedb_storage_commit(storage, first_batch, 2, &sequence, &outcome, error, sizeof(error)) == 0);
    CHECK(sequence == 1 && outcome == WISEDB_STORAGE_OUTCOME_COMMITTED);
    CHECK(expect_value(storage, "alpha", "one", 1) == 0);
    CHECK(expect_value(storage, "beta", "two", 1) == 0);

    wisedb_batch_mutation invalid_batch[] = {
        {key_a, sizeof(key_a) - 1, value_a, sizeof(value_a) - 1, 0},
        {key_a, sizeof(key_a) - 1, value_b, sizeof(value_b) - 1, 0}
    };
    CHECK(wisedb_storage_commit(storage, invalid_batch, 2, &sequence, &outcome, error, sizeof(error)) != 0);
    CHECK(outcome == WISEDB_STORAGE_OUTCOME_NOT_COMMITTED);
    CHECK(expect_value(storage, "alpha", "one", 1) == 0);

    wisedb_storage_close(storage);
    storage = NULL;
    storage = wisedb_storage_open(directory, 1024 * 1024, 1, 256 * 1024, error, sizeof(error));
    CHECK(storage == NULL);
    storage = wisedb_storage_open(directory, 1024 * 1024, 4096, 256 * 1024, error, sizeof(error));
    CHECK(storage != NULL);
    CHECK(expect_value(storage, "alpha", "one", 1) == 0);
    CHECK(expect_value(storage, "beta", "two", 1) == 0);

    CHECK(wisedb_storage_checkpoint(storage, error, sizeof(error)) == 0);
    CHECK(wisedb_storage_get_status(storage, &status) == 0);
    CHECK(status.sequence == 1 && status.generation == 1 && status.live_key_count == 2);

    wisedb_batch_mutation second_batch[] = {
        {key_a, sizeof(key_a) - 1, value_c, sizeof(value_c) - 1, 0},
        {key_b, sizeof(key_b) - 1, NULL, 0, 1},
        {key_c, sizeof(key_c) - 1, value_b, sizeof(value_b) - 1, 0}
    };
    CHECK(wisedb_storage_commit(storage, second_batch, 3, &sequence, &outcome, error, sizeof(error)) == 0);
    CHECK(sequence == 2 && outcome == WISEDB_STORAGE_OUTCOME_COMMITTED);
    CHECK(expect_value(storage, "alpha", "three", 2) == 0);
    CHECK(expect_absent(storage, "beta") == 0);
    CHECK(expect_value(storage, "gamma", "two", 2) == 0);
    wisedb_storage_close(storage);
    storage = NULL;

    storage = wisedb_storage_open(directory, 1024 * 1024, 4096, 256 * 1024, error, sizeof(error));
    CHECK(storage != NULL);
    CHECK(expect_value(storage, "alpha", "three", 2) == 0);
    CHECK(expect_absent(storage, "beta") == 0);
    CHECK(expect_value(storage, "gamma", "two", 2) == 0);

    CHECK(wisedb_storage_checkpoint(storage, error, sizeof(error)) == 0);
    wisedb_storage_close(storage);
    storage = NULL;
    storage = wisedb_storage_open(directory, 1024 * 1024, 4096, 256 * 1024, error, sizeof(error));
    CHECK(storage != NULL);
    CHECK(expect_value(storage, "alpha", "three", 2) == 0);
    CHECK(expect_absent(storage, "beta") == 0);
    CHECK(expect_value(storage, "gamma", "two", 2) == 0);
    CHECK(wisedb_storage_get_status(storage, &status) == 0);
    CHECK(status.sequence == 2 && status.generation == 2 && status.live_key_count == 2);

    wisedb_fault_io_fail_nth(WISEDB_FAULT_FSYNC, 1);
    outcome = WISEDB_STORAGE_OUTCOME_NOT_COMMITTED;
    CHECK(commit_one(storage, "uncertain", "durable-maybe", &outcome) != 0);
    CHECK(outcome == WISEDB_STORAGE_OUTCOME_UNKNOWN);
    CHECK(wisedb_storage_get_status(storage, &status) == 0 && status.writes_frozen);
    CHECK(expect_absent(storage, "uncertain") == 0);
    outcome = WISEDB_STORAGE_OUTCOME_NOT_COMMITTED;
    CHECK(commit_one(storage, "blocked", "must-not-write", &outcome) != 0);
    CHECK(outcome == WISEDB_STORAGE_OUTCOME_UNKNOWN);
    CHECK(expect_absent(storage, "blocked") == 0);
    wisedb_fault_io_disable();
    wisedb_storage_close(storage);
    storage = NULL;
    storage = wisedb_storage_open(directory, 1024 * 1024, 4096, 256 * 1024, error, sizeof(error));
    CHECK(storage != NULL);
    CHECK(expect_value(storage, "uncertain", "durable-maybe", 3) == 0);
    CHECK(wisedb_storage_get_status(storage, &status) == 0 && status.sequence == 3);

    wisedb_fault_alloc_fail_nth(SIZE_MAX);
    outcome = WISEDB_STORAGE_OUTCOME_NOT_COMMITTED;
    CHECK(commit_one(storage, "alloc-count", "baseline", &outcome) == 0);
    size_t commit_allocation_count = wisedb_fault_alloc_count();
    wisedb_fault_alloc_disable();
    CHECK(commit_allocation_count > 0);
    wisedb_batch_mutation delete_baseline = {
        (const uint8_t *)"alloc-count", 11, NULL, 0, 1
    };
    CHECK(wisedb_storage_commit(storage, &delete_baseline, 1, &sequence, &outcome,
                                error, sizeof(error)) == 0);
    for (size_t nth = 1; nth <= commit_allocation_count; nth++) {
        char key[64];
        int key_length = snprintf(key, sizeof(key), "alloc-%zu", nth);
        CHECK(key_length > 0 && (size_t)key_length < sizeof(key));
        wisedb_fault_alloc_fail_nth(nth);
        int commit_result = commit_one(storage, key, "value", &outcome);
        size_t attempts = wisedb_fault_alloc_count();
        wisedb_fault_alloc_disable();
        CHECK(attempts >= nth);
        if (commit_result == 0) {
            CHECK(outcome == WISEDB_STORAGE_OUTCOME_COMMITTED);
            CHECK(expect_value(storage, key, "value", sequence + 1) == 0);
            wisedb_batch_mutation remove_key = {
                (const uint8_t *)key, strlen(key), NULL, 0, 1
            };
            CHECK(wisedb_storage_commit(storage, &remove_key, 1, &sequence, &outcome,
                                        error, sizeof(error)) == 0);
        } else {
            CHECK(outcome == WISEDB_STORAGE_OUTCOME_NOT_COMMITTED);
            CHECK(expect_absent(storage, key) == 0);
        }
    }

    const wisedb_fault_kind write_failures[] = {
        WISEDB_FAULT_WRITE_ENOSPC,
        WISEDB_FAULT_WRITE_SHORT
    };
    for (size_t i = 0; i < sizeof(write_failures) / sizeof(write_failures[0]); i++) {
        char key[64];
        (void)snprintf(key, sizeof(key), "write-fault-%zu", i);
        wisedb_fault_io_fail_nth(write_failures[i], 1);
        outcome = WISEDB_STORAGE_OUTCOME_NOT_COMMITTED;
        CHECK(commit_one(storage, key, "must-not-appear", &outcome) != 0);
        CHECK(outcome == WISEDB_STORAGE_OUTCOME_UNKNOWN);
        CHECK(wisedb_storage_get_status(storage, &status) == 0 && status.writes_frozen);
        CHECK(expect_absent(storage, key) == 0);
        wisedb_fault_io_disable();
        wisedb_storage_close(storage);
        storage = NULL;
        storage = wisedb_storage_open(directory, 1024 * 1024, 4096, 256 * 1024,
                                      error, sizeof(error));
        CHECK(storage != NULL);
        wisedb_storage_status recovered_status;
        CHECK(wisedb_storage_get_status(storage, &recovered_status) == 0);
        int recovered = expect_value(storage, key, "must-not-appear", recovered_status.sequence);
        CHECK(recovered == 0 || recovered == -1);
        if (recovered == 0) CHECK(recovered_status.sequence >= 1);
    }

    /* Failure of the final directory sync occurs after manifest rename: freeze, then resolve by reopen. */
    wisedb_fault_io_fail_nth(WISEDB_FAULT_FSYNC, 3);
    CHECK(wisedb_storage_checkpoint(storage, error, sizeof(error)) != 0);
    CHECK(wisedb_storage_get_status(storage, &status) == 0 && status.writes_frozen);
    outcome = WISEDB_STORAGE_OUTCOME_NOT_COMMITTED;
    CHECK(commit_one(storage, "blocked-checkpoint", "must-not-write", &outcome) != 0);
    CHECK(outcome == WISEDB_STORAGE_OUTCOME_UNKNOWN);
    wisedb_fault_io_disable();
    wisedb_storage_close(storage);
    storage = NULL;
    storage = wisedb_storage_open(directory, 1024 * 1024, 4096, 256 * 1024, error, sizeof(error));
    CHECK(storage != NULL);
    CHECK(expect_value(storage, "uncertain", "durable-maybe", 3) == 0);
    CHECK(expect_absent(storage, "beta") == 0);

    wisedb_storage_close(storage);
    storage = NULL;
    char manifest_path[4096];
    int manifest_length = snprintf(manifest_path, sizeof(manifest_path), "%s/MANIFEST", directory);
    CHECK(manifest_length > 0 && (size_t)manifest_length < sizeof(manifest_path));
    int manifest_fd = open(manifest_path, O_RDWR);
    CHECK(manifest_fd >= 0);
    uint8_t byte = 0;
    CHECK(pread(manifest_fd, &byte, 1, 0) == 1);
    byte ^= 0x80;
    CHECK(pwrite(manifest_fd, &byte, 1, 0) == 1);
    CHECK(close(manifest_fd) == 0);
    CHECK(wisedb_storage_open(directory, 1024 * 1024, 4096, 256 * 1024,
                              error, sizeof(error)) == NULL);
    CHECK(strstr(error, "manifest") != NULL);

cleanup:
    if (storage) wisedb_storage_close(storage);
    if (directory) (void)remove_tree(directory);
    return result;
}
