#include "wisedb_internal.h"
#include "catalog_filter.h"

#ifndef _WIN32
#include <unistd.h>
#endif

#include "fault_injection.h"

static const char *WISEDB_SCHEMA_TEXT =
    "# WiseDB agent memory (v3)\n\n"
    "Operations: remember, read, edit, search, list, update, delete.\n\n"
    "## Page format (WikiLLM)\n"
    "```markdown\n"
    "---\n"
    "type: entity\n"
    "slug: my-topic\n"
    "title: My Topic\n"
    "summary: One-line for catalog\n"
    "---\n"
    "Body with [[other-slug]] links.\n"
    "```\n\n"
    "Types: entity, concept, source, comparison, synthesis, playbook, media.\n"
    "Media files (jpg, png, mp3, mp4, ...) are nodes; use sidecar slug.md for caption/links.\n"
    "Max page tokens: 4096 (approx chars/4).\n";

static int metadata_line_safe(const char *value) {
    if (!value) return 1;
    if (!wisedb_json_is_valid_utf8(value)) return 0;
    for (const unsigned char *p = (const unsigned char *)value; *p; p++) {
        if (*p == '\n' || *p == '\r' || *p < 0x20) return 0;
    }
    size_t length = strlen(value);
    if (length > 0 && (isspace((unsigned char)value[0]) || isspace((unsigned char)value[length - 1]))) return 0;
    return 1;
}

static int node_path_available(const wisedb_engine *eng, const wisedb_node *node) {
    if (!eng || !node || !node->slug || !node->rel_path) return 0;
    int matching_node_exists = 0;
    for (size_t i = 0; i < eng->node_count; i++) {
        const wisedb_node *current = &eng->nodes[i];
        if (!current->slug || !current->rel_path) return 0;
        if (strcmp(current->rel_path, node->rel_path) == 0 && strcmp(current->slug, node->slug) != 0) return 0;
        if (strcmp(current->slug, node->slug) == 0 && strcmp(current->rel_path, node->rel_path) != 0) return 0;
        if (strcmp(current->slug, node->slug) == 0 && strcmp(current->rel_path, node->rel_path) == 0) matching_node_exists = 1;
    }
#ifndef _WIN32
    struct stat path_stat;
    if (wisedb_stat_relative_no_follow((wisedb_engine *)eng, node->rel_path, &path_stat) == 0) {
        if (S_ISLNK(path_stat.st_mode) || !S_ISREG(path_stat.st_mode)) return 0;
        if (!matching_node_exists) return 0;
    } else if (errno != ENOENT) {
        return 0;
    }
    if (node->is_media) {
        char sidecar[WISEDB_MAX_PATH];
        int size = snprintf(sidecar, sizeof(sidecar), "%s.md", node->rel_path);
        if (size < 0 || (size_t)size >= sizeof(sidecar)) return 0;
        if (wisedb_stat_relative_no_follow((wisedb_engine *)eng, sidecar, &path_stat) == 0) {
            if (S_ISLNK(path_stat.st_mode) || !S_ISREG(path_stat.st_mode) || !matching_node_exists) return 0;
        } else if (errno != ENOENT) {
            return 0;
        }
    }
#endif
    return 1;
}

static int metadata_safe(const wisedb_node *node) {
    return node && metadata_line_safe(node->slug) && metadata_line_safe(node->title)
        && metadata_line_safe(node->summary) && metadata_line_safe(node->mime)
        && wisedb_json_is_valid_utf8(node->body) && wisedb_json_is_valid_utf8(node->rel_path);
}

static void copy_open_error(char *out, size_t capacity, const char *message) {
    if (out && capacity > 0) (void)snprintf(out, capacity, "%s", message ? message : "");
}

wisedb_engine *wisedb_open_ex(const char *wiki_root, char *error_out, size_t error_capacity) {
    copy_open_error(error_out, error_capacity, "");
    if (!wiki_root || !wiki_root[0]) {
        copy_open_error(error_out, error_capacity, "wiki root is empty");
        return NULL;
    }
    wisedb_engine *eng = (wisedb_engine *)calloc(1, sizeof(wisedb_engine));
    if (!eng) {
        copy_open_error(error_out, error_capacity, "out of memory allocating engine");
        return NULL;
    }
#ifndef _WIN32
    eng->root_fd = -1;
    eng->owner_lock_fd = -1;
#endif
    if (wisedb_ensure_root_dir(wiki_root) != 0) {
        copy_open_error(error_out, error_capacity, "cannot create or access wiki root");
        wisedb_close(eng);
        return NULL;
    }
    if (wisedb_lock_root(eng, wiki_root) != 0) {
        if (errno == EWOULDBLOCK || errno == EAGAIN || errno == EACCES)
            copy_open_error(error_out, error_capacity, "wiki root is already owned by another process");
        else copy_open_error(error_out, error_capacity, "wiki root is unsafe or cannot be exclusively locked");
        wisedb_close(eng);
        return NULL;
    }
    char *canonical_root = realpath(wiki_root, NULL);
    if (!canonical_root) {
        copy_open_error(error_out, error_capacity, "cannot resolve wiki root");
        wisedb_close(eng);
        return NULL;
    }
#ifndef _WIN32
    struct stat opened_stat, named_stat;
    if (fstat(eng->root_fd, &opened_stat) != 0 || lstat(canonical_root, &named_stat) != 0
        || !S_ISDIR(named_stat.st_mode) || S_ISLNK(named_stat.st_mode)
        || opened_stat.st_dev != named_stat.st_dev || opened_stat.st_ino != named_stat.st_ino) {
        free(canonical_root);
        copy_open_error(error_out, error_capacity, "wiki root changed while it was being opened");
        wisedb_close(eng);
        return NULL;
    }
#endif
    eng->wiki_root = canonical_root;
    if (wisedb_scan_wiki(eng) != 0) {
        copy_open_error(error_out, error_capacity, "wiki scan failed; refusing partially loaded catalog");
        wisedb_close(eng);
        return NULL;
    }
    return eng;
}

wisedb_engine *wisedb_open(const char *wiki_root) {
    return wisedb_open_ex(wiki_root, NULL, 0);
}

void wisedb_close(wisedb_engine *eng) {
    if (!eng) return;
    for (size_t i = 0; i < eng->node_count; i++) wisedb_free_node(&eng->nodes[i]);
    free(eng->nodes);
    for (size_t i = 0; i < eng->edge_count; i++) {
        free(eng->edges[i].from_slug);
        free(eng->edges[i].to_slug);
        free(eng->edges[i].edge_type);
    }
    free(eng->edges);
    free(eng->wiki_root);
    free(eng->last_error);
    wisedb_storage_close(eng);
    free(eng);
}

