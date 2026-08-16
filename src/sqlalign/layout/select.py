"""SELECT-statement layout: SELECT list, FROM, and orchestration of the clause
handlers (WHERE/HAVING via conditions, GROUP BY/ORDER BY/LIMIT via grouporder).

SELECT list geometry: the first item rides the `SELECT ` line; every later item
gets its own line with a leading comma two columns before the item column
(`anchor + len("SELECT ")`). Aliased items emit the expression and an `AS`-tagged
segment (`kind="as"`) so the resolver aligns the AS column across items. A
**scalar subquery** item (`(SELECT ...)`, optionally `AS alias`: Task 10)
expands to multiple lines instead: `SELECT (`/`, (` opens inline, the body
recurses at its own `SELECT` column via subquery.py, and the closing `)` --
plus `AS alias`, tagged into this SAME scope like any other item's alias --
lands on the body's last line.

`_guard` declines: via `Unsupported`, i.e. a byte-identical passthrough — every
construct this task does not yet render exactly (DISTINCT-on, embedded
comments, derived-table FROM). This keeps the formatter a safe identity
transform on everything outside its proven subset. Scalar subqueries in the
select list, derived-table JOINs, and `IN`/`EXISTS (SELECT ...)` in
WHERE/HAVING are the three shapes Task 10 adds; `_guard`'s tree walk exempts
(prunes) exactly those three attachment points: see `_subquery_roots` --
while still declining a `Subquery`/`Select` anywhere else (e.g. one nested
inside an arithmetic expression), and the inner query itself gets its OWN
independent `_guard` call when `layout_statement` recurses into it.

A select-list item whose value is exactly `exp.Case`/`exp.Window` (Task 11)
is a third exemption, see `_case_window_roots` -- but, unlike the
subquery exemption above, `_guard`'s walk does NOT prune into a Case/Window
root's children: case.py/window.py never re-`_guard` their own contents the
way a recursed subquery does, so the walk still descends and validates a
CASE's WHEN/THEN/ELSE or a window's PARTITION BY/ORDER BY/frame for
embedded comments or a nested, non-root Case/Window/Subquery: only the
exempted root node itself is spared the blanket `_UNSUPPORTED_NODES` check.
A CASE/window value buried inside some OTHER expression (e.g.
`COALESCE(SUM(CASE ...))`, sample 21) is never a "root" and stays declined,
exactly as before this task.
`with_` (a CTE-bearing statement) never reaches here: layout/__init__.py's
dispatch routes it to layout/cte.py first, which recurses back into this
module on a `with_`-stripped copy of the node.
"""
from sqlglot import exp

from sqlalign.casing import active_style, render_expr
from sqlalign.commas import COMMA_KIND
from sqlalign.ir import Line, Seg, comment_seg
from sqlalign.layout import (
    Unsupported,
    attach_comments,
    clause_head,
    column_alias,
    guard_args,
    select_item_col,
    table_alias,
    table_name,
)
from sqlalign.layout.case import case_lines
from sqlalign.layout.conditions import condition_block
from sqlalign.layout.expr import nested_break_body, nested_break_case
from sqlalign.layout.fromjoin import from_join_lines
from sqlalign.layout.grouporder import group_lines, order_limit_lines
from sqlalign.layout.subquery import guard_subquery, subquery_body
from sqlalign.layout.window import named_window_lines, window_lines

# Clause args this task lays out. Any other truthy Select arg (with_, qualify,
# windows, laterals, distinct, ...) means "not yet handled" -> passthrough.
# DISTINCT is deliberately excluded: a later task implements it properly
# (multi-item DISTINCT currently misaligns). `joins` is allowed here but is
# itself further gated by fromjoin.py's own guard (derived-table joins,
# NATURAL/CROSS, join hints, ... stay Unsupported until later tasks).
_ALLOWED_ARGS = {
    "expressions", "from_", "joins", "where", "group", "having", "order", "limit", "offset",
    # `distinct` is `SELECT DISTINCT` / `SELECT DISTINCT ON (...)`; it rides the
    # SELECT head, exactly like T-SQL's TOP.
    "distinct",
    # `QUALIFY <predicate>` filters on a window function, and is the same
    # shape as HAVING, a keyword and a predicate, so it reuses the same
    # block. Redshift and Snowflake have it; Postgres does not, but the
    # author wrote it, so preserving it is not the same as introducing it.
    "qualify",
    # `WINDOW w AS (...)`: a named spec that `OVER w` refers to.
    "windows",
    # `FOR UPDATE [OF t] [SKIP LOCKED|NOWAIT]` and the SHARE variants: a
    # trailing clause of its own, rendered whole by sqlglot's generator rather
    # than re-derived here: the update/wait flags spell four keyword pairs and
    # getting one backwards would change the locking behaviour silently.
    "locks",
    # `into` is `SELECT ... INTO target`: in a plpgsql body this assigns the row
    # to a variable (spec §3.5); rendered inline after the last select item. Only a
    # plain (non-temporary, non-unlogged) target is handled; anything else declines.
    "into",
}

