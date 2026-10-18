#define _POSIX_C_SOURCE 200809L
#define _DARWIN_C_SOURCE 1

#include "wisedb.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int insert_page(wisedb_engine *eng, const char *slug, const char *markdown) {
    char *out = NULL;
    int rc = wisedb_remember(eng, slug, markdown, NULL, 0, NULL, &out);
    if (out) wisedb_free_string(out);
    return rc;
}

static int edge_exists(wisedb_engine *eng, const char *from_slug, const char *to_slug) {
    char *out = NULL;
    if (wisedb_query_edges(eng, from_slug, to_slug, "links_to", &out) != 0) {
        if (out) wisedb_free_string(out);
        return 0;
    }
    int found = out && strstr(out, to_slug) != NULL;
    wisedb_free_string(out);
    return found;
}

int main(void) {
    char root_template[] = "/tmp/wisedb-semantics-XXXXXX";
    const char *root = mkdtemp(root_template);
    if (!root) return 1;

    wisedb_engine *eng = wisedb_open(root);
    if (!eng) return 1;

    const char *target =
        "---\n"
        "type: concept\n"
        "slug: target-node\n"
        "title: Target Title\n"
        "summary: s\n"
        "---\n"
        "Body.\n";

    const char *source_body =
        "---\n"
        "type: concept\n"
        "slug: source-node\n"
        "title: Source\n"
        "summary: s\n"
        "---\n"
        "See {{Target Title}} here.\n";

    const char *source_title =
        "---\n"
        "type: concept\n"
        "slug: title-source\n"
        "title: Mentions {{Target Title}}\n"
        "summary: s\n"
        "---\n"
        "Body.\n";

    if (insert_page(eng, "target-node", target) != 0
        || insert_page(eng, "source-node", source_body) != 0
        || insert_page(eng, "title-source", source_title) != 0) {
        fprintf(stderr, "insert failed: %s\n", wisedb_last_error(eng));
        wisedb_close(eng);
        return 1;
    }

    if (edge_exists(eng, "source-node", "target-node")
        || edge_exists(eng, "title-source", "target-node")) {
        fprintf(stderr, "semantic edges present while semantics disabled\n");
        wisedb_close(eng);
        return 1;
    }

    char *settings = NULL;
    if (wisedb_set_semantics(eng, 1, &settings) != 0 || !settings || !strstr(settings, "true")) {
        fprintf(stderr, "enable semantics failed: %s\n", wisedb_last_error(eng));
        wisedb_free_string(settings);
        wisedb_close(eng);
        return 1;
    }
    wisedb_free_string(settings);

    if (!edge_exists(eng, "source-node", "target-node")
        || !edge_exists(eng, "title-source", "target-node")) {
        fprintf(stderr, "semantic edges missing after enable\n");
        wisedb_close(eng);
        return 1;
    }

    const char *self_loop =
        "---\n"
        "type: concept\n"
        "slug: loop-node\n"
        "title: Loop Title\n"
        "summary: s\n"
        "---\n"
        "Self {{Loop Title}}.\n";
    if (insert_page(eng, "loop-node", self_loop) != 0) {
        fprintf(stderr, "loop insert failed\n");
        wisedb_close(eng);
        return 1;
    }
    if (edge_exists(eng, "loop-node", "loop-node")) {
        fprintf(stderr, "self semantic edge was created\n");
        wisedb_close(eng);
        return 1;
    }

    const char *dup_target =
        "---\n"
        "type: concept\n"
        "slug: dup-target\n"
        "title: Shared Name\n"
        "summary: s\n"
        "---\n"
        "One.\n";
    const char *dup_target2 =
        "---\n"
        "type: concept\n"
        "slug: dup-target-2\n"
        "title: Shared Name\n"
        "summary: s\n"
        "---\n"
        "Two.\n";
    const char *dup_source =
        "---\n"
        "type: concept\n"
        "slug: dup-source\n"
        "title: Dup Source\n"
        "summary: s\n"
        "---\n"
        "Both {{Shared Name}}.\n";
    if (insert_page(eng, "dup-target", dup_target) != 0
        || insert_page(eng, "dup-target-2", dup_target2) != 0
        || insert_page(eng, "dup-source", dup_source) != 0) {
        fprintf(stderr, "duplicate title setup failed\n");
        wisedb_close(eng);
        return 1;
    }
    if (!edge_exists(eng, "dup-source", "dup-target")
        || !edge_exists(eng, "dup-source", "dup-target-2")) {
        fprintf(stderr, "duplicate title edges missing\n");
        wisedb_close(eng);
        return 1;
    }

    if (wisedb_update(eng, "target-node", NULL, "Renamed Title", NULL, NULL, &settings) != 0) {
        fprintf(stderr, "title rename failed\n");
        wisedb_close(eng);
        return 1;
    }
    wisedb_free_string(settings);
    if (edge_exists(eng, "source-node", "target-node")) {
        fprintf(stderr, "edge still points after title rename away from mention\n");
        wisedb_close(eng);
        return 1;
    }

    if (wisedb_set_semantics(eng, 0, &settings) != 0) {
        fprintf(stderr, "disable semantics failed\n");
        wisedb_close(eng);
        return 1;
    }
    wisedb_free_string(settings);
    if (edge_exists(eng, "dup-source", "dup-target")) {
        fprintf(stderr, "semantic edges still present after disable\n");
        wisedb_close(eng);
        return 1;
    }

    wisedb_close(eng);
    return 0;
}
