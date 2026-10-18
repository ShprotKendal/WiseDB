#define _POSIX_C_SOURCE 200809L

#include "codec.h"
#include "wal.h"

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

typedef struct {
    size_t count;
    uint64_t last_sequence;
    const uint8_t *expected_payloads[3];
    size_t expected_sizes[3];
} replay_context;

static int check_record(uint64_t sequence, const uint8_t *payload, size_t payload_size, void *opaque) {
    replay_context *context = (replay_context *)opaque;
    if (sequence != context->last_sequence + 1 || sequence > 3
        || payload_size != context->expected_sizes[sequence - 1]
        || (payload_size > 0
            && memcmp(payload, context->expected_payloads[sequence - 1], payload_size) != 0)) {
        return -1;
    }
    context->last_sequence = sequence;
    context->count++;
    return 0;
}

static int replay_count(uint64_t sequence, const uint8_t *payload, size_t payload_size, void *opaque) {
    (void)sequence;
    (void)payload;
    (void)payload_size;
    (*(size_t *)opaque)++;
    return 0;
}

static int append_bytes(const char *path, const uint8_t *bytes, size_t size) {
    int fd = open(path, O_WRONLY | O_APPEND);
    if (fd < 0) return -1;
    size_t written = 0;
    while (written < size) {
        ssize_t result = write(fd, bytes + written, size - written);
        if (result <= 0) {
            close(fd);
            return -1;
        }
        written += (size_t)result;
    }
    return close(fd);
}

static off_t file_size(const char *path) {
    struct stat info;
    return stat(path, &info) == 0 ? info.st_size : (off_t)-1;
}

static uint32_t reference_crc32c(uint32_t crc, const uint8_t *data, size_t size) {
    crc = ~crc;
    for (size_t i = 0; i < size; ++i) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8; ++bit) {
            uint32_t mask = (uint32_t)-(int32_t)(crc & 1u);
            crc = (crc >> 1) ^ (0x82f63b78u & mask);
        }
    }
    return ~crc;
}

static int verify_first_frame_crc(const char *path, const uint8_t *payload, size_t payload_size) {
    (void)payload;
    uint8_t header[32];
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    ssize_t header_read = pread(fd, header, sizeof(header), 0);
    uint8_t *frame_payload = payload_size ? (uint8_t *)malloc(payload_size) : NULL;
    ssize_t payload_read = payload_size == 0
        ? 0
        : pread(fd, frame_payload, payload_size, (off_t)sizeof(header));
    close(fd);
    if (header_read != (ssize_t)sizeof(header)
        || (payload_size > 0 && payload_read != (ssize_t)payload_size)) {
        free(frame_payload);
        return -1;
    }
    uint32_t crc = reference_crc32c(0, header + 8, 16);
    crc = reference_crc32c(crc, frame_payload, payload_size);
    free(frame_payload);
    return wisedb_load_u32_le(header + 24) == crc
        && wisedb_load_u32_le(header + 28) == wisedb_crc32c(header, 28) ? 0 : -1;
}

static int write_legacy_v1_record(const char *path, const uint8_t *payload, size_t payload_size) {
    uint8_t header[28] = {0};
    memcpy(header, "WDBWAL1\0", 8);
    wisedb_store_u32_le(header + 8, WISEDB_WAL_LEGACY_FORMAT_VERSION);
    wisedb_store_u32_le(header + 12, (uint32_t)payload_size);
    wisedb_store_u64_le(header + 16, 1);
    uint32_t crc_state = wisedb_crc32c_extend(UINT32_MAX, header + 8, 16);
    crc_state = wisedb_crc32c_extend(crc_state, payload, payload_size);
    wisedb_store_u32_le(header + 24, wisedb_crc32c_finish(crc_state));
    int fd = open(path, O_WRONLY | O_TRUNC);
    if (fd < 0) return -1;
    size_t total = 0;
    while (total < sizeof(header)) {
        ssize_t written = write(fd, header + total, sizeof(header) - total);
        if (written <= 0) { close(fd); return -1; }
        total += (size_t)written;
    }
    total = 0;
    while (total < payload_size) {
        ssize_t written = write(fd, payload + total, payload_size - total);
        if (written <= 0) { close(fd); return -1; }
        total += (size_t)written;
    }
    return close(fd);
}