# Node kinds whose presence anywhere in the statement forces a passthrough.
#
# Case, Window, Subquery and Select are not here: `render_expr` renders a nested
# one inline, which is the only sane answer for it: the multi-line geometry
# belongs to a select item that owns its row, and a CASE buried in an argument
# list has none.
#
#   - exp.Pivot: a table's `pivots` arg. Declined per dialect, detected by
#     rendering the node rather than by a hardcoded list. See `_guard`.
_UNSUPPORTED_NODES: tuple = ()


def layout_select(select, dialect, width, anchor=0):
    _guard(select, anchor, dialect, width)
    # Scope strings are namespaced by id(select), not just anchor: a CTE body
    # or a set-operation arm can call layout_select more than once at the same
    # anchor within one render() (e.g. sample 10's three UNION ALL arms all
    # sit at anchor 0), and align.py's resolver aligns everything sharing one
    # (scope, kind) pair regardless of which call produced it. Anchor alone
    # would wrongly merge, say, every arm's "AS" column into one; id(select)
    # (unique for the lifetime of this still-referenced node) keeps each call
    # its own alignment group. Anchor stays in the string purely for
    # debug-readability: it plays no role in uniqueness.
    tag = f"{anchor}:{id(select)}"
    # T-SQL expresses a row limit as `SELECT TOP n`, not a trailing LIMIT clause,
    # so it rides the SELECT head. Author's ruling: continuation commas keep their
    # usual column 7: the comma column is a fixed house constant, not derived
    # from where the first item happens to start.
    head_extra = _distinct_text(select, dialect)
    top_text = None
    if dialect == "tsql":
        # Only an `exp.Limit` becomes TOP. `OFFSET n ROWS FETCH NEXT m ROWS
        # ONLY` also lands in the `limit` arg, as an `exp.Fetch` whose count is
        # in `count` rather than `expression`; it is T-SQL's own paging syntax
        # and goes through the shared FETCH path.
        limit = select.args.get("limit")
        if isinstance(limit, exp.Limit):
            # `TOP n PERCENT` and `TOP n WITH TIES`. The modifiers live on a
            # `limit_options` node beside the count, and dropping them yields a
            # different query.
            top_text = "TOP " + render_expr(limit.expression, dialect)
            opts = limit.args.get("limit_options")
            if opts is not None:
                if opts.args.get("percent"):
                    top_text += " PERCENT"
                if opts.args.get("with_ties"):
                    top_text += " WITH TIES"
            if select.args.get("offset") is not None:
                # `SELECT TOP n ... OFFSET m` is not valid T-SQL: paging is
                # spelled OFFSET/FETCH, which is the branch above.
                raise Unsupported("tsql: TOP with OFFSET")
    # DISTINCT precedes TOP: `SELECT DISTINCT TOP 10 ...`.
    head_extra = " ".join(p for p in (head_extra, top_text) if p) or None
    lines = list(select_items(select, f"sel@{tag}", anchor, dialect, width, head_extra))

    into = select.args.get("into")
    if into is not None:
        # `SELECT <items> INTO <target>`: the target rides on the last select-item
        # line, before FROM (render()'s inter-seg space supplies the gap). _guard has
        # already declined temporary/unlogged/non-table targets.
        lines[-1].segs.append(Seg("INTO " + render_expr(into.this, dialect)))

    frm = select.args.get("from_")
    joins = select.args.get("joins")
    if joins:
        lines += from_join_lines(select, anchor, dialect, width)
    elif frm is not None:
        lines += _from_lines(frm, anchor, dialect, tag, width)

    where = select.args.get("where")
    if where is not None:
        lines += condition_block("WHERE", where.this, f"where@{tag}", anchor, dialect, width)

    lines += group_lines(select, anchor, dialect)

    having = select.args.get("having")
    if having is not None:
        lines += condition_block("HAVING", having.this, f"having@{tag}", anchor, dialect, width)

    windows = select.args.get("windows")
    if windows:
        lines += named_window_lines(windows, anchor, dialect)

    qualify = select.args.get("qualify")
    if qualify is not None:
        lines += condition_block("QUALIFY", qualify.this, f"qualify@{tag}", anchor,
                                 dialect, width)

    lines += order_limit_lines(select, anchor, dialect, width)

    # `FOR UPDATE …` closes the statement, after LIMIT/OFFSET. Each lock is
    # rendered whole by sqlglot's own generator: the update/wait flags spell four
    # different keyword pairs (FOR UPDATE / FOR SHARE / FOR NO KEY UPDATE /
    # FOR KEY SHARE, each optionally SKIP LOCKED or NOWAIT), and getting one
    # backwards would change the locking behaviour with nothing to catch it.
    for lock in select.args.get("locks") or []:
        kw_anchor, kw_text = clause_head("FOR", anchor)
        text = render_expr(lock, dialect)
        lines.append(Line(kw_anchor, [Seg(kw_text + text[len("FOR"):])]))
    return lines


