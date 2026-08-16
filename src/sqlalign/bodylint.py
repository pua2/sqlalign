"""Expose the SQL inside a `$$ … $$` body to a linter.

sqlfluff parses a plpgsql body as a single string literal, so nothing inside it
is ever linted. `lint_view` returns the file with everything that is not
lintable SQL replaced by spaces, leaving every line and column where it was, so
a position the linter reports is a position in the original file.

Blanking rather than deleting is what preserves the columns. The cost is that
the view carries whitespace the source did not, so whitespace rules fire on
padding lines; `artifact_lines` identifies those. sqlalign's own coexistence
config excludes the whole `layout` group, which covers them.
"""
from __future__ import annotations

import re

from sqlalign import plpgsql
from sqlalign.splitter import split_statements

# A control keyword the splitter leaves glued to the front of the statement
# after it (see `plpgsql.body_clauses`). The statement behind it is still SQL,
# so the keyword is skipped rather than the whole clause being discarded.
_GLUED = re.compile(r"^\s*(?:begin|declare|then|else|loop)\b", re.I)


def lint_view(source: str, dialect: str = "postgres") -> str:
    """`source` with only its lintable SQL left standing, at its own coordinates.

    Statements outside a `$$` body are untouched: a linter could already see
    those, and rewriting them would risk changing what it reports about them.
    """
    out = list(source)
    for start, end in _body_spans(source, dialect):
        _blank(out, start, end)
        for keep_start, keep_end in _sql_spans(source, start, end, dialect):
            out[keep_start:keep_end] = list(source[keep_start:keep_end])
    return "".join(out)


def artifact_lines(source: str, dialect: str = "postgres") -> set[int]:
    """1-based line numbers that carry no real content in the view.

    A finding on one of these is about padding this module introduced, not
    about the author's SQL.
    """
    view = lint_view(source, dialect)
    return {i for i, line in enumerate(view.split("\n"), 1) if not line.strip()}


def body_lines(source: str, dialect: str = "postgres") -> set[int]:
    """1-based line numbers holding SQL that ONLY the view can see.

    A caller lints twice, once the real file, once the view, and keeps the
    view's findings only for these lines, which makes the two sets disjoint so
    they merge without having to recognise the same finding twice.

    Derived from the kept SQL spans rather than from the whole `CREATE`
    statement. A statement piece carries the blank line before it, so a span
    starts on the PREVIOUS statement's line -- and using that would have
    claimed a top-level statement as body content, reporting its findings
    twice.
    """
    lines: set[int] = set()
    for start, end in _body_spans(source, dialect):
        for sql_start, sql_end in _sql_spans(source, start, end, dialect):
            first = source.count("\n", 0, sql_start) + 1
            last = source.count("\n", 0, sql_end) + 1
            lines.update(range(first, last + 1))
    return lines


def has_bodies(source: str, dialect: str = "postgres") -> bool:
    """Whether `source` holds a dollar-quoted body worth looking inside."""
    return bool(_body_spans(source, dialect))


def _body_spans(source: str, dialect: str) -> list[tuple[int, int]]:
    """`(start, end)` of every dollar-quoted CREATE in `source`, whole statement.

    Found by walking the same splitter the formatter uses, so a `$$` inside a
    string literal cannot be mistaken for a body.
    """
    spans = []
    cursor = 0
    for piece in split_statements(source, dialect):
        start, end = cursor, cursor + len(piece)
        cursor = end
        if plpgsql.split_body(piece.strip()) is not None:
            spans.append((start, end))
    return spans


def _sql_spans(source: str, start: int, end: int, dialect: str) -> list[tuple[int, int]]:
    """The spans inside one body statement that are lintable SQL."""
    statement = source[start:end]
    parts = plpgsql.split_body(statement.strip())
    if parts is None:
        return []
    header, body, _tail = parts
    if not body:
        return []
    # `statement.strip()` may have dropped leading whitespace, so locate the
    # body in the UNSTRIPPED slice to keep the offsets true.
    offset = statement.index(body, len(header) - 1 if header else 0)

    spans = []
    cursor = start + offset
    for piece in split_statements(body, dialect):
        piece_start, piece_end = cursor, cursor + len(piece)
        cursor = piece_end
        if not piece.strip():
            continue
        glued = _GLUED.match(piece)
        skip = glued.end() if glued else 0
        candidate = piece[skip:].strip().rstrip(";").strip()
        if candidate and plpgsql._is_sql(candidate, dialect):
            spans.append((piece_start + skip, piece_end))
    return spans


def _blank(chars: list[str], start: int, end: int) -> None:
    """Replace a span with spaces, keeping every newline where it was."""
    for i in range(start, end):
        if chars[i] != "\n":
            chars[i] = " "