static int count_legacy_frames(const char *path, size_t *count_out) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return -1;
    off_t offset = 0;
    size_t count = 0;
    for (;;) {
        uint8_t header[28];
        ssize_t got = pread(fd, header, sizeof(header), offset);
        if (got == 0) break;
        if (got != (ssize_t)sizeof(header)) { close(fd); return -1; }
        if (memcmp(header, "WDBWAL1\0", 8) != 0
            || wisedb_load_u32_le(header + 8) != WISEDB_WAL_LEGACY_FORMAT_VERSION) break;
        uint32_t payload_size = wisedb_load_u32_le(header + 12);
        offset += (off_t)sizeof(header) + payload_size;
        count++;
    }
    close(fd);
    *count_out = count;
    return 0;
}

static int fail_replay(uint64_t sequence, const uint8_t *payload, size_t payload_size, void *context) {
    (void)sequence;
    (void)payload;
    (void)payload_size;
    (void)context;
    return -1;
}

typedef struct {
    size_t expected_size;
    uint8_t expected_byte;
    size_t count;
} large_record_context;

static int check_large_record(uint64_t sequence, const uint8_t *payload, size_t payload_size, void *opaque) {
    large_record_context *context = (large_record_context *)opaque;
    if (sequence != 1 || payload_size != context->expected_size) return -1;
    for (size_t i = 0; i < payload_size; ++i) {
        if (payload[i] != context->expected_byte) return -1;
    }
    context->count++;
    return 0;
}

