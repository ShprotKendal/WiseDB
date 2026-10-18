#define _POSIX_C_SOURCE 200809L

#include "json.h"

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fault_injection.h"

#define WISEDB_JSON_MAX_BYTES (64u * 1024u * 1024u)

static int reserve(wisedb_json_buffer *buffer, size_t additional) {
    if (!buffer || buffer->length > WISEDB_JSON_MAX_BYTES
        || additional > WISEDB_JSON_MAX_BYTES - buffer->length
        || additional > SIZE_MAX - buffer->length - 1) return -1;
    size_t needed = buffer->length + additional + 1;
    if (needed <= buffer->capacity) return 0;
    size_t capacity = buffer->capacity ? buffer->capacity : 128;
    while (capacity < needed) {
        if (capacity > SIZE_MAX / 2) {
            capacity = needed;
            break;
        }
        capacity *= 2;
    }
    if (capacity > WISEDB_JSON_MAX_BYTES + 1u) capacity = needed;
    char *data = (char *)realloc(buffer->data, capacity);
    if (!data) return -1;
    buffer->data = data;
    buffer->capacity = capacity;
    return 0;
}

int wisedb_json_is_valid_utf8(const char *value) {
    if (!value) return 1;
    const unsigned char *text = (const unsigned char *)value;
    while (*text) {
        if (*text < 0x80) {
            text++;
            continue;
        }
        uint32_t codepoint;
        size_t continuation_count;
        if (*text >= 0xc2 && *text <= 0xdf) {
            codepoint = *text & 0x1fu;
            continuation_count = 1;
        } else if (*text >= 0xe0 && *text <= 0xef) {
            codepoint = *text & 0x0fu;
            continuation_count = 2;
        } else if (*text >= 0xf0 && *text <= 0xf4) {
            codepoint = *text & 0x07u;
            continuation_count = 3;
        } else {
            return 0;
        }
        text++;
        for (size_t i = 0; i < continuation_count; i++) {
            if (!text[i] || (text[i] & 0xc0u) != 0x80u) return 0;
            codepoint = (codepoint << 6) | (text[i] & 0x3fu);
        }
        if ((continuation_count == 1 && codepoint < 0x80)
            || (continuation_count == 2 && codepoint < 0x800)
            || (continuation_count == 3 && codepoint < 0x10000)
            || (codepoint >= 0xd800 && codepoint <= 0xdfff)
            || codepoint > 0x10ffff) return 0;
        text += continuation_count;
    }
    return 1;
}

void wisedb_json_buffer_init(wisedb_json_buffer *buffer) {
    if (buffer) memset(buffer, 0, sizeof(*buffer));
}

void wisedb_json_buffer_dispose(wisedb_json_buffer *buffer) {
    if (!buffer) return;
    free(buffer->data);
    memset(buffer, 0, sizeof(*buffer));
}

int wisedb_json_buffer_append(wisedb_json_buffer *buffer, const char *text) {
    if (!buffer || !text) return -1;
    size_t length = strlen(text);
    if (reserve(buffer, length) != 0) return -1;
    memcpy(buffer->data + buffer->length, text, length + 1);
    buffer->length += length;
    return 0;
}

int wisedb_json_buffer_appendf(wisedb_json_buffer *buffer, const char *format, ...) {
    if (!buffer || !format) return -1;
    va_list args;
    va_start(args, format);
    va_list measure;
    va_copy(measure, args);
    int required = vsnprintf(NULL, 0, format, measure);
    va_end(measure);
    if (required < 0 || reserve(buffer, (size_t)required) != 0) {
        va_end(args);
        return -1;
    }
    int written = vsnprintf(buffer->data + buffer->length,
                            buffer->capacity - buffer->length, format, args);
    va_end(args);
    if (written != required) return -1;
    buffer->length += (size_t)written;
    return 0;
}

int wisedb_json_buffer_append_string(wisedb_json_buffer *buffer, const char *text) {
    if (!buffer) return -1;
    if (!text) text = "";
    if (!wisedb_json_is_valid_utf8(text)) return -1;
    if (wisedb_json_buffer_append(buffer, "\"") != 0) return -1;
    for (const unsigned char *p = (const unsigned char *)text; *p; ++p) {
        const char *escape = NULL;
        char encoded[7];
        switch (*p) {
            case '"': escape = "\\\""; break;
            case '\\': escape = "\\\\"; break;
            case '\b': escape = "\\b"; break;
            case '\f': escape = "\\f"; break;
            case '\n': escape = "\\n"; break;
            case '\r': escape = "\\r"; break;
            case '\t': escape = "\\t"; break;
            default:
                if (*p < 0x20) {
                    (void)snprintf(encoded, sizeof(encoded), "\\u%04x", (unsigned)*p);
                    escape = encoded;
                }
                break;
        }
        if (escape) {
            if (wisedb_json_buffer_append(buffer, escape) != 0) return -1;
        } else {
            char byte[2] = {(char)*p, '\0'};
            if (reserve(buffer, 1) != 0) return -1;
            buffer->data[buffer->length++] = byte[0];
            buffer->data[buffer->length] = '\0';
        }
    }
    return wisedb_json_buffer_append(buffer, "\"");
}

char *wisedb_json_buffer_take(wisedb_json_buffer *buffer) {
    if (!buffer) return NULL;
    if (!buffer->data) {
        buffer->data = (char *)malloc(1);
        if (!buffer->data) return NULL;
        buffer->data[0] = '\0';
    }
    char *result = buffer->data;
    memset(buffer, 0, sizeof(*buffer));
    return result;
}
