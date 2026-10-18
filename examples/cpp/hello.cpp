#include "wisedb.hpp"

#include <iostream>

int main(int argc, char* argv[]) {
    const char* root = argc > 1 ? argv[1] : "./data/cpp-example";
    wisedb::Wiki wiki(root);

    const char* page =
        "---\n"
        "type: concept\n"
        "slug: hello\n"
        "title: Hello\n"
        "summary: C++ example\n"
        "---\n"
        "WiseDB from C++.\n";

    std::cout << wiki.remember("hello", page) << std::endl;
    std::cout << wiki.list() << std::endl;
    std::cout << wiki.update("hello", nullptr, "Hello World", nullptr, "Updated from C++.") << std::endl;
    std::cout << wiki.markdown("hello") << std::endl;
    wiki.remove("hello");
    return 0;
}
