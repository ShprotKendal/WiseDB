#define _DARWIN_C_SOURCE 1
#define _FILE_OFFSET_BITS 64
#define _POSIX_C_SOURCE 200809L

#include "sstable.h"
#include "codec.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "fault_injection.h"

#define SST_HEADER_SIZE 28u
#define SST_RECORD_HEADER_SIZE 24u
#define SST_FOOTER_SIZE 20u
#define SST_MAGIC "WDBSST1\0"
#define SST_FOOTER_MAGIC "WDBEND1\0"
#define SST_HEADER_CRC_OFFSET 24u
#define SST_FOOTER_CRC_OFFSET 16u
#define SST_TOMBSTONE 1u

struct wisedb_sstable_writer {
    int fd;
    char *target_path;
    char *temporary_path;
    size_t max_key_size;
    size_t max_value_size;
    off_t offset;
    uint64_t checkpoint_sequence;
    uint64_t entry_count;
    uint8_t *previous_key;
    size_t previous_key_size;
    int finished;
    int failed;
    int temporary_created;
    char error[256];
};

struct wisedb_sstable_reader {
    int fd;
    off_t offset;
    off_t data_end;
    size_t max_key_size;
    size_t max_value_size;
    uint64_t checkpoint_sequence;
    uint64_t entries_remaining;
    uint64_t entry_count;
    uint8_t *previous_key;
    size_t previous_key_size;
    char error[256];
};

static void copy_error(char *out, size_t capacity, const char *message) {
    if (out && capacity > 0) (void)snprintf(out, capacity, "%s", message ? message : "");
}

static void copy_errno_error(char *out, size_t capacity, const char *operation) {
    if (out && capacity > 0) (void)snprintf(out, capacity, "%s: %s", operation, strerror(errno));
}

static void set_writer_error(wisedb_sstable_writer *writer, const char *operation) {
    (void)snprintf(writer->error, sizeof(writer->error), "%s: %s", operation, strerror(errno));
}

static void set_reader_error(wisedb_sstable_reader *reader, const char *message) {
    (void)snprintf(reader->error, sizeof(reader->error), "%s", message);
}

static int write_all(int fd, const uint8_t *data, size_t size) {
    size_t written = 0;
    while (written < size) {
        ssize_t amount = write(fd, data + written, size - written);
        if (amount > 0) {
            written += (size_t)amount;
            continue;
        }
        if (amount < 0 && errno == EINTR) continue;
        if (amount == 0) errno = EIO;
        return -1;
    }
    return 0;
}

static int read_exact_at(int fd, uint8_t *data, size_t size, off_t offset) {
    size_t read_size = 0;
    while (read_size < size) {
        ssize_t amount = pread(fd, data + read_size, size - read_size, offset + (off_t)read_size);
        if (amount > 0) {
            read_size += (size_t)amount;
            continue;
        }
        if (amount < 0 && errno == EINTR) continue;
        if (amount == 0) errno = EIO;
        return -1;
    }
    return 0;
}

static int sync_parent_directory(const char *path, char *error_out, size_t error_capacity) {
    char *directory = strdup(path);
    if (!directory) {
        copy_error(error_out, error_capacity, "out of memory resolving SSTable directory");
        return -1;
    }
    char *slash = strrchr(directory, '/');
    if (!slash) {
        free(directory);
        directory = strdup(".");
        if (!directory) {
            copy_error(error_out, error_capacity, "out of memory resolving SSTable directory");
            return -1;
        }
    } else if (slash == directory) {
        slash[1] = '\0';
    } else {
        *slash = '\0';
    }

    int fd = open(directory, O_RDONLY
#ifdef O_CLOEXEC
        | O_CLOEXEC
#endif
    );
    int saved_errno = errno;
    free(directory);
    if (fd < 0) {
        errno = saved_errno;
        copy_errno_error(error_out, error_capacity, "open SSTable directory");
        return -1;
    }
    int result = fsync(fd);
    saved_errno = errno;
    close(fd);
    if (result != 0) {
        errno = saved_errno;
        copy_errno_error(error_out, error_capacity, "synchronize SSTable directory");
        return -1;
    }
    return 0;
}

static int compare_keys(const uint8_t *left, size_t left_size, const uint8_t *right, size_t right_size) {
    size_t common_size = left_size < right_size ? left_size : right_size;
    int result = common_size ? memcmp(left, right, common_size) : 0;
    if (result != 0) return result;
    return (left_size > right_size) - (left_size < right_size);
}

