#include "catalog_filter.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

typedef enum {
    WISEDB_PRED_CMP = 0,
    WISEDB_PRED_AND,
    WISEDB_PRED_OR,
    WISEDB_PRED_NOT
} wisedb_pred_kind;

typedef enum {
    WISEDB_VAL_NULL = 0,
    WISEDB_VAL_BOOL,
    WISEDB_VAL_NUMBER,
    WISEDB_VAL_STRING
} wisedb_pred_value_kind;

struct wisedb_predicate {
    wisedb_pred_kind kind;
    char field[64];
    char op[4];
    wisedb_pred_value_kind value_kind;
    int bool_value;
    double number_value;
    char *string_value;
    wisedb_predicate *left;
    wisedb_predicate *right;
    wisedb_predicate *child;
};

typedef struct {
    const char *json;
    size_t index;
} json_cursor;

static void skip_ws(json_cursor *cursor) {
    while (cursor->json[cursor->index] && isspace((unsigned char)cursor->json[cursor->index])) cursor->index++;
}

static int match_char(json_cursor *cursor, char expected) {
    skip_ws(cursor);
    if (cursor->json[cursor->index] != expected) return 0;
    cursor->index++;
    return 1;
}

static int parse_string(json_cursor *cursor, char **out) {
    skip_ws(cursor);
    if (cursor->json[cursor->index] != '"') return -1;
    cursor->index++;
    size_t start = cursor->index;
    while (cursor->json[cursor->index] && cursor->json[cursor->index] != '"') {
        if (cursor->json[cursor->index] == '\\') cursor->index++;
        cursor->index++;
    }
    if (cursor->json[cursor->index] != '"') return -1;
    size_t len = cursor->index - start;
    char *value = (char *)malloc(len + 1);
    if (!value) return -1;
    memcpy(value, cursor->json + start, len);
    value[len] = '\0';
    cursor->index++;
    *out = value;
    return 0;
}

static int parse_literal(json_cursor *cursor, wisedb_pred_value_kind *kind, int *bool_value, double *number_value, char **string_value) {
    skip_ws(cursor);
    if (strncmp(cursor->json + cursor->index, "null", 4) == 0) {
        cursor->index += 4;
        *kind = WISEDB_VAL_NULL;
        return 0;
    }
    if (strncmp(cursor->json + cursor->index, "true", 4) == 0) {
        cursor->index += 4;
        *kind = WISEDB_VAL_BOOL;
        *bool_value = 1;
        return 0;
    }
    if (strncmp(cursor->json + cursor->index, "false", 5) == 0) {
        cursor->index += 5;
        *kind = WISEDB_VAL_BOOL;
        *bool_value = 0;
        return 0;
    }
    if (cursor->json[cursor->index] == '"') {
        *kind = WISEDB_VAL_STRING;
        return parse_string(cursor, string_value);
    }
    if (isdigit((unsigned char)cursor->json[cursor->index]) || cursor->json[cursor->index] == '-') {
        char *end = NULL;
        *number_value = strtod(cursor->json + cursor->index, &end);
        if (!end || end == cursor->json + cursor->index) return -1;
        cursor->index = (size_t)(end - cursor->json);
        *kind = WISEDB_VAL_NUMBER;
        return 0;
    }
    return -1;
}

static wisedb_predicate *parse_predicate(json_cursor *cursor);

static wisedb_predicate *parse_cmp_object(json_cursor *cursor) {
    wisedb_predicate *predicate = (wisedb_predicate *)calloc(1, sizeof(*predicate));
    if (!predicate) return NULL;
    predicate->kind = WISEDB_PRED_CMP;
    if (!match_char(cursor, '{')) goto cmp_fail;
    while (1) {
        char *key = NULL;
        if (parse_string(cursor, &key) != 0) goto cmp_fail;
        if (!match_char(cursor, ':')) {
            free(key);
            goto cmp_fail;
        }
        if (strcmp(key, "field") == 0) {
            char *field = NULL;
            if (parse_string(cursor, &field) != 0) {
                free(key);
                goto cmp_fail;
            }
            strncpy(predicate->field, field, sizeof(predicate->field) - 1);
            free(field);
        } else if (strcmp(key, "op") == 0) {
            char *op = NULL;
            if (parse_string(cursor, &op) != 0) {
                free(key);
                goto cmp_fail;
            }
            strncpy(predicate->op, op, sizeof(predicate->op) - 1);
            free(op);
        } else if (strcmp(key, "value") == 0) {
            if (parse_literal(cursor, &predicate->value_kind, &predicate->bool_value, &predicate->number_value, &predicate->string_value) != 0) {
                free(key);
                goto cmp_fail;
            }
        } else {
            free(key);
            goto cmp_fail;
        }
        free(key);
        skip_ws(cursor);
        if (cursor->json[cursor->index] == '}') {
            cursor->index++;
            return predicate;
        }
        if (!match_char(cursor, ',')) goto cmp_fail;
    }
cmp_fail:
    wisedb_predicate_free(predicate);
    return NULL;
}

