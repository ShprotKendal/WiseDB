#define _POSIX_C_SOURCE 200809L

#include "wisedb_internal.h"

static int parse_semantics_flag(const char *content) {
    if (!content) return 0;
    const char *key = strstr(content, "\"semantics\"");
    if (!key) return 0;
    const char *colon = strchr(key, ':');
    if (!colon) return 0;
    const char *value = colon + 1;
    while (*value && isspace((unsigned char)*value)) value++;
    if (strncmp(value, "true", 4) == 0) return 1;
    return 0;
}

int wisedb_settings_load(wisedb_engine *eng) {
    if (!eng) return -1;
    eng->semantics_enabled = 0;
    char *content = NULL;
    if (wisedb_read_settings_blob(eng, &content, NULL) != 0) {
        return -1;
    }
    if (!content) {
        return 0;
    }
    eng->semantics_enabled = parse_semantics_flag(content);
    free(content);
    return 0;
}

int wisedb_settings_write_semantics(wisedb_engine *eng, int enabled) {
    if (!eng) return -1;
    char payload[64];
    int written = snprintf(payload, sizeof(payload), "{\"semantics\":%s}\n", enabled ? "true" : "false");
    if (written < 0 || (size_t)written >= sizeof(payload)) return -1;
    return wisedb_write_settings_blob(eng, (const uint8_t *)payload, (size_t)written);
}
