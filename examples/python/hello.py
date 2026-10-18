#!/usr/bin/env python3
"""Minimal WiseDB Python library example."""

from wisedb import Wiki

wiki = Wiki("./data/python-example")

wiki.remember(
    "hello",
    markdown="""---
type: concept
slug: hello
title: Hello
summary: Python example
---
WiseDB from Python.
""",
)

print(wiki.list())
print(wiki.update("hello", title="Hello World", body="Updated from Python."))
print(wiki.markdown("hello"))
wiki.delete("hello")
