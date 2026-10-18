#define _POSIX_C_SOURCE 200809L
#define WISEDB_FAULT_IMPLEMENTATION
#include "fault_injection.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static size_t alloc_fail_at;
static size_t alloc_counter;
static size_t io_fail_at[WISEDB_FAULT_KIND_COUNT];
static size_t io_counter[WISEDB_FAULT_KIND_COUNT];

static int should_trigger_io(wisedb_fault_kind kind) {
    if ((unsigned)kind >= WISEDB_FAULT_KIND_COUNT) return 0;
    io_counter[kind]++;
    if (io_fail_at[kind] != 0 && io_counter[kind] == io_fail_at[kind]) {
        return 1;
    }
    return 0;
}

static int should_fail_io(wisedb_fault_kind kind) {
    if (!should_trigger_io(kind)) return 0;
    errno = EIO;
    return 1;
}

void wisedb_fault_alloc_fail_nth(size_t nth) {
    alloc_counter = 0;
    alloc_fail_at = nth;
}

void wisedb_fault_alloc_disable(void) {
    alloc_fail_at = 0;
    alloc_counter = 0;
}

size_t wisedb_fault_alloc_count(void) { return alloc_counter; }

void wisedb_fault_io_fail_nth(wisedb_fault_kind kind, size_t nth) {
    if ((unsigned)kind >= WISEDB_FAULT_KIND_COUNT) return;
    io_counter[kind] = 0;
    io_fail_at[kind] = nth;
}

void wisedb_fault_io_disable(void) {
    memset(io_fail_at, 0, sizeof(io_fail_at));
    memset(io_counter, 0, sizeof(io_counter));
}

size_t wisedb_fault_io_count(wisedb_fault_kind kind) {
    return (unsigned)kind < WISEDB_FAULT_KIND_COUNT ? io_counter[kind] : 0;
}

static int should_fail_alloc(void) {
    alloc_counter++;
    return alloc_fail_at != 0 && alloc_counter == alloc_fail_at;
}

void *wisedb_fault_malloc(size_t size) {
    if (should_fail_alloc()) return NULL;
    return malloc(size);
}

void *wisedb_fault_calloc(size_t count, size_t size) {
    if (should_fail_alloc()) return NULL;
    return calloc(count, size);
}

void *wisedb_fault_realloc(void *pointer, size_t size) {
    if (should_fail_alloc()) return NULL;
    return realloc(pointer, size);
}

char *wisedb_fault_strdup(const char *value) {
    if (should_fail_alloc()) return NULL;
    return strdup(value);
}

ssize_t wisedb_fault_write(int fd, const void *buffer, size_t size) {
    if (should_trigger_io(WISEDB_FAULT_WRITE_ENOSPC)) {
        errno = ENOSPC;
        return -1;
    }
    if (should_trigger_io(WISEDB_FAULT_WRITE_SHORT) && size > 1) return (ssize_t)(size / 2);
    return should_fail_io(WISEDB_FAULT_WRITE) ? -1 : write(fd, buffer, size);
}

int wisedb_fault_fsync(int fd) {
    return should_fail_io(WISEDB_FAULT_FSYNC) ? -1 : fsync(fd);
}

int wisedb_fault_linkat(int olddirfd, const char *oldpath, int newdirfd, const char *newpath, int flags) {
    return should_fail_io(WISEDB_FAULT_LINKAT) ? -1 : linkat(olddirfd, oldpath, newdirfd, newpath, flags);
}

int wisedb_fault_renameat(int olddirfd, const char *oldpath, int newdirfd, const char *newpath) {
    return should_fail_io(WISEDB_FAULT_RENAMEAT) ? -1 : renameat(olddirfd, oldpath, newdirfd, newpath);
}

int wisedb_fault_unlinkat(int dirfd, const char *path, int flags) {
    return should_fail_io(WISEDB_FAULT_UNLINKAT) ? -1 : unlinkat(dirfd, path, flags);
}

int wisedb_fault_close(int fd) {
    int result = close(fd);
    int saved_errno = errno;
    if (should_fail_io(WISEDB_FAULT_CLOSE)) return -1;
    errno = saved_errno;
    return result;
}
