#include "wisedb_internal.h"
#include "fault_injection.h"

#include <limits.h>

#include <stdarg.h>

void wisedb_set_error(wisedb_engine *eng, const char *fmt, ...) {
    if (!eng) return;
    free(eng->last_error);
    eng->last_error = NULL;
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(NULL, 0, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    eng->last_error = (char *)malloc((size_t)n + 1);
    if (!eng->last_error) return;
    va_start(ap, fmt);
    vsnprintf(eng->last_error, (size_t)n + 1, fmt, ap);
    va_end(ap);
}

char *wisedb_json_escape(const char *s) {
    if (!s) s = "";
    size_t extra = 0;
    for (const char *p = s; *p; p++) {
        unsigned char c = (unsigned char)*p;
        if (c < 0x20) extra += c == '\b' || c == '\f' || c == '\n' || c == '\r' || c == '\t' ? 1 : 5;
        else if (c == '"' || c == '\\') extra++;
    }
    if (strlen(s) > SIZE_MAX - extra - 1) return NULL;
    size_t len = strlen(s) + extra;
    char *out = (char *)malloc(len + 1);
    if (!out) return NULL;
    char *w = out;
    for (const char *p = s; *p; p++) {
        switch (*p) {
            case '"': *w++ = '\\'; *w++ = '"'; break;
            case '\\': *w++ = '\\'; *w++ = '\\'; break;
            case '\n': *w++ = '\\'; *w++ = 'n'; break;
            case '\r': *w++ = '\\'; *w++ = 'r'; break;
            case '\t': *w++ = '\\'; *w++ = 't'; break;
            default:
                if ((unsigned char)*p < 0x20) {
                    (void)snprintf(w, 7, "\\u%04x", (unsigned char)*p);
                    w += 6;
                } else {
                    *w++ = *p;
                }
                break;
        }
    }
    *w = '\0';
    return out;
}

int wisedb_estimate_tokens(const char *text) {
    if (!text) return 0;
    size_t estimate = strlen(text) / 4;
    return estimate > INT_MAX ? INT_MAX : (int)estimate;
}

wisedb_node_type wisedb_type_from_string(const char *s) {
    if (!s) return WISEDB_NODE_UNKNOWN;
    if (strcmp(s, "entity") == 0) return WISEDB_NODE_ENTITY;
    if (strcmp(s, "concept") == 0) return WISEDB_NODE_CONCEPT;
    if (strcmp(s, "source") == 0) return WISEDB_NODE_SOURCE;
    if (strcmp(s, "comparison") == 0) return WISEDB_NODE_COMPARISON;
    if (strcmp(s, "synthesis") == 0) return WISEDB_NODE_SYNTHESIS;
    if (strcmp(s, "playbook") == 0) return WISEDB_NODE_PLAYBOOK;
    if (strcmp(s, "media") == 0) return WISEDB_NODE_MEDIA;
    return WISEDB_NODE_UNKNOWN;
}

const char *wisedb_type_to_string(wisedb_node_type t) {
    switch (t) {
        case WISEDB_NODE_ENTITY: return "entity";
        case WISEDB_NODE_CONCEPT: return "concept";
        case WISEDB_NODE_SOURCE: return "source";
        case WISEDB_NODE_COMPARISON: return "comparison";
        case WISEDB_NODE_SYNTHESIS: return "synthesis";
        case WISEDB_NODE_PLAYBOOK: return "playbook";
        case WISEDB_NODE_MEDIA: return "media";
        default: return "concept";
    }
}

static void trim(char *s) {
    if (!s) return;
    char *start = s;
    while (*start && isspace((unsigned char)*start)) start++;
    if (start != s) memmove(s, start, strlen(start) + 1);
    size_t len = strlen(s);
    while (len > 0 && isspace((unsigned char)s[len - 1])) s[--len] = '\0';
}

static char *dup_str(const char *s) {
    if (!s) return NULL;
    size_t n = strlen(s);
    char *d = (char *)malloc(n + 1);
    if (!d) return NULL;
    memcpy(d, s, n + 1);
    return d;
}

static int parse_frontmatter_line(const char *line, char *key, size_t key_sz, char *val, size_t val_sz) {
    const char *colon = strchr(line, ':');
    if (!colon) return line[0] == '\0' ? 0 : -1;
    size_t klen = (size_t)(colon - line);
    if (klen >= key_sz) return -1;
    memcpy(key, line, klen);
    key[klen] = '\0';
    for (const unsigned char *p = (const unsigned char *)key; *p; p++) {
        if (isspace(*p)) return -1;
    }
    trim(key);
    if (!key[0]) return -1;
    const char *vstart = colon + 1;
    if (*vstart == ' ') vstart++;
    size_t value_length = strlen(vstart);
    if (value_length >= val_sz) return -1;
    if (value_length > 0 && (isspace((unsigned char)vstart[0])
        || isspace((unsigned char)vstart[value_length - 1]))) return -1;
    memcpy(val, vstart, value_length + 1);
    trim(val);
    return 1;
}

static int extract_wiki_link(const char *p, char *slug_out, size_t slug_sz) {
    if (p[0] != '[' || p[1] != '[') return 0;
    const char *end = strstr(p + 2, "]]");
    if (!end) return 0;
    size_t len = (size_t)(end - (p + 2));
    if (len >= slug_sz) return -1;
    memcpy(slug_out, p + 2, len);
    slug_out[len] = '\0';
    /* strip display text after | */
    char *pipe = strchr(slug_out, '|');
    if (pipe) *pipe = '\0';
    trim(slug_out);
    return (int)(end - p + 2);
}

static int extract_semantic_mention(const char *p, char *text_out, size_t text_sz) {
    if (p[0] != '{' || p[1] != '{') return 0;
    const char *start = p + 2;
    const char *end = strstr(start, "}}");
    if (!end) return 0;
    for (const char *c = start; c < end; c++) {
        if (*c == '\n' || *c == '\r') return 0;
    }
    size_t len = (size_t)(end - start);
    if (len >= text_sz) return -1;
    memcpy(text_out, start, len);
    text_out[len] = '\0';
    trim(text_out);
    if (!text_out[0]) return 0;
    return (int)(end - p + 2);
}

static int append_parsed_string(char ***items, size_t *count, size_t *cap, const char *value) {
    if (!value || !value[0]) return 0;
    if (*count >= *cap) {
        size_t next_cap = *cap ? *cap * 2 : 8;
        if (next_cap < *cap || next_cap > SIZE_MAX / sizeof(**items)) return -1;
        char **grown = (char **)realloc(*items, next_cap * sizeof(**items));
        if (!grown) return -1;
        *items = grown;
        *cap = next_cap;
    }
    (*items)[*count] = dup_str(value);
    if (!(*items)[*count]) return -1;
    (*count)++;
    return 0;
}

static int extract_mentions_from_text(const char *text, char ***mentions, size_t *count, size_t *cap) {
    if (!text) return 0;
    const char *p = text;
    while (*p) {
        if (p[0] == '{' && p[1] == '{') {
            char mention[WISEDB_MAX_MENTION];
            int adv = extract_semantic_mention(p, mention, sizeof(mention));
            if (adv < 0) return -1;
            if (adv > 0) {
                if (append_parsed_string(mentions, count, cap, mention) != 0) return -1;
                p += adv;
                continue;
            }
        }
        p++;
    }
    return 0;
}

const char *wisedb_mime_from_ext(const char *path) {
    const char *dot = strrchr(path, '.');
    if (!dot) return "application/octet-stream";
    dot++;
    if (strcasecmp(dot, "md") == 0) return "text/markdown";
    if (strcasecmp(dot, "jpg") == 0 || strcasecmp(dot, "jpeg") == 0) return "image/jpeg";
    if (strcasecmp(dot, "png") == 0) return "image/png";
    if (strcasecmp(dot, "gif") == 0) return "image/gif";
    if (strcasecmp(dot, "webp") == 0) return "image/webp";
    if (strcasecmp(dot, "mp3") == 0) return "audio/mpeg";
    if (strcasecmp(dot, "wav") == 0) return "audio/wav";
    if (strcasecmp(dot, "mp4") == 0) return "video/mp4";
    if (strcasecmp(dot, "bin") == 0) return "application/octet-stream";
    return "application/octet-stream";
}

int wisedb_is_media_path(const char *path) {
    if (!path) return 0;
    const char *dot = strrchr(path, '.');
    if (!dot) return 0;
    dot++;
    static const char *exts[] = {
        "jpg", "jpeg", "png", "gif", "webp", "svg",
        "mp3", "wav", "ogg", "flac",
        "mp4", "webm", "mov", "avi", "bin", NULL
    };
    for (int i = 0; exts[i]; i++) {
        if (strcasecmp(dot, exts[i]) == 0) return 1;
    }
    return 0;
}

int wisedb_parse_markdown(
    const char *markdown,
    wisedb_node *node_out,
    char ***links_out,
    size_t *link_count_out,
    char ***mentions_out,
    size_t *mention_count_out
) {
    if (!markdown || !node_out || !wisedb_json_is_valid_utf8(markdown)) return -1;
    memset(node_out, 0, sizeof(*node_out));
    if (links_out) *links_out = NULL;
    if (link_count_out) *link_count_out = 0;
    if (mentions_out) *mentions_out = NULL;
    if (mention_count_out) *mention_count_out = 0;

    const char *body_start = markdown;
    if (strncmp(markdown, "---", 3) == 0) {
        if (strlen(markdown) < 4 || (markdown[3] != '\n' && markdown[3] != '\r')) return -1;
        const char *fm_end = strstr(markdown + 3, "\n---");
        if (!fm_end) return -1;
        if (fm_end[4] != '\0' && fm_end[4] != '\n' && fm_end[4] != '\r') return -1;
        {
            size_t fm_len = (size_t)(fm_end - (markdown + 3));
            char *fm = (char *)malloc(fm_len + 1);
            if (!fm) return -1;
            memcpy(fm, markdown + 3, fm_len);
            fm[fm_len] = '\0';
            char *line = fm;
            char key[128], val[1024];
            unsigned seen_fields = 0;
            while (line && *line) {
                char *next = strchr(line, '\n');
                if (next) *next++ = '\0';
                size_t line_length = strlen(line);
                if (line_length > 0 && line[line_length - 1] == '\r') line[line_length - 1] = '\0';
                int parsed = parse_frontmatter_line(line, key, sizeof(key), val, sizeof(val));
                if (parsed < 0) {
                    free(fm);
                    wisedb_free_node(node_out);
                    return -1;
                }
                if (parsed > 0) {
                    unsigned field_bit = 0;
                    if (strcmp(key, "type") == 0) field_bit = 1u << 0;
                    else if (strcmp(key, "title") == 0) field_bit = 1u << 1;
                    else if (strcmp(key, "summary") == 0) field_bit = 1u << 2;
                    else if (strcmp(key, "slug") == 0) field_bit = 1u << 3;
                    else if (strcmp(key, "mime") == 0) field_bit = 1u << 4;
                    if (field_bit == 0 || (seen_fields & field_bit) != 0) {
                        free(fm);
                        wisedb_free_node(node_out);
                        return -1;
                    }
                    seen_fields |= field_bit;
                    char **field = NULL;
                    if (strcmp(key, "title") == 0) field = &node_out->title;
                    else if (strcmp(key, "summary") == 0) field = &node_out->summary;
                    else if (strcmp(key, "slug") == 0) field = &node_out->slug;
                    else if (strcmp(key, "mime") == 0) field = &node_out->mime;
                    if (strcmp(key, "type") == 0) {
                        node_out->type = wisedb_type_from_string(val);
                        if (node_out->type == WISEDB_NODE_UNKNOWN) {
                            free(fm);
                            wisedb_free_node(node_out);
                            return -1;
                        }
                    } else if (field) {
                        char *replacement = dup_str(val);
                        if (!replacement) {
                            free(fm);
                            wisedb_free_node(node_out);
                            return -1;
                        }
                        free(*field);
                        *field = replacement;
                    }
                }
                line = next;
            }
            free(fm);
            body_start = fm_end + 4;
            if (*body_start == '\r') body_start++;
            if (*body_start == '\n') body_start++;
        }
    }

    node_out->body = dup_str(body_start);
    if (!node_out->summary) node_out->summary = dup_str("");
    if (!node_out->title) node_out->title = dup_str(node_out->slug ? node_out->slug : "");
    if (!node_out->body || !node_out->summary || !node_out->title) {
        wisedb_free_node(node_out);
        return -1;
    }

    /* extract [[links]] */
    char **links = NULL;
    size_t link_count = 0, link_cap = 0;
    const char *p = body_start;
    while (p && *p) {
        if (p[0] == '[' && p[1] == '[') {
            char slug[WISEDB_MAX_SLUG];
            int adv = extract_wiki_link(p, slug, sizeof(slug));
            if (adv < 0) {
                wisedb_free_parsed_links(links, link_count);
                wisedb_free_node(node_out);
                return -1;
            }
            if (adv > 0 && slug[0]) {
                if (link_count >= link_cap) {
                    size_t next_cap = link_cap ? link_cap * 2 : 8;
                    if (next_cap < link_cap || next_cap > SIZE_MAX / sizeof(*links)) {
                        wisedb_free_parsed_links(links, link_count);
                        wisedb_free_node(node_out);
                        return -1;
                    }
                    char **grown = (char **)realloc(links, next_cap * sizeof(*links));
                    if (!grown) {
                        wisedb_free_parsed_links(links, link_count);
                        wisedb_free_node(node_out);
                        return -1;
                    }
                    links = grown;
                    link_cap = next_cap;
                }
                links[link_count] = dup_str(slug);
                if (!links[link_count]) {
                    wisedb_free_parsed_links(links, link_count);
                    wisedb_free_node(node_out);
                    return -1;
                }
                link_count++;
                p += adv;
                continue;
            }
        }
        p++;
    }
    if (links_out) *links_out = links;
    else wisedb_free_parsed_links(links, link_count);
    if (link_count_out) *link_count_out = link_count;

    char **mentions = NULL;
    size_t mention_count = 0, mention_cap = 0;
    if (extract_mentions_from_text(node_out->title, &mentions, &mention_count, &mention_cap) != 0
        || extract_mentions_from_text(body_start, &mentions, &mention_count, &mention_cap) != 0) {
        wisedb_free_parsed_links(mentions, mention_count);
        if (links_out) {
            wisedb_free_parsed_links(links, link_count);
            *links_out = NULL;
            if (link_count_out) *link_count_out = 0;
        }
        wisedb_free_node(node_out);
        return -1;
    }
    if (mentions_out) *mentions_out = mentions;
    else wisedb_free_parsed_links(mentions, mention_count);
    if (mention_count_out) *mention_count_out = mention_count;

    node_out->token_count = wisedb_estimate_tokens(node_out->body);
    return 0;
}

void wisedb_free_parsed_links(char **links, size_t count) {
    if (!links) return;
    for (size_t i = 0; i < count; i++) free(links[i]);
    free(links);
}

char *wisedb_serialize_page(const wisedb_node *node) {
    if (!node) return NULL;
    const char *type = wisedb_type_to_string(node->type);
    const char *title = node->title ? node->title : "";
    const char *summary = node->summary ? node->summary : "";
    const char *slug = node->slug ? node->slug : "";
    const char *mime = node->mime ? node->mime : "";
    const char *body = node->body ? node->body : "";
    const char *parts[] = {type, title, summary, slug, mime, body};
    size_t sz = 64;
    for (size_t i = 0; i < sizeof(parts) / sizeof(parts[0]); i++) {
        size_t length = strlen(parts[i]);
        if (length > SIZE_MAX - sz) return NULL;
        sz += length;
    }
    if (sz > 64u * 1024u * 1024u) return NULL;
    char *out = (char *)malloc(sz);
    if (!out) return NULL;
    int written;
    if (node->is_media) {
        written = snprintf(out, sz,
            "---\ntype: media\nslug: %s\ntitle: %s\nsummary: %s\nmime: %s\n---\n%s",
            slug, title, summary, mime, body);
    } else {
        written = snprintf(out, sz,
            "---\ntype: %s\nslug: %s\ntitle: %s\nsummary: %s\n---\n%s",
            type, slug, title, summary, body);
    }
    if (written < 0 || (size_t)written >= sz) { free(out); return NULL; }
    return out;
}