def select_items(select, scope, anchor, dialect, width, head_extra=None):
    """One line per select item; leading commas two columns before the item
    column. `head_seg` (`SELECT`/`, `, no trailing space) is a separate Seg
    from the expression -- render()'s automatic one-space inter-seg separator
    supplies the space between them, the same convention conditions.py and
    fromjoin.py use, so a scalar-subquery item's merged `(` seg (see
    subquery.py's `_open_inline`) composes without a hand-counted space.
    """
    keyword = "SELECT" if head_extra is None else f"SELECT {head_extra}"
    style = active_style()
    own_line = style.select_placement == "own_line"
    item_col = select_item_col(anchor)
    kw_anchor, kw_text = clause_head(keyword, anchor)
    # The comma hangs two columns before the item column, wherever that is.
    comma_lead = " " * (item_col - anchor - 2)

    lines = []
    if own_line:
        lines.append(Line(kw_anchor, [Seg(kw_text)]))

    for i, item in enumerate(select.expressions):
        # Inline, the lead-in carries its own left padding and every row sits at
        # `anchor`. On its own line the list has no lead-in to pad with, so the
        # ROW carries the indent instead and a leading comma hangs two columns
        # back: the same two columns it hangs back by inline.
        if own_line:
            head_seg = Seg("") if i == 0 else Seg(",", kind=COMMA_KIND)
            row_anchor = item_col if i == 0 else item_col - 2
        else:
            head_seg = (Seg(kw_text) if i == 0
                        else Seg(comma_lead + ",", kind=COMMA_KIND))
            row_anchor = kw_anchor if i == 0 else anchor
        target = item.this if isinstance(item, exp.Alias) else item
        if isinstance(target, exp.Subquery):
            lines += _scalar_subquery_lines(item, target, head_seg, anchor, scope,
                                            dialect, width, row_anchor)
            continue
        if isinstance(target, exp.Case):
            lines += _multiline_item_lines(item, head_seg, scope,
                                            case_lines(target, anchor, dialect, width),
                                            row_anchor, dialect)
            continue
        if isinstance(target, exp.Window):
            lines += _multiline_item_lines(item, head_seg, scope,
                                            window_lines(target, anchor, dialect, width),
                                            row_anchor, dialect)
            continue
        # An over-width item wrapping a breakable CASE (sample 21) breaks AT the
        # CASE; expr.py returns the body Lines (or None if the item isn't that
        # shape, in which case we fall through to the plain one-line path). The
        # wrapped CASE is not a select-list ROOT, so `_guard` would decline it
        # unless it is in `_nested_break_roots` below.
        break_body = nested_break_body(item, anchor, dialect, width)
        if break_body is not None:
            lines += _multiline_item_lines(item, head_seg, scope, break_body, row_anchor,
                                           dialect)
            continue
        target = item.this if isinstance(item, exp.Alias) else item
        expr_text = render_expr(target, dialect)
        # Comment engine annotations (comments.py): a LEADING comment is a plain
        # Seg before the expression: part of the content the resolver measures
        # for the `AS` column (so #12's widest, comment-bearing item sets it); a
        # TRAILING comment is appended AFTER the alias, UNTAGGED: one space
        # after the content, never in an alignment column (spec §3.3).
        lead = item.meta.get("sqlalign_lead")
        trail = item.meta.get("sqlalign_trail")
        segs = [head_seg]
        if lead is not None and ("\n" in lead or lead.startswith("--")):
            # A line comment runs to end of line, so anything after it on that
            # line is inside it: emitted inline the way a block comment is, it
            # would swallow the item it annotates. It goes at the end of the
            # previous row, which is both where the author wrote it and the only
            # idempotent placement: on its own line it re-parses as trailing the
            # row above.
            if lines:
                lines[-1].segs.append(comment_seg(lead))
            else:
                # Nothing precedes the first item, so the comment takes the row
                # above SELECT: at the statement's own column, not the item
                # column, which would leave it floating in mid-air.
                lines.append(Line(anchor, [comment_seg(lead)]))
            lead = None
        if lead is not None:
            segs.append(comment_seg(lead))
        segs.append(Seg(expr_text))
        if isinstance(item, exp.Alias):
            segs.append(Seg("AS " + column_alias(item, dialect), scope=scope, kind="as"))
        if trail is not None:
            segs.append(comment_seg(trail))
        lines.append(Line(row_anchor, segs))
    return lines


