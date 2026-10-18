#define _DARWIN_C_SOURCE 1
#define _FILE_OFFSET_BITS 64
#define _POSIX_C_SOURCE 200809L

#include "wal.h"
#include "codec.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include "fault_injection.h"

#define WAL_HEADER_SIZE 28u
#define WAL_MAGIC "WDBWAL1\0"
#define WAL_VERSION_OFFSET 8u
#define WAL_LENGTH_OFFSET 12u
#define WAL_SEQUENCE_OFFSET 16u
#define WAL_CRC_OFFSET 24u
#define WAL_V2_HEADER_SIZE 32u
#define WAL_V2_HEADER_CRC_OFFSET 28u

struct wisedb_wal {
    int fd;
    size_t max_record_size;
    off_t end_offset;
    uint64_t last_sequence;
    int poisoned;
    wisedb_wal_outcome last_append_outcome;
    char error[256];
};

static void set_error(wisedb_wal *wal, const char *message) {
    if (!wal) return;
    (void)snprintf(wal->error, sizeof(wal->error), "%s", message);
}

static void set_errno_error(wisedb_wal *wal, const char *operation) {
    if (!wal) return;
    (void)snprintf(wal->error, sizeof(wal->error), "%s: %s", operation, strerror(errno));
}

static int read_exact_at(int fd, uint8_t *buffer, size_t size, off_t offset, size_t *read_out);

static int crc32c_file_range(
    int fd,
    off_t offset,
    size_t size,
    uint32_t initial_crc,
    uint32_t *crc_out
) {
    uint8_t chunk[64 * 1024];
    uint32_t crc = initial_crc;
    size_t remaining = size;
    while (remaining > 0) {
        size_t request = remaining < sizeof(chunk) ? remaining : sizeof(chunk);
        size_t got = 0;
        if (read_exact_at(fd, chunk, request, offset, &got) != 0) return -1;
        if (got != request) {
            errno = EIO;
            return -1;
        }
        crc = wisedb_crc32c_extend(crc, chunk, got);
        offset += (off_t)got;
        remaining -= got;
    }
    *crc_out = crc;
    return 0;
}

static int read_exact_at(int fd, uint8_t *buffer, size_t size, off_t offset, size_t *read_out) {
    size_t total = 0;
    while (total < size) {
        ssize_t n = pread(fd, buffer + total, size - total, offset + (off_t)total);
        if (n > 0) {
            total += (size_t)n;
            continue;
        }
        if (n == 0) break;
        if (errno == EINTR) continue;
        if (read_out) *read_out = total;
        return -1;
    }
    if (read_out) *read_out = total;
    return 0;
}

static int write_all(int fd, const uint8_t *buffer, size_t size) {
    size_t total = 0;
    while (total < size) {
        ssize_t n = write(fd, buffer + total, size - total);
        if (n > 0) {
            total += (size_t)n;
            continue;
        }
        if (n < 0 && errno == EINTR) continue;
        if (n == 0) errno = EIO;
        return -1;
    }
    return 0;
}

static int sync_parent_directory(const char *path, wisedb_wal *wal) {
    char *directory = strdup(path);
    if (!directory) {
        set_error(wal, "out of memory resolving WAL parent directory");
        return -1;
    }
    char *slash = strrchr(directory, '/');
    if (!slash) {
        free(directory);
        directory = strdup(".");
        if (!directory) {
            set_error(wal, "out of memory resolving WAL parent directory");
            return -1;
        }
    } else if (slash == directory) {
        slash[1] = '\0';
    } else {
        *slash = '\0';
    }

    int dir_fd = open(directory, O_RDONLY);
    free(directory);
    if (dir_fd < 0) {
        set_errno_error(wal, "open WAL parent directory");
        return -1;
    }
    int result = fsync(dir_fd);
    int saved_errno = errno;
    close(dir_fd);
    if (result != 0) {
        errno = saved_errno;
        set_errno_error(wal, "synchronize WAL parent directory");
        return -1;
    }
    return 0;
}

