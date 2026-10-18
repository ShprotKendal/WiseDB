#include "wisedb_internal.h"
#include "fault_injection.h"

static void free_string_array(char **items, size_t count) {
    wisedb_free_parsed_links(items, count);
}

static int dup_string_array(char ***dest, size_t *dest_count, char **src, size_t src_count) {
    free_string_array(*dest, *dest_count);
    *dest = NULL;
    *dest_count = 0;
    if (!src_count) return 0;
    size_t cap = 0;
    for (size_t i = 0; i < src_count; i++) {
        if (!src[i] || !src[i][0]) continue;
        if (*dest_count >= cap) {
            size_t next_cap = cap ? cap * 2 : 8;
            if (next_cap < cap || next_cap > SIZE_MAX / sizeof(**dest)) return -1;
            char **grown = (char **)realloc(*dest, next_cap * sizeof(**dest));
            if (!grown) {
                free_string_array(*dest, *dest_count);
                *dest = NULL;
                *dest_count = 0;
                return -1;
            }
            *dest = grown;
            cap = next_cap;
        }
        (*dest)[*dest_count] = strdup(src[i]);
        if (!(*dest)[*dest_count]) {
            free_string_array(*dest, *dest_count);
            *dest = NULL;
            *dest_count = 0;
            return -1;
        }
        (*dest_count)++;
    }
    return 0;
}

void wisedb_free_node(wisedb_node *node) {
    if (!node) return;
    free(node->slug);
    free(node->title);
    free(node->summary);
    free(node->body);
    free(node->rel_path);
    free(node->mime);
    free_string_array(node->wiki_links, node->wiki_link_count);
    free_string_array(node->semantic_mentions, node->semantic_mention_count);
    memset(node, 0, sizeof(*node));
}

static int clone_node(wisedb_node *target, const wisedb_node *source) {
    memset(target, 0, sizeof(*target));
    target->slug = source->slug ? strdup(source->slug) : NULL;
    target->title = source->title ? strdup(source->title) : strdup("");
    target->summary = source->summary ? strdup(source->summary) : strdup("");
    target->body = source->body ? strdup(source->body) : strdup("");
    target->rel_path = source->rel_path ? strdup(source->rel_path) : NULL;
    target->mime = source->mime ? strdup(source->mime) : strdup("text/markdown");
    target->type = source->type;
    target->token_count = source->token_count;
    target->mtime = source->mtime ? source->mtime : time(NULL);
    target->is_media = source->is_media;
    if (!target->slug || !target->title || !target->summary || !target->body || !target->mime
        || (source->rel_path && !target->rel_path)) {
        wisedb_free_node(target);
        return -1;
    }
    if (dup_string_array(&target->wiki_links, &target->wiki_link_count,
                         source->wiki_links, source->wiki_link_count) != 0
        || dup_string_array(&target->semantic_mentions, &target->semantic_mention_count,
                            source->semantic_mentions, source->semantic_mention_count) != 0) {
        wisedb_free_node(target);
        return -1;
    }
    return 0;
}

wisedb_node *wisedb_find_node(wisedb_engine *eng, const char *slug) {
    if (!eng || !slug) return NULL;
    for (size_t i = 0; i < eng->node_count; i++) {
        if (strcmp(eng->nodes[i].slug, slug) == 0) return &eng->nodes[i];
    }
    return NULL;
}

int wisedb_upsert_node(wisedb_engine *eng, wisedb_node *node) {
    if (!eng || !node || !node->slug) return -1;
    wisedb_node *existing = wisedb_find_node(eng, node->slug);
    if (existing) {
        wisedb_node replacement;
        if (clone_node(&replacement, node) != 0) return -1;
        wisedb_free_node(existing);
        *existing = replacement;
        return 0;
    }
    if (eng->node_count >= eng->node_cap) {
        size_t nc = eng->node_cap ? eng->node_cap * 2 : WISEDB_INITIAL_CAP;
        if (nc < eng->node_cap || nc > SIZE_MAX / sizeof(*eng->nodes)) return -1;
        wisedb_node *nn = (wisedb_node *)realloc(eng->nodes, nc * sizeof(*eng->nodes));
        if (!nn) return -1;
        eng->nodes = nn;
        eng->node_cap = nc;
    }
    wisedb_node replacement;
    if (clone_node(&replacement, node) != 0) return -1;
    eng->nodes[eng->node_count++] = replacement;
    return 0;
}

static int edge_pair_exists(const wisedb_edge *edges, size_t count, const char *from_slug, const char *to_slug) {
    for (size_t i = 0; i < count; i++) {
        if (!edges[i].from_slug || !edges[i].to_slug || !edges[i].edge_type) continue;
        if (strcmp(edges[i].from_slug, from_slug) == 0
            && strcmp(edges[i].to_slug, to_slug) == 0
            && strcmp(edges[i].edge_type, "links_to") == 0) return 1;
    }
    return 0;
}