def _multiline_item_lines(item, head_seg, scope, body, row_anchor, dialect):
    """Splice a CASE/window item's own `body` (from `case_lines`/
    `window_lines`, both `list[Line]` with line 0's `indent` already set to
    this select's own `anchor` -- see either module's docstring) into a
    select-list row: `head_seg` (the `SELECT`/leading-comma lead-in) fronts
    line 0 exactly like a plain item's expression segment, and -- if
    aliased -- `AS alias` lands on the body's LAST line, tagged into this
    select's own `as` scope, exactly like a scalar-subquery item's alias
    (`_scalar_subquery_lines`) or any plain item's.
    """
    body[0].segs.insert(0, head_seg)
    body[0].indent = row_anchor
    if isinstance(item, exp.Alias):
        body[-1].segs.append(Seg("AS " + column_alias(item, dialect), scope=scope, kind="as"))
    return body


def _scalar_subquery_lines(item, subquery, head_seg, anchor, scope, dialect, width,
                           row_anchor):
    """A select-list item whose value is a scalar subquery (`item.this` when
    `item` is `exp.Alias`, else `item` itself). `(` opens exactly at the item
    column every other item's expression starts at (`anchor + len("SELECT ")`
    -- `head_seg`'s own text is 6 chars either way, `"SELECT"` or the leading
    comma, plus render()'s one automatic separator space = 7); the body
    recurses there via subquery.py's `subquery_body`, which also glues the
    closing `)` onto the last body line (spec: subqueries in a select list
    close inline). If aliased, `AS alias` is tagged into THIS select's `as`
    scope on that same last line, exactly like a non-subquery item's alias.
    """
    guard_subquery(subquery, allow_alias=False)
    inner_anchor = select_item_col(anchor) + 1
    open_segs, trailing = subquery_body(subquery.this, dialect, width, inner_anchor)
    segs = [head_seg, *open_segs]
    if isinstance(item, exp.Alias):
        as_seg = Seg("AS " + column_alias(item, dialect), scope=scope, kind="as")
        (trailing[-1].segs if trailing else segs).append(as_seg)
    return [Line(row_anchor, segs), *trailing]


def _distinct_text(select, dialect):
    """`"DISTINCT"` / `"DISTINCT ON (a, b)"` for `select`, or None.

    It rides the SELECT head rather than getting a line of its own, which is
    also where T-SQL's TOP goes. The continuation commas do NOT move to follow
    it: the comma column is a fixed house constant, not derived from wherever
    the first item happens to start (the ruling `TOP` already established).

        SELECT DISTINCT ON (cust.id) cust.id
             , cust.email
        FROM customers cust
    """
    distinct = select.args.get("distinct")
    if distinct is None:
        return None
    guard_args(distinct, {"on"}, "DISTINCT")
    on = distinct.args.get("on")
    if on is None:
        return "DISTINCT"
    if not isinstance(on, exp.Tuple):
        raise Unsupported("DISTINCT ON: unexpected form")
    return "DISTINCT ON (" + ", ".join(render_expr(e, dialect) for e in on.expressions) + ")"


