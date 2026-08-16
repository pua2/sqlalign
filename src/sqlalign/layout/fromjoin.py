"""FROM/JOIN block geometry: a FROM table followed by one or more JOINs.

Three alignment columns are GLOBAL across the whole block: one shared scope id
(`f"fj@{id(select)}"`) spans every table-ref row and every ON/AND condition row:

  1. `alias`  table aliases align at (longest "FROM/JOIN + table" prefix) + 1.
  2. `on`     ON keywords align at (longest alias) + 1; continuation AND lines
              are tagged the SAME kind="on" so the resolver right-aligns them
              to end at the same column ON ends at (2 vs 3 chars).
  3. `op`     comparison operators in every ON condition (first condition AND
              every continuation AND, across ALL joins) share one block-global
              END column.

Composition note: these three columns chain: `on` sits after the (padded)
`alias`, and `op` sits after the (padded) `on`/`AND`. The resolver (align.py)
composes dependent same-line tags: it iterates its target computation to a
fixpoint, so scoring `on` accounts for the `alias` before it being padded
rightward, and scoring `op` accounts for the `on`/`AND` before it. This module
therefore emits each segment with its NATURAL text and does no column math of
its own: no manual padding, no space counting — per the house convention that
all alignment lives in align.py. The prefix segment is untagged (single-space
joined); the `alias` tag left-aligns it into the block's alias column, the `on`
tag right-aligns ON/AND to a common end column, and the `op` tag right-aligns
every comparison operator to the block-global operator column.

`JOIN ... USING (col)` has no comparison operator: emitted as a single
`Seg("USING (...)", scope, kind="on")` (no continuation, no `op` tag).

Derived-table joins (subquery as a table, `join.this` is `exp.Subquery`) are
laid out via subquery.py's `derived_table_lines` instead of this
module's shared-scope geometry above: excludes them from the
FROM-block's alias/`ON`/operator scopes entirely — their alias and `ON` sit on
the closing-paren line, in a scope namespaced by `id(join)`, never padded into
this block's columns. `from_join_lines` below therefore builds `alias`/`op`
scope membership only from the REGULAR (non-derived) rows, and splices each
derived-table join's independently-produced lines back in at its original
position. `LATERAL` derived tables (`join.this` is `exp.Lateral`, wrapping the
`Subquery`) are a distinct, un-modeled shape: `_guard_joins` declines them
the same as any other non-Table/non-Subquery `join.this`.
Single-table FROM (no joins) never reaches this module: select.py keeps
handling that case directly so samples 01/02/04 are untouched.
"""
from sqlglot import exp

from sqlalign.casing import active_style, render_expr
from sqlalign.commas import COMMA_KIND
from sqlalign.ir import Line, Seg
from sqlalign.layout import (
    COMMA_ROW,
    Unsupported,
    attach_comments,
    clause_head,
    guard_args,
    row_keyword,
    table_alias,
    table_name,
)
from sqlalign.layout.conditions import predicate_segs, split_conjunction
from sqlalign.layout.subquery import derived_from_lines, derived_table_lines

# Join args (verified empirically against pinned sqlglot v30.14, exp.Join.arg_types)
# this task's block geometry does not model: NATURAL/hash-join methods, join
# hints, ASOF match conditions, CROSS-JOIN-LATERAL directionality, multiple
# comma-joined tables in one clause (`expressions`), and table PIVOT/UNPIVOT.
# Any of these forces a passthrough rather than a guessed/wrong layout.
_EXOTIC_JOIN_ARGS = ("global_", "hint", "match_condition", "directed",
                     "expressions", "pivots")


