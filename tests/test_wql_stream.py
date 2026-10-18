from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from wisedb import Wiki
from wisedb.wql import WqlError, execute, iter_stream
from wisedb.wql.ast import StreamStmt
from wisedb.wql.lexer import tokenize
from wisedb.wql.parser import parse


@pytest.fixture
def wiki(tmp_path: Path) -> Generator[Wiki, None, None]:
    instance = Wiki(tmp_path / "wiki")
    try:
        yield instance
    finally:
        instance.close()


def test_lexer_param_ref():
    types = [t.type.name for t in tokenize("SELECT * FROM pages WHERE slug = $.slug")]
    assert "PARAM_REF" in types


def test_parse_ws_stream():
    q = (
        "SELECT * FROM pages WHERE type = 'concept' "
        "WS[SELECT markdown FROM pages WHERE slug = $.slug]"
    )
    stmt = parse(q)
    assert isinstance(stmt, StreamStmt)


def test_param_ref_outside_ws_rejected():
    with pytest.raises(WqlError, match="inside WS"):
        parse("SELECT * FROM pages WHERE slug = $.slug")


def test_execute_rejects_ws():
    with pytest.raises(WqlError, match="WebSocket"):
        execute(None, "SELECT * FROM pages WS[SELECT * FROM pages]")  # type: ignore[arg-type]


def test_iter_stream_markdown(wiki: Wiki):
    wiki.remember(
        "a",
        markdown="---\ntype: concept\nslug: a\ntitle: A\n---\nA body",
    )
    wiki.remember(
        "b",
        markdown="---\ntype: concept\nslug: b\ntitle: B\n---\nB body",
    )
    query = "SELECT * FROM pages WS[SELECT markdown FROM pages WHERE slug = $.slug]"
    rows = list(iter_stream(wiki, query))
    assert len(rows) == 2
    slugs = {row[1]["slug"] for row in rows}
    assert slugs == {"a", "b"}
    for _index, _row, result in rows:
        assert "markdown" in result
        assert "body" in result["markdown"]
