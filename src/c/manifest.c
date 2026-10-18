#define _DARWIN_C_SOURCE 1
#define _FILE_OFFSET_BITS 64
#define _POSIX_C_SOURCE 200809L

#include "manifest.h"
#include "codec.h"
#include "sstable.h"

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

#define MANIFEST_SIZE 48u
#define MANIFEST_MAGIC "WDBMAN1\0"
#define MANIFEST_CRC_OFFSET 44u

static void set_error(char *out, size_t capacity, const char *message) {
    if (out && capacity > 0) (void)snprintf(out, capacity, "%s", message ? message : "");
}

static void set_errno_error(char *out, size_t capacity, const char *operation) {
    if (out && capacity > 0) (void)snprintf(out, capacity, "%s: %s", operation, strerror(errno));
}

static int write_all(int fd, const uint8_t *data, size_t size) {
    size_t offset = 0;
    while (offset < size) {
        ssize_t amount = write(fd, data + offset, size - offset);
        if (amount > 0) {
            offset += (size_t)amount;
            continue;
        }
        if (amount < 0 && errno == EINTR) continue;
        if (amount == 0) errno = EIO;
        return -1;
    }
    return 0;
}

static int read_all(int fd, uint8_t *data, size_t size) {
    size_t offset = 0;
    while (offset < size) {
        ssize_t amount = read(fd, data + offset, size - offset);
        if (amount > 0) {
            offset += (size_t)amount;
            continue;
        }
        if (amount < 0 && errno == EINTR) continue;
        if (amount == 0) errno = EIO;
        return -1;
    }
    return 0;
}

static int path_join(char *out, size_t capacity, const char *directory, const char *name) {
    size_t directory_size = strlen(directory);
    int needs_slash = directory_size > 0 && directory[directory_size - 1] != '/';
    int written = snprintf(out, capacity, "%s%s%s", directory, needs_slash ? "/" : "", name);
    return written >= 0 && (size_t)written < capacity ? 0 : -1;
}

static int table_name_is_valid(const char *name) {
    if (!name || !name[0] || name[0] == '.') return 0;
    size_t size = strlen(name);
    if (size >= sizeof(((wisedb_manifest *)0)->table_name)) return 0;
    if (strstr(name, "..") || strchr(name, '/') || strchr(name, '\\')) return 0;
    return 1;
}

static int format_table_name(uint64_t table_id, char *out, size_t capacity) {
    int written = snprintf(out, capacity, "table-%020" PRIu64 ".sst", table_id);
    return written >= 0 && (size_t)written < capacity ? 0 : -1;
}

int wisedb_manifest_table_name(uint64_t table_id, char *out, size_t capacity) {
    if (table_id == 0 || !out || capacity == 0) return -1;
    return format_table_name(table_id, out, capacity);
}

static int sync_directory(const char *directory, char *error_out, size_t error_capacity) {
    int fd = open(directory, O_RDONLY
#ifdef O_CLOEXEC
        | O_CLOEXEC
#endif
    );
    if (fd < 0) {
        set_errno_error(error_out, error_capacity, "open manifest directory");
        return -1;
    }
    int result = fsync(fd);
    int saved_errno = errno;
    close(fd);
    if (result != 0) {
        errno = saved_errno;
        set_errno_error(error_out, error_capacity, "synchronize manifest directory");
        return -1;
    }
    return 0;
}