static void clear_temporary(wisedb_sstable_writer *writer) {
    if (writer->fd >= 0) close(writer->fd);
    if (writer->temporary_created && writer->temporary_path) unlink(writer->temporary_path);
    free(writer->target_path);
    free(writer->temporary_path);
    free(writer->previous_key);
    free(writer);
}

wisedb_sstable_writer *wisedb_sstable_writer_open(
    const char *path,
    uint64_t checkpoint_sequence,
    size_t max_key_size,
    size_t max_value_size,
    char *error_out,
    size_t error_capacity
) {
    copy_error(error_out, error_capacity, "");
    if (!path || !path[0] || max_key_size == 0 || max_value_size == 0
        || max_key_size > UINT32_MAX || max_value_size > UINT32_MAX) {
        copy_error(error_out, error_capacity, "invalid SSTable writer arguments");
        return NULL;
    }
    if (access(path, F_OK) == 0 || errno != ENOENT) {
        copy_error(error_out, error_capacity, "SSTable target already exists or cannot be checked");
        return NULL;
    }

    wisedb_sstable_writer *writer = (wisedb_sstable_writer *)calloc(1, sizeof(*writer));
    if (!writer) {
        copy_error(error_out, error_capacity, "out of memory allocating SSTable writer");
        return NULL;
    }
    writer->fd = -1;
    writer->target_path = strdup(path);
    size_t path_size = strlen(path);
    static const char suffix[] = ".tmp.XXXXXX";
    if (path_size > SIZE_MAX - sizeof(suffix)) {
        copy_error(error_out, error_capacity, "SSTable path is too long");
        clear_temporary(writer);
        return NULL;
    }
    writer->temporary_path = (char *)malloc(path_size + sizeof(suffix));
    if (writer->temporary_path) writer->temporary_path[0] = '\0';
    if (!writer->target_path || !writer->temporary_path) {
        copy_error(error_out, error_capacity, "out of memory allocating SSTable paths");
        clear_temporary(writer);
        return NULL;
    }
    memcpy(writer->temporary_path, path, path_size);
    memcpy(writer->temporary_path + path_size, suffix, sizeof(suffix));
    writer->max_key_size = max_key_size;
    writer->max_value_size = max_value_size;
    writer->checkpoint_sequence = checkpoint_sequence;

    writer->fd = mkstemp(writer->temporary_path);
    if (writer->fd < 0) {
        copy_errno_error(error_out, error_capacity, "create SSTable temporary file");
        clear_temporary(writer);
        return NULL;
    }
    writer->temporary_created = 1;
    if (fchmod(writer->fd, S_IRUSR | S_IWUSR) != 0) {
        set_writer_error(writer, "restrict SSTable permissions");
        copy_error(error_out, error_capacity, writer->error);
        clear_temporary(writer);
        return NULL;
    }

    uint8_t header[SST_HEADER_SIZE] = {0};
    memcpy(header, SST_MAGIC, 8);
    wisedb_store_u32_le(header + 8, WISEDB_SSTABLE_FORMAT_VERSION);
    wisedb_store_u32_le(header + 12, SST_HEADER_SIZE);
    wisedb_store_u64_le(header + 16, checkpoint_sequence);
    uint32_t crc = wisedb_crc32c(header, SST_HEADER_CRC_OFFSET);
    wisedb_store_u32_le(header + SST_HEADER_CRC_OFFSET, crc);
    if (write_all(writer->fd, header, sizeof(header)) != 0) {
        set_writer_error(writer, "write SSTable header");
        copy_error(error_out, error_capacity, writer->error);
        clear_temporary(writer);
        return NULL;
    }
    writer->offset = (off_t)sizeof(header);
    return writer;
}

