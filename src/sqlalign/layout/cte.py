"""WITH-clause (CTE) layout.

`WITH name AS (` opens the first CTE at column 0 (`, next AS (` for every CTE
after the first); the CTE body is a full statement, laid out via
`layout_statement` at `anchor=2`; the closing `)` returns to column 0. A blank
line follows every CTE's `)` -- both the ones before the next `, name AS (` and
the one before the final query, which is itself a full statement laid out via
`layout_statement` at `anchor=0` on a `with_`-stripped copy of `node`.

`layout_with` only ever runs at the top of a statement (anchor 0): a `with_` arg
can appear on the outermost Select/SetOperation, and layout/__init__.py's
dispatch checks for it before anything else, so this module never needs an
`anchor` parameter of its own: nested CTEs (a CTE body that is itself a
`WITH ...` statement) would recurse through `layout_statement` -> `layout_with`
again and restart at column 0/2, which is untested and out of scope here (no
sample nests a WITH inside a CTE).
"""
from sqlglot import exp

from sqlalign.casing import active_style
from sqlalign.commas import COMMA_KIND
from sqlalign.ir import Line, Seg
from sqlalign.layout import Unsupported, cte_name

_BODY_ANCHOR = 2


def layout_with(node: exp.Expression, dialect: str, width) -> list[Line]:
    from sqlalign.layout import layout_statement

    with_ = node.args["with_"]
    _guard(with_)

    keyword = "WITH RECURSIVE" if with_.args.get("recursive") else "WITH"
    trailing = active_style().comma_position == "trailing"
    lines: list[Line] = []
    for i, cte in enumerate(with_.expressions):
        # `AS MATERIALIZED (` / `AS NOT MATERIALIZED (`: Postgres's optimiser
        # fence: one keyword between `AS` and the paren. It changes planning,
        # so dropping it would change how the query runs.
        materialized = cte.args.get("materialized")
        hint = "" if materialized is None else (
            "MATERIALIZED " if materialized else "NOT MATERIALIZED ")
        # `.alias` returns the identifier's NAME, unquoted, so `WITH "cte" AS`
        # came out as bare `WITH cte AS` -- a different relation in Postgres, and
        # a syntax error whenever the name needs quoting at all. The re-parse
        # guard caught it and passed the statement through, which is the silent
        # decline `column_alias` and `table_alias` document one node type over.
        body = f"{cte_name(cte, dialect)} AS {hint}("
        # This site applies comma position itself rather than going through
        # commas.py's generic pass (like window.py). That pass blanks the comma
        # while KEEPING its width, which is right where the comma head is as wide
        # as the first item's head (`SELECT` vs `     ,`), but a CTE's head is
        # a bare `,` against a `WITH ...` opener, so blanking would indent every
        # later CTE by two columns instead of leaving it at column 0.
        if i == 0:
            segs = [Seg(f"{keyword} {body}")]
        elif trailing:
            segs = [Seg(body)]                     # comma went on the `)` above
        else:
            segs = [Seg(",", kind=COMMA_KIND), Seg(body)]
        lines.append(Line(0, segs))
        lines += layout_statement(cte.this, dialect, width, anchor=_BODY_ANCHOR)
        close = ")" + ("," if trailing and i < len(with_.expressions) - 1 else "")
        lines.append(Line(0, [Seg(close)]))
        lines.append(Line(0, []))

    query = node.copy()
    query.set("with_", None)
    lines += layout_statement(query, dialect, width, anchor=0)
    return lines


def _guard(with_: exp.With) -> None:
    if with_.args.get("search"):
        raise Unsupported("WITH ... SEARCH")
    if with_.comments:
        raise Unsupported("embedded comment")
    for cte in with_.expressions:
        if cte.comments:
            raise Unsupported("embedded comment")
        if cte.args.get("key_expressions"):
            raise Unsupported("WITH ... CYCLE")
        alias = cte.args.get("alias")
        if alias is not None and alias.args.get("columns"):
            raise Unsupported("CTE column list")