int wisedb_refresh(wisedb_engine *eng) {
    if (!eng || !eng->wiki_root) return -1;
    wisedb_engine staged;
    memset(&staged, 0, sizeof(staged));
    staged.wiki_root = eng->wiki_root;
#ifndef _WIN32
    staged.root_fd = eng->root_fd;
    staged.owner_lock_fd = eng->owner_lock_fd;
#endif
    if (wisedb_scan_wiki(&staged) != 0) {
        for (size_t i = 0; i < staged.node_count; i++) wisedb_free_node(&staged.nodes[i]);
        free(staged.nodes);
        for (size_t i = 0; i < staged.edge_count; i++) {
            free(staged.edges[i].from_slug);
            free(staged.edges[i].to_slug);
            free(staged.edges[i].edge_type);
        }
        free(staged.edges);
        wisedb_set_error(eng, "refresh failed; keeping previous in-memory index");
        return -1;
    }
    for (size_t i = 0; i < staged.node_count; i++) {
        if (!metadata_safe(&staged.nodes[i])) {
            for (size_t j = 0; j < staged.node_count; j++) wisedb_free_node(&staged.nodes[j]);
            free(staged.nodes);
            for (size_t j = 0; j < staged.edge_count; j++) {
                free(staged.edges[j].from_slug);
                free(staged.edges[j].to_slug);
                free(staged.edges[j].edge_type);
            }
            free(staged.edges);
            wisedb_set_error(eng, "refresh found invalid UTF-8 or metadata; keeping previous catalog");
            return -1;
        }
    }
    for (size_t i = 0; i < eng->node_count; i++) wisedb_free_node(&eng->nodes[i]);
    free(eng->nodes);
    for (size_t i = 0; i < eng->edge_count; i++) {
        free(eng->edges[i].from_slug);
        free(eng->edges[i].to_slug);
        free(eng->edges[i].edge_type);
    }
    free(eng->edges);
    eng->nodes = staged.nodes;
    eng->node_count = staged.node_count;
    eng->node_cap = staged.node_cap;
    eng->edges = staged.edges;
    eng->edge_count = staged.edge_count;
    eng->edge_cap = staged.edge_cap;
    eng->avg_doc_len = staged.avg_doc_len;
    eng->doc_count = staged.doc_count;
    eng->semantics_enabled = staged.semantics_enabled;
    return 0;
}

int wisedb_root_is_current(wisedb_engine *eng) {
    return wisedb_storage_root_is_current(eng);
}

void wisedb_free_string(char *s) { free(s); }


const char *wisedb_schema(void) { return WISEDB_SCHEMA_TEXT; }

const char *wisedb_last_error(wisedb_engine *eng) {
    return eng && eng->last_error ? eng->last_error : "";
}

static char *build_result_json(
    wisedb_engine *eng,
    const wisedb_node *node,
    char **links,
    size_t link_count,
    const char *status,
    const wisedb_edge *result_edges,
    size_t result_edge_count
) {
    wisedb_json_buffer buffer;
    wisedb_json_buffer_init(&buffer);
    if (wisedb_json_buffer_append(&buffer, "{\"status\":") != 0
        || wisedb_json_buffer_append_string(&buffer, status) != 0
        || wisedb_json_buffer_append(&buffer, ",\"node\":") != 0) goto fail;
    if (!node) {
        if (wisedb_json_buffer_append(&buffer, "null") != 0) goto fail;
    } else if (wisedb_json_buffer_append(&buffer, "{\"slug\":") != 0
        || wisedb_json_buffer_append_string(&buffer, node->slug) != 0
        || wisedb_json_buffer_append(&buffer, ",\"type\":") != 0
        || wisedb_json_buffer_append_string(&buffer, wisedb_type_to_string(node->type)) != 0
        || wisedb_json_buffer_append(&buffer, ",\"title\":") != 0
        || wisedb_json_buffer_append_string(&buffer, node->title) != 0
        || wisedb_json_buffer_append(&buffer, ",\"summary\":") != 0
        || wisedb_json_buffer_append_string(&buffer, node->summary) != 0
        || wisedb_json_buffer_append(&buffer, ",\"body\":") != 0
        || wisedb_json_buffer_append_string(&buffer, node->body) != 0
        || wisedb_json_buffer_append(&buffer, ",\"rel_path\":") != 0
        || wisedb_json_buffer_append_string(&buffer, node->rel_path) != 0
        || wisedb_json_buffer_append(&buffer, ",\"mime\":") != 0
        || wisedb_json_buffer_append_string(&buffer, node->mime) != 0
        || wisedb_json_buffer_appendf(&buffer, ",\"token_count\":%d,\"is_media\":%s}",
                                     node->token_count, node->is_media ? "true" : "false") != 0) goto fail;

    if (wisedb_json_buffer_append(&buffer, ",\"unknown_slugs\":[") != 0) goto fail;
    int first = 1;
    for (size_t i = 0; i < link_count; i++) {
        if (!links[i] || wisedb_find_node(eng, links[i])
            || (node && node->slug && strcmp(node->slug, links[i]) == 0)) continue;
        if ((!first && wisedb_json_buffer_append(&buffer, ",") != 0)
            || wisedb_json_buffer_append_string(&buffer, links[i]) != 0) goto fail;
        first = 0;
    }
    if (wisedb_json_buffer_append(&buffer, "],\"backlinks\":[") != 0) goto fail;
    first = 1;
    if (node && node->slug) {
        const wisedb_edge *edges = result_edges ? result_edges : eng->edges;
        size_t edge_count = result_edges ? result_edge_count : eng->edge_count;
        for (size_t e = 0; e < edge_count; e++) {
            if (!edges[e].to_slug || strcmp(edges[e].to_slug, node->slug) != 0) continue;
            if ((!first && wisedb_json_buffer_append(&buffer, ",") != 0)
                || wisedb_json_buffer_append_string(&buffer, edges[e].from_slug) != 0) goto fail;
            first = 0;
        }
    }
    if (wisedb_json_buffer_append(&buffer, "]}") != 0) goto fail;
    return wisedb_json_buffer_take(&buffer);

fail:
    wisedb_json_buffer_dispose(&buffer);
    return NULL;
}

