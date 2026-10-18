#include "wisedb_internal.h"
#include "fault_injection.h"

#include <ctype.h>
#include <limits.h>
#include <math.h>
#include <stdint.h>

#ifndef WISEDB_MAX_SEARCH_QUERY_BYTES
#define WISEDB_MAX_SEARCH_QUERY_BYTES (64u * 1024u)
#endif
#define WISEDB_MAX_SEARCH_TERMS 256u

typedef struct {
    size_t idx;
    double score;
} scored_t;

static int is_word_byte(unsigned char c) { return c >= 0x80 || isalnum(c); }

static uint32_t fold_codepoint(uint32_t cp) {
    if (cp >= 'A' && cp <= 'Z') return cp + 32u;
    if (cp >= 0x410u && cp <= 0x42fu) return cp + 32u;
    if (cp == 0x401u) return 0x451u;
    return cp;
}

static int utf8_read(const unsigned char **cursor, const unsigned char *end, uint32_t *codepoint_out) {
    const unsigned char *p = *cursor;
    if (end && p >= end) return 0;
    if (!*p) return 0;
    unsigned char lead = p[0];
    if (lead < 0x80) {
        *codepoint_out = lead;
        *cursor = p + 1;
        return 1;
    }
    uint32_t cp;
    size_t continuation_count;
    if (lead >= 0xc2 && lead <= 0xdf) {
        cp = lead & 0x1fu;
        continuation_count = 1;
    } else if (lead >= 0xe0 && lead <= 0xef) {
        cp = lead & 0x0fu;
        continuation_count = 2;
    } else if (lead >= 0xf0 && lead <= 0xf4) {
        cp = lead & 0x07u;
        continuation_count = 3;
    } else {
        return 0;
    }
    p++;
    if (end && (size_t)(end - p) < continuation_count) return 0;
    for (size_t i = 0; i < continuation_count; i++) {
        if (!p[i] || (p[i] & 0xc0u) != 0x80u) return 0;
        cp = (cp << 6) | (p[i] & 0x3fu);
    }
    *codepoint_out = cp;
    *cursor = p + continuation_count;
    return 1;
}

static int utf8_append_folded(uint32_t cp, char **buffer, size_t *length, size_t *capacity) {
    unsigned char encoded[4];
    size_t encoded_len;
    cp = fold_codepoint(cp);
    if (cp < 0x80) {
        encoded[0] = (unsigned char)cp;
        encoded_len = 1;
    } else if (cp < 0x800) {
        encoded[0] = (unsigned char)(0xc0u | (cp >> 6));
        encoded[1] = (unsigned char)(0x80u | (cp & 0x3fu));
        encoded_len = 2;
    } else if (cp < 0x10000) {
        encoded[0] = (unsigned char)(0xe0u | (cp >> 12));
        encoded[1] = (unsigned char)(0x80u | ((cp >> 6) & 0x3fu));
        encoded[2] = (unsigned char)(0x80u | (cp & 0x3fu));
        encoded_len = 3;
    } else if (cp <= 0x10ffff) {
        encoded[0] = (unsigned char)(0xf0u | (cp >> 18));
        encoded[1] = (unsigned char)(0x80u | ((cp >> 12) & 0x3fu));
        encoded[2] = (unsigned char)(0x80u | ((cp >> 6) & 0x3fu));
        encoded[3] = (unsigned char)(0x80u | (cp & 0x3fu));
        encoded_len = 4;
    } else {
        return -1;
    }
    if (*length + encoded_len + 1 > *capacity) {
        size_t next = *capacity ? *capacity * 2 : 16;
        while (next < *length + encoded_len + 1) {
            if (next > SIZE_MAX / 2) return -1;
            next *= 2;
        }
        char *grown = (char *)realloc(*buffer, next);
        if (!grown) return -1;
        *buffer = grown;
        *capacity = next;
    }
    memcpy(*buffer + *length, encoded, encoded_len);
    *length += encoded_len;
    (*buffer)[*length] = '\0';
    return 0;
}

static int fold_text_span(const char *start, size_t span_len, char **out, size_t *out_len) {
    *out = NULL;
    *out_len = 0;
    size_t capacity = 0;
    const unsigned char *cursor = (const unsigned char *)start;
    const unsigned char *end = cursor + span_len;
    while (cursor < end) {
        uint32_t cp;
        if (!utf8_read(&cursor, end, &cp)) return -1;
        if (utf8_append_folded(cp, out, out_len, &capacity) != 0) {
            free(*out);
            *out = NULL;
            *out_len = 0;
            return -1;
        }
    }
    return 0;
}

static int word_equals_term(const char *word_start, size_t word_len, const char *term) {
    char *folded = NULL;
    size_t folded_len = 0;
    if (fold_text_span(word_start, word_len, &folded, &folded_len) != 0) return 0;
    int match = strcmp(folded, term) == 0;
    free(folded);
    return match;
}

