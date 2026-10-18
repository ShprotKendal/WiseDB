#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE 1
#define WISEDB_FAULT_IMPLEMENTATION

#include "wisedb.h"
#include "fault_injection.h"

#include <dirent.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define CHECK(condition) do { \
    if (!(condition)) { \
        fprintf(stderr, "fault-injection check failed at %s:%d: %s\n", __FILE__, __LINE__, #condition); \
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

static int directory_has_temporary(const char *path) {
    DIR *dir = opendir(path);
    if (!dir) return -1;
    struct dirent *entry;
    int found = 0;
    while ((entry = readdir(dir)) != NULL) {
        if (strncmp(entry->d_name, ".wisedb-tmp-", 11) == 0) {
            found = 1;
            break;
        }
    }
    closedir(dir);
    return found;
}

static int contains_slug(wisedb_engine *eng, const char *slug) {
    char *json = NULL;
    if (wisedb_list(eng, &json) != 0) return -1;
    char needle[WISEDB_MAX_SLUG + 8];
    int length = snprintf(needle, sizeof(needle), "\"slug\":\"%s\"", slug);
    int found = length > 0 && (size_t)length < sizeof(needle) && strstr(json, needle) != NULL;
    wisedb_free_string(json);
    return found;
}

static int remove_slug(wisedb_engine *eng, const char *slug) {
    if (wisedb_delete(eng, slug) == 0) return 0;
    char *json = NULL;
    int read_result = wisedb_read(eng, slug, 0, &json);
    wisedb_free_string(json);
    return read_result == 0 ? -1 : 0;
}

int main(void) {
    int result = 0;
    char root_template[] = "/tmp/wisedb-fault-test-XXXXXX";
    char *root = mkdtemp(root_template);
    wisedb_engine *eng = NULL;
    char *out = NULL;
    CHECK(root != NULL);

    wisedb_fault_alloc_disable();
    wisedb_fault_io_disable();
    eng = wisedb_open(root);
    CHECK(eng != NULL);

    const char *markdown = "---\ntitle: Fault page\n---\nbody [[target]].";
    wisedb_fault_alloc_fail_nth(1);
    CHECK(wisedb_remember(eng, "fault-page", markdown, NULL, 0, NULL, &out) != 0);
    CHECK(out == NULL);
    wisedb_fault_alloc_disable();
    CHECK(wisedb_list(eng, &out) == 0);
    CHECK(strstr(out, "fault-page") == NULL);
    wisedb_free_string(out);
    out = NULL;

    const wisedb_fault_kind io_failures[] = {
        WISEDB_FAULT_WRITE,
        WISEDB_FAULT_FSYNC
    };
    for (size_t i = 0; i < sizeof(io_failures) / sizeof(io_failures[0]); i++) {
        wisedb_fault_io_fail_nth(io_failures[i], 1);
        CHECK(wisedb_remember(eng, "fault-page", markdown, NULL, 0, NULL, &out) != 0);
        CHECK(out == NULL);
        wisedb_fault_io_disable();
        CHECK(wisedb_list(eng, &out) == 0);
        CHECK(strstr(out, "fault-page") == NULL);
        wisedb_free_string(out);
        out = NULL;
        char pages_path[4096];
        int pages_length = snprintf(pages_path, sizeof(pages_path), "%s/pages", root);
        CHECK(pages_length > 0 && (size_t)pages_length < sizeof(pages_path));
        CHECK(directory_has_temporary(pages_path) == 0);
    }

    wisedb_fault_io_fail_nth(WISEDB_FAULT_CLOSE, SIZE_MAX);
    CHECK(wisedb_remember(eng, "close-count", markdown, NULL, 0, NULL, &out) == 0);
    CHECK(out != NULL);
    wisedb_free_string(out);
    out = NULL;
    size_t close_count = wisedb_fault_io_count(WISEDB_FAULT_CLOSE);
    wisedb_fault_io_disable();
    CHECK(wisedb_delete(eng, "close-count") == 0);
    CHECK(close_count > 0);
    for (size_t nth = 1; nth <= close_count; nth++) {
        char slug[WISEDB_MAX_SLUG];
        int slug_length = snprintf(slug, sizeof(slug), "close-%zu", nth);
        CHECK(slug_length > 0 && (size_t)slug_length < sizeof(slug));
        wisedb_fault_io_fail_nth(WISEDB_FAULT_CLOSE, nth);
        int remember_result = wisedb_remember(eng, slug, markdown, NULL, 0, NULL, &out);
        wisedb_fault_io_disable();
        if (remember_result == 0) {
            CHECK(out != NULL);
            wisedb_free_string(out);
            out = NULL;
        } else {
            CHECK(out == NULL);
        }
        CHECK(wisedb_refresh(eng) == 0);
        CHECK(remove_slug(eng, slug) == 0);
    }

    wisedb_fault_io_fail_nth(WISEDB_FAULT_LINKAT, 1);
    CHECK(wisedb_remember(eng, "link-failure", markdown, NULL, 0, NULL, &out) != 0);
    CHECK(out == NULL);
    wisedb_fault_io_disable();
    CHECK(contains_slug(eng, "link-failure") == 0);
    char link_pages[4096];
    int link_pages_length = snprintf(link_pages, sizeof(link_pages), "%s/pages", root);
    CHECK(link_pages_length > 0 && (size_t)link_pages_length < sizeof(link_pages));
    CHECK(directory_has_temporary(link_pages) == 0);

    wisedb_fault_alloc_fail_nth(SIZE_MAX);
    CHECK(wisedb_remember(eng, "allocation-count", markdown, NULL, 0, NULL, &out) == 0);
    size_t allocation_count = wisedb_fault_alloc_count();
    wisedb_fault_alloc_disable();
    CHECK(allocation_count > 0);
    CHECK(wisedb_delete(eng, "allocation-count") == 0);

    for (size_t nth = 1; nth <= allocation_count; nth++) {
        char slug[WISEDB_MAX_SLUG];
        int slug_length = snprintf(slug, sizeof(slug), "allocation-%zu", nth);
        CHECK(slug_length > 0 && (size_t)slug_length < sizeof(slug));
        wisedb_fault_alloc_fail_nth(nth);
        int remember_result = wisedb_remember(eng, slug, markdown, NULL, 0, NULL, &out);
        int injected = wisedb_fault_alloc_count() >= nth;
        wisedb_fault_alloc_disable();
        if (remember_result == 0) {
            CHECK(out != NULL);
            wisedb_free_string(out);
            out = NULL;
        } else {
            CHECK(injected);
            CHECK(out == NULL);
        }
        CHECK(wisedb_refresh(eng) == 0);
        if (remember_result != 0) CHECK(contains_slug(eng, slug) == 0);
        CHECK(remove_slug(eng, slug) == 0);
    }

    const char *original_edit = "---\nslug: edit-oom\ntitle: Before\n---\nold body";
    const char *edited_edit = "---\nslug: edit-oom\ntitle: After\n---\nnew body";
    CHECK(wisedb_remember(eng, "edit-oom", original_edit, NULL, 0, NULL, &out) == 0);
    wisedb_free_string(out);
    out = NULL;
    wisedb_fault_alloc_fail_nth(SIZE_MAX);
    CHECK(wisedb_edit(eng, "edit-oom", edited_edit, &out) == 0);
    size_t edit_allocation_count = wisedb_fault_alloc_count();
    wisedb_free_string(out);
    out = NULL;
    wisedb_fault_alloc_disable();
    CHECK(edit_allocation_count > 0);
    CHECK(wisedb_edit(eng, "edit-oom", original_edit, &out) == 0);
    wisedb_free_string(out);
    out = NULL;
    for (size_t nth = 1; nth <= edit_allocation_count; nth++) {
        wisedb_fault_alloc_fail_nth(nth);
        int edit_result = wisedb_edit(eng, "edit-oom", edited_edit, &out);
        int injected = wisedb_fault_alloc_count() >= nth;
        wisedb_fault_alloc_disable();
        CHECK(injected);
        if (edit_result == 0) {
            CHECK(out != NULL);
            wisedb_free_string(out);
            out = NULL;
            CHECK(wisedb_edit(eng, "edit-oom", original_edit, &out) == 0);
            wisedb_free_string(out);
            out = NULL;
        } else {
            CHECK(out == NULL);
        }
        CHECK(wisedb_read_markdown(eng, "edit-oom", &out) == 0);
        CHECK(strstr(out, "old body") != NULL);
        wisedb_free_string(out);
        out = NULL;
    }
    CHECK(wisedb_delete(eng, "edit-oom") == 0);

    const char *rename_markdown = "---\nslug: rename-target\ntitle: Old\n---\nold body";
    CHECK(wisedb_remember(eng, "rename-target", rename_markdown, NULL, 0, NULL, &out) == 0);
    wisedb_free_string(out);
    out = NULL;
    wisedb_fault_io_fail_nth(WISEDB_FAULT_RENAMEAT, 1);
    CHECK(wisedb_edit(eng, "rename-target", "---\nslug: rename-target\ntitle: New\n---\nnew body", &out) != 0);
    CHECK(out == NULL);
    wisedb_fault_io_disable();
    CHECK(wisedb_read_markdown(eng, "rename-target", &out) == 0);
    CHECK(strstr(out, "old body") != NULL);
    wisedb_free_string(out);
    out = NULL;
    CHECK(wisedb_delete(eng, "rename-target") == 0);

    wisedb_fault_io_fail_nth(WISEDB_FAULT_UNLINKAT, 1);
    CHECK(wisedb_remember(eng, "unlink-outcome", markdown, NULL, 0, NULL, &out) != 0);
    CHECK(out == NULL);
    wisedb_fault_io_disable();
    CHECK(wisedb_refresh(eng) == 0);
    CHECK(contains_slug(eng, "unlink-outcome") == 1);
    CHECK(wisedb_delete(eng, "unlink-outcome") == 0);

    const uint8_t media_payload[] = {0x89, 0x50, 0x4e, 0x47, 0x00, 0xff};
    const char *media_caption = "---\ntype: media\nslug: media-write-failure\ntitle: Caption\n---\ncaption";
    wisedb_fault_io_fail_nth(WISEDB_FAULT_WRITE, 2);
    CHECK(wisedb_remember(eng, "media-write-failure", media_caption, media_payload,
                          sizeof(media_payload), "assets/media-write-failure.png", &out) != 0);
    CHECK(out == NULL);
    wisedb_fault_io_disable();
    CHECK(wisedb_refresh(eng) == 0);
    CHECK(contains_slug(eng, "media-write-failure") == 0);

    const char *partial_caption = "---\ntype: media\nslug: media-partial\ntitle: Partial caption\n---\ncaption";
    wisedb_fault_io_fail_nth(WISEDB_FAULT_FSYNC, 4);
    CHECK(wisedb_remember(eng, "media-partial", partial_caption, media_payload,
                          sizeof(media_payload), "assets/media-partial.png", &out) != 0);
    CHECK(out == NULL);
    wisedb_fault_io_disable();
    CHECK(wisedb_refresh(eng) == 0);
    CHECK(contains_slug(eng, "media-partial") == 0);
    char partial_payload_path[4096];
    char partial_sidecar_path[4096];
    int payload_path_length = snprintf(partial_payload_path, sizeof(partial_payload_path),
                                       "%s/assets/media-partial.png", root);
    int sidecar_path_length = snprintf(partial_sidecar_path, sizeof(partial_sidecar_path),
                                       "%s/assets/media-partial.png.md", root);
    CHECK(payload_path_length > 0 && (size_t)payload_path_length < sizeof(partial_payload_path));
    CHECK(sidecar_path_length > 0 && (size_t)sidecar_path_length < sizeof(partial_sidecar_path));
    CHECK(access(partial_payload_path, F_OK) == 0);
    CHECK(access(partial_sidecar_path, F_OK) != 0);
    CHECK(unlink(partial_payload_path) == 0);
    CHECK(rmdir(strcat(strcpy(partial_payload_path, root), "/assets")) == 0);

    wisedb_fault_io_fail_nth(WISEDB_FAULT_FSYNC, 2);
    CHECK(wisedb_remember(eng, "uncertain-page", markdown, NULL, 0, NULL, &out) != 0);
    CHECK(out == NULL);
    wisedb_fault_io_disable();
    CHECK(wisedb_refresh(eng) == 0);
    CHECK(wisedb_read(eng, "uncertain-page", 0, &out) == 0);
    CHECK(strstr(out, "uncertain-page") != NULL);
    wisedb_free_string(out);
    out = NULL;

    wisedb_fault_alloc_fail_nth(SIZE_MAX);
    CHECK(wisedb_refresh(eng) == 0);
    size_t refresh_allocation_count = wisedb_fault_alloc_count();
    wisedb_fault_alloc_disable();
    CHECK(refresh_allocation_count > 0);
    for (size_t nth = 1; nth <= refresh_allocation_count; nth++) {
        wisedb_fault_alloc_fail_nth(nth);
        CHECK(wisedb_refresh(eng) != 0);
        wisedb_fault_alloc_disable();
        CHECK(contains_slug(eng, "uncertain-page") == 1);
        CHECK(contains_slug(eng, "rename-target") == 0);
    }

    wisedb_close(eng);
    eng = NULL;
    CHECK(remove_tree(root) == 0);
    return result;

cleanup:
    wisedb_fault_alloc_disable();
    wisedb_fault_io_disable();
    if (eng) wisedb_close(eng);
    if (root) (void)remove_tree(root);
    return result ? result : 1;
}