def _from_lines(frm, anchor, dialect, tag, width=None):
    # Every branch below routes through `attach_comments`, because a branch that
    # forgets to does not decline: it DROPS the comment, which `ast_equal`
    # cannot see. The derived-table branch did exactly that.
    kw_anchor, _ = clause_head("FROM", anchor)
    return attach_comments(_from_body(frm, anchor, dialect, tag, width),
                           frm.this, kw_anchor)


def _from_body(frm, anchor, dialect, tag, width):
    if isinstance(frm.this, exp.Values):
        # `FROM (VALUES (1, 2), (3, 4)) AS v(a, b)`: an inline lookup table.
        # It stays on one line: the rows are a literal list, not a query with
        # clauses, so there is no body to recurse and nothing to align against.
        kw_anchor, kw_text = clause_head("FROM", anchor)
        return [Line(kw_anchor, [Seg(kw_text), Seg(render_expr(frm.this, dialect))])]
    if isinstance(frm.this, exp.Subquery):       # FROM (SELECT ...) d
        from sqlalign.layout.subquery import derived_from_lines
        return derived_from_lines(frm, anchor, dialect, width)
    table = frm.this                             # exp.Table (enforced by _guard)
    kw_anchor, kw_text = clause_head("FROM", anchor)
    segs = [Seg(kw_text), Seg(table_name(table, dialect), scope=f"from@{tag}", kind="table")]
    alias = table_alias(table, dialect)
    if alias is not None:
        segs.append(Seg(alias, scope=f"from@{tag}", kind="alias"))
    return [Line(kw_anchor, segs)]


def _guard(select, anchor, dialect, width):
    for name, value in select.args.items():
        if value in (None, [], False):
            continue
        if name not in _ALLOWED_ARGS:
            raise Unsupported(f"select arg: {name}")

    frm = select.args.get("from_")
    # A derived table (`FROM (SELECT ...) d`) is laid out by subquery.py, the
    # same shape a derived-table JOIN already had; anything else in the FROM
    # position is still unmodelled.
    if frm is not None and not isinstance(frm.this, (exp.Table, exp.Subquery, exp.Values)):
        raise Unsupported("from: non-table")

    into = select.args.get("into")
    if into is not None:
        # Only a bare-variable/table target is modeled. `INTO TEMPORARY`/`UNLOGGED`
        # (a CREATE-TABLE-AS spelling with a different meaning) is declined so it is
        # never silently mis-rendered as a plpgsql assignment.
        if into.args.get("temporary") or into.args.get("unlogged"):
            raise Unsupported("select into: temporary/unlogged")
        if not isinstance(into.this, (exp.Table, exp.Column, exp.Identifier)):
            raise Unsupported("select into: non-simple target")
        # Only a single target is rendered; decline multi-target `INTO x, y`
        # explicitly rather than silently dropping the extras (the safety net
        # would passthrough anyway, but an explicit decline is the house rule).
        if into.args.get("expressions"):
            raise Unsupported("select into: multiple targets")

    exempt = _subquery_roots(select)
    case_window_exempt = _case_window_roots(select)
    # A CASE wrapped inside an over-width select item (sample 21) is broken by
    # expr.py; like the Case/Window roots it is exempted from the blanket
    # `_UNSUPPORTED_NODES` rejection but NOT pruned: its WHEN/THEN contents are
    # still validated below (expr.py, like case.py, does not re-`_guard` them).
    nested_break_exempt = {
        id(case_node)
        for item in select.expressions
        if (case_node := nested_break_case(item, anchor, dialect, width)) is not None
    }

    def prune(node):
        # Stop descending at a recognized subquery attachment point: its
        # inner Select/SetOperation gets its OWN independent `_guard` call
        # when layout_statement recurses into it (select.py's
        # _scalar_subquery_lines, fromjoin.py's derived_table_lines,
        # conditions.py's _render_in_subquery/_render_exists), so re-walking
        # it here would both be redundant and wrongly reject its own
        # Select/Subquery node against _UNSUPPORTED_NODES below. Case/Window
        # roots (`case_window_exempt`) are deliberately NOT pruned here --
        # see the module docstring: their own contents still need the
        # comment/nested-unsupported check below since case.py/window.py
        # never independently re-`_guard` them.
        return id(node) in exempt

    for node in select.walk(prune=prune):
        # Comment check FIRST: before the `node is select` skip — so a comment
        # attached to the Select node itself (e.g. `SELECT /* c */ a`) is not
        # silently dropped but forces a passthrough like any other embedded comment.
        if node.comments:
            raise Unsupported("embedded comment")
        if node is select:
            continue
        if id(node) in exempt or id(node) in case_window_exempt or id(node) in nested_break_exempt:
            continue
        if isinstance(node, _UNSUPPORTED_NODES):
            raise Unsupported(type(node).__name__)
        if isinstance(node, exp.Pivot) and not render_expr(node, dialect).strip():
            # PIVOT/UNPIVOT declined for years on the grounds that `exp.Pivot`
            # carries fifteen args and the syntax diverges across dialects. True,
            # and beside the point: nothing here has to REBUILD it. It hangs off
            # a table's `pivots` arg, so `table_name` renders it with the table
            # and sqlglot spells each dialect's own form.
            #
            # What does have to be checked is that the dialect HAS the form.
            # sqlglot's postgres generator drops a pivot silently: PIVOT is not
            # Postgres syntax, and `SELECT * FROM t PIVOT(...)` comes back as
            # `SELECT * FROM t`, the entire clause gone, with only the re-parse
            # guard between that and shipping. Detected by rendering rather than
            # by a hardcoded dialect list, so this stops declining by itself if
            # sqlglot ever grows the support.
            raise Unsupported("PIVOT: this dialect has no such syntax")