int wisedb_remember(
    wisedb_engine *eng,
    const char *slug,
    const char *markdown,
    const uint8_t *file_bytes,
    size_t file_len,
    const char *rel_path,
    char **result_json_out
) {
    if (!result_json_out) return -1;
    *result_json_out = NULL;
    if (!eng || !slug) return -1;
    if (!slug[0] || strlen(slug) >= WISEDB_MAX_SLUG) {
        wisedb_set_error(eng, "slug is empty or exceeds the legacy byte limit");
        return -1;
    }
    if (!metadata_line_safe(slug)) {
        wisedb_set_error(eng, "slug contains an invalid line break or control character");
        return -1;
    }

    wisedb_node parsed;
    memset(&parsed, 0, sizeof(parsed));
    wisedb_prepared_index prepared_index;
    memset(&prepared_index, 0, sizeof(prepared_index));
    char **links = NULL;
    size_t link_count = 0;
    char **mentions = NULL;
    size_t mention_count = 0;

    if (file_bytes && file_len > 0) {
        char rpath[WISEDB_MAX_PATH];
        if (rel_path && rel_path[0]) {
            int path_len = snprintf(rpath, sizeof(rpath), "%s", rel_path);
            if (path_len < 0 || (size_t)path_len >= sizeof(rpath)) {
                wisedb_set_error(eng, "media path is too long");
                return -1;
            }
        } else {
            int path_len = snprintf(rpath, sizeof(rpath), "media/%s.bin", slug);
            if (path_len < 0 || (size_t)path_len >= sizeof(rpath)) {
                wisedb_set_error(eng, "media path is too long");
                return -1;
            }
        }
        if (!wisedb_is_media_path(rpath) || wisedb_validate_relative_path(eng, rpath) != 0) {
            wisedb_set_error(eng, "invalid or unsafe media path");
            return -1;
        }
        int payload_exists = 0;
        if (wisedb_file_exists(eng, rpath, &payload_exists) != 0) {
            wisedb_set_error(eng, "cannot inspect media payload path");
            return -1;
        }
        if (payload_exists) {
            wisedb_set_error(eng, "media payload already exists; remember is create-only");
            return -1;
        }
        wisedb_node sidecar_node;
        memset(&sidecar_node, 0, sizeof(sidecar_node));
        if (markdown) {
            if (wisedb_parse_markdown(markdown, &sidecar_node, &links, &link_count, &mentions, &mention_count) != 0) {
                wisedb_free_node(&sidecar_node);
                wisedb_set_error(eng, "media sidecar parse failed");
                return -1;
            }
            if (!sidecar_node.slug || !sidecar_node.slug[0]) {
                free(sidecar_node.slug);
                sidecar_node.slug = strdup(slug);
            }
            if (!sidecar_node.slug || strcmp(sidecar_node.slug, slug) != 0) {
                wisedb_free_node(&sidecar_node);
                wisedb_free_parsed_links(links, link_count);
                wisedb_set_error(eng, "remember slug does not match markdown slug");
                return -1;
            }
        } else {
            sidecar_node.slug = strdup(slug);
            sidecar_node.title = strdup(slug);
            sidecar_node.summary = strdup("");
            sidecar_node.body = strdup("");
            if (!sidecar_node.slug || !sidecar_node.title || !sidecar_node.summary || !sidecar_node.body) {
                wisedb_free_node(&sidecar_node);
                wisedb_set_error(eng, "out of memory preparing media metadata");
                return -1;
            }
        }

        parsed.slug = sidecar_node.slug;
        sidecar_node.slug = NULL;
        parsed.title = sidecar_node.title;
        sidecar_node.title = NULL;
        parsed.summary = sidecar_node.summary;
        sidecar_node.summary = NULL;
        parsed.body = sidecar_node.body;
        sidecar_node.body = NULL;
        parsed.token_count = sidecar_node.token_count;
        wisedb_free_node(&sidecar_node);
        parsed.rel_path = strdup(rpath);
        parsed.is_media = 1;
        parsed.type = WISEDB_NODE_MEDIA;
        parsed.mime = strdup(wisedb_mime_from_ext(rpath));
        parsed.mtime = time(NULL);
        if (!parsed.slug || !parsed.rel_path || !parsed.mime) {
            wisedb_free_node(&parsed);
            wisedb_free_parsed_links(links, link_count);
            wisedb_set_error(eng, "out of memory preparing media node");
            return -1;
        }
        if (!metadata_safe(&parsed) || !node_path_available(eng, &parsed)) {
            wisedb_free_node(&parsed);
            wisedb_free_parsed_links(links, link_count);
            wisedb_set_error(eng, "media metadata is invalid or slug/path conflicts with an existing node");
            return -1;
        }
        if (parsed.token_count > WISEDB_MAX_PAGE_TOKENS) {
            wisedb_free_node(&parsed);
            wisedb_free_parsed_links(links, link_count);
            wisedb_set_error(eng, "media caption exceeds token limit");
            return -1;
        }
        if (wisedb_index_prepare(eng, &parsed, links, link_count, mentions, mention_count, &prepared_index) != 0) {
            wisedb_free_node(&parsed);
            wisedb_free_parsed_links(links, link_count);
            wisedb_set_error(eng, "failed to prepare media index");
            return -1;
        }

        char *serialized = wisedb_serialize_page(&parsed);
        if (!serialized) {
            wisedb_index_discard(&prepared_index);
            free(serialized);
            wisedb_free_node(&parsed);
            wisedb_free_parsed_links(links, link_count);
            wisedb_set_error(eng, "out of memory serializing media sidecar");
            return -1;
        }
        if (wisedb_save_node_file(eng, &parsed, serialized) != 0) {
            wisedb_index_discard(&prepared_index);
            free(serialized);
            wisedb_free_node(&parsed);
            wisedb_free_parsed_links(links, link_count);
            wisedb_free_parsed_links(mentions, mention_count);
            wisedb_set_error(eng, "failed to publish media sidecar");
            return -1;
        }
        free(serialized);
        if (wisedb_write_file(eng, rpath, file_bytes, file_len, 0) != 0) {
            char sidecar_rel[WISEDB_MAX_PATH];
            int sidecar_len = snprintf(sidecar_rel, sizeof(sidecar_rel), "%s.md", rpath);
            if (sidecar_len > 0 && (size_t)sidecar_len < sizeof(sidecar_rel)) {
                (void)wisedb_remove_file(eng, sidecar_rel);
            }
            wisedb_index_discard(&prepared_index);
            wisedb_free_node(&parsed);
            wisedb_free_parsed_links(links, link_count);
            wisedb_free_parsed_links(mentions, mention_count);
            wisedb_set_error(eng, "failed to publish media payload; sidecar rollback attempted");
            return -1;
        }
        wisedb_index_publish(eng, &prepared_index);
        {
            wisedb_node *stored = wisedb_find_node(eng, slug);
            char *result_json = build_result_json(eng, stored, links, link_count, "ok", NULL, 0);
            if (!result_json) {
                wisedb_free_node(&parsed);
                wisedb_free_parsed_links(links, link_count);
                wisedb_free_parsed_links(mentions, mention_count);
                wisedb_set_error(eng, "out of memory preparing media response");
                return -1;
            }
            *result_json_out = result_json;
        }
    } else if (markdown) {
        if (wisedb_parse_markdown(markdown, &parsed, &links, &link_count, &mentions, &mention_count) != 0) {
            wisedb_set_error(eng, "parse failed");
            return -1;
        }
        if (!parsed.slug || !parsed.slug[0]) {
            free(parsed.slug);
            parsed.slug = strdup(slug);
        }
        if (!parsed.slug || strcmp(parsed.slug, slug) != 0) {
            wisedb_free_node(&parsed);
            wisedb_free_parsed_links(links, link_count);
            wisedb_set_error(eng, "remember slug does not match markdown slug");
            return -1;
        }
        if (parsed.token_count > WISEDB_MAX_PAGE_TOKENS) {
            wisedb_set_error(eng, "page exceeds token limit; split into multiple pages");
            wisedb_free_node(&parsed);
            wisedb_free_parsed_links(links, link_count);
            return -1;
        }
        char rpath[WISEDB_MAX_PATH];
        int path_len;
        if (rel_path && rel_path[0]) path_len = snprintf(rpath, sizeof(rpath), "%s", rel_path);
        else path_len = snprintf(rpath, sizeof(rpath), "pages/%s.md", parsed.slug ? parsed.slug : "");
        if (path_len < 0 || (size_t)path_len >= sizeof(rpath)
            || wisedb_validate_relative_path(eng, rpath) != 0) {
            wisedb_set_error(eng, "invalid or unsafe page path");
            wisedb_free_node(&parsed);
            wisedb_free_parsed_links(links, link_count);
            return -1;
        }
        parsed.rel_path = strdup(rpath);
        parsed.is_media = 0;
        if (!parsed.mime) parsed.mime = strdup("text/markdown");
        if (!parsed.rel_path || !parsed.mime || !metadata_safe(&parsed)
            || !node_path_available(eng, &parsed)) {
            wisedb_set_error(eng, "page metadata is invalid or slug/path conflicts with an existing node");
            wisedb_free_node(&parsed);
            wisedb_free_parsed_links(links, link_count);
            return -1;
        }
        if (wisedb_index_prepare(eng, &parsed, links, link_count, mentions, mention_count, &prepared_index) != 0) {
            wisedb_set_error(eng, "failed to prepare page index");
            wisedb_free_node(&parsed);
            wisedb_free_parsed_links(links, link_count);
            return -1;
        }
        char *serialized = wisedb_serialize_page(&parsed);
        if (!serialized) {
            wisedb_index_discard(&prepared_index);
            wisedb_free_node(&parsed);
            wisedb_free_parsed_links(links, link_count);
            return -1;
        }
        if (wisedb_save_node_file(eng, &parsed, serialized) != 0) {
            wisedb_index_discard(&prepared_index);
            free(serialized);
            wisedb_set_error(eng, "write failed");
            wisedb_free_node(&parsed);
            wisedb_free_parsed_links(links, link_count);
            wisedb_free_parsed_links(mentions, mention_count);
            return -1;
        }
        free(serialized);
        wisedb_index_publish(eng, &prepared_index);
        {
            wisedb_node *stored = wisedb_find_node(eng, slug);
            char *result_json = build_result_json(eng, stored, links, link_count, "ok", NULL, 0);
            if (!result_json) {
                wisedb_free_node(&parsed);
                wisedb_free_parsed_links(links, link_count);
                wisedb_free_parsed_links(mentions, mention_count);
                wisedb_set_error(eng, "out of memory preparing page response");
                return -1;
            }
            *result_json_out = result_json;
        }
    } else {
        wisedb_set_error(eng, "markdown or file_bytes required");
        return -1;
    }

    wisedb_free_parsed_links(links, link_count);
    wisedb_free_parsed_links(mentions, mention_count);
    wisedb_free_node(&parsed);

    /* update avg */
    double total = 0;
    for (size_t i = 0; i < eng->node_count; i++) total += eng->nodes[i].token_count;
    eng->doc_count = eng->node_count;
    eng->avg_doc_len = eng->doc_count ? total / eng->doc_count : 0;
    return 0;
}

