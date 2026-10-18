#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE 1

#include "wisedb.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(void) {
    char root_template[] = "/tmp/wisedb-c-test-XXXXXX";
    const char *root = mkdtemp(root_template);
    if (!root) return 1;
    wisedb_engine *eng = wisedb_open(root);
    if (!eng) {
        rmdir(root);
        return 1;
    }
    char owner_error[256] = {0};
    if (wisedb_open_ex(root, owner_error, sizeof(owner_error)) != NULL
        || strstr(owner_error, "already owned") == NULL) {
        fprintf(stderr, "second writable owner was not rejected: %s\n", owner_error);
        wisedb_close(eng);
        return 1;
    }

    const char *page =
        "---\n"
        "type: concept\n"
        "slug: test-page\n"
        "title: Test\n"
        "summary: hello\n"
        "---\n"
        "Body [[other]].\n";

    char *out = NULL;
    if (wisedb_remember(eng, "test-page", page, NULL, 0, NULL, &out) != 0) {
        fprintf(stderr, "remember failed: %s\n", wisedb_last_error(eng));
        wisedb_close(eng);
        return 1;
    }
    wisedb_free_string(out);
    char expected_page_path[512];
    int expected_page_len = snprintf(expected_page_path, sizeof(expected_page_path), "%s/pages/test-page.md", root);
    if (expected_page_len < 0 || (size_t)expected_page_len >= sizeof(expected_page_path)
        || access(expected_page_path, F_OK) != 0) {
        fprintf(stderr, "remember did not persist page under %s\n", expected_page_path);
        wisedb_close(eng);
        return 1;
    }

    out = NULL;
    if (wisedb_read(eng, "test-page", 0, &out) != 0) {
        fprintf(stderr, "read failed\n");
        wisedb_close(eng);
        return 1;
    }
    if (!strstr(out, "Test")) {
        fprintf(stderr, "unexpected read output\n");
        wisedb_free_string(out);
        wisedb_close(eng);
        return 1;
    }
    wisedb_free_string(out);

    out = NULL;
    if (wisedb_list(eng, &out) != 0) {
        fprintf(stderr, "list failed\n");
        wisedb_close(eng);
        return 1;
    }
    if (!strstr(out, "test-page")) {
        fprintf(stderr, "list missing slug\n");
        wisedb_free_string(out);
        wisedb_close(eng);
        return 1;
    }
    wisedb_free_string(out);

    char *md = NULL;
    if (wisedb_read_markdown(eng, "test-page", &md) != 0) {
        fprintf(stderr, "read_markdown failed: %s\n", wisedb_last_error(eng));
        wisedb_close(eng);
        return 1;
    }
    if (!strstr(md, "test-page") || !strstr(md, "Body")) {
        fprintf(stderr, "unexpected markdown output\n");
        wisedb_free_string(md);
        wisedb_close(eng);
        return 1;
    }
    wisedb_free_string(md);

    out = NULL;
    if (wisedb_update(eng, "test-page", NULL, "Updated Title", NULL, "New body text.", &out) != 0) {
        fprintf(stderr, "update failed: %s\n", wisedb_last_error(eng));
        wisedb_close(eng);
        return 1;
    }
    if (!strstr(out, "Updated Title")) {
        fprintf(stderr, "update result missing title\n");
        wisedb_free_string(out);
        wisedb_close(eng);
        return 1;
    }
    wisedb_free_string(out);

    const uint8_t media_bytes[] = {0x89, 0x50, 0x4e, 0x47};
    out = NULL;
    if (wisedb_remember(eng, "media-node", NULL, media_bytes, sizeof(media_bytes), "assets/test.png", &out) != 0) {
        fprintf(stderr, "media remember failed: %s\n", wisedb_last_error(eng));
        wisedb_close(eng);
        return 1;
    }
    wisedb_free_string(out);

    const char *media_edit_markdown =
        "---\ntype: media\nslug: media-node\ntitle: Updated caption\n---\nCaption body.\n";
    out = NULL;
    if (wisedb_edit(eng, "media-node", media_edit_markdown, &out) != 0
        || !out || !strstr(out, "Updated caption")) {
        fprintf(stderr, "media sidecar edit failed: %s\n", wisedb_last_error(eng));
        wisedb_free_string(out);
        wisedb_close(eng);
        return 1;
    }
    wisedb_free_string(out);
    char media_path[512];
    int media_path_len = snprintf(media_path, sizeof(media_path), "%s/assets/test.png", root);
    if (media_path_len < 0 || (size_t)media_path_len >= sizeof(media_path)) return 1;
    FILE *media_file = fopen(media_path, "rb");
    uint8_t media_check[sizeof(media_bytes)];
    if (!media_file || fread(media_check, 1, sizeof(media_check), media_file) != sizeof(media_check)
        || memcmp(media_check, media_bytes, sizeof(media_bytes)) != 0) {
        if (media_file) fclose(media_file);
        fprintf(stderr, "media payload changed after sidecar edit\n");
        wisedb_close(eng);
        return 1;
    }
    fclose(media_file);

    char outside_path[512];
    int outside_len = snprintf(outside_path, sizeof(outside_path), "%s/outside.md", root);
    if (outside_len < 0 || (size_t)outside_len >= sizeof(outside_path)) return 1;
    FILE *outside_file = fopen(outside_path, "wb");
    if (!outside_file || fclose(outside_file) != 0) {
        wisedb_close(eng);
        return 1;
    }
    char escaped_link[512];
    int escaped_len = snprintf(escaped_link, sizeof(escaped_link), "%s/assets/escape.png", root);
    if (escaped_len < 0 || (size_t)escaped_len >= sizeof(escaped_link)
        || symlink(outside_path, escaped_link) != 0) {
        wisedb_close(eng);
        return 1;
    }
    out = NULL;
    if (wisedb_remember(eng, "symlink-target", NULL, media_bytes, sizeof(media_bytes),
                        "assets/escape.png", &out) == 0) {
        fprintf(stderr, "media upload replaced a symlink target\n");
        wisedb_free_string(out);
        wisedb_close(eng);
        return 1;
    }
    wisedb_free_string(out);
    if (unlink(escaped_link) != 0 || unlink(outside_path) != 0) {
        wisedb_close(eng);
        return 1;
    }

    wisedb_close(eng);
    eng = wisedb_open(root);
    if (!eng) {
        fprintf(stderr, "reopen failed\n");
        return 1;
    }

    out = NULL;
    if (wisedb_read(eng, "test-page", 0, &out) != 0
        || !strstr(out, "pages/test-page.md")
        || !strstr(out, "\"is_media\":false")) {
        fprintf(stderr, "page metadata was not preserved after reopen\n");
        wisedb_free_string(out);
        wisedb_close(eng);
        return 1;
    }
    wisedb_free_string(out);

    wisedb_engine *second_owner = wisedb_open(root);
    if (second_owner) {
        fprintf(stderr, "second writable owner unexpectedly opened\n");
        wisedb_close(second_owner);
        wisedb_close(eng);
        return 1;
    }

    out = NULL;
    if (wisedb_read(eng, "media-node", 0, &out) != 0
        || !strstr(out, "assets/test.png")
        || !strstr(out, "image/png")
        || !strstr(out, "Updated caption")
        || !strstr(out, "\"is_media\":true")) {
        fprintf(stderr, "media metadata was not preserved after reopen\n");
        wisedb_free_string(out);
        wisedb_close(eng);
        return 1;
    }
    wisedb_free_string(out);

    out = NULL;
    if (wisedb_search(eng, "hello", 256, 5, &out) != 0) {
        fprintf(stderr, "search failed\n");
        wisedb_close(eng);
        return 1;
    }
    wisedb_free_string(out);

    if (wisedb_delete(eng, "media-node") != 0) {
        fprintf(stderr, "media delete failed: %s\n", wisedb_last_error(eng));
        wisedb_close(eng);
        return 1;
    }

    if (wisedb_delete(eng, "test-page") != 0) {
        fprintf(stderr, "delete failed: %s\n", wisedb_last_error(eng));
        wisedb_close(eng);
        return 1;
    }

    out = NULL;
    if (wisedb_read(eng, "test-page", 0, &out) == 0) {
        fprintf(stderr, "read after delete should fail\n");
        wisedb_free_string(out);
        wisedb_close(eng);
        return 1;
    }

    wisedb_close(eng);
    char cleanup_path[512];
    const char *cleanup_rel[] = {
        "pages/test-page.md", "assets/test.png", "assets/test.png.md",
        "_wisedb/catalog.json", "_wisedb/OWNER.lock"
    };
    for (size_t i = 0; i < sizeof(cleanup_rel) / sizeof(cleanup_rel[0]); i++) {
        if (snprintf(cleanup_path, sizeof(cleanup_path), "%s/%s", root, cleanup_rel[i]) > 0) unlink(cleanup_path);
    }
    snprintf(cleanup_path, sizeof(cleanup_path), "%s/pages", root);
    rmdir(cleanup_path);
    snprintf(cleanup_path, sizeof(cleanup_path), "%s/assets", root);
    rmdir(cleanup_path);
    snprintf(cleanup_path, sizeof(cleanup_path), "%s/_wisedb", root);
    rmdir(cleanup_path);
    rmdir(root);
    return 0;
}