def _subquery_roots(select):
    """id()s of the `exp.Subquery`/`exp.Select` nodes that anchor one of
    Task 10's three recognized subquery shapes: a scalar subquery in the
    select list, a derived-table JOIN, or `IN`/`EXISTS (SELECT ...)` inside
    WHERE/HAVING (searched with a plain `.walk()`, not just the top
    conjunction level, so one nested inside a parenthesized group is found
    too -- conditions.py's `_render_condition` handles that generically).
    `_guard` prunes its own walk at exactly these ids; layout downstream
    (this module, fromjoin.py, conditions.py) declines: via `Unsupported`
   , anything within them this task does not model, so under-collecting
    here is safe (falls back to the blanket Subquery/Select rejection
    below); the risk is only in OVER-collecting a shape nothing downstream
    actually knows how to render.
    """
    roots = set()
    for item in select.expressions:
        target = item.this if isinstance(item, exp.Alias) else item
        if isinstance(target, exp.Subquery):
            roots.add(id(target))
    for join in select.args.get("joins") or []:
        # A derived table, directly or wrapped in a LATERAL: both get
        # subquery.py's derived-table geometry, so both are roots.
        inner = join.this.this if isinstance(join.this, exp.Lateral) else join.this
        if isinstance(inner, exp.Subquery):
            roots.add(id(inner))
    for clause_name in ("where", "having"):
        clause = select.args.get(clause_name)
        if clause is None:
            continue
        for node in clause.walk():
            if isinstance(node, exp.In) and node.args.get("query") is not None:
                roots.add(id(node.args["query"]))
            elif isinstance(node, exp.Exists):
                roots.add(id(node.this))
    return roots


def _case_window_roots(select):
    """id()s of the `exp.Case`/`exp.Window` nodes that are a select-list
    item's own value (optionally under an `exp.Alias`), Task 11's third
    exemption from the blanket `_UNSUPPORTED_NODES` check: see the module
    docstring for why, unlike `_subquery_roots`, `_guard` does NOT prune
    into these. A Case/Window buried inside some OTHER expression (e.g.
    `COALESCE(SUM(CASE ...))`, sample 21) is not a "root" and is not
    collected here, so it still hits the blanket rejection below,
    unchanged from pre-Task-11 behavior.
    """
    roots = set()
    for item in select.expressions:
        target = item.this if isinstance(item, exp.Alias) else item
        if isinstance(target, (exp.Case, exp.Window)):
            roots.add(id(target))
    # A named window in the `WINDOW w AS (...)` clause is a fourth root: it is
    # not a select-list item at all, but it IS laid out (by
    # `window.named_window_lines`) rather than being an unmodelled node hiding
    # somewhere in an expression. `SUM(x) OVER w`, the REFERENCE to it -- is
    # itself an exp.Window whose spec is the bare name, and is a select-list
    # root already collected above.
    for win in select.args.get("windows") or []:
        roots.add(id(win))
    return roots