static int read_raw_manifest(const char *directory, wisedb_manifest *manifest, char *error_out, size_t error_capacity) {
    char path[PATH_MAX];
    if (path_join(path, sizeof(path), directory, "MANIFEST") != 0) {
        set_error(error_out, error_capacity, "manifest path is too long");
        return -1;
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
        set_errno_error(error_out, error_capacity, "open manifest");
        return -1;
    }
    struct stat st;
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size != (off_t)MANIFEST_SIZE) {
        close(fd);
        set_error(error_out, error_capacity, "manifest must be a fixed-size regular file");
        return -1;
    }
    uint8_t bytes[MANIFEST_SIZE];
    if (read_all(fd, bytes, sizeof(bytes)) != 0) {
        int saved_errno = errno;
        close(fd);
        errno = saved_errno;
        set_errno_error(error_out, error_capacity, "read manifest");
        return -1;
    }
    close(fd);
    if (memcmp(bytes, MANIFEST_MAGIC, 8) != 0
        || wisedb_load_u32_le(bytes + 8) != 1
        || wisedb_crc32c(bytes, MANIFEST_CRC_OFFSET) != wisedb_load_u32_le(bytes + MANIFEST_CRC_OFFSET)) {
        set_error(error_out, error_capacity, "manifest magic, version, or checksum is invalid");
        return -1;
    }
    memset(manifest, 0, sizeof(*manifest));
    manifest->generation = wisedb_load_u64_le(bytes + 12);
    manifest->checkpoint_sequence = wisedb_load_u64_le(bytes + 20);
    manifest->table_id = wisedb_load_u64_le(bytes + 28);
    manifest->entry_count = wisedb_load_u64_le(bytes + 36);
    if (manifest->generation == 0 || manifest->table_id == 0
        || format_table_name(manifest->table_id, manifest->table_name, sizeof(manifest->table_name)) != 0) {
        set_error(error_out, error_capacity, "manifest generation or table id is invalid");
        return -1;
    }
    return 0;
}

int wisedb_manifest_read(
    const char *directory,
    wisedb_manifest *manifest_out,
    char *error_out,
    size_t error_capacity
) {
    if (error_out && error_capacity > 0) error_out[0] = '\0';
    if (!directory || !directory[0] || !manifest_out) {
        set_error(error_out, error_capacity, "invalid manifest read arguments");
        return -1;
    }
    wisedb_manifest parsed;
    memset(&parsed, 0, sizeof(parsed));
    if (read_raw_manifest(directory, &parsed, error_out, error_capacity) != 0) return -1;

    char table_path[PATH_MAX];
    if (path_join(table_path, sizeof(table_path), directory, parsed.table_name) != 0) {
        set_error(error_out, error_capacity, "manifest table path is too long");
        return -1;
    }
    wisedb_sstable_reader *reader = wisedb_sstable_reader_open(
        table_path,
        WISEDB_SSTABLE_DEFAULT_MAX_KEY_SIZE,
        WISEDB_SSTABLE_DEFAULT_MAX_VALUE_SIZE,
        error_out,
        error_capacity
    );
    if (!reader) return -1;
    uint64_t table_checkpoint = wisedb_sstable_reader_checkpoint(reader);
    uint64_t table_entry_count = wisedb_sstable_reader_entry_count(reader);
    int valid = table_checkpoint == parsed.checkpoint_sequence
        && table_entry_count == parsed.entry_count;
    wisedb_sstable_reader_close(reader);
    if (!valid) {
        if (error_out && error_capacity > 0) {
            (void)snprintf(error_out, error_capacity,
                "manifest/SST mismatch (seq=%" PRIu64 "/%" PRIu64 ", entries=%" PRIu64 "/%" PRIu64 ")",
                parsed.checkpoint_sequence, table_checkpoint,
                parsed.entry_count, table_entry_count);
        }
        return -1;
    }
    *manifest_out = parsed;
    return 0;
}