static wisedb_predicate *parse_array_predicates(json_cursor *cursor, wisedb_pred_kind combiner) {
    wisedb_predicate *result = NULL;
    if (!match_char(cursor, '[')) return NULL;
    skip_ws(cursor);
    if (match_char(cursor, ']')) return NULL;
    do {
        wisedb_predicate *item = parse_predicate(cursor);
        if (!item) {
            wisedb_predicate_free(result);
            return NULL;
        }
        if (!result) {
            result = item;
        } else {
            wisedb_predicate *combined = (wisedb_predicate *)calloc(1, sizeof(*combined));
            if (!combined) {
                wisedb_predicate_free(item);
                wisedb_predicate_free(result);
                return NULL;
            }
            combined->kind = combiner;
            combined->left = result;
            combined->right = item;
            result = combined;
        }
        skip_ws(cursor);
        if (cursor->json[cursor->index] == ']') {
            cursor->index++;
            break;
        }
        if (!match_char(cursor, ',')) {
            wisedb_predicate_free(result);
            return NULL;
        }
    } while (1);
    return result;
}

static wisedb_predicate *parse_predicate(json_cursor *cursor) {
    skip_ws(cursor);
    if (!match_char(cursor, '{')) return NULL;
    char *key = NULL;
    if (parse_string(cursor, &key) != 0) return NULL;
    if (!match_char(cursor, ':')) {
        free(key);
        return NULL;
    }
    wisedb_predicate *predicate = NULL;
    if (strcmp(key, "cmp") == 0) {
        predicate = parse_cmp_object(cursor);
    } else if (strcmp(key, "and") == 0) {
        predicate = parse_array_predicates(cursor, WISEDB_PRED_AND);
    } else if (strcmp(key, "or") == 0) {
        predicate = parse_array_predicates(cursor, WISEDB_PRED_OR);
    } else if (strcmp(key, "not") == 0) {
        predicate = (wisedb_predicate *)calloc(1, sizeof(*predicate));
        if (predicate) {
            predicate->kind = WISEDB_PRED_NOT;
            predicate->child = parse_predicate(cursor);
            if (!predicate->child) {
                wisedb_predicate_free(predicate);
                predicate = NULL;
            }
        }
    }
    free(key);
    if (!predicate) return NULL;
    skip_ws(cursor);
    if (!match_char(cursor, '}')) {
        wisedb_predicate_free(predicate);
        return NULL;
    }
    return predicate;
}

wisedb_predicate *wisedb_predicate_parse_json(const char *json) {
    if (!json || !json[0]) return NULL;
    json_cursor cursor = {.json = json, .index = 0};
    return parse_predicate(&cursor);
}

void wisedb_predicate_free(wisedb_predicate *predicate) {
    if (!predicate) return;
    wisedb_predicate_free(predicate->left);
    wisedb_predicate_free(predicate->right);
    wisedb_predicate_free(predicate->child);
    free(predicate->string_value);
    free(predicate);
}