/* removed unused forward decl */
int wisedb_read(
    wisedb_engine *eng,
    const char *slug,
    int include_neighbors,
    char **result_json_out
) {
    if (!eng || !slug || !result_json_out) return -1;
    *result_json_out = NULL;
    wisedb_node *n = wisedb_find_node(eng, slug);
    if (!n) {
        wisedb_set_error(eng, "slug not found: %s", slug);
        return -1;
    }
    *result_json_out = NULL;
    wisedb_json_buffer buffer;
    wisedb_json_buffer_init(&buffer);
    if (wisedb_json_buffer_append(&buffer,
        "{\"status\":\"ok\",\"node\":{\"slug\":") != 0
        || wisedb_json_buffer_append_string(&buffer, n->slug) != 0
        || wisedb_json_buffer_append(&buffer, ",\"type\":") != 0
        || wisedb_json_buffer_append_string(&buffer, wisedb_type_to_string(n->type)) != 0
        || wisedb_json_buffer_append(&buffer, ",\"title\":") != 0
        || wisedb_json_buffer_append_string(&buffer, n->title) != 0
        || wisedb_json_buffer_append(&buffer, ",\"summary\":") != 0
        || wisedb_json_buffer_append_string(&buffer, n->summary) != 0
        || wisedb_json_buffer_append(&buffer, ",\"body\":") != 0
        || wisedb_json_buffer_append_string(&buffer, n->body) != 0
        || wisedb_json_buffer_append(&buffer, ",\"rel_path\":") != 0
        || wisedb_json_buffer_append_string(&buffer, n->rel_path) != 0
        || wisedb_json_buffer_append(&buffer, ",\"mime\":") != 0
        || wisedb_json_buffer_append_string(&buffer, n->mime) != 0
        || wisedb_json_buffer_appendf(&buffer,
            ",\"token_count\":%d,\"is_media\":%s},\"neighbors\":[",
            n->token_count, n->is_media ? "true" : "false") != 0) goto read_fail;
    if (include_neighbors) {
        wisedb_node **neighbors = NULL;
        size_t nc = 0;
        if (wisedb_get_neighbors(eng, slug, &neighbors, &nc) != 0) goto read_fail;
        for (size_t i = 0; i < nc; i++) {
            if ((i > 0 && wisedb_json_buffer_append(&buffer, ",") != 0)
                || wisedb_json_buffer_append(&buffer, "{\"slug\":") != 0
                || wisedb_json_buffer_append_string(&buffer, neighbors[i]->slug) != 0
                || wisedb_json_buffer_append(&buffer, ",\"title\":") != 0
                || wisedb_json_buffer_append_string(&buffer, neighbors[i]->title) != 0
                || wisedb_json_buffer_append(&buffer, ",\"summary\":") != 0
                || wisedb_json_buffer_append_string(&buffer, neighbors[i]->summary) != 0
                || wisedb_json_buffer_append(&buffer, ",\"type\":") != 0
                || wisedb_json_buffer_append_string(&buffer, wisedb_type_to_string(neighbors[i]->type)) != 0
                || wisedb_json_buffer_append(&buffer, "}") != 0) {
                free(neighbors);
                goto read_fail;
            }
        }
        free(neighbors);
    }
    if (wisedb_json_buffer_append(&buffer, "]}") != 0) goto read_fail;
    *result_json_out = wisedb_json_buffer_take(&buffer);
    if (!*result_json_out) goto read_fail;
    return 0;

read_fail:
    wisedb_json_buffer_dispose(&buffer);
    wisedb_set_error(eng, "out of memory serializing read response");
    return -1;
}

int wisedb_edit(
    wisedb_engine *eng,
    const char *slug,
    const char *markdown,
    char **result_json_out
) {
    if (!result_json_out) return -1;
    *result_json_out = NULL;
    if (!eng || !slug || !markdown) return -1;
    if (!metadata_line_safe(slug)) {
        wisedb_set_error(eng, "slug contains an invalid line break or control character");
        return -1;
    }
    wisedb_node *existing = wisedb_find_node(eng, slug);
    if (!existing) {
        wisedb_set_error(eng, "slug not found: %s", slug);
        return -1;
    }
    wisedb_node updated;
    memset(&updated, 0, sizeof(updated));
    char **links = NULL;
    size_t link_count = 0;
    char **mentions = NULL;
    size_t mention_count = 0;
    if (wisedb_parse_markdown(markdown, &updated, &links, &link_count, &mentions, &mention_count) != 0) {
        wisedb_set_error(eng, "edit markdown parse failed");
        return -1;
    }
    if (!updated.slug) updated.slug = strdup(slug);
    if (!updated.slug || strcmp(updated.slug, slug) != 0) {
        wisedb_set_error(eng, "edit cannot change the page slug");
        wisedb_free_node(&updated);
        wisedb_free_parsed_links(links, link_count);
        return -1;
    }
    if (updated.token_count > WISEDB_MAX_PAGE_TOKENS) {
        wisedb_set_error(eng, "page exceeds token limit; split into multiple pages");
        wisedb_free_node(&updated);
        wisedb_free_parsed_links(links, link_count);
        return -1;
    }
    updated.rel_path = strdup(existing->rel_path);
    updated.mime = strdup(existing->mime ? existing->mime : "text/markdown");
    updated.is_media = existing->is_media;
    if (existing->is_media) updated.type = WISEDB_NODE_MEDIA;
    updated.mtime = time(NULL);

    if (!updated.slug || !updated.title || !updated.summary || !updated.body
        || !updated.rel_path || !updated.mime) {
        wisedb_set_error(eng, "out of memory preparing updated page");
        wisedb_free_node(&updated);
        wisedb_free_parsed_links(links, link_count);
        return -1;
    }
    if (!metadata_safe(&updated)) {
        wisedb_set_error(eng, "updated page contains invalid UTF-8 or control characters");
        wisedb_free_node(&updated);
        wisedb_free_parsed_links(links, link_count);
        return -1;
    }
    wisedb_prepared_index prepared_index;
    if (wisedb_index_prepare(eng, &updated, links, link_count, mentions, mention_count, &prepared_index) != 0) {
        wisedb_set_error(eng, "failed to prepare edited page index");
        wisedb_free_node(&updated);
        wisedb_free_parsed_links(links, link_count);
        return -1;
    }
    char *serialized = wisedb_serialize_page(&updated);
    if (!serialized) {
        wisedb_index_discard(&prepared_index);
        wisedb_set_error(eng, "failed to prepare edited page response");
        wisedb_free_node(&updated);
        wisedb_free_parsed_links(links, link_count);
        wisedb_free_parsed_links(mentions, mention_count);
        return -1;
    }
    if (wisedb_save_node_file(eng, &updated, serialized) != 0) {
        wisedb_index_discard(&prepared_index);
        free(serialized);
        wisedb_set_error(eng, "failed to write edited page");
        wisedb_free_node(&updated);
        wisedb_free_parsed_links(links, link_count);
        wisedb_free_parsed_links(mentions, mention_count);
        return -1;
    }
    free(serialized);
    wisedb_index_publish(eng, &prepared_index);
    wisedb_node *stored = wisedb_find_node(eng, slug);
    char *result_json = build_result_json(eng, stored, links, link_count, "ok", NULL, 0);
    if (!result_json) {
        wisedb_set_error(eng, "failed to prepare edited page response");
        wisedb_free_node(&updated);
        wisedb_free_parsed_links(links, link_count);
        wisedb_free_parsed_links(mentions, mention_count);
        return -1;
    }
    *result_json_out = result_json;
    wisedb_free_node(&updated);
    wisedb_free_parsed_links(links, link_count);
    wisedb_free_parsed_links(mentions, mention_count);
    return 0;
}