int wisedb_manifest_publish(
    const char *directory,
    const wisedb_manifest *manifest,
    char *error_out,
    size_t error_capacity
) {
    if (error_out && error_capacity > 0) error_out[0] = '\0';
    char expected_table_name[128];
    if (!directory || !directory[0] || !manifest || manifest->generation == 0 || manifest->table_id == 0
        || format_table_name(manifest->table_id, expected_table_name, sizeof(expected_table_name)) != 0
        || strcmp(expected_table_name, manifest->table_name) != 0
        || !table_name_is_valid(manifest->table_name)) {
        set_error(error_out, error_capacity, "invalid manifest publish arguments");
        return -1;
    }

    char table_path[PATH_MAX];
    if (path_join(table_path, sizeof(table_path), directory, manifest->table_name) != 0) {
        set_error(error_out, error_capacity, "manifest table path is too long");
        return -1;
    }
    wisedb_sstable_reader *table = wisedb_sstable_reader_open(
        table_path,
        WISEDB_SSTABLE_DEFAULT_MAX_KEY_SIZE,
        WISEDB_SSTABLE_DEFAULT_MAX_VALUE_SIZE,
        error_out,
        error_capacity
    );
    if (!table) return -1;
    int table_valid = wisedb_sstable_reader_checkpoint(table) == manifest->checkpoint_sequence
        && wisedb_sstable_reader_entry_count(table) == manifest->entry_count;
    wisedb_sstable_reader_close(table);
    if (!table_valid) {
        set_error(error_out, error_capacity, "manifest checkpoint does not match SSTable");
        return -1;
    }

    char manifest_path[PATH_MAX];
    char temporary_path[PATH_MAX];
    if (path_join(manifest_path, sizeof(manifest_path), directory, "MANIFEST") != 0
        || path_join(temporary_path, sizeof(temporary_path), directory, "MANIFEST.tmp.XXXXXX") != 0) {
        set_error(error_out, error_capacity, "manifest path is too long");
        return -1;
    }

    struct stat current_stat;
    if (lstat(manifest_path, &current_stat) == 0) {
        if (!S_ISREG(current_stat.st_mode)) {
            set_error(error_out, error_capacity, "current manifest is not a regular file");
            return -1;
        }
        wisedb_manifest current;
        if (wisedb_manifest_read(directory, &current, error_out, error_capacity) != 0) return -1;
        if (current.generation == UINT64_MAX || manifest->generation != current.generation + 1
            || manifest->checkpoint_sequence < current.checkpoint_sequence) {
            set_error(error_out, error_capacity, "manifest generation or checkpoint is not monotonic");
            return -1;
        }
    } else if (errno != ENOENT || manifest->generation != 1) {
        set_error(error_out, error_capacity, "first manifest generation must be 1");
        return -1;
    }

    int fd = mkstemp(temporary_path);
    if (fd < 0) {
        set_errno_error(error_out, error_capacity, "create temporary manifest");
        return -1;
    }
    if (fchmod(fd, S_IRUSR | S_IWUSR) != 0) {
        int saved_errno = errno;
        close(fd);
        unlink(temporary_path);
        errno = saved_errno;
        set_errno_error(error_out, error_capacity, "restrict temporary manifest permissions");
        return -1;
    }
    uint8_t bytes[MANIFEST_SIZE] = {0};
    memcpy(bytes, MANIFEST_MAGIC, 8);
    wisedb_store_u32_le(bytes + 8, 1);
    wisedb_store_u64_le(bytes + 12, manifest->generation);
    wisedb_store_u64_le(bytes + 20, manifest->checkpoint_sequence);
    wisedb_store_u64_le(bytes + 28, manifest->table_id);
    wisedb_store_u64_le(bytes + 36, manifest->entry_count);
    wisedb_store_u32_le(bytes + MANIFEST_CRC_OFFSET, wisedb_crc32c(bytes, MANIFEST_CRC_OFFSET));
    if (write_all(fd, bytes, sizeof(bytes)) != 0) {
        int saved_errno = errno;
        close(fd);
        unlink(temporary_path);
        errno = saved_errno;
        set_errno_error(error_out, error_capacity, "write temporary manifest");
        return -1;
    }
    if (fsync(fd) != 0) {
        int saved_errno = errno;
        close(fd);
        unlink(temporary_path);
        errno = saved_errno;
        set_errno_error(error_out, error_capacity, "synchronize temporary manifest");
        return -1;
    }
    if (close(fd) != 0) {
        int saved_errno = errno;
        unlink(temporary_path);
        errno = saved_errno;
        set_errno_error(error_out, error_capacity, "close temporary manifest");
        return -1;
    }
    if (rename(temporary_path, manifest_path) != 0) {
        int saved_errno = errno;
        unlink(temporary_path);
        errno = saved_errno;
        set_errno_error(error_out, error_capacity, "publish manifest");
        return -1;
    }
    return sync_directory(directory, error_out, error_capacity);
}