static int validate_and_repair_tail(wisedb_wal *wal) {
    struct stat st;
    if (fstat(wal->fd, &st) != 0) {
        set_errno_error(wal, "fstat WAL");
        return -1;
    }
    if (!S_ISREG(st.st_mode) || st.st_size < 0) {
        set_error(wal, "WAL path is not a regular file");
        return -1;
    }

    off_t offset = 0;
    uint64_t expected_sequence = 1;
    uint8_t header[WAL_V2_HEADER_SIZE];
    while (offset < st.st_size) {
        off_t remaining = st.st_size - offset;
        size_t magic_bytes = remaining < 8 ? (size_t)remaining : 8;
        size_t got = 0;
        if (read_exact_at(wal->fd, header, magic_bytes, offset, &got) != 0) {
            set_errno_error(wal, "read WAL magic");
            return -1;
        }
        if (got != magic_bytes || memcmp(header, WAL_MAGIC, magic_bytes) != 0) {
            set_error(wal, "invalid WAL magic or ambiguous trailing bytes");
            return -1;
        }
        if (remaining < 8) {
            set_error(wal, "incomplete WAL magic at tail; refusing ambiguous repair");
            return -1;
        }
        if (remaining < (off_t)WAL_HEADER_SIZE) {
            set_error(wal, "incomplete WAL header at tail; refusing ambiguous repair");
            return -1;
        }

        got = 0;
        if (read_exact_at(wal->fd, header, WAL_HEADER_SIZE, offset, &got) != 0) {
            set_errno_error(wal, "read WAL header");
            return -1;
        }
        if (got != WAL_HEADER_SIZE) {
            set_error(wal, "short read while validating WAL header");
            return -1;
        }
        uint32_t version = wisedb_load_u32_le(header + WAL_VERSION_OFFSET);
        uint32_t payload_size = wisedb_load_u32_le(header + WAL_LENGTH_OFFSET);
        uint64_t sequence = wisedb_load_u64_le(header + WAL_SEQUENCE_OFFSET);
        uint32_t expected_crc = wisedb_load_u32_le(header + WAL_CRC_OFFSET);

        if (version != WISEDB_WAL_FORMAT_VERSION) {
            if (version != WISEDB_WAL_LEGACY_FORMAT_VERSION) {
                set_error(wal, "unsupported WAL format version");
                return -1;
            }
        }
        size_t header_size = version == WISEDB_WAL_FORMAT_VERSION
            ? WAL_V2_HEADER_SIZE : WAL_HEADER_SIZE;
        if (remaining < (off_t)header_size) {
            if (version == WISEDB_WAL_LEGACY_FORMAT_VERSION) {
                set_error(wal, "incomplete legacy WAL header is ambiguous; refusing repair");
                return -1;
            }
            set_error(wal, "incomplete version-2 WAL header at tail; refusing ambiguous repair");
            return -1;
        }
        if (version == WISEDB_WAL_FORMAT_VERSION) {
            got = 0;
            if (read_exact_at(wal->fd, header, header_size, offset, &got) != 0 || got != header_size) {
                set_error(wal, "short read while validating version-2 WAL header");
                return -1;
            }
            uint32_t header_crc = wisedb_crc32c(header, WAL_V2_HEADER_CRC_OFFSET);
            if (header_crc != wisedb_load_u32_le(header + WAL_V2_HEADER_CRC_OFFSET)) {
                set_error(wal, "WAL header checksum mismatch; refusing tail repair");
                return -1;
            }
        }
        if (payload_size > wal->max_record_size) {
            set_error(wal, "WAL record exceeds configured size limit");
            return -1;
        }
        if ((uintmax_t)payload_size > (uintmax_t)INT64_MAX - (uintmax_t)header_size) {
            set_error(wal, "WAL record length overflows file offsets");
            return -1;
        }

        if ((uintmax_t)offset > (uintmax_t)INT64_MAX
            || (uintmax_t)payload_size + (uintmax_t)header_size
                > (uintmax_t)INT64_MAX - (uintmax_t)offset) {
            set_error(wal, "WAL frame offset overflows during replay");
            return -1;
        }
        if (sequence != expected_sequence || sequence == 0) {
            set_error(wal, "WAL sequence is invalid or discontinuous");
            return -1;
        }
        off_t frame_size = (off_t)header_size + (off_t)payload_size;
        if (remaining < frame_size) {
            if (version == WISEDB_WAL_LEGACY_FORMAT_VERSION) {
                set_error(wal, "incomplete legacy WAL payload is ambiguous; refusing repair");
                return -1;
            }
            break;
        }
        if ((uintmax_t)offset + (uintmax_t)frame_size > (uintmax_t)INT64_MAX) {
            set_error(wal, "WAL file offset overflow");
            return -1;
        }

        uint32_t crc_state = wisedb_crc32c_extend(UINT32_MAX, header + WAL_VERSION_OFFSET, 16);
        if (crc32c_file_range(
                wal->fd,
                offset + (off_t)header_size,
                payload_size,
                crc_state,
                &crc_state) != 0) {
            set_errno_error(wal, "read WAL payload during validation");
            return -1;
        }
        uint32_t crc = wisedb_crc32c_finish(crc_state);
        if (crc != expected_crc) {
            set_error(wal, "WAL checksum mismatch in complete frame");
            return -1;
        }

        offset += frame_size;
        wal->last_sequence = sequence;
        if (expected_sequence == UINT64_MAX) {
            if (offset != st.st_size) {
                set_error(wal, "WAL sequence exhausted");
                return -1;
            }
        } else {
            expected_sequence++;
        }
    }

    if (offset < st.st_size) {
        if (ftruncate(wal->fd, offset) != 0) {
            set_errno_error(wal, "truncate incomplete WAL tail");
            return -1;
        }
        if (fsync(wal->fd) != 0) {
            set_errno_error(wal, "synchronize repaired WAL");
            return -1;
        }
    }
    wal->end_offset = offset;
    return 0;
}

