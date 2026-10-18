#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L

#include "codec.h"
#include "sstable.h"

#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "CHECK failed at %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        goto cleanup; \
    } \
} while (0)

static int write_byte_at(const char *path, off_t offset, uint8_t value) {
    int fd = open(path, O_RDWR);
    if (fd < 0) return -1;
    ssize_t amount = pwrite(fd, &value, 1, offset);
    int close_result = close(fd);
    return amount == 1 && close_result == 0 ? 0 : -1;
}

static int file_exists(const char *path) {
    return access(path, F_OK) == 0;
}

int main(void) {
    char directory[] = "/tmp/wisedb-sstable-test-XXXXXX";
    char path[512] = {0};
    char empty_path[512] = {0};
    char large_path[512] = {0};
    char aborted_path[512] = {0};
    char error[256] = {0};
    wisedb_sstable_writer *writer = NULL;
    wisedb_sstable_reader *reader = NULL;
    uint8_t *large_value = NULL;
    int result = 1;
    CHECK(mkdtemp(directory) != NULL);
    CHECK(snprintf(path, sizeof(path), "%s/table-1.sst", directory) > 0);
    CHECK(snprintf(empty_path, sizeof(empty_path), "%s/empty.sst", directory) > 0);
    CHECK(snprintf(large_path, sizeof(large_path), "%s/large.sst", directory) > 0);
    CHECK(snprintf(aborted_path, sizeof(aborted_path), "%s/aborted.sst", directory) > 0);

    writer = wisedb_sstable_writer_open(path, 9, 64, 1024, error, sizeof(error));
    CHECK(writer != NULL);
    const uint8_t key_a[] = {'a'};
    const uint8_t value_a[] = {0x00, 0x01, 0x02, 0xff};
    const uint8_t key_ab[] = {'a', 'b'};
    const uint8_t value_ab[] = "value-b";
    const uint8_t key_b[] = {'b'};
    wisedb_sstable_entry entry = {key_a, sizeof(key_a), value_a, sizeof(value_a), 2, 0};
    CHECK(wisedb_sstable_writer_add(writer, &entry) == 0);

    /* Duplicate and out-of-order keys are rejected without poisoning the writer. */
    CHECK(wisedb_sstable_writer_add(writer, &entry) != 0);
    const uint8_t key_before_a[] = {'0'};
    entry.key = key_before_a;
    entry.key_size = sizeof(key_before_a);
    entry.sequence = 3;
    CHECK(wisedb_sstable_writer_add(writer, &entry) != 0);
    entry.key = key_ab;
    entry.key_size = sizeof(key_ab);
    entry.value = value_ab;
    entry.value_size = sizeof(value_ab) - 1;
    entry.sequence = 0;
    CHECK(wisedb_sstable_writer_add(writer, &entry) != 0);
    entry.sequence = 5;
    CHECK(wisedb_sstable_writer_add(writer, &entry) == 0);

    entry.key = key_b;
    entry.key_size = sizeof(key_b);
    entry.value = value_a;
    entry.value_size = sizeof(value_a);
    entry.sequence = 9;
    entry.tombstone = 1;
    CHECK(wisedb_sstable_writer_add(writer, &entry) != 0);
    entry.value = NULL;
    entry.value_size = 0;
    CHECK(wisedb_sstable_writer_add(writer, &entry) == 0);
    CHECK(wisedb_sstable_writer_finish(writer) == 0);
    writer = NULL;
    CHECK(file_exists(path));

    /* A published immutable table may never replace an existing target. */
    writer = wisedb_sstable_writer_open(path, 10, 64, 1024, error, sizeof(error));
    CHECK(writer == NULL);
    CHECK(strstr(error, "already exists") != NULL);

    reader = wisedb_sstable_reader_open(path, 64, 1024, error, sizeof(error));
    CHECK(reader != NULL);
    CHECK(wisedb_sstable_reader_checkpoint(reader) == 9);

    wisedb_sstable_entry actual = {0};
    CHECK(wisedb_sstable_reader_next(reader, &actual) == 1);
    CHECK(actual.key_size == sizeof(key_a) && memcmp(actual.key, key_a, sizeof(key_a)) == 0);
    CHECK(actual.value_size == sizeof(value_a) && memcmp(actual.value, value_a, sizeof(value_a)) == 0);
    CHECK(actual.sequence == 2 && actual.tombstone == 0);
    wisedb_sstable_entry_free(&actual);

    CHECK(wisedb_sstable_reader_next(reader, &actual) == 1);
    CHECK(actual.key_size == sizeof(key_ab) && memcmp(actual.key, key_ab, sizeof(key_ab)) == 0);
    CHECK(actual.value_size == sizeof(value_ab) - 1 && memcmp(actual.value, value_ab, actual.value_size) == 0);
    CHECK(actual.sequence == 5 && actual.tombstone == 0);
    wisedb_sstable_entry_free(&actual);

    CHECK(wisedb_sstable_reader_next(reader, &actual) == 1);
    CHECK(actual.key_size == sizeof(key_b) && memcmp(actual.key, key_b, sizeof(key_b)) == 0);
    CHECK(actual.value == NULL && actual.value_size == 0);
    CHECK(actual.sequence == 9 && actual.tombstone == 1);
    wisedb_sstable_entry_free(&actual);
    CHECK(wisedb_sstable_reader_next(reader, &actual) == 0);
    wisedb_sstable_reader_close(reader);
    reader = NULL;

    writer = wisedb_sstable_writer_open(aborted_path, 1, 64, 1024, error, sizeof(error));
    CHECK(writer != NULL);
    wisedb_sstable_writer_abort(writer);
    writer = NULL;
    CHECK(!file_exists(aborted_path));

    writer = wisedb_sstable_writer_open(empty_path, 0, 64, 1024, error, sizeof(error));
    CHECK(writer != NULL);
    CHECK(wisedb_sstable_writer_finish(writer) == 0);
    writer = NULL;
    reader = wisedb_sstable_reader_open(empty_path, 64, 1024, error, sizeof(error));
    CHECK(reader != NULL);
    CHECK(wisedb_sstable_reader_next(reader, &actual) == 0);
    wisedb_sstable_reader_close(reader);
    reader = NULL;

    const size_t large_size = 192 * 1024;
    large_value = (uint8_t *)malloc(large_size);
    CHECK(large_value != NULL);
    memset(large_value, 0x6d, large_size);
    writer = wisedb_sstable_writer_open(large_path, 1, 64, 256 * 1024, error, sizeof(error));
    CHECK(writer != NULL);
    const uint8_t large_key[] = {'z'};
    entry.key = large_key;
    entry.key_size = sizeof(large_key);
    entry.value = large_value;
    entry.value_size = large_size;
    entry.sequence = 1;
    entry.tombstone = 0;
    CHECK(wisedb_sstable_writer_add(writer, &entry) == 0);
    CHECK(wisedb_sstable_writer_finish(writer) == 0);
    writer = NULL;
    free(large_value);
    large_value = NULL;

    reader = wisedb_sstable_reader_open(large_path, 64, 256 * 1024, error, sizeof(error));
    CHECK(reader != NULL);
    CHECK(wisedb_sstable_reader_next(reader, &actual) == 1);
    CHECK(actual.value_size == large_size);
    for (size_t i = 0; i < actual.value_size; ++i) CHECK(actual.value[i] == 0x6d);
    wisedb_sstable_entry_free(&actual);
    CHECK(wisedb_sstable_reader_next(reader, &actual) == 0);
    wisedb_sstable_reader_close(reader);
    reader = NULL;

    /* Corrupt an entry value; open validates every record and fails closed. */
    CHECK(write_byte_at(path, 28 + 24 + 1, 0x7e) == 0);
    reader = wisedb_sstable_reader_open(path, 64, 1024, error, sizeof(error));
    CHECK(reader == NULL);
    CHECK(strstr(error, "checksum") != NULL);

    CHECK(unlink(path) == 0);
    CHECK(unlink(empty_path) == 0);
    CHECK(unlink(large_path) == 0);
    CHECK(rmdir(directory) == 0);
    result = 0;

cleanup:
    if (writer) wisedb_sstable_writer_abort(writer);
    if (reader) wisedb_sstable_reader_close(reader);
    free(large_value);
    unlink(path);
    unlink(empty_path);
    unlink(large_path);
    rmdir(directory);
    return result;
}
