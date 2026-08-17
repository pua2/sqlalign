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

# `AS` before a table alias is `Style.table_alias_style`, which exists to add or
# remove exactly this token. It carries no meaning of its own -- `FROM t a` and
# `FROM t AS a` are the same tree -- so it is dropped from both sides rather
# than compared.
_OPTIONAL = frozenset({"AS"})

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

    Case is normalized because `Style.keyword_case` chooses it, optional `AS` is
    dropped, and a spelling with a synonym is replaced by its canonical form.
    What survives is what sqlalign has no licence to change: identifiers,
    literals, operators, and every keyword that is not one of its settings.
    """
    tokenizer = sqlglot.Dialect.get_or_raise(dialect).tokenizer_class()
    synonyms = type_synonyms(dialect)
    tokens = []
    for token in tokenizer.tokenize(sql):
        word = token.text.strip().upper()
        if not word or word in _OPTIONAL:
            continue
        tokens.append(synonyms.get(word, _OPERATOR_CANON.get(word, word)))
    return tokens
