#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE 1

#include "wisedb_internal.h"

#ifdef _WIN32
#include <windows.h>
#else
#include <dirent.h>
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
#endif

#include "fault_injection.h"

#ifndef _WIN32
static int open_parent_fd(
    wisedb_engine *eng,
    const char *rel_path,
    int create_parents,
    int *parent_fd_out,
    char *leaf_out,
    size_t leaf_capacity
) {
    if (!eng || eng->root_fd < 0 || !rel_path || !parent_fd_out || !leaf_out || leaf_capacity == 0
        || wisedb_validate_relative_path(eng, rel_path) != 0) return -1;
    char path[WISEDB_MAX_PATH];
    size_t path_size = strlen(rel_path);
    if (path_size >= sizeof(path)) return -1;
    memcpy(path, rel_path, path_size + 1);
    char *slash = strrchr(path, '/');
    int current_fd = dup(eng->root_fd);
    if (current_fd < 0) return -1;
    if (slash) {
        *slash = '\0';
        const char *leaf = slash + 1;
        char *component = path;
        while (*component) {
            char *next = strchr(component, '/');
            if (next) *next = '\0';
            int next_fd = openat(current_fd, component, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            if (next_fd < 0 && errno == ENOENT && create_parents) {
                if (mkdirat(current_fd, component, 0755) != 0 && errno != EEXIST) {
                    close(current_fd);
                    return -1;
                }
                next_fd = openat(current_fd, component, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            }
            if (next_fd < 0) {
                close(current_fd);
                return -1;
            }
            close(current_fd);
            current_fd = next_fd;
            if (!next) break;
            component = next + 1;
        }
        size_t leaf_size = strlen(leaf);
        if (leaf_size == 0 || leaf_size >= leaf_capacity) {
            close(current_fd);
            return -1;
        }
        memcpy(leaf_out, leaf, leaf_size + 1);
        *parent_fd_out = current_fd;
        return 0;
    }
    size_t leaf_size = strlen(path);
    if (leaf_size == 0 || leaf_size >= leaf_capacity) {
        close(current_fd);
        return -1;
    }
    memcpy(leaf_out, path, leaf_size + 1);
    *parent_fd_out = current_fd;
    return 0;
}

static int open_regular_relative(wisedb_engine *eng, const char *rel_path, int flags) {
    int parent_fd = -1;
    char leaf[WISEDB_MAX_PATH];
    if (open_parent_fd(eng, rel_path, 0, &parent_fd, leaf, sizeof(leaf)) != 0) return -1;
    int fd = openat(parent_fd, leaf, flags | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
    int saved_errno = errno;
    close(parent_fd);
    if (fd < 0) {
        errno = saved_errno;
        return -1;
    }
    struct stat st;
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode)) {
        saved_errno = errno ? errno : EINVAL;
        close(fd);
        errno = saved_errno;
        return -1;
    }
    return fd;
}

static int write_all_fd(int fd, const uint8_t *data, size_t size) {
    size_t offset = 0;
    while (offset < size) {
        ssize_t written = write(fd, data + offset, size - offset);
        if (written > 0) {
            offset += (size_t)written;
            continue;
        }
        if (written < 0 && errno == EINTR) continue;
        if (written == 0) errno = EIO;
        return -1;
    }
    return 0;
}

static int reserved_component(const char *value, size_t size) {
    static const char reserved[] = "_wisedb";
    if (size < sizeof(reserved) - 1) return 0;
    for (size_t i = 0; i < sizeof(reserved) - 1; i++) {
        if (tolower((unsigned char)value[i]) != reserved[i]) return 0;
    }
    return 1;
}
#endif

int wisedb_ensure_dir(const char *path) {
    if (!path || !path[0]) return -1;
    char *copy = strdup(path);
    if (!copy) return -1;
    size_t length = strlen(copy);
    while (length > 1 && (copy[length - 1] == '/' || copy[length - 1] == '\\')) copy[--length] = '\0';
    for (size_t i = 1; i <= length; i++) {
        if (copy[i] != '/' && copy[i] != '\\' && copy[i] != '\0') continue;
        char saved = copy[i];
        copy[i] = '\0';
        if (copy[0]) {
            struct stat st;
            if (stat(copy, &st) != 0) {
                if (WISEDB_MKDIR(copy) != 0) {
                    if (errno != EEXIST || stat(copy, &st) != 0 || !S_ISDIR(st.st_mode)) {
                        free(copy);
                        return -1;
                    }
                }
            } else if (!S_ISDIR(st.st_mode)) {
                free(copy);
                return -1;
            }
        }
        copy[i] = saved;
    }
    free(copy);
    return 0;
}

int wisedb_ensure_root_dir(const char *path) {
    if (!path || !path[0]) return -1;
#ifdef _WIN32
    return -1;
#else
    struct stat st;
    if (lstat(path, &st) == 0) return S_ISDIR(st.st_mode) && !S_ISLNK(st.st_mode) ? 0 : -1;
    if (errno != ENOENT) return -1;
    char *parent = strdup(path);
    if (!parent) return -1;
    size_t length = strlen(parent);
    while (length > 1 && parent[length - 1] == '/') parent[--length] = '\0';
    char *slash = strrchr(parent, '/');
    if (!slash) {
        free(parent);
        parent = strdup(".");
        if (!parent) return -1;
    } else if (slash == parent) {
        slash[1] = '\0';
    } else {
        *slash = '\0';
    }
    if (wisedb_ensure_dir(parent) != 0) {
        free(parent);
        return -1;
    }
    free(parent);
    if (mkdir(path, 0700) == 0) return 0;
    if (errno != EEXIST || lstat(path, &st) != 0) return -1;
    return S_ISDIR(st.st_mode) && !S_ISLNK(st.st_mode) ? 0 : -1;
#endif
}

int wisedb_lock_root(wisedb_engine *eng, const char *wiki_root) {
    if (!eng || !wiki_root || !wiki_root[0]) return -1;
#ifdef _WIN32
    (void)wiki_root;
    return -1;
#else
    eng->root_fd = -1;
    eng->owner_lock_fd = -1;
    eng->root_fd = open(wiki_root, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (eng->root_fd < 0) return -1;
    struct stat root_stat;
    if (fstat(eng->root_fd, &root_stat) != 0 || !S_ISDIR(root_stat.st_mode)
        || root_stat.st_uid != geteuid() || (root_stat.st_mode & (S_IWGRP | S_IWOTH)) != 0) goto fail;
    if (fchmod(eng->root_fd, 0700) != 0) goto fail;
    if (mkdirat(eng->root_fd, "_wisedb", 0700) != 0 && errno != EEXIST) goto fail;
    struct stat metadata_entry;
    if (fstatat(eng->root_fd, "_wisedb", &metadata_entry, AT_SYMLINK_NOFOLLOW) != 0
        || !S_ISDIR(metadata_entry.st_mode)) goto fail;
    int meta_fd = openat(eng->root_fd, "_wisedb", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (meta_fd < 0) goto fail;
    struct stat meta_stat;
    if (fstat(meta_fd, &meta_stat) != 0 || !S_ISDIR(meta_stat.st_mode)
        || meta_stat.st_uid != geteuid() || (meta_stat.st_mode & (S_IWGRP | S_IWOTH)) != 0
        || fchmod(meta_fd, 0700) != 0) {
        close(meta_fd);
        goto fail;
    }
    eng->owner_lock_fd = openat(meta_fd, "OWNER.lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
    int saved_errno = errno;
    close(meta_fd);
    if (eng->owner_lock_fd < 0) {
        errno = saved_errno;
        goto fail;
    }
    struct stat lock_stat;
    if (fstat(eng->owner_lock_fd, &lock_stat) != 0 || !S_ISREG(lock_stat.st_mode)
        || lock_stat.st_uid != geteuid() || fchmod(eng->owner_lock_fd, 0600) != 0
        || flock(eng->owner_lock_fd, LOCK_EX | LOCK_NB) != 0) goto fail;
    return 0;

fail:
    {
        int saved_errno = errno;
        wisedb_storage_close(eng);
        errno = saved_errno;
    }
    return -1;
#endif
}

int wisedb_storage_open(wisedb_engine *eng, const char *wiki_root) {
    return wisedb_lock_root(eng, wiki_root);
}

int wisedb_storage_root_is_current(wisedb_engine *eng) {
#ifdef _WIN32
    (void)eng;
    return 0;
#else
    if (!eng || eng->root_fd < 0 || !eng->wiki_root) return 0;
    struct stat held_stat;
    struct stat path_stat;
    if (fstat(eng->root_fd, &held_stat) != 0 || lstat(eng->wiki_root, &path_stat) != 0) return 0;
    return S_ISDIR(path_stat.st_mode) && !S_ISLNK(path_stat.st_mode)
        && held_stat.st_dev == path_stat.st_dev && held_stat.st_ino == path_stat.st_ino;
#endif
}

static int stat_relative(wisedb_engine *eng, const char *rel_path, struct stat *stat_out) {
    int parent_fd = -1;
    char leaf[WISEDB_MAX_PATH];
    if (open_parent_fd(eng, rel_path, 0, &parent_fd, leaf, sizeof(leaf)) != 0) return -1;
    int result = fstatat(parent_fd, leaf, stat_out, AT_SYMLINK_NOFOLLOW);
    int saved_errno = errno;
    close(parent_fd);
    errno = saved_errno;
    return result;
}

int wisedb_stat_relative_no_follow(wisedb_engine *eng, const char *rel_path, struct stat *stat_out) {
    if (!eng || eng->root_fd < 0 || !rel_path || !stat_out) return -1;
    char path[WISEDB_MAX_PATH];
    size_t size = strlen(rel_path);
    if (size >= sizeof(path)) return -1;
    memcpy(path, rel_path, size + 1);
    char *cursor = path;
    int current_fd = dup(eng->root_fd);
    if (current_fd < 0) return -1;
    for (;;) {
        char *slash = strchr(cursor, '/');
        if (slash) *slash = '\0';
        if (!slash) {
            int result = fstatat(current_fd, cursor, stat_out, AT_SYMLINK_NOFOLLOW);
            int saved_errno = errno;
            close(current_fd);
            errno = saved_errno;
            return result;
        }
        int next_fd = openat(current_fd, cursor, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        int saved_errno = errno;
        close(current_fd);
        if (next_fd < 0) { errno = saved_errno; return -1; }
        current_fd = next_fd;
        cursor = slash + 1;
    }
}

static int unlink_relative(wisedb_engine *eng, const char *rel_path) {
    int parent_fd = -1;
    char leaf[WISEDB_MAX_PATH];
    if (open_parent_fd(eng, rel_path, 0, &parent_fd, leaf, sizeof(leaf)) != 0) return -1;
    int result = unlinkat(parent_fd, leaf, 0);
    int saved_errno = errno;
    close(parent_fd);
    errno = saved_errno;
    return result;
}

int wisedb_remove_file(wisedb_engine *eng, const char *rel_path) {
#ifdef _WIN32
    (void)eng; (void)rel_path;
    return -1;
#else
    struct stat st;
    if (stat_relative(eng, rel_path, &st) != 0) return errno == ENOENT ? 0 : -1;
    if (!S_ISREG(st.st_mode)) { errno = EINVAL; return -1; }
    if (unlink_relative(eng, rel_path) != 0) return -1;
    int parent_fd = -1;
    char leaf[WISEDB_MAX_PATH];
    if (open_parent_fd(eng, rel_path, 0, &parent_fd, leaf, sizeof(leaf)) != 0) return -1;
    int result = fsync(parent_fd);
    int saved_errno = errno;
    close(parent_fd);
    errno = saved_errno;
    return result;
#endif
}

int wisedb_file_exists(wisedb_engine *eng, const char *rel_path, int *exists_out) {
    if (!exists_out) return -1;
    *exists_out = 0;
#ifdef _WIN32
    (void)eng; (void)rel_path;
    return -1;
#else
    struct stat st;
    if (stat_relative(eng, rel_path, &st) == 0) {
        if (!S_ISREG(st.st_mode)) { errno = EINVAL; return -1; }
        *exists_out = 1;
        return 0;
    }
    return errno == ENOENT ? 0 : -1;
#endif
}

void wisedb_storage_close(wisedb_engine *eng) {
    if (!eng) return;
#ifndef _WIN32
    if (eng->owner_lock_fd >= 0) close(eng->owner_lock_fd);
    if (eng->root_fd >= 0) close(eng->root_fd);
    eng->owner_lock_fd = -1;
    eng->root_fd = -1;
#endif
}

int wisedb_read_settings_blob(wisedb_engine *eng, char **out, size_t *out_len) {
#ifdef _WIN32
    (void)eng; (void)out; (void)out_len;
    return -1;
#else
    if (!eng || eng->root_fd < 0) return -1;
    if (out) *out = NULL;
    if (out_len) *out_len = 0;
    int meta_fd = openat(eng->root_fd, "_wisedb", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (meta_fd < 0) return -1;
    int fd = openat(meta_fd, "settings.json", O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) {
        int saved = errno;
        close(meta_fd);
        return saved == ENOENT ? 0 : -1;
    }
    size_t capacity = 256;
    size_t length = 0;
    char *buffer = (char *)malloc(capacity);
    if (!buffer) { close(fd); close(meta_fd); return -1; }
    for (;;) {
        if (length + 1 > capacity) {
            if (capacity > 64u * 1024u) { free(buffer); close(fd); close(meta_fd); return -1; }
            size_t next = capacity * 2;
            char *grown = (char *)realloc(buffer, next);
            if (!grown) { free(buffer); close(fd); close(meta_fd); return -1; }
            buffer = grown;
            capacity = next;
        }
        ssize_t chunk = read(fd, buffer + length, capacity - length - 1);
        if (chunk < 0) { free(buffer); close(fd); close(meta_fd); return -1; }
        if (chunk == 0) break;
        length += (size_t)chunk;
    }
    buffer[length] = '\0';
    close(fd);
    close(meta_fd);
    if (out) *out = buffer;
    else free(buffer);
    if (out_len) *out_len = length;
    return 0;
#endif
}

int wisedb_write_settings_blob(wisedb_engine *eng, const uint8_t *data, size_t len) {
#ifdef _WIN32
    (void)eng; (void)data; (void)len;
    return -1;
#else
    if (!eng || eng->root_fd < 0 || (!data && len != 0) || len > 64u * 1024u) return -1;
    int parent_fd = openat(eng->root_fd, "_wisedb", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (parent_fd < 0) return -1;
    const char *leaf = "settings.json";
    int replace_allowed = 0;
    struct stat existing;
    if (fstatat(parent_fd, leaf, &existing, AT_SYMLINK_NOFOLLOW) == 0) {
        if (!S_ISREG(existing.st_mode)) { close(parent_fd); errno = EINVAL; return -1; }
        replace_allowed = 1;
    } else if (errno != ENOENT) {
        close(parent_fd);
        return -1;
    }
    static unsigned long temporary_counter;
    char temporary[96];
    int fd = -1;
    for (unsigned attempt = 0; attempt < 128; attempt++) {
        unsigned long candidate = ++temporary_counter;
        int written = snprintf(temporary, sizeof(temporary), ".wisedb-tmp-%ld-%lu",
                               (long)getpid(), candidate);
        if (written < 0 || (size_t)written >= sizeof(temporary)) break;
        fd = openat(parent_fd, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (fd >= 0 || errno != EEXIST) break;
    }
    if (fd < 0) { close(parent_fd); return -1; }
    int result = 0;
    if (write_all_fd(fd, data, len) != 0 || fsync(fd) != 0) result = -1;
    int saved_errno = errno;
    if (close(fd) != 0 && result == 0) { result = -1; saved_errno = errno; }
    if (result == 0) {
        if (replace_allowed && renameat(parent_fd, temporary, parent_fd, leaf) != 0) {
            result = -1;
            saved_errno = errno;
        } else if (!replace_allowed && linkat(parent_fd, temporary, parent_fd, leaf, 0) != 0) {
            result = -1;
            saved_errno = errno;
        } else if (!replace_allowed && unlinkat(parent_fd, temporary, 0) != 0) {
            result = -1;
            saved_errno = errno;
        }
    }
    if (result == 0 && fsync(parent_fd) != 0) {
        result = -1;
        saved_errno = errno;
    }
    if (result != 0) unlinkat(parent_fd, temporary, 0);
    close(parent_fd);
    if (result != 0) errno = saved_errno;
    return result;
#endif
}

int wisedb_write_file(wisedb_engine *eng, const char *rel_path, const uint8_t *data, size_t len, int replace_existing) {
#ifdef _WIN32
    (void)eng; (void)rel_path; (void)data; (void)len; (void)replace_existing;
    return -1;
#else
    if ((!data && len != 0) || len > 64u * 1024u * 1024u) return -1;
    int replace_allowed = 0;
    int parent_fd = -1;
    char leaf[WISEDB_MAX_PATH];
    if (open_parent_fd(eng, rel_path, 1, &parent_fd, leaf, sizeof(leaf)) != 0) return -1;
    struct stat existing;
    if (fstatat(parent_fd, leaf, &existing, AT_SYMLINK_NOFOLLOW) == 0) {
        if (!S_ISREG(existing.st_mode)) { close(parent_fd); errno = EINVAL; return -1; }
        if (!replace_existing) { close(parent_fd); errno = EEXIST; return -1; }
        replace_allowed = 1;
    } else if (errno != ENOENT) {
        close(parent_fd);
        return -1;
    }
    static unsigned long temporary_counter;
    char temporary[96];
    int fd = -1;
    for (unsigned attempt = 0; attempt < 128; attempt++) {
        unsigned long candidate = ++temporary_counter;
        int written = snprintf(temporary, sizeof(temporary), ".wisedb-tmp-%ld-%lu",
                               (long)getpid(), candidate);
        if (written < 0 || (size_t)written >= sizeof(temporary)) break;
        fd = openat(parent_fd, temporary, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
        if (fd >= 0 || errno != EEXIST) break;
    }
    if (fd < 0) { close(parent_fd); return -1; }
    int result = 0;
    if (write_all_fd(fd, data, len) != 0 || fsync(fd) != 0) result = -1;
    int saved_errno = errno;
    if (close(fd) != 0 && result == 0) { result = -1; saved_errno = errno; }
    if (result == 0) {
        struct stat current;
        int exists_now = fstatat(parent_fd, leaf, &current, AT_SYMLINK_NOFOLLOW) == 0;
        if (exists_now && !S_ISREG(current.st_mode)) { result = -1; saved_errno = EINVAL; }
        else if (!exists_now && errno != ENOENT) { result = -1; saved_errno = errno; }
        else if (exists_now != replace_allowed) { result = -1; saved_errno = EAGAIN; }
        else if (replace_allowed && renameat(parent_fd, temporary, parent_fd, leaf) != 0) {
            result = -1;
            saved_errno = errno;
        } else if (!replace_allowed && linkat(parent_fd, temporary, parent_fd, leaf, 0) != 0) {
            result = -1;
            saved_errno = errno;
        } else if (!replace_allowed && unlinkat(parent_fd, temporary, 0) != 0) {
            result = -1;
            saved_errno = errno;
        }
    }
    if (result == 0 && fsync(parent_fd) != 0) {
        result = -1;
        saved_errno = errno;
    }
    if (result != 0) unlinkat(parent_fd, temporary, 0);
    close(parent_fd);
    if (result != 0) errno = saved_errno;
    return result;
#endif
}

int wisedb_join_path(char *out, size_t out_sz, const char *a, const char *b) {
    if (!out || out_sz == 0) return -1;
    size_t alen = a ? strlen(a) : 0;
    int need_sep = (alen > 0 && a[alen - 1] != '/' && a[alen - 1] != '\\');
    int n = snprintf(out, out_sz, "%s%s%s", a ? a : "", (need_sep ? "/" : ""), b ? b : "");
    return (n < 0 || (size_t)n >= out_sz) ? -1 : 0;
}

int wisedb_validate_relative_path(const wisedb_engine *eng, const char *rel_path) {
    if (!eng || !eng->wiki_root || !rel_path || !rel_path[0] || rel_path[0] == '/' || rel_path[0] == '\\') return -1;
    size_t path_size = strlen(rel_path);
    if (path_size >= WISEDB_MAX_PATH || strchr(rel_path, '\\') || strchr(rel_path, ':')) return -1;
#ifdef _WIN32
    return -1;
#else
    if (eng->root_fd < 0) return -1;
    int current_fd = dup(eng->root_fd);
    if (current_fd < 0) return -1;
    const char *cursor = rel_path;
    int result = 0;
    while (*cursor) {
        const char *end = strchr(cursor, '/');
        size_t component_size = end ? (size_t)(end - cursor) : strlen(cursor);
        if (component_size == 0 || (component_size == 1 && cursor[0] == '.')
            || (component_size == 2 && cursor[0] == '.' && cursor[1] == '.')) { result = -1; break; }
    #ifndef _WIN32
        if (reserved_component(cursor, component_size)) { result = -1; break; }
    #endif
        char component[WISEDB_MAX_PATH];
        if (component_size >= sizeof(component)) { result = -1; break; }
        memcpy(component, cursor, component_size);
        component[component_size] = '\0';
        struct stat st;
        if (current_fd >= 0 && fstatat(current_fd, component, &st, AT_SYMLINK_NOFOLLOW) == 0) {
            if (S_ISLNK(st.st_mode) || (end && !S_ISDIR(st.st_mode)) || (!end && !S_ISREG(st.st_mode))) {
                result = -1;
                break;
            }
            if (end) {
                int next_fd = openat(current_fd, component, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
                if (next_fd < 0) { result = -1; break; }
                close(current_fd);
                current_fd = next_fd;
            }
        } else if (current_fd >= 0 && errno != ENOENT) {
            result = -1;
            break;
        } else if (current_fd >= 0 && end) {
            /* Remaining components do not yet exist: lexical checks still apply. */
            close(current_fd);
            current_fd = -1;
        }
        if (!end) break;
        cursor = end + 1;
        if (!*cursor) { result = -1; break; }
    }
    if (current_fd >= 0) close(current_fd);
    return result;
#endif
}

int wisedb_read_file(wisedb_engine *eng, const char *rel_path, char **out, size_t *out_len) {
    if (!out) return -1;
    *out = NULL;
#ifdef _WIN32
    (void)eng; (void)rel_path; (void)out_len;
    return -1;
#else
    int fd = open_regular_relative(eng, rel_path, O_RDONLY);
    if (fd < 0) return -1;
    struct stat st;
    if (fstat(fd, &st) != 0 || st.st_size < 0 || (uintmax_t)st.st_size > SIZE_MAX - 1u
        || (uintmax_t)st.st_size > 16u * 1024u * 1024u) {
        int saved_errno = errno ? errno : EFBIG;
        close(fd);
        errno = saved_errno;
        return -1;
    }
    size_t size = (size_t)st.st_size;
    char *buffer = (char *)malloc(size + 1);
    if (!buffer) { close(fd); return -1; }
    size_t offset = 0;
    while (offset < size) {
        ssize_t amount = read(fd, buffer + offset, size - offset);
        if (amount > 0) { offset += (size_t)amount; continue; }
        if (amount < 0 && errno == EINTR) continue;
        free(buffer);
        close(fd);
        if (amount == 0) errno = EIO;
        return -1;
    }
    buffer[size] = '\0';
    if (memchr(buffer, '\0', size) != NULL || !wisedb_json_is_valid_utf8(buffer)) {
        free(buffer);
        close(fd);
        errno = EILSEQ;
        return -1;
    }
    if (close(fd) != 0) { free(buffer); return -1; }
    *out = buffer;
    if (out_len) *out_len = size;
    return 0;
#endif
}

static void slugify(const char *in, char *out, size_t out_sz) {
    size_t j = 0;
    for (size_t i = 0; in[i] && j + 1 < out_sz; i++) {
        char c = in[i];
        if (c == '\\') c = '/';
        if (c == '/' || c == ' ') {
            if (j > 0 && out[j - 1] != '-') out[j++] = '-';
            continue;
        }
        if (isalnum((unsigned char)c) || c == '-' || c == '_') {
            out[j++] = (char)tolower((unsigned char)c);
        }
    }
    while (j > 0 && out[j - 1] == '-') j--;
    out[j] = '\0';
}

int wisedb_slug_from_path(const char *rel_path, char *slug_out, size_t slug_sz) {
    if (!rel_path || !slug_out) return -1;
    char tmp[WISEDB_MAX_PATH];
    strncpy(tmp, rel_path, sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';
    char *dot = strrchr(tmp, '.');
    if (dot && wisedb_is_media_path(rel_path)) *dot = '\0';
    slugify(tmp, slug_out, slug_sz);
    return slug_out[0] ? 0 : -1;
}

static int load_node_from_file(wisedb_engine *eng, const char *rel_path) {
    if (wisedb_validate_relative_path(eng, rel_path) != 0) return -1;
    struct stat st;
    if (stat_relative(eng, rel_path, &st) != 0) return -1;
    if (!S_ISREG(st.st_mode)) return -1;

    char slug[WISEDB_MAX_SLUG];
    if (wisedb_slug_from_path(rel_path, slug, sizeof(slug)) != 0) return -1;

    if (wisedb_is_media_path(rel_path)) {
        char sidecar[WISEDB_MAX_PATH];
        int sidecar_len = snprintf(sidecar, sizeof(sidecar), "%s.md", rel_path);
        if (sidecar_len < 0 || (size_t)sidecar_len >= sizeof(sidecar)
            || wisedb_validate_relative_path(eng, sidecar) != 0) return -1;
        wisedb_node node;
        memset(&node, 0, sizeof(node));
        char **links = NULL;
        size_t link_count = 0;
        char **mentions = NULL;
        size_t mention_count = 0;
        char *md = NULL;
        int sidecar_exists = 0;
        if (wisedb_file_exists(eng, sidecar, &sidecar_exists) != 0) return -1;
        if (sidecar_exists) {
            if (wisedb_read_file(eng, sidecar, &md, NULL) != 0 || !md) return -1;
            if (wisedb_parse_markdown(md, &node, &links, &link_count, &mentions, &mention_count) != 0) {
                free(md);
                wisedb_free_parsed_links(links, link_count);
                wisedb_free_parsed_links(mentions, mention_count);
                wisedb_free_node(&node);
                return -1;
            }
            free(md);
        } else {
            node.slug = strdup(slug);
            node.title = strdup(slug);
            node.summary = strdup("");
            node.body = strdup("");
        }
        if (!node.slug || !node.slug[0]) {
            free(node.slug);
            node.slug = strdup(slug);
        }
        node.rel_path = strdup(rel_path);
        if (!node.mime) node.mime = strdup(wisedb_mime_from_ext(rel_path));
        node.mtime = st.st_mtime;
        node.is_media = 1;
        node.type = WISEDB_NODE_MEDIA;
        if (!node.rel_path || !node.mime || !node.slug
            || wisedb_upsert_node_from_scan(eng, &node, links, link_count, mentions, mention_count) != 0) {
            wisedb_free_parsed_links(links, link_count);
            wisedb_free_parsed_links(mentions, mention_count);
            wisedb_free_node(&node);
            return -1;
        }
        wisedb_free_parsed_links(links, link_count);
        wisedb_free_parsed_links(mentions, mention_count);
        wisedb_free_node(&node);
        return 0;
    }

    size_t rlen = strlen(rel_path);
    if (rlen <= 3 || strcmp(rel_path + rlen - 3, ".md") != 0) {
        return 0;
    }

    char *md = NULL;
    if (wisedb_read_file(eng, rel_path, &md, NULL) != 0) return -1;
    wisedb_node node;
    memset(&node, 0, sizeof(node));
    char **links = NULL;
    size_t lc = 0;
    char **mentions = NULL;
    size_t mention_count = 0;
    if (wisedb_parse_markdown(md, &node, &links, &lc, &mentions, &mention_count) != 0) {
        free(md);
        wisedb_free_parsed_links(links, lc);
        wisedb_free_parsed_links(mentions, mention_count);
        wisedb_free_node(&node);
        return -1;
    }
    free(md);
    if (!node.slug || !node.slug[0]) {
        free(node.slug);
        node.slug = strdup(slug);
    }
    node.rel_path = strdup(rel_path);
    node.mime = strdup(wisedb_mime_from_ext(rel_path));
    node.mtime = st.st_mtime;
    node.is_media = 0;
    if (!node.slug || !node.rel_path || !node.mime
        || wisedb_upsert_node_from_scan(eng, &node, links, lc, mentions, mention_count) != 0) {
        wisedb_free_node(&node);
        wisedb_free_parsed_links(links, lc);
        wisedb_free_parsed_links(mentions, mention_count);
        return -1;
    }
    wisedb_free_parsed_links(links, lc);
    wisedb_free_parsed_links(mentions, mention_count);
    wisedb_free_node(&node);
    return 0;
}

#ifndef _WIN32
static int scan_dir(wisedb_engine *eng, int dir_fd, const char *rel_base, unsigned depth) {
    if (depth > 64) return -1;
    int scan_fd = openat(dir_fd, ".", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (scan_fd < 0) return -1;
    DIR *d = fdopendir(scan_fd);
    if (!d) { close(scan_fd); return -1; }

    struct dirent *ent;
    int result = 0;
    for (;;) {
        errno = 0;
        ent = readdir(d);
        if (!ent) {
            if (errno != 0) result = -1;
            break;
        }
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) continue;
        if (strcmp(ent->d_name, "_wisedb") == 0) continue;

        char rel[WISEDB_MAX_PATH];
        int written;
        if (rel_base[0]) {
            written = snprintf(rel, sizeof(rel), "%s/%s", rel_base, ent->d_name);
        } else {
            written = snprintf(rel, sizeof(rel), "%s", ent->d_name);
        }
        if (written < 0 || (size_t)written >= sizeof(rel)) { result = -1; break; }

        struct stat st;
        if (fstatat(dir_fd, ent->d_name, &st, AT_SYMLINK_NOFOLLOW) != 0) { result = -1; break; }
        if (S_ISLNK(st.st_mode)) { result = -1; break; }

        if (S_ISDIR(st.st_mode)) {
            int child_fd = openat(dir_fd, ent->d_name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
            if (child_fd < 0) { result = -1; break; }
            int child_result = scan_dir(eng, child_fd, rel, depth + 1);
            close(child_fd);
            if (child_result != 0) { result = -1; break; }
        } else {
            /* skip sidecar-only files handled with media */
            size_t len = strlen(ent->d_name);
            if (len > 3 && strcmp(ent->d_name + len - 3, ".md") == 0) {
                /* if it's a sidecar for media (foo.jpg.md), skip standalone load */
                char parent[WISEDB_MAX_PATH];
                int parent_len = snprintf(parent, sizeof(parent), "%s", rel);
                if (parent_len < 0 || (size_t)parent_len >= sizeof(parent)) { result = -1; break; }
                char *slash = strrchr(parent, '/');
                if (slash) *slash = '\0';
                char media_candidate[WISEDB_MAX_PATH];
                int media_len = snprintf(media_candidate, sizeof(media_candidate), "%.*s", (int)(strlen(rel) - 3), rel);
                if (media_len < 0 || (size_t)media_len >= sizeof(media_candidate)) { result = -1; break; }
                if (wisedb_is_media_path(media_candidate)) continue;
            }
            if (load_node_from_file(eng, rel) != 0) { result = -1; break; }
        }
    }
    closedir(d);
    return result;
}
#endif

int wisedb_scan_wiki(wisedb_engine *eng) {
    if (!eng) return -1;
#ifndef _WIN32
    if (eng->root_fd < 0 || scan_dir(eng, eng->root_fd, "", 0) != 0) return -1;
#else
    return -1;
#endif
    /* recompute avg doc len */
    double total = 0;
    for (size_t i = 0; i < eng->node_count; i++) {
        total += (double)eng->nodes[i].token_count;
    }
    eng->doc_count = eng->node_count;
    eng->avg_doc_len = eng->doc_count > 0 ? total / (double)eng->doc_count : 0.0;
    if (wisedb_settings_load(eng) != 0) return -1;
    if (wisedb_rebuild_edges(eng) != 0) return -1;
    return 0;
}

int wisedb_save_node_file(wisedb_engine *eng, const wisedb_node *node, const char *markdown_full) {
    if (!eng || !node || !node->rel_path) return -1;
    if (wisedb_validate_relative_path(eng, node->rel_path) != 0) return -1;
    if (node->is_media) {
        if (markdown_full) {
            char sidecar_rel[WISEDB_MAX_PATH];
            int sidecar_len = snprintf(sidecar_rel, sizeof(sidecar_rel), "%s.md", node->rel_path);
            if (sidecar_len < 0 || (size_t)sidecar_len >= sizeof(sidecar_rel)
                || wisedb_validate_relative_path(eng, sidecar_rel) != 0) return -1;
            int existing_node = wisedb_find_node(eng, node->slug) != NULL;
            return wisedb_write_file(eng, sidecar_rel, (const uint8_t *)markdown_full,
                                     strlen(markdown_full), existing_node);
        }
        return 0;
    }
    return wisedb_write_file(eng, node->rel_path, (const uint8_t *)markdown_full,
                             strlen(markdown_full), wisedb_find_node(eng, node->slug) != NULL);
}

int wisedb_delete_node_file(wisedb_engine *eng, const wisedb_node *node) {
    if (!eng || !node || !node->rel_path) return -1;
    if (wisedb_validate_relative_path(eng, node->rel_path) != 0) return -1;
    if (node->is_media) {
        char sidecar_rel[WISEDB_MAX_PATH];
        int sidecar_len = snprintf(sidecar_rel, sizeof(sidecar_rel), "%s.md", node->rel_path);
        if (sidecar_len < 0 || (size_t)sidecar_len >= sizeof(sidecar_rel)
            || wisedb_validate_relative_path(eng, sidecar_rel) != 0) return -1;
        int payload_exists = 0;
        int sidecar_exists = 0;
        if (wisedb_file_exists(eng, node->rel_path, &payload_exists) != 0
            || wisedb_file_exists(eng, sidecar_rel, &sidecar_exists) != 0) return -1;
        char *sidecar_data = NULL;
        size_t sidecar_size = 0;
        if (sidecar_exists && wisedb_read_file(eng, sidecar_rel, &sidecar_data, &sidecar_size) != 0) return -1;
        if (sidecar_exists && unlink_relative(eng, sidecar_rel) != 0) {
            free(sidecar_data);
            return -1;
        }
        if (payload_exists && unlink_relative(eng, node->rel_path) != 0) {
            if (sidecar_exists) {
                (void)wisedb_write_file(eng, sidecar_rel, (const uint8_t *)sidecar_data, sidecar_size, 0);
            }
            free(sidecar_data);
            return -1;
        }
        free(sidecar_data);
        return 0;
    }
    return unlink_relative(eng, node->rel_path) == 0 ? 0 : -1;
}
