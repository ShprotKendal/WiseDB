#ifndef WISEDB_JSON_H
#define WISEDB_JSON_H

#include <stddef.h>

typedef struct {
    char *data;
    size_t length;
    size_t capacity;
} wisedb_json_buffer;

void wisedb_json_buffer_init(wisedb_json_buffer *buffer);
void wisedb_json_buffer_dispose(wisedb_json_buffer *buffer);
int wisedb_json_buffer_append(wisedb_json_buffer *buffer, const char *text);
int wisedb_json_buffer_appendf(wisedb_json_buffer *buffer, const char *format, ...);
int wisedb_json_buffer_append_string(wisedb_json_buffer *buffer, const char *text);
char *wisedb_json_buffer_take(wisedb_json_buffer *buffer);
int wisedb_json_is_valid_utf8(const char *text);

#endif /* WISEDB_JSON_H */