wisedb_wal *wisedb_wal_open_ex(
    const char *path,
    size_t max_record_size,
    char *error_out,
    size_t error_capacity
) {
    if (error_out && error_capacity > 0) error_out[0] = '\0';
    if (!path || !path[0] || max_record_size == 0 || max_record_size > UINT32_MAX) {
        if (error_out && error_capacity > 0) {
            (void)snprintf(error_out, error_capacity, "%s", "invalid WAL open arguments");
        }
        return NULL;
    }

    int open_flags = O_RDWR | O_CREAT | O_APPEND;
#ifdef O_NOFOLLOW
    open_flags |= O_NOFOLLOW;
#endif
#ifdef O_CLOEXEC
    open_flags |= O_CLOEXEC;
#endif
    int fd = open(path, open_flags, 0600);
    if (fd < 0) {
        if (error_out && error_capacity > 0) {
            (void)snprintf(error_out, error_capacity, "open WAL: %s", strerror(errno));
        }
        return NULL;
    }

    struct stat path_stat;
    if (fstat(fd, &path_stat) != 0) {
        int saved_errno = errno;
        close(fd);
        if (error_out && error_capacity > 0) {
            (void)snprintf(error_out, error_capacity, "stat WAL: %s", strerror(saved_errno));
        }
        return NULL;
    }
    if (!S_ISREG(path_stat.st_mode)) {
        close(fd);
        if (error_out && error_capacity > 0) {
            (void)snprintf(error_out, error_capacity, "%s", "WAL path is not a regular file");
        }
        return NULL;
    }
    if (flock(fd, LOCK_EX | LOCK_NB) != 0) {
        int saved_errno = errno;
        close(fd);
        if (error_out && error_capacity > 0) {
            (void)snprintf(error_out, error_capacity, "lock WAL: %s", strerror(saved_errno));
        }
        return NULL;
    }
    wisedb_wal *wal = (wisedb_wal *)calloc(1, sizeof(*wal));
    if (!wal) {
        int saved_errno = errno;
        close(fd);
        if (error_out && error_capacity > 0) {
            (void)snprintf(error_out, error_capacity, "allocate WAL handle: %s", strerror(saved_errno));
        }
        return NULL;
    }
    wal->fd = fd;
    wal->max_record_size = max_record_size;

    if (validate_and_repair_tail(wal) != 0) {
        if (error_out && error_capacity > 0) {
            (void)snprintf(error_out, error_capacity, "%s", wal->error);
        }
        wisedb_wal_close(wal);
        return NULL;
    }
    if (fchmod(fd, S_IRUSR | S_IWUSR) != 0) {
        set_errno_error(wal, "restrict WAL permissions");
    } else if (fsync(fd) != 0) {
        set_errno_error(wal, "synchronize WAL metadata");
    } else if (sync_parent_directory(path, wal) != 0) {
        /* sync_parent_directory sets the detailed error */
    }
    if (wal->error[0]) {
        if (error_out && error_capacity > 0) {
            (void)snprintf(error_out, error_capacity, "%s", wal->error);
        }
        wisedb_wal_close(wal);
        return NULL;
    }
    wal->error[0] = '\0';
    return wal;
}

