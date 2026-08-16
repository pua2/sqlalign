"""Subquery layout: the three shapes sample 07
exercises --

  1. a **scalar subquery** in a SELECT-list item (select.py),
  2. `IN (SELECT ...)` / `EXISTS (SELECT ...)` in a WHERE/HAVING predicate
     (conditions.py), and
  3. a **derived table** (subquery as a FROM/JOIN table ref) (fromjoin.py) --

all recurse the subquery's body through `layout_statement` at the column one
past its opening `(`, then splice the result into the surrounding line. They
differ only in how the close reads: scalar/IN/EXISTS close
*inline*, glued (zero space) onto the last body line; a derived-table JOIN
closes on its *own* line, before the alias.

Geometry is precomputed, not resolver-driven: every inner-anchor column below
is derived from the caller's own *natural* (unpadded) layout, the same
simplification `conditions.py`'s `_render_group` already relies on for a
parenthesized condition group's `(` column. A tag's resolver-driven padding
(e.g. a WHERE clause's shared `op` column, when a subquery predicate shares
scope with siblings) can shift where a segment actually lands at render time,
but the inner body's own line *indents* are plain ints baked in at
layout-build time: they cannot depend on a later resolver pass. This is
only safe because every segment feeding into an inner-anchor calculation
above is itself untagged (no scope/kind), so its natural column IS its
rendered column: the SELECT-list `head_seg` (fixed-width keyword/comma), the
FROM/JOIN `keyword` for a derived table, and `conditions.py`'s `IN` keyword.
`IN` is deliberately untagged: in the WHERE clause's shared `op` scope, a
sibling condition with a longer LHS would pad it rightward at
render time while the subquery body's baked-in indent stayed at the
now-stale natural column: silently detaching the body from its own `(`
(caught on `WHERE a = <long> AND b IN (SELECT ...)`, ast_equal-safe so
invisible to the safety net). `conditions.py::_render_in_subquery` now emits
`IN` untagged, matching `EXISTS` (already untagged) and eliminating the
class: nothing an inner-anchor formula in this module depends on can ever be
resolver-padded, so natural and rendered can no longer diverge.

Alignment scopes are id(node)-namespaced (`f"dtjoin@{id(join)}"`, etc.) per
house convention -- see fromjoin.py's and select.py's module docstrings.
"""
from sqlglot import exp

from sqlalign.casing import active_style, render_expr
from sqlalign.ir import Line, Seg
from sqlalign.layout import (
    COMMA_ROW,
    Unsupported,
    clause_head,
    row_keyword,
    table_alias,
)
from sqlalign.layout.conditions import predicate_segs, split_conjunction


def guard_subquery(subquery: exp.Subquery, *, allow_alias: bool = False) -> None:
    """Raise `Unsupported` for any `exp.Subquery` arg this layer does not
    model. Verified empirically (pinned sqlglot v30.14): a plain
    `(SELECT ...)` only ever populates `this` (always) and `alias` (derived
    tables only): every other key in `exp.Subquery.arg_types` (`with_`,
    `sample`, `for_`, `pivots`, ...) is exotic syntax outside this task's
    scope. The wrapped `Select`/`SetOperation`/`With` itself is validated
    separately, when `layout_statement` recurses into it.
    """
    if subquery.comments:
        raise Unsupported("embedded comment")
    # `pivots` is allowed alongside an alias because `FROM (SELECT …) s PIVOT(…)`
    # is the ordinary way to write a pivot: you pivot a projection, not a bare
    # table. The pivot carries its own alias and renders whole, so it is a
    # suffix on the closing line rather than anything to lay out.
    allowed = {"this", "alias", "pivots"} if allow_alias else {"this"}
    for name, value in subquery.args.items():
        if value in (None, [], False):
            continue
        if name not in allowed:
            raise Unsupported(f"subquery arg: {name}")