int wisedb_sstable_writer_add(wisedb_sstable_writer *writer, const wisedb_sstable_entry *entry) {
    if (!writer || !entry || writer->finished || writer->failed || !entry->key || entry->key_size == 0
        || (!entry->value && entry->value_size != 0)) return -1;
    writer->error[0] = '\0';
    if (entry->key_size > writer->max_key_size || entry->key_size > UINT32_MAX
        || entry->value_size > writer->max_value_size || entry->value_size > UINT32_MAX
        || (entry->tombstone && entry->value_size != 0)) {
        (void)snprintf(writer->error, sizeof(writer->error), "%s", "SSTable entry exceeds limits or has invalid tombstone");
        return -1;
    }
    if (writer->entry_count > 0
        && compare_keys(writer->previous_key, writer->previous_key_size, entry->key, entry->key_size) >= 0) {
        (void)snprintf(writer->error, sizeof(writer->error), "%s", "SSTable keys must be strictly increasing");
        return -1;
    }
    if (entry->sequence == 0 || entry->sequence > writer->checkpoint_sequence) {
        (void)snprintf(writer->error, sizeof(writer->error), "%s", "entry sequence exceeds checkpoint sequence");
        return -1;
    }
    uintmax_t frame_size = (uintmax_t)SST_RECORD_HEADER_SIZE
        + (uintmax_t)entry->key_size + (uintmax_t)entry->value_size;
    if ((uintmax_t)writer->offset + frame_size > (uintmax_t)INT64_MAX
        || writer->entry_count == UINT64_MAX) {
        (void)snprintf(writer->error, sizeof(writer->error), "%s", "SSTable size or entry count limit exceeded");
        return -1;
    }

    uint8_t record_header[SST_RECORD_HEADER_SIZE] = {0};
    wisedb_store_u32_le(record_header, (uint32_t)entry->key_size);
    wisedb_store_u32_le(record_header + 4, (uint32_t)entry->value_size);
    wisedb_store_u64_le(record_header + 8, entry->sequence);
    wisedb_store_u32_le(record_header + 16, entry->tombstone ? SST_TOMBSTONE : 0u);
    uint32_t crc_state = wisedb_crc32c_extend(UINT32_MAX, record_header, 20);
    crc_state = wisedb_crc32c_extend(crc_state, entry->key, entry->key_size);
    crc_state = wisedb_crc32c_extend(crc_state, entry->value, entry->value_size);
    wisedb_store_u32_le(record_header + 20, wisedb_crc32c_finish(crc_state));

    if (write_all(writer->fd, record_header, sizeof(record_header)) != 0
        || write_all(writer->fd, entry->key, entry->key_size) != 0
        || (entry->value_size > 0 && write_all(writer->fd, entry->value, entry->value_size) != 0)) {
        set_writer_error(writer, "write SSTable entry");
        writer->failed = 1;
        return -1;
    }

    uint8_t *new_key = (uint8_t *)malloc(entry->key_size);
    if (!new_key) {
        (void)snprintf(writer->error, sizeof(writer->error), "%s", "out of memory copying last SSTable key");
        writer->failed = 1;
        return -1;
    }
    memcpy(new_key, entry->key, entry->key_size);
    free(writer->previous_key);
    writer->previous_key = new_key;
    writer->previous_key_size = entry->key_size;
    writer->entry_count++;
    writer->offset += (off_t)sizeof(record_header) + (off_t)entry->key_size + (off_t)entry->value_size;
    return 0;
}

int wisedb_sstable_writer_finish(wisedb_sstable_writer *writer) {
    if (!writer || writer->finished || writer->failed) return -1;
    uint8_t footer[SST_FOOTER_SIZE] = {0};
    memcpy(footer, SST_FOOTER_MAGIC, 8);
    wisedb_store_u64_le(footer + 8, writer->entry_count);
    wisedb_store_u32_le(footer + SST_FOOTER_CRC_OFFSET, wisedb_crc32c(footer, SST_FOOTER_CRC_OFFSET));

    if (write_all(writer->fd, footer, sizeof(footer)) != 0) {
        set_writer_error(writer, "write SSTable footer");
        writer->failed = 1;
        return -1;
    }
    if (fsync(writer->fd) != 0) {
        set_writer_error(writer, "synchronize SSTable");
        writer->failed = 1;
        return -1;
    }
    if (close(writer->fd) != 0) {
        writer->fd = -1;
        set_writer_error(writer, "close SSTable");
        writer->failed = 1;
        return -1;
    }
    writer->fd = -1;
    if (link(writer->temporary_path, writer->target_path) != 0) {
        set_writer_error(writer, "publish SSTable without replacing an existing file");
        writer->failed = 1;
        return -1;
    }
    if (unlink(writer->temporary_path) != 0) {
        set_writer_error(writer, "remove SSTable temporary name after publish");
        writer->failed = 1;
        return -1;
    }
    char error[256] = {0};
    if (sync_parent_directory(writer->target_path, error, sizeof(error)) != 0) {
        (void)snprintf(writer->error, sizeof(writer->error), "%s", error);
        writer->failed = 1;
        return -1;
    }

    writer->finished = 1;
    free(writer->target_path);
    free(writer->temporary_path);
    free(writer->previous_key);
    free(writer);
    return 0;
}

