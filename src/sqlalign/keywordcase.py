"""Keyword casing (`Style.keyword_case`) — driven by the AST, not a word list.

House style renders every keyword, function name, and type name in upper case.
`lower` renders them in lower case, which is what most published SQL style guides
and the dbt ecosystem use.

**Why this is AST-driven.** The obvious implementation: lowercase any word that
appears in a keyword list: is both unsafe and incomplete:

* *Unsafe*: `COMMENT`, `END`, `FILTER`, `FORMAT`, `RANGE` and `ROWS` are all real
  column names AND tokenizer keywords. Lowercasing an identifier changes it, which
  the safety net then rejects, passing the whole file through unformatted.
* *Incomplete*: `BY`, `CAST`, `GROUP`, `ORDER`, `PRIMARY` and `KEY` are keywords
  that are NOT in sqlglot's single-word keyword set, so a list-based pass leaves
  them upper while lowering everything around them.

The parse tree already knows precisely which words are identifiers. So the rule
inverts: lowercase every bare word EXCEPT the ones the AST names as identifiers.
That is complete by construction (anything the formatter emitted that is not your
identifier is something sqlalign chose to write) and safe by construction (an
identifier is never touched, whatever it is called).

Casing never changes a string's width, so no alignment column moves.
"""
import re

import sqlglot
from sqlglot import exp

# A bare word: identifier-shaped, and UNICODE-aware on purpose. `apply` enters
# this pattern on any `ch.isalpha()`, which is true of `é`: an ASCII-only
# pattern then fails to match there and crashes the pass, which formatter's
# catch-all turns into an "internal error" passthrough for the whole statement.
# `[^\W\d]\w*` is exactly `[A-Za-z_][A-Za-z0-9_]*` over ASCII, so nothing else
# changes: it only extends the same shape to non-ASCII letters (`café` is one
# word, matching the Identifier the parse tree recorded, so it is preserved).
_WORD = re.compile(r"[^\W\d]\w*")


def identifier_names(statement: str, dialect: str) -> set[str]:
    """Every identifier the parse tree knows about in `statement`.

    A dollar-quoted plpgsql body parses as one opaque string, so its identifiers
    are invisible here — the body's own statements are parsed separately and
    folded in, otherwise a variable inside a procedure would be treated as a
    keyword and lowered.
    """
    names: set[str] = set()
    try:
        trees = [t for t in sqlglot.parse(statement, read=dialect) if t is not None]
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
        return names

    for tree in trees:
        for ident in tree.find_all(exp.Identifier):
            # Only bare identifiers. A quoted one renders inside quotes, which
            # `apply`'s scanner never enters -- adding its text here did nothing
            # for it and immunised every KEYWORD spelled the same way, so
            # `SELECT a AS "FROM"` left the real FROM upper in a lowercase file.
            if not ident.args.get("quoted"):
                names.add(ident.this)
        for heredoc in tree.find_all(exp.Heredoc):        # plpgsql body
            body = heredoc.this
            if isinstance(body, str):
                names |= _body_identifiers(body, dialect)
    return names


def _body_identifiers(body: str, dialect: str) -> set[str]:
    from sqlalign.plpgsql import body_clauses, strip_struct

    names: set[str] = set()
    for clause in body_clauses(body):
        try:
            parsed = sqlglot.parse(strip_struct(clause), read=dialect)
        except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
            # An unparseable clause (RAISE, GET DIAGNOSTICS, `:=`) contributes its
            # bare words defensively: better to leave a keyword upper than to
            # lower a variable name.
            names |= set(_WORD.findall(clause))
            continue
        for tree in parsed:
            if tree is None:
                continue
            for ident in tree.find_all(exp.Identifier):
                if not ident.args.get("quoted"):
                    names.add(ident.this)
    return names


def apply(text: str, names: set[str], case: str) -> str:
    """Case every bare word outside strings, quoted identifiers, and comments,
    leaving anything in `names` exactly as written."""
    if case == "upper":
        return text                       # what the handlers already emit
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"'):              # string literal / quoted identifier
            end = text.find(ch, i + 1)
            end = n if end == -1 else end + 1
            out.append(text[i:end])
            i = end
        elif ch == "[":
            # T-SQL bracket-quoted identifier. Its inner words are not separate
            # identifiers in the parse tree (`[Order Id]` is one Identifier named
            # "Order Id"), so without this they would be cased individually --
            # changing a quoted identifier, which the safety net then rejects,
            # leaving the whole statement unformatted.
            end = text.find("]", i + 1)
            end = n if end == -1 else end + 1
            out.append(text[i:end])
            i = end
        elif text.startswith("--", i):
            end = text.find("\n", i)      # the newline itself is cased normally
            if end == -1:
                end = n
            out.append(text[i:end])
            i = end
        elif text.startswith("/*", i):
            end = text.find("*/", i)
            end = n if end == -1 else end + 2
            out.append(text[i:end])
            i = end
        elif ch.isalpha() or ch == "_":
            word = _WORD.match(text, i).group(0)
            out.append(word if word in names else word.lower())
            i += len(word)
        else:
            out.append(ch)
            i += 1
    return "".join(out)
