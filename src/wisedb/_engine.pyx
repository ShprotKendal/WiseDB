# distutils: language = c
# cython: language_level=3

from libc.stdint cimport uint8_t
from libc.stdlib cimport free

cdef extern from "wisedb.h":
    ctypedef struct wisedb_engine:
        pass

    wisedb_engine* wisedb_open_ex(const char* wiki_root, char* error_out, size_t error_capacity)
    void wisedb_close(wisedb_engine* eng)
    int wisedb_refresh(wisedb_engine* eng)
    int wisedb_root_is_current(wisedb_engine* eng)
    int wisedb_remember(
        wisedb_engine* eng,
        const char* slug,
        const char* markdown,
        const uint8_t* file_bytes,
        size_t file_len,
        const char* rel_path,
        char** result_json_out,
    )
    int wisedb_read(
        wisedb_engine* eng,
        const char* slug,
        int include_neighbors,
        char** result_json_out,
    )
    int wisedb_edit(
        wisedb_engine* eng,
        const char* slug,
        const char* markdown,
        char** result_json_out,
    )
    int wisedb_search(
        wisedb_engine* eng,
        const char* query,
        int budget_tokens,
        int top_k,
        char** result_json_out,
    )
    int wisedb_list(
        wisedb_engine* eng,
        char** result_json_out,
    )
    int wisedb_read_markdown(
        wisedb_engine* eng,
        const char* slug,
        char** markdown_out,
    )
    int wisedb_update(
        wisedb_engine* eng,
        const char* slug,
        const char* type,
        const char* title,
        const char* summary,
        const char* body,
        char** result_json_out,
    )
    int wisedb_delete(
        wisedb_engine* eng,
        const char* slug,
    )
    ctypedef enum wisedb_link_direction:
        WISEDB_LINK_DIR_OUTGOING
        WISEDB_LINK_DIR_INCOMING
        WISEDB_LINK_DIR_BOTH
    int wisedb_links(
        wisedb_engine* eng,
        const char* slug,
        int depth,
        wisedb_link_direction direction,
        char** result_json_out,
    )
    int wisedb_query_edges(
        wisedb_engine* eng,
        const char* from_slug,
        const char* to_slug,
        const char* edge_type,
        char** result_json_out,
    )
    int wisedb_list_catalog_json(
        wisedb_engine* eng,
        const char* filter_json,
        const char* order_field,
        int order_asc,
        int limit,
        char** result_json_out,
    )
    int wisedb_get_settings(
        wisedb_engine* eng,
        char** result_json_out,
    )
    int wisedb_set_semantics(
        wisedb_engine* eng,
        int enabled,
        char** result_json_out,
    )
    const char* wisedb_schema()
    void wisedb_free_string(char* s)
    const char* wisedb_last_error(wisedb_engine* eng)


