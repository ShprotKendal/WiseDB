#ifndef WISEDB_FAULT_INJECTION_H
#define WISEDB_FAULT_INJECTION_H

#include <stddef.h>
#include <sys/types.h>
#include <unistd.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    WISEDB_FAULT_WRITE = 0,
    WISEDB_FAULT_WRITE_ENOSPC,
    WISEDB_FAULT_WRITE_SHORT,
    WISEDB_FAULT_FSYNC,
    WISEDB_FAULT_LINKAT,
    WISEDB_FAULT_RENAMEAT,
    WISEDB_FAULT_UNLINKAT,
    WISEDB_FAULT_CLOSE,
    WISEDB_FAULT_KIND_COUNT
} wisedb_fault_kind;

void wisedb_fault_alloc_fail_nth(size_t nth);
void wisedb_fault_alloc_disable(void);
size_t wisedb_fault_alloc_count(void);
void wisedb_fault_io_fail_nth(wisedb_fault_kind kind, size_t nth);
void wisedb_fault_io_disable(void);
size_t wisedb_fault_io_count(wisedb_fault_kind kind);

void *wisedb_fault_malloc(size_t size);
void *wisedb_fault_calloc(size_t count, size_t size);
void *wisedb_fault_realloc(void *pointer, size_t size);
char *wisedb_fault_strdup(const char *value);
ssize_t wisedb_fault_write(int fd, const void *buffer, size_t size);
int wisedb_fault_fsync(int fd);
int wisedb_fault_linkat(int olddirfd, const char *oldpath, int newdirfd, const char *newpath, int flags);
int wisedb_fault_renameat(int olddirfd, const char *oldpath, int newdirfd, const char *newpath);
int wisedb_fault_unlinkat(int dirfd, const char *path, int flags);
/* Deterministically closes fd, then reports EIO to model an ambiguous close result. */
int wisedb_fault_close(int fd);

#ifdef __cplusplus
}
#endif

#if defined(WISEDB_ENABLE_FAULT_INJECTION) && !defined(WISEDB_FAULT_IMPLEMENTATION)
#define malloc wisedb_fault_malloc
#define calloc wisedb_fault_calloc
#define realloc wisedb_fault_realloc
#define strdup wisedb_fault_strdup
#define write wisedb_fault_write
#define fsync wisedb_fault_fsync
#define linkat wisedb_fault_linkat
#define renameat wisedb_fault_renameat
#define unlinkat wisedb_fault_unlinkat
#define close wisedb_fault_close
#endif

#endif
