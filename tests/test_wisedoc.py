"""Tests for WiseDoc frontmatter parsing."""

from wisedb.wisedoc import parse_wisedoc, text_for_embedding


def test_triple_plus_toml() -> None:
    raw = """+++
kind = "doc"
caption = "Hello cap"

[[wisedb.assets]]
slot = "a1"
mime = "image/png"
uri = "https://ex/img.png"
+++

# Title

Body text with ![alt](wisedb://a1).
"""
    fm, body = parse_wisedoc(raw)
    assert fm.get("kind") == "doc"
    assert "Title" in body
    assert "Body text" in body
    te = text_for_embedding(title="My title", body=body, frontmatter=fm)
    assert "My title" in te
    assert "Hello cap" in te
    assert "https://ex/img.png" in te
    assert "alt" in te


def test_plain_markdown_no_frontmatter() -> None:
    fm, body = parse_wisedoc("# Hi\n\nx")
    assert fm == {}
    assert "Hi" in body
