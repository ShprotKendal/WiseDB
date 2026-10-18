#ifndef WISEDB_HPP
#define WISEDB_HPP

#include "wisedb.h"

#include <stdexcept>
#include <string>
#include <utility>

namespace wisedb {

class WikiError : public std::runtime_error {
public:
    explicit WikiError(const std::string& message) : std::runtime_error(message) {}
};

class Wiki {
public:
    explicit Wiki(const std::string& root) : eng_(wisedb_open(root.c_str())) {
        if (!eng_) {
            throw WikiError("failed to open wiki");
        }
    }

    Wiki(const Wiki&) = delete;
    Wiki& operator=(const Wiki&) = delete;

    Wiki(Wiki&& other) noexcept : eng_(other.eng_) { other.eng_ = nullptr; }

    Wiki& operator=(Wiki&& other) noexcept {
        if (this != &other) {
            close();
            eng_ = other.eng_;
            other.eng_ = nullptr;
        }
        return *this;
    }

    ~Wiki() { close(); }

    void refresh() {
        ensure_open();
        if (wisedb_refresh(eng_) != 0) {
            const char* err = wisedb_last_error(eng_);
            throw WikiError(err && err[0] ? err : "refresh failed");
        }
    }

    std::string remember(const std::string& slug) {
        ensure_open();
        ensure_no_nul(slug);
        char* out = nullptr;
        int rc = wisedb_remember(eng_, slug.c_str(), nullptr, nullptr, 0, nullptr, &out);
        return take_json(rc, out);
    }

    std::string remember(
        const std::string& slug,
        const std::string& markdown,
        const uint8_t* file_bytes = nullptr,
        size_t file_len = 0,
        const char* rel_path = nullptr
    ) {
        ensure_open();
        ensure_no_nul(slug);
        ensure_no_nul(markdown);
        if (rel_path) ensure_no_nul(std::string(rel_path));
        char* out = nullptr;
        int rc = wisedb_remember(eng_, slug.c_str(), markdown.c_str(), file_bytes, file_len, rel_path, &out);
        return take_json(rc, out);
    }

    std::string read(const std::string& slug, bool neighbors = false) {
        ensure_open();
        ensure_no_nul(slug);
        char* out = nullptr;
        int rc = wisedb_read(eng_, slug.c_str(), neighbors ? 1 : 0, &out);
        return take_json(rc, out);
    }

    std::string edit(const std::string& slug, const std::string& markdown) {
        ensure_open();
        ensure_no_nul(slug);
        ensure_no_nul(markdown);
        char* out = nullptr;
        int rc = wisedb_edit(eng_, slug.c_str(), markdown.c_str(), &out);
        return take_json(rc, out);
    }

    std::string search(const std::string& query, int budget_tokens = 2048, int top_k = 10) {
        ensure_open();
        ensure_no_nul(query);
        char* out = nullptr;
        int rc = wisedb_search(eng_, query.c_str(), budget_tokens, top_k, &out);
        return take_json(rc, out);
    }

    std::string list() {
        ensure_open();
        char* out = nullptr;
        int rc = wisedb_list(eng_, &out);
        return take_json(rc, out);
    }

    std::string markdown(const std::string& slug) {
        ensure_open();
        ensure_no_nul(slug);
        char* out = nullptr;
        int rc = wisedb_read_markdown(eng_, slug.c_str(), &out);
        if (rc != 0) {
            const char* err = wisedb_last_error(eng_);
            throw WikiError(err && err[0] ? err : "read_markdown failed");
        }
        std::string result(out ? out : "");
        wisedb_free_string(out);
        return result;
    }

    std::string update(
        const std::string& slug,
        const char* type = nullptr,
        const char* title = nullptr,
        const char* summary = nullptr,
        const char* body = nullptr
    ) {
        ensure_open();
        ensure_no_nul(slug);
        if (type) ensure_no_nul(std::string(type));
        if (title) ensure_no_nul(std::string(title));
        if (summary) ensure_no_nul(std::string(summary));
        if (body) ensure_no_nul(std::string(body));
        char* out = nullptr;
        int rc = wisedb_update(eng_, slug.c_str(), type, title, summary, body, &out);
        return take_json(rc, out);
    }

    void remove(const std::string& slug) {
        ensure_open();
        ensure_no_nul(slug);
        int rc = wisedb_delete(eng_, slug.c_str());
        if (rc != 0) {
            const char* err = wisedb_last_error(eng_);
            throw WikiError(err && err[0] ? err : "delete failed");
        }
    }

    static std::string schema() {
        const char* s = wisedb_schema();
        return s ? std::string(s) : std::string();
    }

private:
    wisedb_engine* eng_ = nullptr;

    void ensure_open() const {
        if (!eng_) throw WikiError("engine is closed");
    }

    static void ensure_no_nul(const std::string& value) {
        if (value.find('\0') != std::string::npos) {
            throw WikiError("string arguments must not contain embedded NUL");
        }
    }

    void close() {
        if (eng_) {
            wisedb_close(eng_);
            eng_ = nullptr;
        }
    }

    std::string take_json(int rc, char* out) {
        if (rc != 0) {
            const char* err = wisedb_last_error(eng_);
            throw WikiError(err && err[0] ? err : "operation failed");
        }
        std::string result(out ? out : "");
        wisedb_free_string(out);
        return result;
    }
};

}  // namespace wisedb

#endif /* WISEDB_HPP */