def _open_inline(select, dialect, width, inner_anchor):
    """Recurse `select`'s body at `inner_anchor` (one column past the open
    paren) and return `(open_segs, trailing_lines)`, UNCLOSED:

    - `open_segs` -- the body's first `Line`'s segs, with `(` glued (zero
      space) onto the front of the first one. Meant to be appended, via
      `render()`'s normal one-space inter-seg separator, right after
      whatever precedes the paren on the caller's current row.
    - `trailing_lines`: every remaining body `Line`, verbatim (already
      indented at `inner_anchor` by `layout_statement`).

    Callers close the `)` per their own shape: `subquery_body` glues it
    inline (scalar/IN/EXISTS); `derived_table_lines` puts it on a new line.
    """
    from sqlalign.layout import layout_statement

    body = layout_statement(select, dialect, width, anchor=inner_anchor)
    first, trailing = body[0], body[1:]
    open_segs = [_prefixed(first.segs[0], "("), *first.segs[1:]]
    return open_segs, trailing


def subquery_body(select, dialect, width, inner_anchor):
    """`_open_inline`, closed inline on its last line -- the scalar / `IN` /
    `EXISTS` shape, and EXISTS (...)
    subqueries close inline"). Returns `(open_segs, trailing_lines)`, the
    same `(inline_segs, trailing_lines)` shape `conditions.py`'s
    `_render_condition` uses, so callers there can return it directly.
    """
    open_segs, trailing = _open_inline(select, dialect, width, inner_anchor)
    if trailing:
        trailing[-1].segs[-1].text += ")"
    else:
        open_segs[-1].text += ")"
    return open_segs, trailing


def derived_table_lines(join: exp.Join, anchor: int, dialect: str, width) -> list[Line]:
    """A derived-table JOIN (`join.this` is `exp.Subquery`): `JOIN (` opens
    inline, the body recurses at its own `SELECT` column, and the closing
    `)` lands on its OWN line, unlike scalar/IN/EXISTS -- followed by the
    alias and `ON`/`USING`.

    Per the exclusion, the alias/`ON`/operator here are NOT part of
    the enclosing FROM-block's shared scopes (`fj@{id(select)}` in
    fromjoin.py): they get their own scope, namespaced by `id(join)` (unique
    for this join's lifetime), so they align only with each other -- a
    second derived-table join, or a multi-condition `ON`/`AND` on THIS join,
    never bleeds into a sibling regular join's alias/ON/op columns, or vice
    versa.
    """
    subquery = join.this
    # `LATERAL (SELECT ...)` is a derived table that may reference columns from
    # the rows to its left. Its geometry is identical; the only differences are
    # the extra keyword and that the ALIAS hangs off the Lateral rather than off
    # the subquery, which is why this could not simply fall through.
    lateral = subquery if isinstance(subquery, exp.Lateral) else None
    if lateral is not None:
        subquery = lateral.this
    guard_subquery(subquery, allow_alias=True)
    alias = (lateral or subquery).args.get("alias")
    if alias is None:
        raise Unsupported("derived table: missing alias")

    keyword = row_keyword(join)
    if keyword is COMMA_ROW:
        # The legacy comma form, which is how `LATERAL` is most often written.
        # The comma hangs two columns before the FROM row's table column, the
        # same place fromjoin.py puts it for a plain comma-joined table.
        from_anchor, from_kw = clause_head("FROM", anchor)
        anchor, keyword = from_anchor + len(from_kw) + 1 - 2, ","
    else:
        anchor, keyword = clause_head(keyword, anchor, hang=True)
    if lateral is not None:
        keyword = f"{keyword} LATERAL"
    open_col = anchor + len(keyword) + 1              # column of the derived table's "("
    inner_anchor = open_col + 1                        # the subquery's own SELECT column
    open_segs, body_lines = _open_inline(subquery.this, dialect, width, inner_anchor)
    first_line = Line(anchor, [_prefixed(open_segs[0], f"{keyword} "), *open_segs[1:]])

    scope = f"dtjoin@{id(join)}"
    # The alias is untagged: excluded from the fj@ scope.
    tail = [Seg(")"), Seg(table_alias(lateral or subquery, dialect))]
    tail += [Seg(render_expr(pivot, dialect)) for pivot in subquery.args.get("pivots") or []]
    using = join.args.get("using")
    if join.args.get("on") is None and not using:
        # No condition: a LATERAL, or a CROSS JOIN of a derived table. The
        # closing paren and the alias are the whole row.
        close_lines = [Line(open_col, tail)]
    elif using:
        cols = ", ".join(render_expr(c, dialect) for c in using)
        close_lines = [Line(open_col, [*tail, Seg(f"USING ({cols})", scope=scope, kind="on")])]
    else:
        conditions, joiner = split_conjunction(join.args["on"])
        # Same two knobs the regular-join path honours (fromjoin.py): a derived
        # table's ON must not keep leading booleans (or an inline ON) while the
        # rest of the file uses the other convention. No fixture exercises a
        # derived join with a multi-condition ON, so this path is covered by its
        # own tests rather than by the cross-golden invariants.
        style = active_style()
        bools_trail = style.boolean_operator_position == "trailing"
        if style.on_placement == "own_line":
            close_lines = [Line(open_col, list(tail))]
            keywords = ["ON"] + [joiner] * (len(conditions) - 1)
            max_kw = max(len(k) for k in keywords)
            for i, (kw, cond) in enumerate(zip(keywords, conditions, strict=False)):
                if bools_trail and i:
                    close_lines[-1].segs[-1].text += " " + kw
                    head = Seg(" " * max_kw)
                else:
                    head = Seg(kw.rjust(max_kw))
                close_lines.append(
                    Line(open_col + 2, [head, *predicate_segs(cond, scope, dialect)]))
        else:
            close_lines = [Line(open_col, [*tail, Seg("ON", scope=scope, kind="on"),
                                           *predicate_segs(conditions[0], scope, dialect)])]
            for cond in conditions[1:]:
                if bools_trail:
                    close_lines[-1].segs[-1].text += " " + joiner
                    head = Seg("", scope=scope, kind="on")
                else:
                    head = Seg(joiner, scope=scope, kind="on")
                close_lines.append(
                    Line(open_col, [head, *predicate_segs(cond, scope, dialect)]))

    return [first_line, *body_lines, *close_lines]