wisedb_wal *wisedb_wal_open(const char *path, size_t max_record_size) {
    return wisedb_wal_open_ex(path, max_record_size, NULL, 0);
}

void wisedb_wal_close(wisedb_wal *wal) {
    if (!wal) return;
    if (wal->fd >= 0) close(wal->fd);
    free(wal);
}

int wisedb_wal_append(
    wisedb_wal *wal,
    const uint8_t *payload,
    size_t payload_size,
    uint64_t *sequence_out
) {
    if (!wal) return -1;
    wal->last_append_outcome = WISEDB_WAL_OUTCOME_NOT_APPENDED;
    if (!payload && payload_size > 0) {
        set_error(wal, "WAL payload is NULL with nonzero size");
        return -1;
    }
    wal->error[0] = '\0';
    if (sequence_out) *sequence_out = 0;
    if (wal->poisoned) {
        set_error(wal, "WAL requires close and recovery after a previous write error");
        return -1;
    }
    if (payload_size > wal->max_record_size || payload_size > UINT32_MAX) {
        set_error(wal, "WAL record exceeds configured size limit");
        return -1;
    }
    if (wal->last_sequence == UINT64_MAX) {
        set_error(wal, "WAL sequence exhausted");
        return -1;
    }
    uintmax_t frame_size = (uintmax_t)WAL_V2_HEADER_SIZE + (uintmax_t)payload_size;
    if ((uintmax_t)wal->end_offset + frame_size > (uintmax_t)INT64_MAX) {
        set_error(wal, "WAL file offset limit exceeded");
        return -1;
    }

    uint8_t header[WAL_V2_HEADER_SIZE] = {0};
    memcpy(header, WAL_MAGIC, 8);
    wisedb_store_u32_le(header + WAL_VERSION_OFFSET, WISEDB_WAL_FORMAT_VERSION);
    wisedb_store_u32_le(header + WAL_LENGTH_OFFSET, (uint32_t)payload_size);
    uint64_t sequence = wal->last_sequence + 1;
    wisedb_store_u64_le(header + WAL_SEQUENCE_OFFSET, sequence);
    uint32_t crc_state = wisedb_crc32c_extend(UINT32_MAX, header + WAL_VERSION_OFFSET, 16);
    crc_state = wisedb_crc32c_extend(crc_state, payload, payload_size);
    uint32_t crc = wisedb_crc32c_finish(crc_state);
    wisedb_store_u32_le(header + WAL_CRC_OFFSET, crc);
    wisedb_store_u32_le(header + WAL_V2_HEADER_CRC_OFFSET,
                         wisedb_crc32c(header, WAL_V2_HEADER_CRC_OFFSET));

    if (write_all(wal->fd, header, sizeof(header)) != 0
        || (payload_size > 0 && write_all(wal->fd, payload, payload_size) != 0)) {
        set_errno_error(wal, "append WAL record");
        wal->poisoned = 1;
        wal->last_append_outcome = WISEDB_WAL_OUTCOME_UNKNOWN;
        return -1;
    }
    if (fsync(wal->fd) != 0) {
        set_errno_error(wal, "synchronize WAL record");
        wal->poisoned = 1;
        wal->last_append_outcome = WISEDB_WAL_OUTCOME_UNKNOWN;
        return -1;
    }

    wal->end_offset += (off_t)sizeof(header) + (off_t)payload_size;
    wal->last_sequence = sequence;
    wal->last_append_outcome = WISEDB_WAL_OUTCOME_DURABLE;
    if (sequence_out) *sequence_out = sequence;
    return 0;
}