static int compare_node_field(
    const wisedb_node *node,
    const char *field,
    const char *op,
    wisedb_pred_value_kind value_kind,
    int bool_value,
    double number_value,
    const char *string_value
) {
    if (!node || !field || !op) return 0;
    int is_equal = strcmp(op, "=") == 0;
    int is_not_equal = strcmp(op, "!=") == 0;
    if (!is_equal && !is_not_equal && strcmp(op, "<") != 0 && strcmp(op, ">") != 0 && strcmp(op, "<=") != 0
        && strcmp(op, ">=") != 0) {
        return 0;
    }

    if (strcmp(field, "slug") == 0) {
        const char *left = node->slug ? node->slug : "";
        const char *right = string_value ? string_value : "";
        int result = strcmp(left, right) == 0;
        if (is_equal) return result;
        if (is_not_equal) return !result;
        return 0;
    }
    if (strcmp(field, "type") == 0) {
        const char *left = wisedb_type_to_string(node->type);
        const char *right = string_value ? string_value : "";
        int result = strcmp(left, right) == 0;
        if (is_equal) return result;
        if (is_not_equal) return !result;
        return 0;
    }
    if (strcmp(field, "title") == 0 || strcmp(field, "summary") == 0 || strcmp(field, "rel_path") == 0) {
        const char *left = NULL;
        if (strcmp(field, "title") == 0) left = node->title;
        else if (strcmp(field, "summary") == 0) left = node->summary;
        else left = node->rel_path;
        left = left ? left : "";
        const char *right = string_value ? string_value : "";
        int result = strcmp(left, right) == 0;
        if (is_equal) return result;
        if (is_not_equal) return !result;
        return 0;
    }
    if (strcmp(field, "is_media") == 0) {
        int left = node->is_media ? 1 : 0;
        int right = value_kind == WISEDB_VAL_BOOL ? bool_value : 0;
        if (is_equal) return left == right;
        if (is_not_equal) return left != right;
        return 0;
    }
    if (strcmp(field, "token_count") == 0) {
        double left = (double)node->token_count;
        double right = value_kind == WISEDB_VAL_NUMBER ? number_value : 0.0;
        int result = 0;
        if (strcmp(op, "=") == 0) result = left == right;
        else if (strcmp(op, "!=") == 0) result = left != right;
        else if (strcmp(op, "<") == 0) result = left < right;
        else if (strcmp(op, ">") == 0) result = left > right;
        else if (strcmp(op, "<=") == 0) result = left <= right;
        else if (strcmp(op, ">=") == 0) result = left >= right;
        return result;
    }
    return 0;
}

int wisedb_predicate_matches_node(const wisedb_predicate *predicate, const wisedb_node *node) {
    if (!predicate) return 1;
    switch (predicate->kind) {
        case WISEDB_PRED_CMP:
            return compare_node_field(
                node,
                predicate->field,
                predicate->op,
                predicate->value_kind,
                predicate->bool_value,
                predicate->number_value,
                predicate->string_value);
        case WISEDB_PRED_AND:
            return wisedb_predicate_matches_node(predicate->left, node)
                && wisedb_predicate_matches_node(predicate->right, node);
        case WISEDB_PRED_OR:
            return wisedb_predicate_matches_node(predicate->left, node)
                || wisedb_predicate_matches_node(predicate->right, node);
        case WISEDB_PRED_NOT:
            return !wisedb_predicate_matches_node(predicate->child, node);
        default:
            return 0;
    }
}

typedef struct {
    size_t *indices;
    size_t count;
    size_t cap;
} index_list;

static int index_list_push(index_list *list, size_t idx) {
    if (list->count >= list->cap) {
        size_t next = list->cap ? list->cap * 2 : 8;
        if (next < list->cap || next > SIZE_MAX / sizeof(size_t)) return -1;
        size_t *grown = (size_t *)realloc(list->indices, next * sizeof(size_t));
        if (!grown) return -1;
        list->indices = grown;
        list->cap = next;
    }
    list->indices[list->count++] = idx;
    return 0;
}

static int catalog_field_value(const wisedb_node *node, const char *field, const char **string_out, double *number_out, int *is_number) {
    if (!node || !field) return -1;
    *is_number = 0;
    if (strcmp(field, "slug") == 0) {
        *string_out = node->slug ? node->slug : "";
        return 0;
    }
    if (strcmp(field, "type") == 0) {
        *string_out = wisedb_type_to_string(node->type);
        return 0;
    }
    if (strcmp(field, "title") == 0) {
        *string_out = node->title ? node->title : "";
        return 0;
    }
    if (strcmp(field, "summary") == 0) {
        *string_out = node->summary ? node->summary : "";
        return 0;
    }
    if (strcmp(field, "rel_path") == 0) {
        *string_out = node->rel_path ? node->rel_path : "";
        return 0;
    }
    if (strcmp(field, "token_count") == 0) {
        *number_out = (double)node->token_count;
        *is_number = 1;
        return 0;
    }
    if (strcmp(field, "is_media") == 0) {
        *number_out = node->is_media ? 1.0 : 0.0;
        *is_number = 1;
        return 0;
    }
    return -1;
}