static int word_token_count(const char *text, const char *term) {
    if (!text || !term || !term[0]) return 0;
    int count = 0;
    const char *cursor = text;
    while (*cursor) {
        while (*cursor && !is_word_byte((unsigned char)*cursor)) cursor++;
        if (!*cursor) break;
        const char *start = cursor;
        while (*cursor && is_word_byte((unsigned char)*cursor)) cursor++;
        if (word_equals_term(start, (size_t)(cursor - start), term)) {
            if (count < INT_MAX) count++;
        }
    }
    return count;
}

static int tokenize_query(const char *query, char ***terms_out, size_t *count_out) {
    *terms_out = NULL;
    *count_out = 0;
    size_t capacity = 0;
    const char *cursor = query;
    while (*cursor) {
        while (*cursor && !is_word_byte((unsigned char)*cursor)) cursor++;
        if (!*cursor) break;
        const char *start = cursor;
        while (*cursor && is_word_byte((unsigned char)*cursor)) cursor++;
        size_t length = (size_t)(cursor - start);
        if (length == 0) continue;
        if (*count_out >= WISEDB_MAX_SEARCH_TERMS) goto fail;
        if (*count_out == capacity) {
            size_t next = capacity ? capacity * 2 : 8;
            if (next < capacity || next > SIZE_MAX / sizeof(char *)) goto fail;
            char **grown = (char **)realloc(*terms_out, next * sizeof(char *));
            if (!grown) goto fail;
            *terms_out = grown;
            capacity = next;
        }
        char *term = NULL;
        size_t term_len = 0;
        if (fold_text_span(start, length, &term, &term_len) != 0) goto fail;
        (*terms_out)[(*count_out)++] = term;
    }
    return 0;

fail:
    for (size_t i = 0; i < *count_out; i++) free((*terms_out)[i]);
    free(*terms_out);
    *terms_out = NULL;
    *count_out = 0;
    return -1;
}

static void free_terms(char **terms, size_t count) {
    for (size_t i = 0; i < count; i++) free(terms[i]);
    free(terms);
}

static int node_term_frequency(const wisedb_node *node, const char *term) {
    int count = word_token_count(node->title, term);
    int extra = word_token_count(node->summary, term);
    if (extra > INT_MAX - count) count = INT_MAX;
    else count += extra;
    extra = word_token_count(node->body, term);
    if (extra > INT_MAX - count) return INT_MAX;
    return count + extra;
}

static double bm25_score(
    wisedb_engine *eng,
    const wisedb_node *node,
    char **terms,
    size_t term_count,
    const int *document_frequencies
) {
    const double k1 = 1.2;
    const double b = 0.75;
    double score = 0.0;
    double dl = (double)node->token_count;
    if (dl < 1.0) dl = 1.0;
    double avgdl = eng->avg_doc_len > 0 ? eng->avg_doc_len : 1.0;

    for (size_t t = 0; t < term_count; t++) {
        int frequency = node_term_frequency(node, terms[t]);
        if (frequency == 0) continue;
        int df = document_frequencies[t];
        if (df == 0) df = 1;
        double corpus_size = (double)(eng->doc_count > 0 ? eng->doc_count : 1);
        double idf = log((corpus_size - df + 0.5) / (df + 0.5) + 1.0);
        double denominator = frequency + k1 * (1.0 - b + b * dl / avgdl);
        score += idf * ((frequency * (k1 + 1.0)) / denominator);
    }
    return score;
}

static int cmp_scored(const void *left, const void *right) {
    const scored_t *a = (const scored_t *)left;
    const scored_t *b = (const scored_t *)right;
    if (a->score < b->score) return 1;
    if (a->score > b->score) return -1;
    return (a->idx > b->idx) - (a->idx < b->idx);
}

static int append_node_json(wisedb_json_buffer *buffer, const wisedb_node *node, int include_body) {
    return wisedb_json_buffer_append(buffer, "{\"slug\":")
        || wisedb_json_buffer_append_string(buffer, node->slug)
        || wisedb_json_buffer_append(buffer, ",\"type\":")
        || wisedb_json_buffer_append_string(buffer, wisedb_type_to_string(node->type))
        || wisedb_json_buffer_append(buffer, ",\"title\":")
        || wisedb_json_buffer_append_string(buffer, node->title)
        || wisedb_json_buffer_append(buffer, ",\"summary\":")
        || wisedb_json_buffer_append_string(buffer, node->summary)
        || wisedb_json_buffer_append(buffer, ",\"body\":")
        || wisedb_json_buffer_append_string(buffer, include_body ? node->body : "")
        || wisedb_json_buffer_append(buffer, ",\"rel_path\":")
        || wisedb_json_buffer_append_string(buffer, node->rel_path)
        || wisedb_json_buffer_append(buffer, ",\"mime\":")
        || wisedb_json_buffer_append_string(buffer, node->mime)
        || wisedb_json_buffer_appendf(buffer, ",\"token_count\":%d,\"is_media\":%s}",
                                      node->token_count, node->is_media ? "true" : "false");
}