const char *wisedb_sstable_writer_last_error(const wisedb_sstable_writer *writer) {
    if (!writer || !writer->error[0]) return "";
    return writer->error;
}

void wisedb_sstable_writer_abort(wisedb_sstable_writer *writer) {
    if (!writer) return;
    clear_temporary(writer);
}

static int validate_snapshot(wisedb_sstable_reader *reader, uint64_t *entry_count_out) {
    struct stat st;
    if (fstat(reader->fd, &st) != 0 || !S_ISREG(st.st_mode)) {
        set_reader_error(reader, "SSTable is not a regular file");
        return -1;
    }
    if (st.st_size < (off_t)(SST_HEADER_SIZE + SST_FOOTER_SIZE)) {
        set_reader_error(reader, "SSTable is truncated");
        return -1;
    }

    uint8_t header[SST_HEADER_SIZE];
    if (read_exact_at(reader->fd, header, sizeof(header), 0) != 0) {
        set_reader_error(reader, "cannot read SSTable header");
        return -1;
    }
    if (memcmp(header, SST_MAGIC, 8) != 0
        || wisedb_load_u32_le(header + 8) != WISEDB_SSTABLE_FORMAT_VERSION
        || wisedb_load_u32_le(header + 12) != SST_HEADER_SIZE
        || wisedb_crc32c(header, SST_HEADER_CRC_OFFSET) != wisedb_load_u32_le(header + SST_HEADER_CRC_OFFSET)) {
        set_reader_error(reader, "invalid SSTable header or checksum");
        return -1;
    }
    reader->checkpoint_sequence = wisedb_load_u64_le(header + 16);

    uint8_t footer[SST_FOOTER_SIZE];
    off_t footer_offset = st.st_size - (off_t)sizeof(footer);
    if (read_exact_at(reader->fd, footer, sizeof(footer), footer_offset) != 0) {
        set_reader_error(reader, "cannot read SSTable footer");
        return -1;
    }
    if (memcmp(footer, SST_FOOTER_MAGIC, 8) != 0
        || wisedb_crc32c(footer, SST_FOOTER_CRC_OFFSET) != wisedb_load_u32_le(footer + SST_FOOTER_CRC_OFFSET)) {
        set_reader_error(reader, "invalid SSTable footer or checksum");
        return -1;
    }

    uint64_t entry_count = wisedb_load_u64_le(footer + 8);
    if (entry_count > (uint64_t)((st.st_size - (off_t)(SST_HEADER_SIZE + SST_FOOTER_SIZE))
                                  / (off_t)SST_RECORD_HEADER_SIZE)) {
        set_reader_error(reader, "SSTable entry count exceeds file bounds");
        return -1;
    }
    reader->data_end = footer_offset;
    reader->entries_remaining = entry_count;
    reader->entry_count = entry_count;
    reader->offset = (off_t)SST_HEADER_SIZE;

    uint8_t *previous_key = NULL;
    size_t previous_key_size = 0;
    for (uint64_t index = 0; index < entry_count; ++index) {
        uint8_t record_header[SST_RECORD_HEADER_SIZE];
        if (reader->data_end - reader->offset < (off_t)sizeof(record_header)
            || read_exact_at(reader->fd, record_header, sizeof(record_header), reader->offset) != 0) {
            free(previous_key);
            set_reader_error(reader, "truncated SSTable record header");
            return -1;
        }
        uint32_t key_size = wisedb_load_u32_le(record_header);
        uint32_t value_size = wisedb_load_u32_le(record_header + 4);
        uint64_t sequence = wisedb_load_u64_le(record_header + 8);
        uint32_t flags = wisedb_load_u32_le(record_header + 16);
        uint32_t stored_crc = wisedb_load_u32_le(record_header + 20);
        if (key_size == 0 || key_size > reader->max_key_size || value_size > reader->max_value_size
            || sequence == 0 || sequence > reader->checkpoint_sequence || (flags & ~SST_TOMBSTONE) != 0
            || ((flags & SST_TOMBSTONE) && value_size != 0)) {
            free(previous_key);
            set_reader_error(reader, "invalid SSTable record metadata");
            return -1;
        }
        uint64_t data_size = (uint64_t)key_size + (uint64_t)value_size;
        if (data_size > (uint64_t)(reader->data_end - reader->offset - (off_t)sizeof(record_header))) {
            free(previous_key);
            set_reader_error(reader, "truncated SSTable record data");
            return -1;
        }
        uint8_t *key = (uint8_t *)malloc(key_size);
        if (!key) {
            free(previous_key);
            set_reader_error(reader, "out of memory validating SSTable key");
            return -1;
        }
        off_t key_offset = reader->offset + (off_t)sizeof(record_header);
        if (read_exact_at(reader->fd, key, key_size, key_offset) != 0) {
            free(previous_key);
            free(key);
            set_reader_error(reader, "cannot read SSTable key");
            return -1;
        }
        if (previous_key && compare_keys(previous_key, previous_key_size, key, key_size) >= 0) {
            free(previous_key);
            free(key);
            set_reader_error(reader, "SSTable keys are not strictly increasing");
            return -1;
        }
        uint32_t crc_state = wisedb_crc32c_extend(UINT32_MAX, record_header, 20);
        crc_state = wisedb_crc32c_extend(crc_state, key, key_size);
        free(previous_key);
        previous_key = key;
        previous_key_size = key_size;

        uint8_t chunk[64 * 1024];
        size_t value_left = value_size;
        off_t value_offset = key_offset + (off_t)key_size;
        while (value_left > 0) {
            size_t request = value_left < sizeof(chunk) ? value_left : sizeof(chunk);
            if (read_exact_at(reader->fd, chunk, request, value_offset) != 0) {
                free(previous_key);
                set_reader_error(reader, "cannot read SSTable value");
                return -1;
            }
            crc_state = wisedb_crc32c_extend(crc_state, chunk, request);
            value_left -= request;
            value_offset += (off_t)request;
        }
        if (wisedb_crc32c_finish(crc_state) != stored_crc) {
            free(previous_key);
            set_reader_error(reader, "SSTable record checksum mismatch");
            return -1;
        }
        reader->offset += (off_t)sizeof(record_header) + (off_t)key_size + (off_t)value_size;
    }
    free(previous_key);
    if (reader->offset != reader->data_end) {
        set_reader_error(reader, "unexpected bytes before SSTable footer");
        return -1;
    }
    *entry_count_out = entry_count;
    reader->offset = (off_t)SST_HEADER_SIZE;
    reader->entries_remaining = entry_count;
    reader->entry_count = entry_count;
    return 0;
}