def from_join_lines(select, anchor, dialect, width):
    """All FROM+JOIN rows for `select`: one line per table ref, plus one line
    per continuation AND/OR condition. `select.args["from_"]` and `["joins"]`
    are both guaranteed present and well-formed by the caller (select.py only
    routes here when `joins` is non-empty; a bare single-table FROM stays with
    select.py's own `_from_lines`).

    The scope id is namespaced by id(select), not just anchor: a CTE body or a
    set-operation arm can call this at the same anchor as a sibling within one
    render() call (e.g. two CTE bodies both at anchor 2), and align.py groups
    purely by (scope, kind): anchor alone would wrongly merge their columns.

    Derived-table joins (`join.this` is `exp.Subquery`) are excluded from the
    `regular` rows below -- and thus from this block's shared alias/ON/op
    scope entirely, and laid out independently via
    `derived_table_lines`; `lines` is reassembled in the original FROM/JOIN
    order so a derived-table join interleaves correctly with regular ones.
    """
    scope = f"fj@{id(select)}"
    frm = select.args["from_"]
    joins = select.args.get("joins") or []
    _guard_joins(joins)

    # A derived table in the FROM position is excluded from `regular` for the
    # same reason a derived-table JOIN is: it is laid out on its own
    # lines by subquery.py and takes no part in this block's shared alias/ON
    # scope. Without this it fell through to `_ref_text` and came out flattened
    # onto one line: the same statement laid out two different ways depending
    # on whether it happened to have a JOIN.
    from_derived = isinstance(frm.this, exp.Subquery)
    # `FROM (VALUES ...) AS v(a, b)` beside a JOIN. `_ref_text` renders the
    # Values node without its parens, which Postgres rejects, and `ast_equal`
    # cannot see it, since sqlglot reads its own lenient output back happily.
    from_values = isinstance(frm.this, exp.Values)

    # (join_keyword_or_"FROM", table_node, join_node_or_None) per REGULAR row.
    regular = [] if (from_derived or from_values) else [("FROM", frm.this, None)]
    regular += [(row_keyword(j), j.this, j)
                for j in joins if not _is_derived(j)]

    prefixes = [(kw, _ref_text(table, dialect)) for kw, table, _ in regular]
    aliases = [table_alias(table, dialect) for _, table, _ in regular]

    # Regular rows' lines, keyed by their join's id() (None for the FROM row).
    by_id = {}
    for (_, _, join), prefix, alias in zip(regular, prefixes, aliases, strict=False):
        # An unaliased table contributes NO alias segment rather than an empty
        # one. It has nothing to place in the alias column, so it simply does not
        # participate: the column is still resolved from the rows that do have an
        # alias, and this row's ON lands wherever the right-aligned `on` column
        # puts it. An empty Seg would align identically but leave a stray
        # separator space behind it whenever alignment is off.
        keyword, name = prefix
        if keyword is COMMA_ROW:
            # The legacy comma form. It hangs two columns before the table
            # column, the way every other stacked list in the house style does,
            # and carries COMMA_KIND so `comma_position` can move it.
            row_anchor, keyword = _table_col(anchor) - 2, ","
        else:
            # A river right-aligns FROM into the gutter and puts every JOIN on
            # the far side of it (Holywell's rule is about joins, not about long
            # keywords: a bare `JOIN` beside a hanging `LEFT JOIN` would read
            # as a different clause).
            row_anchor, keyword = clause_head(keyword, anchor, hang=join is not None)
        # The keyword and the table name are SEPARATE segments so the name can
        # hold the `table_names` column. Untagged they render exactly as the
        # single fused segment they replaced: one space between them.
        head = Seg(keyword, kind=COMMA_KIND) if keyword == "," else Seg(keyword)
        lead = [head, Seg(name, scope=scope, kind="table")]
        if alias is not None:
            lead.append(Seg(alias, scope=scope, kind="alias"))
        if join is None:
            by_id[None] = [Line(row_anchor, lead)]
            continue
        if join.args.get("on") is None and not join.args.get("using"):
            # CROSS/NATURAL: the table reference IS the whole row.
            by_id[id(join)] = [Line(row_anchor, lead)]
            continue
        using = join.args.get("using")
        if using:
            cols = ", ".join(render_expr(c, dialect) for c in using)
            if active_style().on_placement == "own_line":
                by_id[id(join)] = [Line(row_anchor, list(lead)),
                                   Line(row_anchor + 2, [Seg(f"USING ({cols})")])]
            else:
                using_seg = Seg(f"USING ({cols})", scope=scope, kind="on")
                by_id[id(join)] = [Line(row_anchor, [*lead, using_seg])]
            continue
        style = active_style()
        bools_trail = style.boolean_operator_position == "trailing"
        conditions, joiner = split_conjunction(join.args["on"])
        if style.on_placement == "own_line":
            # ON drops below the table reference. The block-global ON column is
            # retired here by definition (nothing sits after each alias to align),
            # so ON/AND are right-justified locally with plain arithmetic: the
            # same treatment conditions.py gives WHERE/AND. Aliases and the
            # operator column still align across the block.
            by_id[id(join)] = _on_own_line_rows(
                lead, conditions, joiner, scope, row_anchor, dialect, bools_trail,
                _block_keyword_width(regular))
            continue
        rows = [Line(row_anchor, [*lead, Seg("ON", scope=scope, kind="on"),
                                  *predicate_segs(conditions[0], scope, dialect)])]
        # A continuation AND/OR row carries a base indent of anchor + 2 (the same
        # 2-space continuation WHERE/HAVING use) rather than bare `anchor`. Its
        # ON-column position still comes from the kind="on" tag, which the
        # resolver right-aligns far past column 2, so aligned output is
        # unchanged. The base indent matters when alignment is OFF
        # (Style.align=False): without it the AND would emit at column 0 and read
        # as a new top-level clause. Invariant: a line must be structurally
        # correct at its natural position; alignment only refines it.
        #
        # With trailing booleans there is no AND at the row head to align, so the
        # head becomes an EMPTY seg still tagged kind="on": the resolver
        # right-aligns its (zero-width) end to the same ON column, which lands the
        # continuation's condition in exactly the first condition's column. That
        # keeps the FROM-block condition column intact under either setting --
        # this row is why boolean position had to be settled before that column
        # could be configured at all.
        for cond in conditions[1:]:
            if bools_trail:
                rows[-1].segs[-1].text += " " + joiner
                head = Seg("", scope=scope, kind="on")
            else:
                head = Seg(joiner, scope=scope, kind="on")
            rows.append(Line(row_anchor + 2, [head, *predicate_segs(cond, scope, dialect)]))
        by_id[id(join)] = rows

    if from_derived:
        lines = derived_from_lines(frm, anchor, dialect, width)
    elif from_values:
        kw_anchor, kw_text = clause_head("FROM", anchor)
        lines = [Line(kw_anchor, [Seg(kw_text), Seg(render_expr(frm.this, dialect))])]
    else:
        lines = list(by_id[None])
    if not from_derived and not from_values:
        lines = attach_comments(lines, frm.this, anchor)
    for j in joins:
        rows = (derived_table_lines(j, anchor, dialect, width)
                if _is_derived(j) else by_id[id(j)])
        lines += attach_comments(rows, j.this, anchor, before=lines)
    return lines


