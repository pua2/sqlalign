"""Set-operation (UNION / INTERSECT / EXCEPT) layout.

sqlglot parses `A op B op C` left-associatively into nested `SetOperation`
nodes: the outermost node's `this` is itself a `SetOperation` over the earlier
arms, and `expression` is the last arm. Recursing `layout_statement` over both
`this` and `expression` therefore naturally unrolls an N-way chain: each level
contributes one more (blank / OPERATOR / blank) separator between the arm it
adds and the ones already laid out, so a 3-way union comes out as
arm / blank / op / blank / arm / blank / op / blank / arm with no special-casing.

Each arm is formatted independently at the same `anchor` ; the operator line sits alone at `anchor`
between two blank lines.
"""
from sqlglot import exp

from sqlalign.ir import Line, Seg
from sqlalign.layout import Unsupported

# op keyword per SetOperation subclass (verified empirically against pinned
# sqlglot v30.14). `distinct=True` is the default (no ALL) for all three;
# `distinct` is falsy only when the source said "... ALL".
_OP_KEYWORD = {exp.Union: "UNION", exp.Intersect: "INTERSECT", exp.Except: "EXCEPT"}

# SetOperation args this layer lays out. Any other truthy arg (by_name, side,
# kind, on, order, limit, offset, with_, ...) means "not yet handled" ->
# passthrough: a combining ORDER BY/LIMIT that applies to the whole set
# operation (rather than one arm), a BY NAME/CORRESPONDING match, or an
# ASOF-style ON are all outside this task's scope. `with_` is excluded here on
# purpose: it is intercepted earlier by layout/__init__.py's dispatch, so it
# would only appear here on a malformed/unexpected call.
_ALLOWED_ARGS = {"this", "expression", "distinct"}


def layout_setop(node: exp.SetOperation, dialect: str, width, anchor: int = 0) -> list[Line]:

    _guard(node)
    keyword = _op_text(node)

    left = _arm_lines(node.this, dialect, width, anchor)
    right = _arm_lines(node.expression, dialect, width, anchor)
    return [*left, Line(anchor, []), Line(anchor, [Seg(keyword)]), Line(anchor, []), *right]


def _arm_lines(arm, dialect, width, anchor):
    """One side of a set operation.

    Usually a bare SELECT. It may also be PARENTHESISED, and there the parens
    are load-bearing rather than decorative: in
    `SELECT a FROM t1 UNION ALL (SELECT b FROM t2 ORDER BY 1)` they scope the
    ORDER BY to the second arm, where without them it orders the whole union.
    `layout_statement` has no case for `exp.Subquery`, so the parenthesised arm
    is laid out here.
    """
    from sqlalign.layout import layout_statement
    from sqlalign.layout.subquery import guard_subquery, subquery_body

    if not isinstance(arm, exp.Subquery):
        return layout_statement(arm, dialect, width, anchor)
    guard_subquery(arm, allow_alias=False)
    open_segs, trailing = subquery_body(arm.this, dialect, width, anchor + 1)
    return [Line(anchor, open_segs), *trailing]


def _op_text(node: exp.SetOperation) -> str:
    base = _OP_KEYWORD.get(type(node))
    if base is None:
        raise Unsupported(type(node).__name__)
    return base if node.args.get("distinct") else f"{base} ALL"


def _guard(node: exp.SetOperation) -> None:
    for name, value in node.args.items():
        if value in (None, [], False):
            continue
        if name not in _ALLOWED_ARGS:
            raise Unsupported(f"setop arg: {name}")
    if node.comments:
        raise Unsupported("embedded comment")