cdef class Engine:
    cdef wisedb_engine* _eng
    cdef bytes _wiki_root

    def __cinit__(self, str wiki_root):
        if "\x00" in wiki_root:
            raise ValueError("wiki_root must not contain NUL")
        self._wiki_root = wiki_root.encode("utf-8")
        cdef char error[256]
        self._eng = wisedb_open_ex(<const char*>self._wiki_root, error, sizeof(error))
        if self._eng == NULL:
            raise RuntimeError((<char*>error).decode("utf-8", "replace") or "failed to open wiki")

    def __dealloc__(self):
        if self._eng != NULL:
            wisedb_close(self._eng)

    cdef void _require_open(self) except *:
        if self._eng == NULL:
            raise RuntimeError("engine is closed")
        if not wisedb_root_is_current(self._eng):
            raise RuntimeError("wiki root pathname changed; reopen the engine")

    def close(self):
        if self._eng != NULL:
            wisedb_close(self._eng)
            self._eng = NULL

    def root_is_current(self):
        self._require_open()
        return bool(wisedb_root_is_current(self._eng))

    def refresh(self):
        self._require_open()
        if wisedb_refresh(self._eng) != 0:
            err = wisedb_last_error(self._eng)
            raise RuntimeError(err.decode("utf-8", "replace") if err else "refresh failed")


    cdef str _take_json(self, char** out):
        cdef char* raw = out[0]
        if raw == NULL:
            err = wisedb_last_error(self._eng)
            if err != NULL and err[0] != 0:
                raise RuntimeError(err.decode("utf-8", "replace"))
            raise RuntimeError("operation failed")
        try:
            return raw.decode("utf-8")
        finally:
            wisedb_free_string(raw)

    def remember(
        self,
        str slug,
        str markdown=None,
        bytes file_bytes=None,
        str rel_path=None,
    ):
        self._require_open()
        cdef char* out = NULL
        cdef const uint8_t* fb = NULL
        cdef size_t flen = 0
        cdef bytes slug_b = slug.encode("utf-8")
        cdef bytes md_b = b""
        cdef bytes rp_b = b""
        cdef const char* md = NULL
        cdef const char* rp = NULL
        if "\x00" in slug:
            raise ValueError("slug must not contain NUL")
        if markdown is not None:
            if "\x00" in markdown:
                raise ValueError("markdown must not contain NUL")
            md_b = markdown.encode("utf-8")
            md = md_b
        if rel_path is not None:
            if "\x00" in rel_path:
                raise ValueError("rel_path must not contain NUL")
            rp_b = rel_path.encode("utf-8")
            rp = rp_b
        if file_bytes is not None:
            fb = file_bytes
            flen = len(file_bytes)
        rc = wisedb_remember(
            self._eng, slug_b, md, fb, flen, rp, &out
        )
        if rc != 0:
            err = wisedb_last_error(self._eng)
            raise RuntimeError(err.decode("utf-8", "replace") if err else "remember failed")
        return self._take_json(&out)

    def read(self, str slug, bint include_neighbors=False):
        self._require_open()
        cdef char* out = NULL
        cdef bytes slug_b = slug.encode("utf-8")
        if "\x00" in slug:
            raise ValueError("slug must not contain NUL")
        rc = wisedb_read(self._eng, slug_b, 1 if include_neighbors else 0, &out)
        if rc != 0:
            err = wisedb_last_error(self._eng)
            raise RuntimeError(err.decode("utf-8", "replace") if err else "read failed")
        return self._take_json(&out)

    def edit(self, str slug, str markdown):
        self._require_open()
        cdef char* out = NULL
        cdef bytes slug_b = slug.encode("utf-8")
        cdef bytes md_b = markdown.encode("utf-8")
        if "\x00" in slug or "\x00" in markdown:
            raise ValueError("slug and markdown must not contain NUL")
        rc = wisedb_edit(self._eng, slug_b, md_b, &out)
        if rc != 0:
            err = wisedb_last_error(self._eng)
            raise RuntimeError(err.decode("utf-8", "replace") if err else "edit failed")
        return self._take_json(&out)

    def search(self, str query, int budget_tokens=2048, int top_k=10):
        self._require_open()
        cdef char* out = NULL
        cdef bytes q = query.encode("utf-8")
        if "\x00" in query:
            raise ValueError("query must not contain NUL")
        rc = wisedb_search(self._eng, q, budget_tokens, top_k, &out)
        if rc != 0:
            err = wisedb_last_error(self._eng)
            raise RuntimeError(err.decode("utf-8", "replace") if err else "search failed")
        return self._take_json(&out)

    def list(self):
        self._require_open()
        cdef char* out = NULL
        rc = wisedb_list(self._eng, &out)
        if rc != 0:
            err = wisedb_last_error(self._eng)
            raise RuntimeError(err.decode("utf-8", "replace") if err else "list failed")
        return self._take_json(&out)

    def read_markdown(self, str slug):
        self._require_open()
        cdef char* out = NULL
        cdef bytes slug_b = slug.encode("utf-8")
        if "\x00" in slug:
            raise ValueError("slug must not contain NUL")
        rc = wisedb_read_markdown(self._eng, slug_b, &out)
        if rc != 0:
            err = wisedb_last_error(self._eng)
            raise RuntimeError(err.decode("utf-8", "replace") if err else "read_markdown failed")
        try:
            return out.decode("utf-8")
        finally:
            wisedb_free_string(out)

    def update(
        self,
        str slug,
        str type=None,
        str title=None,
        str summary=None,
        str body=None,
    ):
        self._require_open()
        cdef char* out = NULL
        cdef bytes slug_b = slug.encode("utf-8")
        if "\x00" in slug or any(value is not None and "\x00" in value
                      for value in (type, title, summary, body)):
            raise ValueError("update fields must not contain NUL")
        cdef bytes type_b = b""
        cdef bytes title_b = b""
        cdef bytes summary_b = b""
        cdef bytes body_b = b""
        cdef const char* type_p = NULL
        cdef const char* title_p = NULL
        cdef const char* summary_p = NULL
        cdef const char* body_p = NULL
        if type is not None:
            type_b = type.encode("utf-8")
            type_p = type_b
        if title is not None:
            title_b = title.encode("utf-8")
            title_p = title_b
        if summary is not None:
            summary_b = summary.encode("utf-8")
            summary_p = summary_b
        if body is not None:
            body_b = body.encode("utf-8")
            body_p = body_b
        rc = wisedb_update(self._eng, slug_b, type_p, title_p, summary_p, body_p, &out)
        if rc != 0:
            err = wisedb_last_error(self._eng)
            raise RuntimeError(err.decode("utf-8", "replace") if err else "update failed")
        return self._take_json(&out)

    def delete(self, str slug):
        self._require_open()
        cdef bytes slug_b = slug.encode("utf-8")
        if "\x00" in slug:
            raise ValueError("slug must not contain NUL")
        rc = wisedb_delete(self._eng, slug_b)
        if rc != 0:
            err = wisedb_last_error(self._eng)
            raise RuntimeError(err.decode("utf-8", "replace") if err else "delete failed")

    def links(self, str slug, int depth=1, str direction="outgoing"):
        self._require_open()
        cdef char* out = NULL
        cdef bytes slug_b = slug.encode("utf-8")
        cdef wisedb_link_direction dir_code = WISEDB_LINK_DIR_OUTGOING
        if "\x00" in slug:
            raise ValueError("slug must not contain NUL")
        if direction == "incoming":
            dir_code = WISEDB_LINK_DIR_INCOMING
        elif direction == "both":
            dir_code = WISEDB_LINK_DIR_BOTH
        elif direction != "outgoing":
            raise ValueError("direction must be outgoing, incoming, or both")
        rc = wisedb_links(self._eng, slug_b, depth, dir_code, &out)
        if rc != 0:
            err = wisedb_last_error(self._eng)
            raise RuntimeError(err.decode("utf-8", "replace") if err else "links failed")
        return self._take_json(&out)

    def list_catalog(
        self,
        str filter_json=None,
        str order_field=None,
        bint order_asc=True,
        int limit=-1,
    ):
        self._require_open()
        cdef char* out = NULL
        cdef bytes filter_b = b""
        cdef bytes order_b = b""
        cdef const char* filter_p = NULL
        cdef const char* order_p = NULL
        if filter_json is not None:
            if "\x00" in filter_json:
                raise ValueError("filter_json must not contain NUL")
            filter_b = filter_json.encode("utf-8")
            filter_p = filter_b
        if order_field is not None:
            if "\x00" in order_field:
                raise ValueError("order_field must not contain NUL")
            order_b = order_field.encode("utf-8")
            order_p = order_b
        rc = wisedb_list_catalog_json(
            self._eng, filter_p, order_p, 1 if order_asc else 0, limit, &out
        )
        if rc != 0:
            err = wisedb_last_error(self._eng)
            raise RuntimeError(err.decode("utf-8", "replace") if err else "list_catalog failed")
        return self._take_json(&out)

    def query_edges(
        self,
        str from_slug=None,
        str to_slug=None,
        str edge_type=None,
    ):
        self._require_open()
        cdef char* out = NULL
        cdef bytes from_b = b""
        cdef bytes to_b = b""
        cdef bytes type_b = b""
        cdef const char* from_p = NULL
        cdef const char* to_p = NULL
        cdef const char* type_p = NULL
        if from_slug is not None:
            if "\x00" in from_slug:
                raise ValueError("from_slug must not contain NUL")
            from_b = from_slug.encode("utf-8")
            from_p = from_b
        if to_slug is not None:
            if "\x00" in to_slug:
                raise ValueError("to_slug must not contain NUL")
            to_b = to_slug.encode("utf-8")
            to_p = to_b
        if edge_type is not None:
            if "\x00" in edge_type:
                raise ValueError("edge_type must not contain NUL")
            type_b = edge_type.encode("utf-8")
            type_p = type_b
        rc = wisedb_query_edges(self._eng, from_p, to_p, type_p, &out)
        if rc != 0:
            err = wisedb_last_error(self._eng)
            raise RuntimeError(err.decode("utf-8", "replace") if err else "query_edges failed")
        return self._take_json(&out)

    def get_settings(self):
        self._require_open()
        cdef char* out = NULL
        rc = wisedb_get_settings(self._eng, &out)
        if rc != 0:
            err = wisedb_last_error(self._eng)
            raise RuntimeError(err.decode("utf-8", "replace") if err else "get_settings failed")
        return self._take_json(&out)

    def set_semantics(self, bint enabled):
        self._require_open()
        cdef char* out = NULL
        rc = wisedb_set_semantics(self._eng, 1 if enabled else 0, &out)
        if rc != 0:
            err = wisedb_last_error(self._eng)
            raise RuntimeError(err.decode("utf-8", "replace") if err else "set_semantics failed")
        return self._take_json(&out)

    @staticmethod
    def schema():
        cdef const char* s = wisedb_schema()
        return s.decode("utf-8")