int main(void) {
    char path[] = "/tmp/wisedb-wal-test-XXXXXX";
    char symlink_path[] = "/tmp/wisedb-wal-link-XXXXXX";
    char large_path[] = "/tmp/wisedb-wal-large-XXXXXX";
    char legacy_path[] = "/tmp/wisedb-wal-legacy-XXXXXX";
    wisedb_wal *wal = NULL;
    uint8_t *large_payload = NULL;
    const uint8_t crc_vector[] = "123456789";
    CHECK(reference_crc32c(0, crc_vector, sizeof(crc_vector) - 1) == 0xe3069283u);
    CHECK(wisedb_crc32c(crc_vector, sizeof(crc_vector) - 1) == 0xe3069283u);
    int temporary_fd = mkstemp(path);
    CHECK(temporary_fd >= 0);
    CHECK(close(temporary_fd) == 0);
    temporary_fd = mkstemp(legacy_path);
    CHECK(temporary_fd >= 0);
    CHECK(close(temporary_fd) == 0);

    const uint8_t record1[] = {0x00, 0x01, 0x7f, 0xff};
    const uint8_t record2[] = "second transaction";
    uint64_t sequence = 0;
    wal = wisedb_wal_open(path, 1024);
    CHECK(wal != NULL);
    struct stat wal_stat;
    CHECK(stat(path, &wal_stat) == 0);
    CHECK((wal_stat.st_mode & 0777) == 0600);
    CHECK(wisedb_wal_append(wal, record1, sizeof(record1), &sequence) == 0);
    CHECK(sequence == 1);
    CHECK(wisedb_wal_append(wal, record2, sizeof(record2) - 1, &sequence) == 0);
    CHECK(sequence == 2);
    CHECK(wisedb_wal_append(wal, NULL, 0, &sequence) == 0);
    CHECK(sequence == 3);
    CHECK(verify_first_frame_crc(path, record1, sizeof(record1)) == 0);

    replay_context context = {
        .count = 0,
        .last_sequence = 0,
        .expected_payloads = {record1, record2, NULL},
        .expected_sizes = {sizeof(record1), sizeof(record2) - 1, 0}
    };
    CHECK(wisedb_wal_replay(wal, check_record, &context) == 0);
    CHECK(context.count == 3 && context.last_sequence == 3);
    CHECK(wisedb_wal_replay(wal, fail_replay, NULL) != 0);
    CHECK(strstr(wisedb_wal_last_error(wal), "callback") != NULL);
    uint8_t too_large[1025] = {0};
    CHECK(wisedb_wal_append(wal, too_large, sizeof(too_large), &sequence) != 0);
    CHECK(sequence == 0);
    CHECK(strstr(wisedb_wal_last_error(wal), "size limit") != NULL);
    wisedb_wal_close(wal);
    wal = NULL;

    wal = wisedb_wal_open(path, 1024);
    CHECK(wal != NULL);
    size_t replayed = 0;
    CHECK(wisedb_wal_replay(wal, replay_count, &replayed) == 0);
    CHECK(replayed == 3);

    /* A second live handle must not acquire ownership of this WAL. */
    char error[256];
    CHECK(wisedb_wal_open_ex(path, 1024, error, sizeof(error)) == NULL);
    CHECK(strstr(error, "lock WAL") != NULL);
    wisedb_wal_close(wal);
    wal = NULL;

    /* An incomplete header is ambiguous, so v2 refuses to truncate it. */
    const uint8_t partial_header[] = {'W', 'D', 'B', 'W'};
    CHECK(append_bytes(path, partial_header, sizeof(partial_header)) == 0);
    CHECK(wisedb_wal_open_ex(path, 1024, error, sizeof(error)) == NULL);
    CHECK(strstr(error, "incomplete WAL magic") != NULL);
    int repair_fd = open(path, O_RDWR);
    CHECK(repair_fd >= 0);
    CHECK(ftruncate(repair_fd, file_size(path) - (off_t)sizeof(partial_header)) == 0);
    CHECK(close(repair_fd) == 0);
    wal = wisedb_wal_open(path, 1024);
    CHECK(wal != NULL);
    replayed = 0;
    CHECK(wisedb_wal_replay(wal, replay_count, &replayed) == 0);
    CHECK(replayed == 3);
    CHECK(file_size(path) == (32 + (off_t)sizeof(record1))
        + (32 + (off_t)(sizeof(record2) - 1)) + 32);
    CHECK(wisedb_wal_append(wal, record1, sizeof(record1), &sequence) == 0);
    CHECK(sequence == 4);
    replayed = 0;
    CHECK(wisedb_wal_replay(wal, replay_count, &replayed) == 0);
    CHECK(replayed == 4);
    wisedb_wal_close(wal);
    wal = NULL;

    /* A complete frame header with a torn payload is also a repairable tail. */
    uint8_t partial_payload_frame[34] = {0};
    memcpy(partial_payload_frame, "WDBWAL1\0", 8);
    wisedb_store_u32_le(partial_payload_frame + 8, WISEDB_WAL_FORMAT_VERSION);
    wisedb_store_u32_le(partial_payload_frame + 12, 5);
    wisedb_store_u64_le(partial_payload_frame + 16, 5);
    wisedb_store_u32_le(partial_payload_frame + 28, wisedb_crc32c(partial_payload_frame, 28));
    partial_payload_frame[32] = 'x';
    partial_payload_frame[33] = 'y';
    CHECK(append_bytes(path, partial_payload_frame, sizeof(partial_payload_frame)) == 0);
    wal = wisedb_wal_open(path, 1024);
    CHECK(wal != NULL);
    replayed = 0;
    CHECK(wisedb_wal_replay(wal, replay_count, &replayed) == 0);
    CHECK(replayed == 4);
    CHECK(wisedb_wal_append(wal, record2, sizeof(record2) - 1, &sequence) == 0);
    CHECK(sequence == 5);
    wisedb_wal_close(wal);
    wal = NULL;

    uint8_t invalid_sequence_tail[34] = {0};
    memcpy(invalid_sequence_tail, "WDBWAL1\0", 8);
    wisedb_store_u32_le(invalid_sequence_tail + 8, WISEDB_WAL_FORMAT_VERSION);
    wisedb_store_u32_le(invalid_sequence_tail + 12, 5);
    wisedb_store_u64_le(invalid_sequence_tail + 16, 99);
    wisedb_store_u32_le(invalid_sequence_tail + 28, wisedb_crc32c(invalid_sequence_tail, 28));
    invalid_sequence_tail[32] = 'x';
    invalid_sequence_tail[33] = 'y';
    CHECK(append_bytes(path, invalid_sequence_tail, sizeof(invalid_sequence_tail)) == 0);
    off_t invalid_sequence_size = file_size(path);
    CHECK(wisedb_wal_open_ex(path, 1024, error, sizeof(error)) == NULL);
    CHECK(strstr(error, "sequence") != NULL);
    CHECK(file_size(path) == invalid_sequence_size);
    int truncate_invalid_tail_fd = open(path, O_RDWR);
    CHECK(truncate_invalid_tail_fd >= 0);
    CHECK(ftruncate(truncate_invalid_tail_fd, invalid_sequence_size - (off_t)sizeof(invalid_sequence_tail)) == 0);
    CHECK(close(truncate_invalid_tail_fd) == 0);

    CHECK(wisedb_wal_open_ex(path, 1, error, sizeof(error)) == NULL);
    CHECK(strstr(error, "size limit") != NULL);

    /* A changed length invalidates the v2 header before any tail truncation. */
    int corrupt_header_fd = open(path, O_RDWR);
    CHECK(corrupt_header_fd >= 0);
    uint8_t changed_length = 5;
    CHECK(pwrite(corrupt_header_fd, &changed_length, 1, 12) == 1);
    CHECK(close(corrupt_header_fd) == 0);
    off_t corrupt_size = file_size(path);
    CHECK(wisedb_wal_open_ex(path, 1024, error, sizeof(error)) == NULL);
    CHECK(strstr(error, "header checksum") != NULL);
    CHECK(file_size(path) == corrupt_size);

    /* A complete frame with a bad checksum must fail closed. */
    int fd = open(path, O_RDWR);
    CHECK(fd >= 0);
    uint8_t byte;
    CHECK(pread(fd, &byte, 1, 32) == 1);
    byte ^= 0x80;
    CHECK(pwrite(fd, &byte, 1, 32) == 1);
    CHECK(close(fd) == 0);
    CHECK(wisedb_wal_open_ex(path, 1024, error, sizeof(error)) == NULL);
    CHECK(strstr(error, "checksum") != NULL);

    const uint8_t legacy_payload[] = "legacy-record";
    CHECK(write_legacy_v1_record(legacy_path, legacy_payload, sizeof(legacy_payload) - 1) == 0);
    off_t legacy_original_size = file_size(legacy_path);
    int legacy_corrupt_fd = open(legacy_path, O_RDWR);
    CHECK(legacy_corrupt_fd >= 0);
    uint8_t enlarged_length = 200;
    CHECK(pwrite(legacy_corrupt_fd, &enlarged_length, 1, 12) == 1);
    CHECK(close(legacy_corrupt_fd) == 0);
    CHECK(wisedb_wal_open_ex(legacy_path, 1024, error, sizeof(error)) == NULL);
    CHECK(strstr(error, "legacy WAL payload is ambiguous") != NULL);
    CHECK(file_size(legacy_path) == legacy_original_size);
    CHECK(write_legacy_v1_record(legacy_path, legacy_payload, sizeof(legacy_payload) - 1) == 0);
    wal = wisedb_wal_open(legacy_path, 1024);
    CHECK(wal != NULL);
    replayed = 0;
    CHECK(wisedb_wal_replay(wal, replay_count, &replayed) == 0 && replayed == 1);
    CHECK(wisedb_wal_append(wal, record1, sizeof(record1), &sequence) == 0 && sequence == 2);
    wisedb_wal_close(wal);
    wal = NULL;
    wal = wisedb_wal_open(legacy_path, 1024);
    CHECK(wal != NULL);
    replayed = 0;
    CHECK(wisedb_wal_replay(wal, replay_count, &replayed) == 0 && replayed == 2);
    size_t legacy_frame_count = 0;
    CHECK(count_legacy_frames(legacy_path, &legacy_frame_count) == 0);
    CHECK(legacy_frame_count == 1);
    wisedb_wal_close(wal);
    wal = NULL;

    int link_fd = mkstemp(symlink_path);
    CHECK(link_fd >= 0);
    CHECK(close(link_fd) == 0);
    CHECK(unlink(symlink_path) == 0);
    CHECK(symlink(path, symlink_path) == 0);
    CHECK(wisedb_wal_open_ex(symlink_path, 1024, error, sizeof(error)) == NULL);
    CHECK(error[0] != '\0');

    int large_fd = mkstemp(large_path);
    CHECK(large_fd >= 0);
    CHECK(close(large_fd) == 0);
    const size_t large_size = 200 * 1024;
    large_payload = (uint8_t *)malloc(large_size);
    CHECK(large_payload != NULL);
    memset(large_payload, 0xa5, large_size);
    wal = wisedb_wal_open(large_path, 256 * 1024);
    CHECK(wal != NULL);
    CHECK(wisedb_wal_append(wal, large_payload, large_size, &sequence) == 0);
    CHECK(sequence == 1);
    wisedb_wal_close(wal);
    wal = NULL;
    wal = wisedb_wal_open(large_path, 256 * 1024);
    CHECK(wal != NULL);
    large_record_context large_context = {large_size, 0xa5, 0};
    CHECK(wisedb_wal_replay(wal, check_large_record, &large_context) == 0);
    CHECK(large_context.count == 1);
    wisedb_wal_close(wal);
    wal = NULL;
    free(large_payload);
    large_payload = NULL;

    CHECK(unlink(path) == 0);
    CHECK(unlink(symlink_path) == 0);
    CHECK(unlink(large_path) == 0);
    CHECK(unlink(legacy_path) == 0);
    return 0;

cleanup:
    if (wal) wisedb_wal_close(wal);
    free(large_payload);
    unlink(path);
    unlink(symlink_path);
    unlink(large_path);
    unlink(legacy_path);
    return 1;
}
