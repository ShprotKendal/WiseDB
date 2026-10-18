#ifndef WISEDB_CATALOG_FILTER_H
#define WISEDB_CATALOG_FILTER_H

#include "wisedb_internal.h"

typedef struct wisedb_predicate wisedb_predicate;

wisedb_predicate *wisedb_predicate_parse_json(const char *json);
void wisedb_predicate_free(wisedb_predicate *predicate);
int wisedb_predicate_matches_node(const wisedb_predicate *predicate, const wisedb_node *node);
int wisedb_list_catalog(
    wisedb_engine *eng,
    const wisedb_predicate *filter,
    const char *order_field,
    int order_asc,
    int limit,
    char **result_json_out
);

int wisedb_list_catalog_json(
    wisedb_engine *eng,
    const char *filter_json,
    const char *order_field,
    int order_asc,
    int limit,
    char **result_json_out
);

#endif /* WISEDB_CATALOG_FILTER_H */
