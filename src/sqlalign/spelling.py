"""The spellings sqlalign is allowed to change, derived rather than listed.

The safety net compares sqlalign's output against its input as a syntax tree.
That cannot see a rewrite sqlglot's own parser performs, because both sides go
through the same parser and collapse to the same tree: `ALTER COLUMN x TYPE
text` and `ALTER COLUMN x SET DATA TYPE text` are one node, so printing the
second for the first is invisible to `ast_equal`.

Comparing the TOKENS either side of formatting closes that. The difficulty is
that some token differences are intended — sqlalign is a formatter, and three of
its settings exist precisely to choose between spellings. Those have to be
normalized away or every formatted file would be reported as rewritten.

Everything here is asked of sqlglot rather than written down. A hand-kept list of
"allowed differences" is the shape of thing this project has now watched rot
four times: it is correct on the day it is written, silently wrong afterwards,
and wrong in the direction of hiding bugs. Deriving it means a new dialect, or a
sqlglot release that renames a type, is followed rather than disagreed with.
"""
from __future__ import annotations

import collections
import functools

import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType

# Tokens whose text is CONTENT rather than syntax: a string literal, a quoted
# identifier, a `$$ ... $$` body. Casefolding these before comparing made the
# census blind to a change inside one -- `INTERVAL '14 days'` came back as
# `INTERVAL '14 DAYS'` and every check passed, because sqlglot normalises the
# unit to `Var(DAYS)` in the tree (so `ast_equal` cannot see it either) and the
# census had uppercased both sides. Compared byte-for-byte instead.
_VERBATIM = frozenset({TokenType.STRING, TokenType.IDENTIFIER,
                       TokenType.HEREDOC_STRING, TokenType.RAW_STRING,
                       TokenType.NATIONAL_STRING, TokenType.BYTE_STRING,
                       TokenType.HEX_STRING, TokenType.UNICODE_STRING})

# `AS` before a table alias is `Style.table_alias_style`, which exists to add or
# remove exactly this token. It carries no meaning of its own -- `FROM t a` and
# `FROM t AS a` are the same tree -- so it is dropped from both sides rather
# than compared.
# `;` joins it for the same reason: the formatter terminates statements that
# arrived without one -- a T-SQL `CREATE PROCEDURE ... END` comes back as
# `END;` -- so counting terminators reports every such statement as rewritten.
# Losing one cannot hide here: it changes where statements begin and end, and
# both the splitter and the per-statement tree comparison see that.
_OPTIONAL = frozenset({"AS", ";"})

# `!=` / `<>` is `Style.neq_style`; the two are one operator to every engine
# sqlalign supports. Written here because it is an operator rather than a type,
# so the type derivation below cannot reach it -- but it is still a knob, not a
# guess: the pair is exactly the flag's own choices.
_OPERATOR_CLASSES = (frozenset({"!=", "<>"}),)
_OPERATOR_CANON = {spelling: sorted(group)[0]
                   for group in _OPERATOR_CLASSES for spelling in group}


@functools.cache
def type_synonyms(dialect: str) -> dict[str, str]:
    """Every type spelling `dialect` accepts, mapped to one canonical name.

    Found by asking sqlglot twice: its tokenizer for the words it treats as
    types, then its parser for which node each one produces. Two spellings that
    parse to the same `DataType` are the same type, so a formatter printing one
    where the author wrote the other has changed nothing that runs -- and
    flagging it would be a false alarm on every `INT` the author spelled
    `INTEGER`.
    """
    resolved = sqlglot.Dialect.get_or_raise(dialect)
    candidates = sorted(
        word for word, token in resolved.tokenizer_class().KEYWORDS.items()
        if isinstance(token, TokenType) and token.name in exp.DataType.Type.__members__)

    by_node: dict[str, set[str]] = {}
    for spelling in candidates:
        try:
            node = sqlglot.parse_one(
                f"CREATE TABLE t (c {spelling})", dialect=dialect).find(exp.DataType)
        except Exception:                     # not a type in this position
            continue
        if node is not None:
            by_node.setdefault(node.this.name, set()).add(spelling)

    return {spelling: sorted(group)[0]
            for group in by_node.values() for spelling in group}