int wisedb_build_search_json(
    wisedb_engine *eng,
    const char *query,
    int budget_tokens,
    int top_k,
    char **result_json_out
) {
    if (!eng || !query || !result_json_out) return -1;
    *result_json_out = NULL;
    if (strlen(query) > WISEDB_MAX_SEARCH_QUERY_BYTES) {
        wisedb_set_error(eng, "search query exceeds byte limit");
        return -1;
    }
    if (budget_tokens < 0) budget_tokens = 0;
    if (top_k <= 0) top_k = 10;

    char **terms = NULL;
    size_t term_count = 0;
    if (tokenize_query(query, &terms, &term_count) != 0) {
        wisedb_set_error(eng, "search query has too many terms or tokenization ran out of memory");
        return -1;
    }
    int *document_frequencies = term_count ? (int *)calloc(term_count, sizeof(*document_frequencies)) : NULL;
    if (term_count && !document_frequencies) {
        free_terms(terms, term_count);
        wisedb_set_error(eng, "out of memory collecting search statistics");
        return -1;
    }
    for (size_t t = 0; t < term_count; t++) {
        for (size_t i = 0; i < eng->node_count; i++) {
            if (node_term_frequency(&eng->nodes[i], terms[t]) > 0
                && document_frequencies[t] < INT_MAX) document_frequencies[t]++;
        }
    }
    if (eng->node_count > SIZE_MAX / sizeof(scored_t)) {
        free(document_frequencies);
        free_terms(terms, term_count);
        wisedb_set_error(eng, "search result count overflow");
        return -1;
    }
    scored_t *scored = eng->node_count ? (scored_t *)calloc(eng->node_count, sizeof(*scored)) : NULL;
    if (eng->node_count && !scored) {
        free(document_frequencies);
        free_terms(terms, term_count);
        wisedb_set_error(eng, "out of memory scoring search results");
        return -1;
    }
    size_t scored_count = 0;
    for (size_t i = 0; i < eng->node_count; i++) {
        double score = bm25_score(eng, &eng->nodes[i], terms, term_count, document_frequencies);
        if (score > 0) {
            scored[scored_count].idx = i;
            scored[scored_count].score = score;
            scored_count++;
        }
    }
    if (scored_count > 1) qsort(scored, scored_count, sizeof(*scored), cmp_scored);

    wisedb_json_buffer buffer;
    wisedb_json_buffer_init(&buffer);
    if (wisedb_json_buffer_append(&buffer, "{\"query\":") != 0
        || wisedb_json_buffer_append_string(&buffer, query) != 0
        || wisedb_json_buffer_appendf(&buffer, ",\"budget_tokens\":%d,\"hits\":[", budget_tokens) != 0) goto fail;

    int hits_written = 0;
    for (size_t i = 0; i < scored_count && hits_written < top_k; i++) {
        if ((hits_written && wisedb_json_buffer_append(&buffer, ",") != 0)
            || append_node_json(&buffer, &eng->nodes[scored[i].idx], 0) != 0) goto fail;
        hits_written++;
    }
    if (wisedb_json_buffer_append(&buffer, "],\"packet\":[") != 0) goto fail;

    int used_tokens = 0;
    int packet_count = 0;
    for (size_t i = 0; i < scored_count && budget_tokens > 0 && used_tokens < budget_tokens; i++) {
        wisedb_node *node = &eng->nodes[scored[i].idx];
        int include_body = !node->is_media;
        int cost = wisedb_estimate_tokens(node->summary) + (include_body ? node->token_count : 0) + 8;
        if (cost < 0 || cost > budget_tokens - used_tokens) continue;
        if ((packet_count && wisedb_json_buffer_append(&buffer, ",") != 0)
            || append_node_json(&buffer, node, include_body) != 0) goto fail;
        used_tokens += cost;
        packet_count++;
    }
    if (wisedb_json_buffer_append(&buffer, "],\"neighborhood\":[") != 0) goto fail;

    int neighbor_count = 0;
    for (size_t i = 0; i < scored_count && budget_tokens > 0 && i < (size_t)top_k; i++) {
        wisedb_node **neighbors = NULL;
        size_t count = 0;
        if (wisedb_get_neighbors(eng, eng->nodes[scored[i].idx].slug, &neighbors, &count) != 0) goto fail;
        for (size_t j = 0; j < count; j++) {
            int cost = wisedb_estimate_tokens(neighbors[j]->summary) + 4;
            if (cost < 0 || cost > budget_tokens - used_tokens) break;
            if ((neighbor_count && wisedb_json_buffer_append(&buffer, ",") != 0)
                || append_node_json(&buffer, neighbors[j], 0) != 0) {
                free(neighbors);
                goto fail;
            }
            used_tokens += cost;
            neighbor_count++;
        }
        free(neighbors);
    }
    if (wisedb_json_buffer_appendf(&buffer, "],\"used_tokens\":%d}", used_tokens) != 0) goto fail;

    *result_json_out = wisedb_json_buffer_take(&buffer);
    if (!*result_json_out) goto fail;
    free(scored);
    free(document_frequencies);
    free_terms(terms, term_count);
    return 0;

fail:
    wisedb_json_buffer_dispose(&buffer);
    free(scored);
    free(document_frequencies);
    free_terms(terms, term_count);
    wisedb_set_error(eng, "out of memory serializing search response");
    return -1;
}