int wisedb_search(
    wisedb_engine *eng,
    const char *query,
    int budget_tokens,
    int top_k,
    char **result_json_out
) {
    if (!result_json_out) return -1;
    *result_json_out = NULL;
    if (!eng || !query) return -1;
    return wisedb_build_search_json(eng, query, budget_tokens, top_k, result_json_out);
}

int wisedb_list(wisedb_engine *eng, char **result_json_out) {
    if (!eng || !result_json_out) return -1;
    *result_json_out = NULL;

    wisedb_json_buffer buffer;
    wisedb_json_buffer_init(&buffer);
    if (wisedb_json_buffer_append(&buffer, "{\"status\":\"ok\",\"pages\":[") != 0) goto list_fail;
    for (size_t i = 0; i < eng->node_count; i++) {
        const wisedb_node *n = &eng->nodes[i];
        if ((i > 0 && wisedb_json_buffer_append(&buffer, ",") != 0)
            || wisedb_json_buffer_append(&buffer, "{\"slug\":") != 0
            || wisedb_json_buffer_append_string(&buffer, n->slug) != 0
            || wisedb_json_buffer_append(&buffer, ",\"type\":") != 0
            || wisedb_json_buffer_append_string(&buffer, wisedb_type_to_string(n->type)) != 0
            || wisedb_json_buffer_append(&buffer, ",\"title\":") != 0
            || wisedb_json_buffer_append_string(&buffer, n->title) != 0
            || wisedb_json_buffer_append(&buffer, ",\"summary\":") != 0
            || wisedb_json_buffer_append_string(&buffer, n->summary) != 0
            || wisedb_json_buffer_appendf(&buffer, ",\"token_count\":%d,\"rel_path\":", n->token_count) != 0
            || wisedb_json_buffer_append_string(&buffer, n->rel_path) != 0
            || wisedb_json_buffer_appendf(&buffer, ",\"is_media\":%s}", n->is_media ? "true" : "false") != 0) goto list_fail;
    }
    if (wisedb_json_buffer_append(&buffer, "]}") != 0) goto list_fail;
    *result_json_out = wisedb_json_buffer_take(&buffer);
    if (!*result_json_out) goto list_fail;
    return 0;

list_fail:
    wisedb_json_buffer_dispose(&buffer);
    wisedb_set_error(eng, "out of memory serializing list response");
    return -1;
}

int wisedb_read_markdown(wisedb_engine *eng, const char *slug, char **markdown_out) {
    if (!eng || !slug || !markdown_out) return -1;
    *markdown_out = NULL;

    wisedb_node *n = wisedb_find_node(eng, slug);
    if (!n) {
        wisedb_set_error(eng, "slug not found: %s", slug);
        return -1;
    }
    if (wisedb_validate_relative_path(eng, n->rel_path) != 0) {
        wisedb_set_error(eng, "unsafe page path");
        return -1;
    }

    char path[WISEDB_MAX_PATH];
    if (n->is_media) {
        int written = snprintf(path, sizeof(path), "%s.md", n->rel_path);
        if (written < 0 || (size_t)written >= sizeof(path)
            || wisedb_validate_relative_path(eng, path) != 0) {
            wisedb_set_error(eng, "unsafe media sidecar path");
            return -1;
        }
    } else {
        strncpy(path, n->rel_path, sizeof(path) - 1);
        path[sizeof(path) - 1] = '\0';
    }

    if (n->is_media) {
        int sidecar_exists = 0;
        if (wisedb_file_exists(eng, path, &sidecar_exists) != 0) {
            wisedb_set_error(eng, "media sidecar is not a safe regular file");
            return -1;
        }
        if (!sidecar_exists) {
            char *serialized = wisedb_serialize_page(n);
            if (!serialized) return -1;
            *markdown_out = serialized;
            return 0;
        }
    }

    char *md = NULL;
    if (wisedb_read_file(eng, path, &md, NULL) != 0) {
        wisedb_set_error(eng, "failed to read markdown for: %s", slug);
        return -1;
    }
    *markdown_out = md;
    return 0;
}