def _ref_text(table, dialect):
    """A table reference rendered without its alias.

    `LATERAL f(x) AS e` is a table reference too: a set-returning function
    that may reference columns from the rows to its left. It renders as one
    piece with the `LATERAL` keyword attached, so the row reads the way every
    other FROM row does and the alias still lands in the block's alias column.
    """
    if isinstance(table, exp.Lateral):
        bare = table.copy()
        bare.set("alias", None)
        return render_expr(bare, dialect)
    return table_name(table, dialect)


def _is_derived(join):
    """True for a join whose table is a subquery — directly, or wrapped in a
    LATERAL. Both get subquery.py's derived-table geometry rather than this
    module's shared columns."""
    inner = join.this.this if isinstance(join.this, exp.Lateral) else join.this
    return isinstance(inner, exp.Subquery)


def _table_col(anchor):
    """The column the FROM row's table name starts at — where a comma row's
    own table must line up."""
    kw_anchor, kw_text = clause_head("FROM", anchor)
    return kw_anchor + len(kw_text) + 1


def _block_keyword_width(regular):
    """Widest ON/AND/OR keyword anywhere in this FROM block, so every join's ON
    lands in ONE column under `on_placement="own_line"` -- justifying per join
    would put `ON` at a different column depending on whether that particular
    join happens to have a continuation. Block-global columns are this module's
    whole premise."""
    widths = [len("ON")]
    for _, _, join in regular:
        if join is None or join.args.get("using") or join.args.get("on") is None:
            continue
        conds, joiner = split_conjunction(join.args["on"])
        if len(conds) > 1:
            widths.append(len(joiner))
    return max(widths)


def _on_own_line_rows(lead, conditions, joiner, scope, anchor, dialect, bools_trail, max_kw):
    """`Style.on_placement == "own_line"`: the table reference keeps its own row
    and every ON/AND condition follows indented 2, keywords right-justified
    against each other (`ON` ending where `AND` ends), which is exactly the
    treatment conditions.py gives a WHERE and its ANDs."""
    rows = [Line(anchor, list(lead))]
    keywords = ["ON"] + [joiner] * (len(conditions) - 1)
    # House drops the condition two columns in and right-justifies the keyword
    # family. A river instead keeps ON at the join's own column and hangs each
    # continuation under ON's OPERAND, which is the geometry Holywell prints.
    river = active_style().clause_keyword_align == "river"
    for i, (kw, cond) in enumerate(zip(keywords, conditions, strict=False)):
        if bools_trail and i:
            rows[-1].segs[-1].text += " " + kw
            head = Seg(" " * max_kw)
        else:
            head = Seg(kw if river else kw.rjust(max_kw))
        row_indent = anchor + (len("ON ") if river and i else 0) if river else anchor + 2
        rows.append(Line(row_indent, [head, *predicate_segs(cond, scope, dialect)]))
    return rows


def _guard_joins(joins):
    for j in joins:
        for name in _EXOTIC_JOIN_ARGS:
            if j.args.get(name):
                raise Unsupported(f"join arg: {name}")
        if isinstance(j.this, exp.Lateral):
            # A LATERAL wrapping a set-returning function is a one-line table
            # reference. One wrapping a SUBQUERY needs the derived-table layout,
            # and its alias lives on the Lateral rather than on the subquery, so
            # it is a distinct shape: declined until it is modelled.
            guard_args(j.this, {"this", "alias"}, "LATERAL")
        elif not isinstance(j.this, (exp.Table, exp.Subquery)):
            raise Unsupported("join: non-table")
        has_on = j.args.get("on") is not None
        has_using = bool(j.args.get("using"))
        if has_on and has_using:                       # both -> undefined here
            raise Unsupported("join: both ON and USING")
        # A join with no condition is only VALID SQL when it says so: `CROSS
        # JOIN` or a `NATURAL` join. Everything else that parses this way is the
        # legacy comma form (`FROM a, b`), which sqlglot also represents as a
        # conditionless join, and rebuilding that as `JOIN b` would emit SQL
        # Postgres rejects outright. The AST check cannot catch it (sqlglot
        # re-parses its own lenient output), so this guard is the only thing
        # standing between the two.
        # A conditionless join is either CROSS/NATURAL (which say so) or the
        # legacy comma form, which `_row_keyword` emits as a comma. Both are
        # valid; neither needs declining.