static int append_edge(
    wisedb_edge **edges,
    size_t *count,
    size_t *cap,
    const char *from_slug,
    const char *to_slug
) {
    if (!from_slug || !to_slug || !from_slug[0] || !to_slug[0]) return 0;
    if (strcmp(from_slug, to_slug) == 0) return 0;
    if (edge_pair_exists(*edges, *count, from_slug, to_slug)) return 0;
    if (*count >= *cap) {
        size_t next_cap = *cap ? *cap * 2 : 8;
        if (next_cap < *cap || next_cap > SIZE_MAX / sizeof(**edges)) return -1;
        wisedb_edge *grown = (wisedb_edge *)realloc(*edges, next_cap * sizeof(*grown));
        if (!grown) return -1;
        *edges = grown;
        *cap = next_cap;
    }
    wisedb_edge *edge = &(*edges)[*count];
    edge->from_slug = strdup(from_slug);
    edge->to_slug = strdup(to_slug);
    edge->edge_type = strdup("links_to");
    if (!edge->from_slug || !edge->to_slug || !edge->edge_type) {
        free(edge->from_slug);
        free(edge->to_slug);
        free(edge->edge_type);
        return -1;
    }
    (*count)++;
    return 0;
}

int wisedb_rebuild_edges(wisedb_engine *eng) {
    if (!eng) return -1;
    for (size_t i = 0; i < eng->edge_count; i++) {
        free(eng->edges[i].from_slug);
        free(eng->edges[i].to_slug);
        free(eng->edges[i].edge_type);
    }
    free(eng->edges);
    eng->edges = NULL;
    eng->edge_count = 0;
    eng->edge_cap = 0;

    wisedb_edge *edges = NULL;
    size_t edge_count = 0, edge_cap = 0;
    for (size_t i = 0; i < eng->node_count; i++) {
        const wisedb_node *node = &eng->nodes[i];
        if (!node->slug) continue;
        for (size_t j = 0; j < node->wiki_link_count; j++) {
            if (append_edge(&edges, &edge_count, &edge_cap, node->slug, node->wiki_links[j]) != 0) goto rebuild_fail;
        }
        if (!eng->semantics_enabled) continue;
        for (size_t j = 0; j < node->semantic_mention_count; j++) {
            const char *mention = node->semantic_mentions[j];
            if (!mention || !mention[0]) continue;
            for (size_t k = 0; k < eng->node_count; k++) {
                const wisedb_node *target = &eng->nodes[k];
                if (!target->slug || !target->title) continue;
                if (strcmp(target->title, mention) != 0) continue;
                if (append_edge(&edges, &edge_count, &edge_cap, node->slug, target->slug) != 0) goto rebuild_fail;
            }
        }
    }
    eng->edges = edges;
    eng->edge_count = edge_count;
    eng->edge_cap = edge_cap;
    return 0;

rebuild_fail:
    for (size_t i = 0; i < edge_count; i++) {
        free(edges[i].from_slug);
        free(edges[i].to_slug);
        free(edges[i].edge_type);
    }
    free(edges);
    return -1;
}

static int build_edges_with_links(
    wisedb_engine *eng,
    const char *from_slug,
    char **links,
    size_t link_count,
    wisedb_edge **edges_out,
    size_t *count_out
) {
    *edges_out = NULL;
    *count_out = 0;
    if (!eng || !from_slug) return -1;
    size_t retained_count = 0;
    for (size_t i = 0; i < eng->edge_count; i++) {
        const wisedb_edge *edge = &eng->edges[i];
        if (!edge->from_slug || !edge->edge_type) return -1;
        if (strcmp(edge->from_slug, from_slug) != 0 || strcmp(edge->edge_type, "links_to") != 0) retained_count++;
    }
    if (link_count > SIZE_MAX - retained_count || retained_count + link_count > SIZE_MAX / sizeof(wisedb_edge)) return -1;
    size_t new_capacity = retained_count + link_count;
    wisedb_edge *replacement = new_capacity ? (wisedb_edge *)calloc(new_capacity, sizeof(*replacement)) : NULL;
    if (new_capacity && !replacement) return -1;
    size_t new_count = 0;
    for (size_t i = 0; i < eng->edge_count; i++) {
        const wisedb_edge *edge = &eng->edges[i];
        if (strcmp(edge->from_slug, from_slug) == 0 && strcmp(edge->edge_type, "links_to") == 0) continue;
        replacement[new_count].from_slug = strdup(edge->from_slug);
        replacement[new_count].to_slug = edge->to_slug ? strdup(edge->to_slug) : NULL;
        replacement[new_count].edge_type = strdup(edge->edge_type);
        if (!replacement[new_count].from_slug || !replacement[new_count].to_slug || !replacement[new_count].edge_type) goto edge_fail;
        new_count++;
    }
    for (size_t i = 0; i < link_count; i++) {
        if (!links || !links[i] || !links[i][0]) continue;
        replacement[new_count].from_slug = strdup(from_slug);
        replacement[new_count].to_slug = strdup(links[i]);
        replacement[new_count].edge_type = strdup("links_to");
        if (!replacement[new_count].from_slug || !replacement[new_count].to_slug || !replacement[new_count].edge_type) goto edge_fail;
        new_count++;
    }
    *edges_out = replacement;
    *count_out = new_count;
    return 0;

edge_fail:
    for (size_t i = 0; i <= new_count && i < new_capacity; i++) {
        free(replacement[i].from_slug);
        free(replacement[i].to_slug);
        free(replacement[i].edge_type);
    }
    free(replacement);
    return -1;
}

