import pytest

from wisedb.wql.lexer import LexerError, TokenType, tokenize


def test_tokenize_basic_query_and_locations():
    tokens = tokenize("SELECT name FROM users WHERE age >= 18")

    assert [(token.type, token.value) for token in tokens] == [
        (TokenType.KEYWORD, "SELECT"),
        (TokenType.IDENTIFIER, "name"),
        (TokenType.KEYWORD, "FROM"),
        (TokenType.IDENTIFIER, "users"),
        (TokenType.KEYWORD, "WHERE"),
        (TokenType.IDENTIFIER, "age"),
        (TokenType.OPERATOR, ">="),
        (TokenType.NUMBER, "18"),
        (TokenType.EOF, ""),
    ]
    assert (tokens[0].line, tokens[0].column) == (1, 1)
    assert (tokens[-1].line, tokens[-1].column) == (1, 39)


def test_tokenize_strings_comments_and_multiline_locations():
    tokens = tokenize("SELECT 'hi\\nthere' /* skip */\nFROM items -- tail")

    assert [(token.type, token.value) for token in tokens] == [
        (TokenType.KEYWORD, "SELECT"),
        (TokenType.STRING, "hi\nthere"),
        (TokenType.KEYWORD, "FROM"),
        (TokenType.IDENTIFIER, "items"),
        (TokenType.EOF, ""),
    ]
    assert (tokens[2].line, tokens[2].column) == (2, 1)


def test_tokenize_numbers_and_operators():
    tokens = tokenize(".5 12. 1.25e-2 != == &&")

    assert [(token.type, token.value) for token in tokens] == [
        (TokenType.NUMBER, ".5"),
        (TokenType.NUMBER, "12."),
        (TokenType.NUMBER, "1.25e-2"),
        (TokenType.OPERATOR, "!="),
        (TokenType.OPERATOR, "=="),
        (TokenType.OPERATOR, "&&"),
        (TokenType.EOF, ""),
    ]


@pytest.mark.parametrize("source", ["'unfinished", "/* unfinished", "1e+"])
def test_tokenize_rejects_unfinished_tokens(source: str):
    with pytest.raises(LexerError):
        tokenize(source)


def test_tokenize_reports_unexpected_character_position():
    with pytest.raises(LexerError, match=r"line 2, column 3"):
        tokenize("SELECT\n  @")
