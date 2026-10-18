#ifndef WISEDB_INTERNAL_H
#define WISEDB_INTERNAL_H

#include "wisedb.h"
#include "json.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <sys/stat.h>
#include <errno.h>
#include <time.h>

#ifdef _WIN32
#include <direct.h>
#define WISEDB_MKDIR(p) _mkdir(p)
#else
#include <unistd.h>
#define WISEDB_MKDIR(p) mkdir(p, 0755)
#endif

#define WISEDB_INITIAL_CAP 64
#define WISEDB_MAX_PATH 4096
#define WISEDB_CATALOG_FILE "_wisedb/catalog.json"
#define WISEDB_SETTINGS_FILE "_wisedb/settings.json"
#define WISEDB_MAX_MENTION 1024

typedef enum {
    WISEDB_NODE_ENTITY = 0,
    WISEDB_NODE_CONCEPT,
    WISEDB_NODE_SOURCE,
    WISEDB_NODE_COMPARISON,
    WISEDB_NODE_SYNTHESIS,
    WISEDB_NODE_PLAYBOOK,
    WISEDB_NODE_MEDIA,
    WISEDB_NODE_UNKNOWN
} wisedb_node_type;

typedef struct {
    char *slug;
    wisedb_node_type type;
    char *title;
    char *summary;
    char *body;       /* markdown body or empty for media */
    char *rel_path;   /* path relative to wiki root */
    char *mime;
    int token_count;
    time_t mtime;
    int is_media;
    char **wiki_links;
    size_t wiki_link_count;
    char **semantic_mentions;
    size_t semantic_mention_count;
} wisedb_node;

typedef struct {
    char *from_slug;
    char *to_slug;
    char *edge_type;
} wisedb_edge;

typedef struct {
    wisedb_node node;
    wisedb_edge *edges;
    size_t edge_count;
    size_t node_index;
    int active;
} wisedb_prepared_index;

struct wisedb_engine {
    wisedb_node *nodes;
    size_t node_count;
    size_t node_cap;
    wisedb_edge *edges;
    size_t edge_count;
    size_t edge_cap;
    char *wiki_root;
    char *last_error;
    double avg_doc_len;
    size_t doc_count;
    int semantics_enabled;
#ifndef _WIN32
    int root_fd;
    int owner_lock_fd;
#endif
};

/* store.c */
int wisedb_ensure_dir(const char *path);
int wisedb_ensure_root_dir(const char *path);
int wisedb_lock_root(wisedb_engine *eng, const char *wiki_root);
int wisedb_storage_open(wisedb_engine *eng, const char *wiki_root);
void wisedb_storage_close(wisedb_engine *eng);
int wisedb_join_path(char *out, size_t out_sz, const char *a, const char *b);
int wisedb_validate_relative_path(const wisedb_engine *eng, const char *rel_path);
int wisedb_write_file(wisedb_engine *eng, const char *rel_path, const uint8_t *data, size_t len, int replace_existing);
int wisedb_read_file(wisedb_engine *eng, const char *rel_path, char **out, size_t *out_len);
int wisedb_file_exists(wisedb_engine *eng, const char *rel_path, int *exists_out);
int wisedb_stat_relative_no_follow(wisedb_engine *eng, const char *rel_path, struct stat *stat_out);
int wisedb_remove_file(wisedb_engine *eng, const char *rel_path);
int wisedb_scan_wiki(wisedb_engine *eng);
int wisedb_storage_root_is_current(wisedb_engine *eng);
int wisedb_save_node_file(wisedb_engine *eng, const wisedb_node *node, const char *markdown_full);
int wisedb_delete_node_file(wisedb_engine *eng, const wisedb_node *node);
int wisedb_slug_from_path(const char *rel_path, char *slug_out, size_t slug_sz);

/* format.c */
int wisedb_parse_markdown(
    const char *markdown,
    wisedb_node *node_out,
    char ***links_out,
    size_t *link_count_out,
    char ***mentions_out,
    size_t *mention_count_out
);
void wisedb_free_parsed_links(char **links, size_t count);
char *wisedb_serialize_page(const wisedb_node *node);
int wisedb_estimate_tokens(const char *text);
wisedb_node_type wisedb_type_from_string(const char *s);
const char *wisedb_type_to_string(wisedb_node_type t);
int wisedb_is_media_path(const char *path);
const char *wisedb_mime_from_ext(const char *path);

/* index.c */
wisedb_node *wisedb_find_node(wisedb_engine *eng, const char *slug);
int wisedb_upsert_node(wisedb_engine *eng, wisedb_node *node);
int wisedb_upsert_node_with_links(
    wisedb_engine *eng,
    wisedb_node *node,
    char **links,
    size_t link_count,
    char **mentions,
    size_t mention_count
);
int wisedb_upsert_node_from_scan(
    wisedb_engine *eng,
    wisedb_node *node,
    char **links,
    size_t link_count,
    char **mentions,
    size_t mention_count
);
int wisedb_index_prepare(
    wisedb_engine *eng,
    const wisedb_node *node,
    char **links,
    size_t link_count,
    char **mentions,
    size_t mention_count,
    wisedb_prepared_index *prepared
);
int wisedb_rebuild_edges(wisedb_engine *eng);
int wisedb_read_settings_blob(wisedb_engine *eng, char **out, size_t *out_len);
int wisedb_write_settings_blob(wisedb_engine *eng, const uint8_t *data, size_t len);
int wisedb_settings_load(wisedb_engine *eng);
int wisedb_settings_write_semantics(wisedb_engine *eng, int enabled);
void wisedb_index_publish(wisedb_engine *eng, wisedb_prepared_index *prepared);
void wisedb_index_discard(wisedb_prepared_index *prepared);
int wisedb_remove_node(wisedb_engine *eng, const char *slug);
void wisedb_free_node(wisedb_node *node);
int wisedb_sync_edges_from_links(wisedb_engine *eng, const char *from_slug, char **links, size_t link_count);
int wisedb_get_neighbors(wisedb_engine *eng, const char *slug, wisedb_node ***out, size_t *count);

/* retrieve.c */
int wisedb_build_search_json(
    wisedb_engine *eng,
    const char *query,
    int budget_tokens,
    int top_k,
    char **result_json_out
);

/* json helpers */
char *wisedb_json_escape(const char *s);
void wisedb_set_error(wisedb_engine *eng, const char *fmt, ...);

#endif /* WISEDB_INTERNAL_H */