static void replace_edges(wisedb_engine *eng, wisedb_edge *replacement, size_t count) {
    for (size_t i = 0; i < eng->edge_count; i++) {
        free(eng->edges[i].from_slug);
        free(eng->edges[i].to_slug);
        free(eng->edges[i].edge_type);
    }
    free(eng->edges);
    eng->edges = replacement;
    eng->edge_count = count;
    eng->edge_cap = count;
}

int wisedb_sync_edges_from_links(wisedb_engine *eng, const char *from_slug, char **links, size_t link_count) {
    if (!eng || !from_slug) return -1;
    wisedb_edge *replacement = NULL;
    size_t replacement_count = 0;
    if (build_edges_with_links(eng, from_slug, links, link_count, &replacement, &replacement_count) != 0) return -1;
    replace_edges(eng, replacement, replacement_count);
    return 0;
}

int wisedb_index_prepare(
    wisedb_engine *eng,
    const wisedb_node *node,
    char **links,
    size_t link_count,
    char **mentions,
    size_t mention_count,
    wisedb_prepared_index *prepared
) {
    if (!eng || !node || !node->slug || !node->rel_path || !prepared) return -1;
    memset(prepared, 0, sizeof(*prepared));
    size_t existing_index = eng->node_count;
    for (size_t i = 0; i < eng->node_count; i++) {
        if (!eng->nodes[i].slug || !eng->nodes[i].rel_path) return -1;
        if (strcmp(eng->nodes[i].rel_path, node->rel_path) == 0
            && strcmp(eng->nodes[i].slug, node->slug) != 0) return -1;
        if (strcmp(eng->nodes[i].slug, node->slug) == 0) {
            if (strcmp(eng->nodes[i].rel_path, node->rel_path) != 0) return -1;
            existing_index = i;
        }
    }
    if (clone_node(&prepared->node, node) != 0) return -1;
    free_string_array(prepared->node.wiki_links, prepared->node.wiki_link_count);
    prepared->node.wiki_links = NULL;
    prepared->node.wiki_link_count = 0;
    free_string_array(prepared->node.semantic_mentions, prepared->node.semantic_mention_count);
    prepared->node.semantic_mentions = NULL;
    prepared->node.semantic_mention_count = 0;
    if (dup_string_array(&prepared->node.wiki_links, &prepared->node.wiki_link_count, links, link_count) != 0
        || dup_string_array(&prepared->node.semantic_mentions, &prepared->node.semantic_mention_count,
                            mentions, mention_count) != 0) {
        wisedb_free_node(&prepared->node);
        return -1;
    }
    if (existing_index == eng->node_count && eng->node_count >= eng->node_cap) {
        size_t next_capacity = eng->node_cap ? eng->node_cap * 2 : WISEDB_INITIAL_CAP;
        if (next_capacity < eng->node_cap || next_capacity > SIZE_MAX / sizeof(*eng->nodes)) {
            wisedb_free_node(&prepared->node);
            return -1;
        }
        wisedb_node *grown = (wisedb_node *)realloc(eng->nodes, next_capacity * sizeof(*eng->nodes));
        if (!grown) {
            wisedb_free_node(&prepared->node);
            return -1;
        }
        eng->nodes = grown;
        eng->node_cap = next_capacity;
    }
    prepared->edges = NULL;
    prepared->edge_count = 0;
    prepared->node_index = existing_index;
    prepared->active = 1;
    return 0;
}

void wisedb_index_publish(wisedb_engine *eng, wisedb_prepared_index *prepared) {
    if (!eng || !prepared || !prepared->active) return;
    if (prepared->node_index == eng->node_count) {
        eng->nodes[eng->node_count++] = prepared->node;
    } else {
        wisedb_free_node(&eng->nodes[prepared->node_index]);
        eng->nodes[prepared->node_index] = prepared->node;
    }
    memset(&prepared->node, 0, sizeof(prepared->node));
    prepared->active = 0;
    if (wisedb_rebuild_edges(eng) != 0) return;
    double total = 0;
    for (size_t i = 0; i < eng->node_count; i++) total += eng->nodes[i].token_count;
    eng->doc_count = eng->node_count;
    eng->avg_doc_len = eng->doc_count ? total / (double)eng->doc_count : 0.0;
}