int wisedb_wal_replay(
    wisedb_wal *wal,
    wisedb_wal_replay_fn callback,
    void *context
) {
    if (!wal || !callback) return -1;
    wal->error[0] = '\0';
    if (wal->poisoned) {
        set_error(wal, "WAL requires close and recovery after a previous write error");
        return -1;
    }
    off_t offset = 0;
    const off_t replay_end = wal->end_offset;
    uint64_t expected_sequence = 1;
    uint8_t header[WAL_V2_HEADER_SIZE];

    while (offset < replay_end) {
        size_t got = 0;
        if (read_exact_at(wal->fd, header, WAL_HEADER_SIZE, offset, &got) != 0 || got != WAL_HEADER_SIZE) {
            if (got != WAL_HEADER_SIZE) set_error(wal, "short read during WAL replay");
            else set_errno_error(wal, "read WAL during replay");
            return -1;
        }
        uint32_t version = wisedb_load_u32_le(header + WAL_VERSION_OFFSET);
        if (memcmp(header, WAL_MAGIC, 8) != 0
            || (version != WISEDB_WAL_FORMAT_VERSION && version != WISEDB_WAL_LEGACY_FORMAT_VERSION)) {
            set_error(wal, "invalid WAL frame during replay");
            return -1;
        }
        size_t header_size = version == WISEDB_WAL_FORMAT_VERSION
            ? WAL_V2_HEADER_SIZE : WAL_HEADER_SIZE;
        if (version == WISEDB_WAL_FORMAT_VERSION) {
            if (replay_end - offset < (off_t)header_size
                || read_exact_at(wal->fd, header, header_size, offset, &got) != 0 || got != header_size) {
                set_error(wal, "truncated version-2 WAL header during replay");
                return -1;
            }
            if (wisedb_crc32c(header, WAL_V2_HEADER_CRC_OFFSET)
                != wisedb_load_u32_le(header + WAL_V2_HEADER_CRC_OFFSET)) {
                set_error(wal, "WAL header checksum changed during replay");
                return -1;
            }
        }
        uint32_t payload_size = wisedb_load_u32_le(header + WAL_LENGTH_OFFSET);
        uint64_t sequence = wisedb_load_u64_le(header + WAL_SEQUENCE_OFFSET);
        uint32_t expected_crc = wisedb_load_u32_le(header + WAL_CRC_OFFSET);
        if (payload_size > wal->max_record_size) {
            set_error(wal, "WAL record exceeds configured size limit during replay");
            return -1;
        }
        if (sequence != expected_sequence || sequence == 0) {
            set_error(wal, "WAL sequence changed during replay");
            return -1;
        }
        off_t frame_size = (off_t)header_size + (off_t)payload_size;
        if (replay_end < offset || replay_end - offset < frame_size) {
            set_error(wal, "WAL frame is truncated during replay");
            return -1;
        }
        uint8_t *payload = NULL;
        if (payload_size > 0) {
            payload = (uint8_t *)malloc(payload_size);
            if (!payload) {
                set_error(wal, "out of memory replaying WAL");
                return -1;
            }
            if (read_exact_at(wal->fd, payload, payload_size, offset + (off_t)header_size, &got) != 0
                || got != payload_size) {
                free(payload);
                if (got != payload_size) set_error(wal, "short read during WAL payload replay");
                else set_errno_error(wal, "read WAL payload during replay");
                return -1;
            }
        }
        uint32_t crc_state = wisedb_crc32c_extend(UINT32_MAX, header + WAL_VERSION_OFFSET, 16);
        crc_state = wisedb_crc32c_extend(crc_state, payload, payload_size);
        if (wisedb_crc32c_finish(crc_state) != expected_crc) {
            free(payload);
            set_error(wal, "WAL checksum changed during replay");
            return -1;
        }
        int callback_result = callback(sequence, payload, payload_size, context);
        free(payload);
        if (callback_result != 0) {
            set_error(wal, "WAL replay callback failed");
            return -1;
        }
        offset += frame_size;
        if (expected_sequence == UINT64_MAX && offset < replay_end) {
            set_error(wal, "WAL sequence exhausted during replay");
            return -1;
        }
        expected_sequence++;
    }
    return 0;
}

const char *wisedb_wal_last_error(const wisedb_wal *wal) {
    if (!wal || !wal->error[0]) return "";
    return wal->error;
}

size_t wisedb_wal_max_record_size(const wisedb_wal *wal) {
    return wal ? wal->max_record_size : 0;
}

wisedb_wal_outcome wisedb_wal_last_append_outcome(const wisedb_wal *wal) {
    return wal ? wal->last_append_outcome : WISEDB_WAL_OUTCOME_UNKNOWN;
}