int wisedb_update(
    wisedb_engine *eng,
    const char *slug,
    const char *type,
    const char *title,
    const char *summary,
    const char *body,
    char **result_json_out
) {
    if (!eng || !slug || !result_json_out) return -1;
    *result_json_out = NULL;

    wisedb_node *existing = wisedb_find_node(eng, slug);
    if (!existing) {
        wisedb_set_error(eng, "slug not found: %s", slug);
        return -1;
    }
    if (!type && !title && !summary && !body) {
        wisedb_set_error(eng, "at least one field required");
        return -1;
    }
    if (!metadata_line_safe(slug) || !metadata_line_safe(type)
        || !metadata_line_safe(title) || !metadata_line_safe(summary)) {
        wisedb_set_error(eng, "metadata fields must not contain line breaks or control characters");
        return -1;
    }

    wisedb_node updated;
    memset(&updated, 0, sizeof(updated));
    updated.slug = strdup(existing->slug);
    updated.type = type ? wisedb_type_from_string(type) : existing->type;
    updated.title = strdup(title ? title : (existing->title ? existing->title : ""));
    updated.summary = strdup(summary ? summary : (existing->summary ? existing->summary : ""));
    updated.body = strdup(body ? body : (existing->body ? existing->body : ""));
    updated.rel_path = strdup(existing->rel_path);
    updated.mime = strdup(existing->mime ? existing->mime : "text/markdown");
    updated.is_media = existing->is_media;
    updated.token_count = wisedb_estimate_tokens(updated.body);
    updated.mtime = time(NULL);

    if (!updated.slug || !updated.title || !updated.summary || !updated.body
        || !updated.rel_path || !updated.mime) {
        wisedb_set_error(eng, "out of memory preparing updated page");
        wisedb_free_node(&updated);
        return -1;
    }
    if (!metadata_safe(&updated)) {
        wisedb_set_error(eng, "updated page contains invalid UTF-8 or control characters");
        wisedb_free_node(&updated);
        return -1;
    }
    if (updated.token_count > WISEDB_MAX_PAGE_TOKENS) {
        wisedb_set_error(eng, "page exceeds token limit; split into multiple pages");
        wisedb_free_node(&updated);
        return -1;
    }

    char *serialized = wisedb_serialize_page(&updated);
    if (!serialized) {
        wisedb_free_node(&updated);
        return -1;
    }

    char **links = NULL;
    size_t link_count = 0;
    char **mentions = NULL;
    size_t mention_count = 0;
    {
        wisedb_node tmp;
        memset(&tmp, 0, sizeof(tmp));
        if (wisedb_parse_markdown(serialized, &tmp, &links, &link_count, &mentions, &mention_count) != 0) {
            wisedb_set_error(eng, "failed to parse updated page links");
            wisedb_free_parsed_links(links, link_count);
            wisedb_free_parsed_links(mentions, mention_count);
            wisedb_free_node(&tmp);
            free(serialized);
            wisedb_free_node(&updated);
            return -1;
        }
        wisedb_free_node(&tmp);
    }

    wisedb_prepared_index prepared_index;
    if (wisedb_index_prepare(eng, &updated, links, link_count, mentions, mention_count, &prepared_index) != 0) {
        wisedb_set_error(eng, "failed to prepare updated page index");
        free(serialized);
        wisedb_free_parsed_links(links, link_count);
        wisedb_free_node(&updated);
        return -1;
    }

    if (wisedb_save_node_file(eng, &updated, serialized) != 0) {
        wisedb_index_discard(&prepared_index);
        free(serialized);
        wisedb_free_parsed_links(links, link_count);
        wisedb_free_parsed_links(mentions, mention_count);
        wisedb_set_error(eng, "write failed");
        wisedb_free_node(&updated);
        return -1;
    }
    free(serialized);

    wisedb_index_publish(eng, &prepared_index);

    wisedb_node *stored = wisedb_find_node(eng, slug);
    char *result_json = build_result_json(eng, stored, links, link_count, "ok", NULL, 0);
    if (!result_json) {
        wisedb_free_parsed_links(links, link_count);
        wisedb_free_parsed_links(mentions, mention_count);
        wisedb_free_node(&updated);
        wisedb_set_error(eng, "out of memory preparing update response");
        return -1;
    }

    *result_json_out = result_json;
    wisedb_free_parsed_links(links, link_count);
    wisedb_free_parsed_links(mentions, mention_count);
    wisedb_free_node(&updated);

    double total = 0;
    for (size_t i = 0; i < eng->node_count; i++) total += eng->nodes[i].token_count;
    eng->doc_count = eng->node_count;
    eng->avg_doc_len = eng->doc_count ? total / eng->doc_count : 0;
    return 0;
}

int wisedb_delete(wisedb_engine *eng, const char *slug) {
    if (!eng || !slug) return -1;

    wisedb_node *existing = wisedb_find_node(eng, slug);
    if (!existing) {
        wisedb_set_error(eng, "slug not found: %s", slug);
        return -1;
    }

    if (wisedb_delete_node_file(eng, existing) != 0) {
        wisedb_set_error(eng, "failed to delete file for: %s", slug);
        return -1;
    }

    if (wisedb_remove_node(eng, slug) != 0) {
        wisedb_set_error(eng, "failed to remove node: %s", slug);
        return -1;
    }
    if (wisedb_rebuild_edges(eng) != 0) {
        wisedb_set_error(eng, "failed to rebuild graph after delete");
        return -1;
    }
    return 0;
}

int wisedb_get_settings(wisedb_engine *eng, char **result_json_out) {
    if (!result_json_out) return -1;
    *result_json_out = NULL;
    if (!eng) return -1;
    wisedb_json_buffer buffer;
    wisedb_json_buffer_init(&buffer);
    if (wisedb_json_buffer_appendf(&buffer, "{\"semantics\":%s}", eng->semantics_enabled ? "true" : "false") != 0) {
        wisedb_json_buffer_dispose(&buffer);
        wisedb_set_error(eng, "out of memory serializing settings");
        return -1;
    }
    *result_json_out = wisedb_json_buffer_take(&buffer);
    if (!*result_json_out) {
        wisedb_set_error(eng, "out of memory serializing settings");
        return -1;
    }
    return 0;
}

int wisedb_set_semantics(wisedb_engine *eng, int enabled, char **result_json_out) {
    if (!result_json_out) return -1;
    *result_json_out = NULL;
    if (!eng) return -1;
    enabled = enabled ? 1 : 0;
    if (wisedb_settings_write_semantics(eng, enabled) != 0) {
        wisedb_set_error(eng, "failed to write database settings");
        return -1;
    }
    eng->semantics_enabled = enabled;
    if (wisedb_rebuild_edges(eng) != 0) {
        wisedb_set_error(eng, "failed to rebuild graph after settings change");
        return -1;
    }
    return wisedb_get_settings(eng, result_json_out);
}

#define WISEDB_LINK_MAX_DEPTH 8
#define WISEDB_LINK_MAX_NODES 256
#define WISEDB_LINK_MAX_EDGES 1024

static int wisedb_slug_list_contains(const char **slugs, size_t count, const char *slug) {
    if (!slug) return 0;
    for (size_t i = 0; i < count; i++) {
        if (slugs[i] && strcmp(slugs[i], slug) == 0) return 1;
    }
    return 0;
}

static int wisedb_append_slug_copy(char ***slugs, size_t *count, size_t *capacity, const char *slug) {
    if (!slug || wisedb_slug_list_contains((const char **)*slugs, *count, slug)) return 0;
    if (*count >= *capacity) {
        size_t next = *capacity ? *capacity * 2 : 8;
        if (next < *capacity || next > SIZE_MAX / sizeof(char *)) return -1;
        char **grown = (char **)realloc(*slugs, next * sizeof(char *));
        if (!grown) return -1;
        *slugs = grown;
        *capacity = next;
    }
    (*slugs)[(*count)++] = strdup(slug);
    return (*slugs)[*count - 1] ? 0 : -1;
}

static int wisedb_append_full_node_json(wisedb_json_buffer *buffer, const wisedb_node *node) {
    if (!node) return wisedb_json_buffer_append(buffer, "null");
    return wisedb_json_buffer_append(buffer, "{\"slug\":")
        || wisedb_json_buffer_append_string(buffer, node->slug)
        || wisedb_json_buffer_append(buffer, ",\"type\":")
        || wisedb_json_buffer_append_string(buffer, wisedb_type_to_string(node->type))
        || wisedb_json_buffer_append(buffer, ",\"title\":")
        || wisedb_json_buffer_append_string(buffer, node->title)
        || wisedb_json_buffer_append(buffer, ",\"summary\":")
        || wisedb_json_buffer_append_string(buffer, node->summary)
        || wisedb_json_buffer_append(buffer, ",\"body\":")
        || wisedb_json_buffer_append_string(buffer, node->body)
        || wisedb_json_buffer_append(buffer, ",\"rel_path\":")
        || wisedb_json_buffer_append_string(buffer, node->rel_path)
        || wisedb_json_buffer_append(buffer, ",\"mime\":")
        || wisedb_json_buffer_append_string(buffer, node->mime)
        || wisedb_json_buffer_appendf(buffer, ",\"token_count\":%d,\"is_media\":%s}",
                                      node->token_count, node->is_media ? "true" : "false");
}

