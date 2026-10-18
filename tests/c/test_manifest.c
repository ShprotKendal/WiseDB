#define _DARWIN_C_SOURCE 1
#define _POSIX_C_SOURCE 200809L

#include "manifest.h"
#include "sstable.h"

#include <fcntl.h>
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

static int make_table(const char *path, uint64_t checkpoint, const uint8_t *key, const uint8_t *value) {
    char error[256] = {0};
    wisedb_sstable_writer *writer = wisedb_sstable_writer_open(path, checkpoint, 64, 1024, error, sizeof(error));
    if (!writer) {
        fprintf(stderr, "open SST writer: %s\n", error);
        return -1;
    }
    wisedb_sstable_entry entry = {key, 1, value, 1, checkpoint, 0};
    if (wisedb_sstable_writer_add(writer, &entry) != 0 || wisedb_sstable_writer_finish(writer) != 0) {
        wisedb_sstable_writer_abort(writer);
        return -1;
    }
    return 0;
}

static int write_byte_at(const char *path, off_t offset, uint8_t value) {
    int fd = open(path, O_RDWR);
    if (fd < 0) return -1;
    ssize_t amount = pwrite(fd, &value, 1, offset);
    int close_result = close(fd);
    return amount == 1 && close_result == 0 ? 0 : -1;
}