static int cmp_nodes(const wisedb_engine *eng, size_t left_idx, size_t right_idx, const char *field, int order_asc) {
    const wisedb_node *left = &eng->nodes[left_idx];
    const wisedb_node *right = &eng->nodes[right_idx];
    const char *left_string = NULL;
    const char *right_string = NULL;
    double left_number = 0.0;
    double right_number = 0.0;
    int left_is_number = 0;
    int right_is_number = 0;
    if (catalog_field_value(left, field, &left_string, &left_number, &left_is_number) != 0
        || catalog_field_value(right, field, &right_string, &right_number, &right_is_number) != 0) {
        return 0;
    }
    int result = 0;
    if (left_is_number && right_is_number) {
        if (left_number < right_number) result = -1;
        else if (left_number > right_number) result = 1;
    } else {
        result = strcmp(left_string, right_string);
    }
    return order_asc ? result : -result;
}

static int append_catalog_page_json(wisedb_json_buffer *buffer, const wisedb_node *node) {
    return wisedb_json_buffer_append(buffer, "{\"slug\":")
        || wisedb_json_buffer_append_string(buffer, node->slug)
        || wisedb_json_buffer_append(buffer, ",\"type\":")
        || wisedb_json_buffer_append_string(buffer, wisedb_type_to_string(node->type))
        || wisedb_json_buffer_append(buffer, ",\"title\":")
        || wisedb_json_buffer_append_string(buffer, node->title)
        || wisedb_json_buffer_append(buffer, ",\"summary\":")
        || wisedb_json_buffer_append_string(buffer, node->summary)
        || wisedb_json_buffer_appendf(buffer, ",\"token_count\":%d,\"rel_path\":", node->token_count)
        || wisedb_json_buffer_append_string(buffer, node->rel_path)
        || wisedb_json_buffer_appendf(buffer, ",\"is_media\":%s}", node->is_media ? "true" : "false");
}

int wisedb_list_catalog(
    wisedb_engine *eng,
    const wisedb_predicate *filter,
    const char *order_field,
    int order_asc,
    int limit,
    char **result_json_out
) {
    if (!eng || !result_json_out) return -1;
    *result_json_out = NULL;

    index_list matches = {0};
    for (size_t i = 0; i < eng->node_count; i++) {
        if (!wisedb_predicate_matches_node(filter, &eng->nodes[i])) continue;
        if (index_list_push(&matches, i) != 0) {
            free(matches.indices);
            wisedb_set_error(eng, "out of memory filtering catalog");
            return -1;
        }
    }

    if (order_field && order_field[0]) {
        for (size_t i = 0; i + 1 < matches.count; i++) {
            for (size_t j = i + 1; j < matches.count; j++) {
                if (cmp_nodes(eng, matches.indices[i], matches.indices[j], order_field, order_asc) > 0) {
                    size_t tmp = matches.indices[i];
                    matches.indices[i] = matches.indices[j];
                    matches.indices[j] = tmp;
                }
            }
        }
    }

    size_t output_count = matches.count;
    if (limit >= 0 && (size_t)limit < output_count) output_count = (size_t)limit;

    wisedb_json_buffer buffer;
    wisedb_json_buffer_init(&buffer);
    if (wisedb_json_buffer_append(&buffer, "{\"status\":\"ok\",\"pages\":[") != 0) goto catalog_fail;
    for (size_t i = 0; i < output_count; i++) {
        if (i > 0 && wisedb_json_buffer_append(&buffer, ",") != 0) goto catalog_fail;
        if (append_catalog_page_json(&buffer, &eng->nodes[matches.indices[i]]) != 0) goto catalog_fail;
    }
    if (wisedb_json_buffer_append(&buffer, "]}") != 0) goto catalog_fail;
    *result_json_out = wisedb_json_buffer_take(&buffer);
    free(matches.indices);
    if (!*result_json_out) {
        wisedb_set_error(eng, "out of memory serializing catalog response");
        return -1;
    }
    return 0;

catalog_fail:
    wisedb_json_buffer_dispose(&buffer);
    free(matches.indices);
    wisedb_set_error(eng, "out of memory serializing catalog response");
    return -1;
}

int wisedb_list_catalog_json(
    wisedb_engine *eng,
    const char *filter_json,
    const char *order_field,
    int order_asc,
    int limit,
    char **result_json_out
) {
    wisedb_predicate *filter = NULL;
    if (filter_json && filter_json[0]) {
        filter = wisedb_predicate_parse_json(filter_json);
        if (!filter) {
            wisedb_set_error(eng, "invalid catalog filter JSON");
            return -1;
        }
    }
    int rc = wisedb_list_catalog(eng, filter, order_field, order_asc, limit, result_json_out);
    wisedb_predicate_free(filter);
    return rc;
}