void wisedb_index_discard(wisedb_prepared_index *prepared) {
    if (!prepared) return;
    wisedb_free_node(&prepared->node);
    if (prepared->edges) {
        for (size_t i = 0; i < prepared->edge_count; i++) {
            free(prepared->edges[i].from_slug);
            free(prepared->edges[i].to_slug);
            free(prepared->edges[i].edge_type);
        }
        free(prepared->edges);
    }
    memset(prepared, 0, sizeof(*prepared));
}

int wisedb_upsert_node_with_links(
    wisedb_engine *eng,
    wisedb_node *node,
    char **links,
    size_t link_count,
    char **mentions,
    size_t mention_count
) {
    wisedb_prepared_index prepared;
    if (wisedb_index_prepare(eng, node, links, link_count, mentions, mention_count, &prepared) != 0) return -1;
    wisedb_index_publish(eng, &prepared);
    return 0;
}

int wisedb_upsert_node_from_scan(
    wisedb_engine *eng,
    wisedb_node *node,
    char **links,
    size_t link_count,
    char **mentions,
    size_t mention_count
) {
    if (!eng || !node) return -1;
    wisedb_node stored;
    if (clone_node(&stored, node) != 0) return -1;
    free_string_array(stored.wiki_links, stored.wiki_link_count);
    stored.wiki_links = NULL;
    stored.wiki_link_count = 0;
    free_string_array(stored.semantic_mentions, stored.semantic_mention_count);
    stored.semantic_mentions = NULL;
    stored.semantic_mention_count = 0;
    if (dup_string_array(&stored.wiki_links, &stored.wiki_link_count, links, link_count) != 0
        || dup_string_array(&stored.semantic_mentions, &stored.semantic_mention_count, mentions, mention_count) != 0) {
        wisedb_free_node(&stored);
        return -1;
    }
    if (wisedb_upsert_node(eng, &stored) != 0) {
        wisedb_free_node(&stored);
        return -1;
    }
    return 0;
}

int wisedb_remove_node(wisedb_engine *eng, const char *slug) {
    if (!eng || !slug) return -1;
    size_t idx = (size_t)-1;
    for (size_t i = 0; i < eng->node_count; i++) {
        if (strcmp(eng->nodes[i].slug, slug) == 0) {
            idx = i;
            break;
        }
    }
    if (idx == (size_t)-1) return -1;

    wisedb_free_node(&eng->nodes[idx]);
    if (idx < eng->node_count - 1) {
        memmove(&eng->nodes[idx], &eng->nodes[idx + 1],
                (eng->node_count - idx - 1) * sizeof(wisedb_node));
    }
    eng->node_count--;

    double total = 0;
    for (size_t i = 0; i < eng->node_count; i++) {
        total += (double)eng->nodes[i].token_count;
    }
    eng->doc_count = eng->node_count;
    eng->avg_doc_len = eng->doc_count > 0 ? total / (double)eng->doc_count : 0.0;
    return 0;
}

int wisedb_get_neighbors(wisedb_engine *eng, const char *slug, wisedb_node ***out, size_t *count) {
    if (!eng || !slug || !out || !count) return -1;
    *out = NULL;
    *count = 0;
    size_t cap = 0;
    wisedb_node **list = NULL;
    for (size_t i = 0; i < eng->edge_count; i++) {
        const char *neighbor_slug = NULL;
        if (strcmp(eng->edges[i].from_slug, slug) == 0) neighbor_slug = eng->edges[i].to_slug;
        else if (strcmp(eng->edges[i].to_slug, slug) == 0) neighbor_slug = eng->edges[i].from_slug;
        else continue;
        wisedb_node *n = wisedb_find_node(eng, neighbor_slug);
        if (!n) continue;
        int dup = 0;
        for (size_t j = 0; j < *count; j++) {
            if (strcmp(list[j]->slug, n->slug) == 0) { dup = 1; break; }
        }
        if (dup) continue;
        if (*count >= cap) {
            size_t next_cap = cap ? cap * 2 : 4;
            if (next_cap < cap || next_cap > SIZE_MAX / sizeof(*list)) {
                free(list);
                *count = 0;
                return -1;
            }
            wisedb_node **grown = (wisedb_node **)realloc(list, next_cap * sizeof(*list));
            if (!grown) {
                free(list);
                *count = 0;
                return -1;
            }
            list = grown;
            cap = next_cap;
        }
        list[(*count)++] = n;
    }
    *out = list;
    return 0;
}