def _prefixed(seg: Seg, prefix: str) -> Seg:
    """A copy of `seg` with `prefix` glued to the front of its text.

    The body's first segment carries its own leading padding under
    `clause_keyword_align="river"` -- the inner `SELECT` is right-aligned into
    its gutter, and the `(` takes that space. Dropping it here keeps the paren
    against the keyword (`(SELECT`, never `(  SELECT`); the inner query's own
    river is measured from `inner_anchor`, which is already the column after the
    paren, so nothing downstream shifts.
    """
    return Seg(prefix + seg.text.lstrip(" "), scope=seg.scope, kind=seg.kind)


def derived_from_lines(frm: exp.From, anchor: int, dialect: str, width) -> list[Line]:
    """`FROM (SELECT ...) d` -- a derived table in the FROM position.

    Geometry identical to a derived-table JOIN, minus the join condition a FROM
    cannot have: `FROM (` opens inline, the body recurses at its own SELECT
    column, and the closing `)` lands on its own line followed by the alias.

    It cannot simply call `derived_table_lines`, which is keyed to a `join` node
    for both its keyword and its ON/USING tail. The part that actually matters --
    where the body recurses and where the paren closes: is `_open_inline`,
    shared verbatim, so the two shapes cannot drift apart.

    The alias is untagged, exactly as it is for a derived-table JOIN:
    a derived table does not join the enclosing FROM block's alias column.
    """
    subquery = frm.this
    guard_subquery(subquery, allow_alias=True)
    alias = subquery.args.get("alias")
    if alias is None:
        raise Unsupported("derived table: missing alias")

    kw_anchor, kw_text = clause_head("FROM", anchor)
    open_col = kw_anchor + len(kw_text) + 1
    open_segs, body_lines = _open_inline(subquery.this, dialect, width, open_col + 1)
    first = Line(kw_anchor, [_prefixed(open_segs[0], f"{kw_text} "), *open_segs[1:]])
    tail = [Seg(")"), Seg(table_alias(subquery, dialect))]
    tail += [Seg(render_expr(pivot, dialect)) for pivot in subquery.args.get("pivots") or []]
    return [first, *body_lines, Line(open_col, tail)]