wisedb_sstable_reader *wisedb_sstable_reader_open(
    const char *path,
    size_t max_key_size,
    size_t max_value_size,
    char *error_out,
    size_t error_capacity
) {
    copy_error(error_out, error_capacity, "");
    if (!path || !path[0] || max_key_size == 0 || max_value_size == 0) {
        copy_error(error_out, error_capacity, "invalid SSTable reader arguments");
        return NULL;
    }
    int flags = O_RDONLY | O_NONBLOCK
#ifdef O_NOFOLLOW
        | O_NOFOLLOW
#endif
#ifdef O_CLOEXEC
        | O_CLOEXEC
#endif
        ;
    int fd = open(path, flags);
    if (fd < 0) {
        copy_errno_error(error_out, error_capacity, "open SSTable");
        return NULL;
    }

    wisedb_sstable_reader *reader = (wisedb_sstable_reader *)calloc(1, sizeof(*reader));
    if (!reader) {
        close(fd);
        copy_error(error_out, error_capacity, "out of memory allocating SSTable reader");
        return NULL;
    }
    reader->fd = fd;
    reader->max_key_size = max_key_size;
    reader->max_value_size = max_value_size;
    uint64_t count = 0;
    if (validate_snapshot(reader, &count) != 0) {
        copy_error(error_out, error_capacity, reader->error);
        wisedb_sstable_reader_close(reader);
        return NULL;
    }
    reader->error[0] = '\0';
    return reader;
}