static int wisedb_append_brief_node_json(wisedb_json_buffer *buffer, const wisedb_node *node) {
    return wisedb_json_buffer_append(buffer, "{\"slug\":")
        || wisedb_json_buffer_append_string(buffer, node->slug)
        || wisedb_json_buffer_append(buffer, ",\"title\":")
        || wisedb_json_buffer_append_string(buffer, node->title)
        || wisedb_json_buffer_append(buffer, ",\"summary\":")
        || wisedb_json_buffer_append_string(buffer, node->summary)
        || wisedb_json_buffer_append(buffer, ",\"type\":")
        || wisedb_json_buffer_append_string(buffer, wisedb_type_to_string(node->type))
        || wisedb_json_buffer_append(buffer, "}");
}

static void wisedb_free_slug_list(char **slugs, size_t count) {
    if (!slugs) return;
    for (size_t i = 0; i < count; i++) free(slugs[i]);
    free(slugs);
}

typedef struct {
    char *from_slug;
    char *to_slug;
    char *edge_type;
} wisedb_walk_edge;

typedef struct {
    wisedb_node **nodes;
    size_t count;
    size_t cap;
} wisedb_walk_level;

static int wisedb_walk_level_push(wisedb_walk_level *level, wisedb_node *node) {
    if (!level || !node) return -1;
    for (size_t i = 0; i < level->count; i++) {
        if (level->nodes[i] == node) return 0;
    }
    if (level->count >= level->cap) {
        size_t next = level->cap ? level->cap * 2 : 4;
        if (next < level->cap || next > SIZE_MAX / sizeof(wisedb_node *)) return -1;
        wisedb_node **grown = (wisedb_node **)realloc(level->nodes, next * sizeof(wisedb_node *));
        if (!grown) return -1;
        level->nodes = grown;
        level->cap = next;
    }
    level->nodes[level->count++] = node;
    return 0;
}

static void wisedb_walk_level_clear(wisedb_walk_level *level) {
    if (!level) return;
    free(level->nodes);
    level->nodes = NULL;
    level->count = 0;
    level->cap = 0;
}

static int wisedb_walk_edge_push(
    wisedb_walk_edge **edges,
    size_t *edge_count,
    size_t *edge_cap,
    const char *from_slug,
    const char *to_slug,
    const char *edge_type
) {
    if (*edge_count >= *edge_cap) {
        size_t next = *edge_cap ? *edge_cap * 2 : 16;
        if (next < *edge_cap || next > SIZE_MAX / sizeof(wisedb_walk_edge)) return -1;
        wisedb_walk_edge *grown = (wisedb_walk_edge *)realloc(*edges, next * sizeof(wisedb_walk_edge));
        if (!grown) return -1;
        *edges = grown;
        *edge_cap = next;
    }
    wisedb_walk_edge *slot = &(*edges)[(*edge_count)++];
    slot->from_slug = from_slug ? strdup(from_slug) : NULL;
    slot->to_slug = to_slug ? strdup(to_slug) : NULL;
    slot->edge_type = edge_type ? strdup(edge_type) : NULL;
    if ((from_slug && !slot->from_slug) || (to_slug && !slot->to_slug) || (edge_type && !slot->edge_type)) return -1;
    return 0;
}

static int wisedb_walk_edge_contains(const wisedb_walk_edge *edges, size_t count, const char *from_slug, const char *to_slug) {
    for (size_t i = 0; i < count; i++) {
        if (edges[i].from_slug && edges[i].to_slug && from_slug && to_slug
            && strcmp(edges[i].from_slug, from_slug) == 0
            && strcmp(edges[i].to_slug, to_slug) == 0) {
            return 1;
        }
    }
    return 0;
}

static void wisedb_walk_edges_free(wisedb_walk_edge *edges, size_t count) {
    if (!edges) return;
    for (size_t i = 0; i < count; i++) {
        free(edges[i].from_slug);
        free(edges[i].to_slug);
        free(edges[i].edge_type);
    }
    free(edges);
}

