#ifndef WISEDB_H
#define WISEDB_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define WISEDB_VERSION "3.0.0"
#define WISEDB_MAX_SLUG 256
#define WISEDB_MAX_PAGE_TOKENS 4096

typedef struct wisedb_engine wisedb_engine;

/**
 * Open or create a POSIX wiki at wiki_root. The directory must be owned by the
 * effective user and not writable by group/others. Only one writer process may
 * own a root. POSIX paths are opened relative to a retained root descriptor.
 * File operations do not promise multi-file transactions; an error after
 * rename/link or directory synchronization can have an uncertain disk outcome.
 * All const char* arguments must point to valid UTF-8 NUL-terminated strings;
 * embedded NUL bytes cannot be represented by this ABI.
 */
wisedb_engine *wisedb_open(const char *wiki_root);

/** Open with a diagnostic buffer; returns NULL and writes a non-secret error on failure. */
wisedb_engine *wisedb_open_ex(const char *wiki_root, char *error_out, size_t error_capacity);

/** Close engine and free memory. */
void wisedb_close(wisedb_engine *eng);

/** Rescan the wiki atomically in the current handle; failure retains the prior catalog. */
int wisedb_refresh(wisedb_engine *eng);

/** Check that the configured root pathname still names the held root inode. */
int wisedb_root_is_current(wisedb_engine *eng);

/**
 * Remember a page or media file.
 * markdown: WikiLLM page content (frontmatter + body), or NULL if uploading bytes.
 * file_bytes/file_len: optional raw file for media nodes.
 * rel_path: optional relative path inside wiki (e.g. "assets/photo.jpg").
 * Returns JSON result; caller frees with wisedb_free_string.
 */
int wisedb_remember(
    wisedb_engine *eng,
    const char *slug,
    const char *markdown,
    const uint8_t *file_bytes,
    size_t file_len,
    const char *rel_path,
    char **result_json_out
);

/**
 * Read node by slug.
 * include_neighbors: if non-zero, attach 1-hop neighbor summaries.
 */
int wisedb_read(
    wisedb_engine *eng,
    const char *slug,
    int include_neighbors,
    char **result_json_out
);

/** Edit existing markdown page or media sidecar. */
int wisedb_edit(
    wisedb_engine *eng,
    const char *slug,
    const char *markdown,
    char **result_json_out
);

/**
 * Search wiki. budget_tokens limits body text in packet (0 = hits only).
 */
int wisedb_search(
    wisedb_engine *eng,
    const char *query,
    int budget_tokens,
    int top_k,
    char **result_json_out
);

/** List all pages (catalog metadata, no bodies). */
int wisedb_list(wisedb_engine *eng, char **result_json_out);

/** Read raw markdown file (frontmatter + body) for a slug. */
int wisedb_read_markdown(wisedb_engine *eng, const char *slug, char **markdown_out);

/**
 * Update selected fields of an existing page.
 * NULL type/title/summary/body means keep the current value.
 */
int wisedb_update(
    wisedb_engine *eng,
    const char *slug,
    const char *type,
    const char *title,
    const char *summary,
    const char *body,
    char **result_json_out
);

/** Delete a page and its disk files. */
int wisedb_delete(wisedb_engine *eng, const char *slug);

typedef enum {
    WISEDB_LINK_DIR_OUTGOING = 0,
    WISEDB_LINK_DIR_INCOMING = 1,
    WISEDB_LINK_DIR_BOTH = 2
} wisedb_link_direction;

/**
 * Walk links_to edges from a page. depth must be 1..8.
 * Returns JSON with status, node, levels, edges, and unknown_slugs.
 */
int wisedb_links(
    wisedb_engine *eng,
    const char *slug,
    int depth,
    wisedb_link_direction direction,
    char **result_json_out
);

/**
 * List edges matching optional from_slug / to_slug filters.
 * At least one filter must be non-NULL. edge_type NULL matches any type.
 */
int wisedb_query_edges(
    wisedb_engine *eng,
    const char *from_slug,
    const char *to_slug,
    const char *edge_type,
    char **result_json_out
);

/**
 * List catalog pages with optional JSON filter, ordering, and limit.
 * filter_json may be NULL for all pages.
 */
int wisedb_list_catalog_json(
    wisedb_engine *eng,
    const char *filter_json,
    const char *order_field,
    int order_asc,
    int limit,
    char **result_json_out
);

/** Read per-database settings as JSON (currently `semantics` only). */
int wisedb_get_settings(wisedb_engine *eng, char **result_json_out);

/** Enable or disable semantic `{{title}}` links; persists settings and rebuilds edges. */
int wisedb_set_semantics(wisedb_engine *eng, int enabled, char **result_json_out);

/** Static schema text for agents. */
const char *wisedb_schema(void);

void wisedb_free_string(char *s);

/** Last error message (valid until next call on same engine). */
const char *wisedb_last_error(wisedb_engine *eng);

#ifdef __cplusplus
}
#endif

#endif /* WISEDB_H */