int wisedb_sstable_reader_next(wisedb_sstable_reader *reader, wisedb_sstable_entry *entry_out) {
    if (!reader || !entry_out) return -1;
    memset(entry_out, 0, sizeof(*entry_out));
    reader->error[0] = '\0';
    if (reader->entries_remaining == 0) return 0;

    uint8_t header[SST_RECORD_HEADER_SIZE];
    if (read_exact_at(reader->fd, header, sizeof(header), reader->offset) != 0) {
        set_reader_error(reader, "cannot read SSTable record header");
        return -1;
    }
    uint32_t key_size = wisedb_load_u32_le(header);
    uint32_t value_size = wisedb_load_u32_le(header + 4);
    uint64_t sequence = wisedb_load_u64_le(header + 8);
    uint32_t flags = wisedb_load_u32_le(header + 16);
    uint32_t stored_crc = wisedb_load_u32_le(header + 20);
    if (key_size == 0 || key_size > reader->max_key_size || value_size > reader->max_value_size
        || sequence == 0 || sequence > reader->checkpoint_sequence || (flags & ~SST_TOMBSTONE) != 0
        || ((flags & SST_TOMBSTONE) && value_size != 0)) {
        set_reader_error(reader, "invalid SSTable record metadata during iteration");
        return -1;
    }
    uint64_t data_size = (uint64_t)key_size + (uint64_t)value_size;
    if (data_size > (uint64_t)(reader->data_end - reader->offset - (off_t)sizeof(header))) {
        set_reader_error(reader, "SSTable record exceeds table bounds during iteration");
        return -1;
    }
    uint8_t *key = (uint8_t *)malloc(key_size);
    uint8_t *value = value_size ? (uint8_t *)malloc(value_size) : NULL;
    if (!key || (value_size > 0 && !value)) {
        free(key);
        free(value);
        set_reader_error(reader, "out of memory reading SSTable entry");
        return -1;
    }
    off_t key_offset = reader->offset + (off_t)sizeof(header);
    off_t value_offset = key_offset + (off_t)key_size;
    if (read_exact_at(reader->fd, key, key_size, key_offset) != 0
        || (value_size > 0 && read_exact_at(reader->fd, value, value_size, value_offset) != 0)) {
        free(key);
        free(value);
        set_reader_error(reader, "cannot read SSTable entry data");
        return -1;
    }
    uint32_t crc_state = wisedb_crc32c_extend(UINT32_MAX, header, 20);
    crc_state = wisedb_crc32c_extend(crc_state, key, key_size);
    crc_state = wisedb_crc32c_extend(crc_state, value, value_size);
    if (wisedb_crc32c_finish(crc_state) != stored_crc) {
        free(key);
        free(value);
        set_reader_error(reader, "SSTable record checksum mismatch during iteration");
        return -1;
    }

    if (reader->previous_key
        && compare_keys(reader->previous_key, reader->previous_key_size, key, key_size) >= 0) {
        free(key);
        free(value);
        set_reader_error(reader, "SSTable key order changed during iteration");
        return -1;
    }
    uint8_t *previous = (uint8_t *)malloc(key_size);
    if (!previous) {
        free(key);
        free(value);
        set_reader_error(reader, "out of memory retaining SSTable key order");
        return -1;
    }
    memcpy(previous, key, key_size);
    free(reader->previous_key);
    reader->previous_key = previous;
    reader->previous_key_size = key_size;

    entry_out->key = key;
    entry_out->key_size = key_size;
    entry_out->value = value;
    entry_out->value_size = value_size;
    entry_out->sequence = sequence;
    entry_out->tombstone = (flags & SST_TOMBSTONE) != 0;
    reader->offset += (off_t)sizeof(header) + (off_t)key_size + (off_t)value_size;
    reader->entries_remaining--;
    return 1;
}

uint64_t wisedb_sstable_reader_checkpoint(const wisedb_sstable_reader *reader) {
    return reader ? reader->checkpoint_sequence : 0;
}

uint64_t wisedb_sstable_reader_entry_count(const wisedb_sstable_reader *reader) {
    return reader ? reader->entry_count : 0;
}

void wisedb_sstable_entry_free(wisedb_sstable_entry *entry) {
    if (!entry) return;
    free((void *)entry->key);
    free((void *)entry->value);
    memset(entry, 0, sizeof(*entry));
}

void wisedb_sstable_reader_close(wisedb_sstable_reader *reader) {
    if (!reader) return;
    if (reader->fd >= 0) close(reader->fd);
    free(reader->previous_key);
    free(reader);
}

const char *wisedb_sstable_last_error(const wisedb_sstable_reader *reader) {
    if (!reader || !reader->error[0]) return "";
    return reader->error;
}