int wisedb_links(
    wisedb_engine *eng,
    const char *slug,
    int depth,
    wisedb_link_direction direction,
    char **result_json_out
) {
    wisedb_json_buffer buffer;
    wisedb_json_buffer_init(&buffer);

    char **expanded = NULL;
    size_t expanded_count = 0;
    size_t expanded_cap = 0;
    char **unknown = NULL;
    size_t unknown_count = 0;
    size_t unknown_cap = 0;
    char **frontier = NULL;
    size_t frontier_count = 0;
    size_t frontier_cap = 0;
    wisedb_walk_level *levels = NULL;
    wisedb_walk_edge *walk_edges = NULL;
    size_t walk_edge_count = 0;
    size_t walk_edge_cap = 0;
    size_t total_level_nodes = 0;

    if (!eng || !slug || !result_json_out) return -1;
    *result_json_out = NULL;
    if (depth < 1 || depth > WISEDB_LINK_MAX_DEPTH) {
        wisedb_set_error(eng, "depth must be between 1 and 8");
        return -1;
    }
    if (direction < WISEDB_LINK_DIR_OUTGOING || direction > WISEDB_LINK_DIR_BOTH) {
        wisedb_set_error(eng, "invalid link direction");
        return -1;
    }

    wisedb_node *root = wisedb_find_node(eng, slug);
    if (!root) {
        wisedb_set_error(eng, "slug not found: %s", slug);
        return -1;
    }

    levels = (wisedb_walk_level *)calloc((size_t)depth, sizeof(wisedb_walk_level));
    if (!levels) goto links_fail;
    if (wisedb_append_slug_copy(&expanded, &expanded_count, &expanded_cap, slug) != 0) goto links_fail;
    if (wisedb_append_slug_copy(&frontier, &frontier_count, &frontier_cap, slug) != 0) goto links_fail;

    for (int level_index = 0; level_index < depth; level_index++) {
        char **next_frontier = NULL;
        size_t next_count = 0;
        size_t next_cap = 0;

        for (size_t f = 0; f < frontier_count; f++) {
            const char *current = frontier[f];
            for (size_t e = 0; e < eng->edge_count; e++) {
                const wisedb_edge *edge = &eng->edges[e];
                if (!edge->edge_type || strcmp(edge->edge_type, "links_to") != 0) continue;

                int use_outgoing = (direction == WISEDB_LINK_DIR_OUTGOING || direction == WISEDB_LINK_DIR_BOTH)
                    && edge->from_slug && strcmp(edge->from_slug, current) == 0;
                int use_incoming = (direction == WISEDB_LINK_DIR_INCOMING || direction == WISEDB_LINK_DIR_BOTH)
                    && edge->to_slug && strcmp(edge->to_slug, current) == 0;
                if (!use_outgoing && !use_incoming) continue;
                if (!wisedb_walk_edge_contains(walk_edges, walk_edge_count, edge->from_slug, edge->to_slug)) {
                    if (walk_edge_count >= WISEDB_LINK_MAX_EDGES) {
                        wisedb_set_error(eng, "link traversal exceeds edge limit");
                        goto links_fail;
                    }
                    if (wisedb_walk_edge_push(
                            &walk_edges, &walk_edge_count, &walk_edge_cap,
                            edge->from_slug, edge->to_slug, edge->edge_type) != 0) {
                        goto links_fail;
                    }
                }

                const char *neighbor_slug = use_outgoing ? edge->to_slug : edge->from_slug;
                wisedb_node *neighbor = neighbor_slug ? wisedb_find_node(eng, neighbor_slug) : NULL;
                if (!neighbor) {
                    if (use_outgoing && neighbor_slug
                        && wisedb_append_slug_copy(&unknown, &unknown_count, &unknown_cap, neighbor_slug) != 0) {
                        goto links_fail;
                    }
                    continue;
                }
                if (wisedb_slug_list_contains((const char **)expanded, expanded_count, neighbor->slug)) {
                    continue;
                }
                if (total_level_nodes + 1 > WISEDB_LINK_MAX_NODES) {
                    wisedb_set_error(eng, "link traversal exceeds node limit");
                    goto links_fail;
                }
                total_level_nodes++;
                if (wisedb_walk_level_push(&levels[level_index], neighbor) != 0) goto links_fail;
                if (wisedb_append_slug_copy(&expanded, &expanded_count, &expanded_cap, neighbor->slug) != 0
                    || wisedb_append_slug_copy(&next_frontier, &next_count, &next_cap, neighbor->slug) != 0) {
                    goto links_fail;
                }
            }
        }

        wisedb_free_slug_list(frontier, frontier_count);
        frontier = next_frontier;
        frontier_count = next_count;
        frontier_cap = next_cap;
        if (frontier_count == 0) break;
    }

    if (wisedb_json_buffer_append(&buffer, "{\"status\":\"ok\",\"node\":") != 0) goto links_fail;
    if (wisedb_append_full_node_json(&buffer, root) != 0
        || wisedb_json_buffer_append(&buffer, ",\"levels\":[") != 0) {
        goto links_fail;
    }

    int levels_written = 0;
    for (int level_index = 0; level_index < depth; level_index++) {
        if (levels[level_index].count == 0) {
            if (levels_written > 0) break;
            if (frontier_count == 0) break;
        }
        if (levels_written && wisedb_json_buffer_append(&buffer, ",") != 0) goto links_fail;
        if (wisedb_json_buffer_append(&buffer, "[") != 0) goto links_fail;
        for (size_t i = 0; i < levels[level_index].count; i++) {
            if (i > 0 && wisedb_json_buffer_append(&buffer, ",") != 0) goto links_fail;
            if (wisedb_append_brief_node_json(&buffer, levels[level_index].nodes[i]) != 0) goto links_fail;
        }
        if (wisedb_json_buffer_append(&buffer, "]") != 0) goto links_fail;
        levels_written++;
        if (levels[level_index].count == 0) break;
    }

    if (wisedb_json_buffer_append(&buffer, "],\"edges\":[") != 0) goto links_fail;
    for (size_t i = 0; i < walk_edge_count; i++) {
        if (i > 0 && wisedb_json_buffer_append(&buffer, ",") != 0) goto links_fail;
        if (wisedb_json_buffer_append(&buffer, "{\"from_slug\":") != 0
            || wisedb_json_buffer_append_string(&buffer, walk_edges[i].from_slug) != 0
            || wisedb_json_buffer_append(&buffer, ",\"to_slug\":") != 0
            || wisedb_json_buffer_append_string(&buffer, walk_edges[i].to_slug) != 0
            || wisedb_json_buffer_append(&buffer, ",\"type\":") != 0
            || wisedb_json_buffer_append_string(&buffer, walk_edges[i].edge_type) != 0
            || wisedb_json_buffer_append(&buffer, "}") != 0) {
            goto links_fail;
        }
    }
    if (wisedb_json_buffer_append(&buffer, "],\"unknown_slugs\":[") != 0) goto links_fail;
    for (size_t i = 0; i < unknown_count; i++) {
        if (i > 0 && wisedb_json_buffer_append(&buffer, ",") != 0) goto links_fail;
        if (wisedb_json_buffer_append_string(&buffer, unknown[i]) != 0) goto links_fail;
    }
    if (wisedb_json_buffer_append(&buffer, "]}") != 0) goto links_fail;

    *result_json_out = wisedb_json_buffer_take(&buffer);
    if (!*result_json_out) goto links_fail;

    for (int level_index = 0; level_index < depth; level_index++) wisedb_walk_level_clear(&levels[level_index]);
    free(levels);
    wisedb_walk_edges_free(walk_edges, walk_edge_count);
    wisedb_free_slug_list(expanded, expanded_count);
    wisedb_free_slug_list(unknown, unknown_count);
    wisedb_free_slug_list(frontier, frontier_count);
    return 0;

links_fail:
    wisedb_json_buffer_dispose(&buffer);
    if (levels) {
        for (int level_index = 0; level_index < depth; level_index++) wisedb_walk_level_clear(&levels[level_index]);
        free(levels);
    }
    wisedb_walk_edges_free(walk_edges, walk_edge_count);
    wisedb_free_slug_list(expanded, expanded_count);
    wisedb_free_slug_list(unknown, unknown_count);
    wisedb_free_slug_list(frontier, frontier_count);
    if (!eng || !eng->last_error || eng->last_error[0] == '\0') {
        if (eng) wisedb_set_error(eng, "link traversal failed");
    }
    return -1;
}

int wisedb_query_edges(
    wisedb_engine *eng,
    const char *from_slug,
    const char *to_slug,
    const char *edge_type,
    char **result_json_out
) {
    wisedb_json_buffer buffer;
    wisedb_json_buffer_init(&buffer);

    if (!eng || !result_json_out) return -1;
    *result_json_out = NULL;
    if (!from_slug && !to_slug) {
        wisedb_set_error(eng, "from_slug or to_slug filter required");
        return -1;
    }

    if (wisedb_json_buffer_append(&buffer, "{\"status\":\"ok\",\"edges\":[") != 0) goto edges_fail;

    int first = 1;
    for (size_t i = 0; i < eng->edge_count; i++) {
        const wisedb_edge *edge = &eng->edges[i];
        if (from_slug && (!edge->from_slug || strcmp(edge->from_slug, from_slug) != 0)) continue;
        if (to_slug && (!edge->to_slug || strcmp(edge->to_slug, to_slug) != 0)) continue;
        if (edge_type && (!edge->edge_type || strcmp(edge->edge_type, edge_type) != 0)) continue;
        if ((!first && wisedb_json_buffer_append(&buffer, ",") != 0)
            || wisedb_json_buffer_append(&buffer, "{\"from_slug\":") != 0
            || wisedb_json_buffer_append_string(&buffer, edge->from_slug) != 0
            || wisedb_json_buffer_append(&buffer, ",\"to_slug\":") != 0
            || wisedb_json_buffer_append_string(&buffer, edge->to_slug) != 0
            || wisedb_json_buffer_append(&buffer, ",\"type\":") != 0
            || wisedb_json_buffer_append_string(&buffer, edge->edge_type) != 0
            || wisedb_json_buffer_append(&buffer, "}") != 0) {
            goto edges_fail;
        }
        first = 0;
    }
    if (wisedb_json_buffer_append(&buffer, "]}") != 0) goto edges_fail;
    *result_json_out = wisedb_json_buffer_take(&buffer);
    if (!*result_json_out) goto edges_fail;
    return 0;

edges_fail:
    wisedb_json_buffer_dispose(&buffer);
    wisedb_set_error(eng, "out of memory serializing edges response");
    return -1;
}
