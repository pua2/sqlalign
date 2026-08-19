"""Case a statement's keywords without rebuilding it from its syntax tree.

The layout engine renders by handing nodes to sqlglot's generator. For most
statements that is right and it is what makes the columnar style possible. For a
handful it is lossy: two spellings collapse to one node, so the generator prints
whichever it prefers and the author's is gone. `ALTER COLUMN x TYPE text` comes
back as `SET DATA TYPE`, `ADD COLUMN c integer array` loses the `ARRAY`, and
`SET search_path TO public` has its `TO` respelt as `=`.

Since 1.2 those are caught and passed through rather than shipped changed, which
is honest but means they do not format at all. This module formats them the only
way that cannot lose a spelling: by starting from the source text and changing
nothing but the case of its keywords.

**Telling a keyword from an identifier is the whole problem.** The tokenizer
alone cannot: it types `add`, `to` and `local` as `VAR` in the statements above,
and a column genuinely named `year` the same way. Uppercasing every word that
appears in the keyword table turns `ADD COLUMN year INT` into `ADD COLUMN YEAR
INT`.

The parse tree answers it. Every identifier, literal and bare `Var` the author
wrote is a node in the tree; the grammar words around them -- `ADD`, `COLUMN`,
`TO`, `SET DATA TYPE` -- are not, because they live in sqlglot's generator
rather than on any node. So a source word absent from the tree's content is
grammar, and a word present in it is the author's and is left alone. A statement
that uses one word both ways keeps both as written, which is the safe direction.

This needs a parse. `exp.Command` -- sqlglot's fallback for syntax it cannot
model -- carries raw text and no content nodes, so every word in it would read
as grammar and a role name would be uppercased. Those still pass through.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType

# Tokens that are content by their type alone, whatever the tree says. A quoted
# identifier is the point: `"Order"` is a name the engine stores case-sensitively
# and casing it changes which column is meant.
_CONTENT_TOKENS = frozenset({
    TokenType.STRING, TokenType.IDENTIFIER, TokenType.NUMBER,
    TokenType.HEREDOC_STRING, TokenType.RAW_STRING, TokenType.NATIONAL_STRING,
    TokenType.BYTE_STRING, TokenType.HEX_STRING, TokenType.UNICODE_STRING,
    TokenType.PARAMETER, TokenType.SESSION_PARAMETER,
})


def renders_from_source(node: exp.Expression) -> bool:
    """Whether `node` can be cased this way at all.

    A `Command` is raw text with no content nodes to protect, so every word in
    it would be taken for grammar.
    """
    return not isinstance(node, exp.Command) and node.find(exp.Command) is None


def content_words(node: exp.Expression) -> set[str]:
    """Casefolded words the author wrote as names or values, not as grammar."""
    words = set()
    for child in node.walk():
        if isinstance(child, (exp.Identifier, exp.Var, exp.Literal)) and isinstance(
                child.this, str):
            words.add(child.this.casefold())
    return words


def recase(sql: str, node: exp.Expression, dialect: str, keyword_case: str) -> str:
    """`sql` with its grammar words cased, byte-identical everywhere else.

    Whitespace, comments and every content token come through from the source
    untouched -- only the spans the tokenizer reported for grammar words are
    rewritten, and only in case. That is what makes this safe to reach for when
    the generator would have respelt something: there is nothing here that can
    change a statement's meaning, only its capitalisation.
    """
    fold = str.upper if keyword_case == "upper" else str.lower
    words = content_words(node)
    tokenizer = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class()
    out: list[str] = []
    cursor = 0
    for token in tokenizer.tokenize(sql):
        out.append(sql[cursor:token.start])
        span = sql[token.start:token.end + 1]
        grammar = (token.token_type not in _CONTENT_TOKENS
                   and span.casefold() not in words)
        out.append(fold(span) if grammar else span)
        cursor = token.end + 1
    out.append(sql[cursor:])
    return "".join(out)