def literal_spellings(sql: str, dialect: str) -> dict[str, str]:
    """`sql`'s string literals, keyed by their uppercase form.

    The renderer's way back to a spelling sqlglot normalised out of the tree.
    Uppercase keys because case is the only thing that can be recovered this
    way: two literals differing by anything else are different literals, and one
    of them must not be printed for the other.

    A statement that writes one literal two ways offers neither.
    """
    tokenizer = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class()
    spellings: dict[str, str] = {}
    ambiguous: set[str] = set()
    for token in tokenizer.tokenize(sql):
        if token.token_type is TokenType.STRING:
            key = token.text.upper()
            if spellings.setdefault(key, token.text) != token.text:
                # The same literal written two ways in one statement. There is
                # no spelling to restore -- either choice respells the other --
                # so neither is offered and sqlglot's own output stands. It
                # matters here because a `$$` body is exempt from the token
                # census, so a wrong substitution inside one has nothing
                # downstream to catch it.
                ambiguous.add(key)
        elif token.token_type is TokenType.HEREDOC_STRING:
            # A `$$ ... $$` body is ONE token here, so its own literals are
            # invisible from outside it -- and the statements inside a body are
            # laid out by the same engine, through the same generator, with the
            # same spelling to lose. Descend rather than tokenize the body
            # separately: the body is part of the statement, and its literals
            # are the statement's.
            for key, text in literal_spellings(token.text, dialect).items():
                if spellings.setdefault(key, text) != text:
                    ambiguous.add(key)
    for key in ambiguous:
        del spellings[key]
    return spellings


def token_census(sql: str, dialect: str) -> collections.Counter:
    """`sql`'s significant tokens counted, order discarded.

    Order is deliberately not compared, and the reason is that something else
    already compares it. `ast_equal` sees any reordering that changes meaning,
    because a different order is a different tree. What it cannot see is a token
    appearing or vanishing when sqlglot's parser collapsed two spellings -- a
    dropped `ARRAY`, a dropped `ROWS`, an invented `WITH`.

    So the two checks divide the space: the tree catches what moved, the census
    catches what was added or lost. Comparing order here as well would flag
    every benign normalisation sqlglot makes -- `GROUP BY ROLLUP(a, b), c`
    printed as `GROUP BY c, ROLLUP(a, b)` is the same grouping sets, and
    `NOT x IS NULL` printed as `x IS NOT NULL` is one tree in the dialects that
    cannot tell them apart -- and a check that fires on correct output is one
    that gets turned off.
    """
    return collections.Counter(significant_tokens(sql, dialect))


def significant_tokens(sql: str, dialect: str) -> list[str]:
    """`sql` as the tokens that carry the author's intent.

    Case is normalized for syntax because `Style.keyword_case` chooses it,
    optional `AS` is dropped, and a spelling with a synonym is replaced by its
    canonical form. Content -- string literals, quoted identifiers, `$$` bodies
    -- is compared as written, since nothing sqlalign does may change a byte
    inside one. What survives is what sqlalign has no licence to change.
    """
    tokenizer = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class()
    synonyms = type_synonyms(dialect)
    tokens = []
    previous = None
    for token in tokenizer.tokenize(sql):
        if (token.token_type is TokenType.STRING
                and previous is TokenType.COMMAND
                and len(token.text) < len(sql)):
            # Not a literal. After a keyword it cannot parse -- `DECLARE`, `SET`
            # -- the tokenizer stops and hands back the rest of the statement as
            # one STRING, so `declare row_count int` arrives here as the "string"
            # `row_count int`. Comparing that as content made keyword casing look
            # like a rewrite, and every procedure with a DECLARE declined.
            # Tokenizing into it keeps real literals inside verbatim.
            tokens.extend(significant_tokens(token.text, dialect))
            previous = token.token_type
            continue
        previous = token.token_type
        if token.token_type in _VERBATIM:
            tokens.append(f"{token.token_type.name}:{token.text}")
            continue
        word = token.text.strip().upper()
        if not word or word in _OPTIONAL:
            continue
        tokens.append(synonyms.get(word, _OPERATOR_CANON.get(word, word)))
    return tokens