int main(void) {
    char directory[] = "/tmp/wisedb-manifest-test-XXXXXX";
    char table1[512];
    char table2[512];
    char table3[512];
    char table4[512];
    char missing_table[512];
    char manifest_path[512];
    char error[256] = {0};
    wisedb_manifest manifest = {0};
    wisedb_manifest loaded = {0};
    int result = 1;
    CHECK(mkdtemp(directory) != NULL);
    CHECK(snprintf(table1, sizeof(table1), "%s/table-%020d.sst", directory, 1) > 0);
    CHECK(snprintf(table2, sizeof(table2), "%s/table-%020d.sst", directory, 2) > 0);
    CHECK(snprintf(table3, sizeof(table3), "%s/table-%020d.sst", directory, 3) > 0);
    CHECK(snprintf(table4, sizeof(table4), "%s/table-%020d.sst", directory, 4) > 0);
    CHECK(snprintf(missing_table, sizeof(missing_table), "%s/table-%020d.sst", directory, 3) > 0);

    const uint8_t key1[] = {'a'};
    const uint8_t value1[] = {'1'};
    const uint8_t key2[] = {'b'};
    const uint8_t value2[] = {'2'};
    CHECK(make_table(table1, 5, key1, value1) == 0);

    manifest.generation = 1;
    manifest.checkpoint_sequence = 5;
    manifest.table_id = 1;
    manifest.entry_count = 1;
    strcpy(manifest.table_name, "table-00000000000000000001.sst");

    wisedb_manifest unchanged = {.generation = 77};
    CHECK(wisedb_manifest_read("", &unchanged, error, sizeof(error)) != 0);
    CHECK(unchanged.generation == 77);
    CHECK(wisedb_manifest_publish("", &manifest, error, sizeof(error)) != 0);
    char cwd_manifest[64];
    CHECK(snprintf(cwd_manifest, sizeof(cwd_manifest), "MANIFEST") > 0);
    CHECK(access(cwd_manifest, F_OK) != 0);
    char table_name[128];
    CHECK(wisedb_manifest_table_name(1, table_name, sizeof(table_name)) == 0);
    CHECK(strcmp(table_name, "table-00000000000000000001.sst") == 0);

    CHECK(wisedb_manifest_publish(directory, &manifest, error, sizeof(error)) == 0);
    if (wisedb_manifest_read(directory, &loaded, error, sizeof(error)) != 0) {
        fprintf(stderr, "first manifest read failed: %s\n", error);
        goto cleanup;
    }
    CHECK(loaded.generation == 1 && loaded.checkpoint_sequence == 5);
    CHECK(loaded.table_id == 1 && loaded.entry_count == 1);
    CHECK(strcmp(loaded.table_name, manifest.table_name) == 0);

    CHECK(make_table(table2, 8, key2, value2) == 0);
    const uint8_t key3[] = {'c'};
    const uint8_t key4[] = {'d'};
    CHECK(make_table(table3, 9, key3, value1) == 0);
    CHECK(make_table(table4, 7, key4, value2) == 0);
    manifest.generation = 2;
    manifest.checkpoint_sequence = 8;
    manifest.table_id = 2;
    manifest.entry_count = 1;
    strcpy(manifest.table_name, "table-00000000000000000002.sst");
    CHECK(wisedb_manifest_publish(directory, &manifest, error, sizeof(error)) == 0);
    if (wisedb_manifest_read(directory, &loaded, error, sizeof(error)) != 0) {
        fprintf(stderr, "manifest reopen failed: %s\n", error);
        goto cleanup;
    }
    CHECK(loaded.generation == 2 && loaded.checkpoint_sequence == 8 && loaded.table_id == 2);

    manifest.generation = 4;
    manifest.table_id = 3;
    manifest.checkpoint_sequence = 9;
    strcpy(manifest.table_name, "table-00000000000000000003.sst");
    CHECK(wisedb_manifest_publish(directory, &manifest, error, sizeof(error)) != 0);

    manifest.generation = 3;
    manifest.table_id = 4;
    manifest.checkpoint_sequence = 7;
    strcpy(manifest.table_name, "table-00000000000000000004.sst");
    CHECK(wisedb_manifest_publish(directory, &manifest, error, sizeof(error)) != 0);

    /* A corrupt active manifest must not be silently replaced by a new generation. */
    char manifest_path_for_corruption[512];
    CHECK(snprintf(manifest_path_for_corruption, sizeof(manifest_path_for_corruption), "%s/MANIFEST", directory) > 0);
    CHECK(write_byte_at(manifest_path_for_corruption, 12, 0xff) == 0);
    manifest.generation = 3;
    manifest.checkpoint_sequence = 8;
    manifest.entry_count = 1;
    CHECK(wisedb_manifest_publish(directory, &manifest, error, sizeof(error)) != 0);
    CHECK(wisedb_manifest_read(directory, &loaded, error, sizeof(error)) != 0);

    /* Restore a valid generation for the referenced-table corruption check. */
    unlink(manifest_path_for_corruption);
    manifest.generation = 1;
    manifest.table_id = 2;
    manifest.checkpoint_sequence = 8;
    strcpy(manifest.table_name, "table-00000000000000000002.sst");
    CHECK(wisedb_manifest_publish(directory, &manifest, error, sizeof(error)) == 0);

    manifest.generation = 3;
    manifest.table_id = 2;
    manifest.checkpoint_sequence = 8;
    manifest.entry_count = 1;
    strcpy(manifest.table_name, "../outside.sst");
    CHECK(wisedb_manifest_publish(directory, &manifest, error, sizeof(error)) != 0);

    /* Corrupt the referenced SSTable; manifest recovery must fail closed. */
    CHECK(write_byte_at(table2, 28 + 24 + 1, 0xff) == 0);
    CHECK(wisedb_manifest_read(directory, &loaded, error, sizeof(error)) != 0);
    CHECK(strstr(error, "checksum") != NULL);

    CHECK(unlink(table1) == 0);
    CHECK(unlink(table2) == 0);
    CHECK(unlink(table3) == 0);
    CHECK(unlink(table4) == 0);
    CHECK(snprintf(manifest_path, sizeof(manifest_path), "%s/MANIFEST", directory) > 0);
    CHECK(unlink(manifest_path) == 0);
    CHECK(rmdir(directory) == 0);
    result = 0;

cleanup:
    if (snprintf(missing_table, sizeof(missing_table), "%s/table-%020d.sst", directory, 3) > 0) unlink(missing_table);
    if (snprintf(table1, sizeof(table1), "%s/table-%020d.sst", directory, 1) > 0) unlink(table1);
    if (snprintf(table2, sizeof(table2), "%s/table-%020d.sst", directory, 2) > 0) unlink(table2);
    if (snprintf(table3, sizeof(table3), "%s/table-%020d.sst", directory, 3) > 0) unlink(table3);
    if (snprintf(table4, sizeof(table4), "%s/table-%020d.sst", directory, 4) > 0) unlink(table4);
    if (snprintf(manifest_path, sizeof(manifest_path), "%s/MANIFEST", directory) > 0) unlink(manifest_path);
    rmdir(directory);
    return result;
}
