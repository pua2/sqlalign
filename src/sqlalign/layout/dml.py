"""DML layout: INSERT, UPDATE, DELETE, MERGE.

Each statement's clauses start at the anchor column. INSERT/MERGE column and
VALUES lists use the house paren-list shape (`(  a` / ` , b`, items at
anchor+3, commas at anchor+1). UPDATE/MERGE `SET` assignments right-align `=`
(`kind="op"`) exactly like a WHERE comparison, with the assignment target at
anchor+4 (after `SET `) and leading commas at anchor+2. WHERE and the MERGE
`ON` block reuse `conditions.condition_block`; the SELECT inside an INSERT
reuses `layout_statement`.

Anything outside the modeled shape (ON CONFLICT, RETURNING, UPDATE...FROM,
DELETE...USING, a conditional `WHEN MATCHED AND ...`, ...) raises `Unsupported`
-> byte-identical passthrough, so the tool never half-renders a DML statement.
"""
from sqlglot import exp

from sqlalign.casing import render_expr
from sqlalign.commas import COMMA_KIND
from sqlalign.ir import Line, Seg
from sqlalign.layout import (
    Unsupported,
    clause_head,
    comma_clause,
    guard_args,
    layout_statement,
    table_alias,
    table_name,
)
from sqlalign.layout.conditions import condition_block

_INSERT_OK = {"this", "expression", "conflict", "returning", "default"}
_UPDATE_OK = {"this", "expressions", "where", "from_", "returning"}
_DELETE_OK = {"this", "where", "using", "returning"}
_MERGE_OK = {"this", "using", "on", "whens"}


def dml_lines(node, dialect, width, anchor=0):
    if isinstance(node, exp.Insert):
        return _insert_lines(node, dialect, width, anchor)
    if isinstance(node, exp.Update):
        return _update_lines(node, dialect, width, anchor)
    if isinstance(node, exp.Delete):
        return _delete_lines(node, dialect, width, anchor)
    if isinstance(node, exp.Merge):
        return _merge_lines(node, dialect, width, anchor)
    raise Unsupported(type(node).__name__)


def values_lines(node, dialect, anchor):
    """An INSERT's `VALUES` rows: one tuple per line, stacked with leading commas
    two columns before the tuple: the same geometry the select list uses, and
    `VALUES ` happens to be the same width as `SELECT `, so the commas land in
    the house column without any special case.

        VALUES (1, 'a')
             , (2, 'b');

    A single row stays inline. The tuples themselves are rendered as-is; their
    contents are data, not a structure this layer models, so nothing inside a
    tuple is aligned across rows.
    """
    guard_args(node, {"expressions"}, "VALUES")
    rows = node.expressions
    if not rows or not all(isinstance(r, exp.Tuple) for r in rows):
        raise Unsupported("VALUES: unexpected row shape")
    return comma_clause("VALUES", [render_expr(r, dialect) for r in rows], anchor)


def _verb_line(keyword, anchor, *rest):
    """A DML verb's own line. Goes through `clause_head` so `UPDATE`/`SET`/
    `WHERE` form a river together when one is asked for, instead of the verb
    sitting flush left while its WHERE right-aligns."""
    kw_anchor, kw_text = clause_head(keyword, anchor)
    return Line(kw_anchor, [Seg(kw_text), *rest])


def _table_with_alias(table, dialect):
    """`schema.name alias` — a table reference in UPDATE/DELETE/MERGE, with the
    alias spelled per `Style.table_alias_style`."""
    alias = table_alias(table, dialect)
    name = table_name(table, dialect)
    return name + " " + alias if alias is not None else name


def _paren_list(items_text, anchor, close_own_line):
    """House column/VALUES list: `(  first` / ` , next`, items at anchor+3.
    `close_own_line` False tucks `)` after the last item (INSERT columns);
    True drops `)` to its own line (a MERGE VALUES tuple)."""
    lines = []
    last = len(items_text) - 1
    for i, text in enumerate(items_text):
        # The head is its own Seg so the separator comma can be relocated by
        # commas.py; render()'s automatic inter-seg space supplies the gap, so
        # `Seg("( ")` + space = the house `(  ` and `Seg(" ,")` + space = ` , `.
        head = Seg("( ") if i == 0 else Seg(" ,", kind=COMMA_KIND)
        segs = [head, Seg(text)]
        if i == last and not close_own_line:
            segs[-1].text += ")"
        lines.append(Line(anchor, segs))
    if close_own_line:
        lines.append(Line(anchor, [Seg(")")]))
    return lines


def _set_lines(eqs, anchor, scope, dialect):
    """`SET target = value` assignments: target at anchor+4, leading commas at
    anchor+2, `=` right-aligned across every assignment (`kind="op"`)."""
    kw_anchor, kw_text = clause_head("SET", anchor)
    # The comma keeps sitting two columns before the target, wherever SET put it.
    comma_lead = " " * (kw_anchor + len(kw_text) + 1 - anchor - 2)
    lines = []
    for i, eq in enumerate(eqs):
        head = Seg(kw_text) if i == 0 else Seg(comma_lead + ",", kind=COMMA_KIND)
        lines.append(Line(kw_anchor if i == 0 else anchor, [
            head,
            Seg(render_expr(eq.this, dialect)),
            Seg("=", scope=scope, kind="op"),
            Seg(render_expr(eq.expression, dialect)),
        ]))
    return lines


def _insert_lines(node, dialect, width, anchor):
    guard_args(node, _INSERT_OK)
    this = node.this
    if isinstance(this, exp.Schema):
        table, cols = this.this, this.expressions
    else:
        table, cols = this, []
    lines = [_verb_line("INSERT INTO", anchor, Seg(render_expr(table, dialect)))]
    if cols:
        lines += _paren_list([render_expr(c, dialect) for c in cols], anchor, close_own_line=False)
    # `INSERT INTO t DEFAULT VALUES`: one row, every column its default. There
    # is no body at all, which is why the body check below rejected it.
    if node.args.get("default"):
        lines.append(Line(anchor, [Seg("DEFAULT VALUES")]))
        return lines + _returning_lines(node, dialect, anchor)
    if _returning_first(dialect):
        lines += _returning_lines(node, dialect, anchor)
    body = node.expression
    if body is None or not isinstance(body, (exp.Select, exp.SetOperation, exp.Values)):
        raise Unsupported("INSERT body")
    lines += layout_statement(body, dialect, width, anchor)
    conflict = node.args.get("conflict")
    if conflict is not None:
        lines += _conflict_lines(conflict, dialect, anchor)
    if _returning_first(dialect):
        return lines
    return lines + _returning_lines(node, dialect, anchor)


def _conflict_lines(conflict, dialect, anchor):
    """`ON CONFLICT ... DO NOTHING` / `... DO UPDATE SET ...` (Postgres upsert).

        ON CONFLICT (customer_id) DO UPDATE
        SET email      = EXCLUDED.email
          , updated_at = NOW()

    The assignments reuse the UPDATE geometry: the same `SET`/leading-comma
    stack with the `=` column aligned — because they are the same construct in
    the same statement, and having them differ would be a wart a reader notices
    immediately.
    """
    guard_args(conflict, {"action", "conflict_keys", "constraint", "expressions", "where"},
               "ON CONFLICT")
    target = ""
    if conflict.args.get("constraint") is not None:
        target = f" ON CONSTRAINT {render_expr(conflict.args['constraint'], dialect)}"
    elif conflict.args.get("conflict_keys"):
        keys = ", ".join(render_expr(k, dialect) for k in conflict.args["conflict_keys"])
        target = f" ({keys})"

    action = conflict.args.get("action")
    if action is None:
        raise Unsupported("ON CONFLICT: no action")
    action_text = render_expr(action, dialect).upper()

    head = f"ON CONFLICT{target} {action_text}"
    lines = [Line(anchor, [Seg(head)])]
    assignments = conflict.args.get("expressions")
    if assignments:
        lines += _set_lines(assignments, anchor, f"conflict@{id(conflict)}", dialect)
    where = conflict.args.get("where")
    if where is not None:
        lines += condition_block("WHERE", where.this, f"conflictwhere@{id(conflict)}",
                                 anchor, dialect, None)
    return lines


def _returning_lines(node, dialect, anchor):
    """`RETURNING a, b` / `RETURNING *`, or T-SQL's `OUTPUT inserted.a`.

    Rendered whole by sqlglot rather than rebuilt from its expressions, so `*`
    and a column list spell themselves the same way they do anywhere else.

    The two spellings share the `returning` arg but NOT their position, which is
    why every caller asks `_returning_first` where to put it: Postgres closes the
    statement with RETURNING, while T-SQL's OUTPUT comes BEFORE the body it
    describes. Emitting it last under T-SQL produced `INSERT INTO t (a) SELECT …
    OUTPUT inserted.a`, which SQL Server rejects, and `ast_equal` cannot see
    it, because sqlglot reads its own lenient output back without complaint.
    """
    returning = node.args.get("returning")
    if returning is None:
        return []
    return [Line(anchor, [Seg(render_expr(returning, dialect))])]


def _returning_first(dialect) -> bool:
    """Whether the returning clause precedes the statement body."""
    return dialect == "tsql"


def _update_lines(node, dialect, width, anchor):
    guard_args(node, _UPDATE_OK)
    lines = [_verb_line("UPDATE", anchor, Seg(_table_with_alias(node.this, dialect)))]
    lines += _set_lines(node.expressions, anchor, f"set@{id(node)}", dialect)
    if _returning_first(dialect):
        lines += _returning_lines(node, dialect, anchor)
    # `UPDATE t SET ... FROM u WHERE u.i = t.i`: Postgres's join-in-an-update,
    # the same shape DELETE's USING has. It sits between SET and the WHERE that
    # references it, which is also where it is written.
    from_ = node.args.get("from_")
    if from_ is not None:
        lines.append(Line(anchor, [Seg("FROM"),
                                   Seg(_table_with_alias(from_.this, dialect))]))
    where = node.args.get("where")
    if where is not None:
        lines += condition_block("WHERE", where.this, f"where@{id(node)}", anchor, dialect, width)
    if _returning_first(dialect):
        return lines
    return lines + _returning_lines(node, dialect, anchor)


def _delete_lines(node, dialect, width, anchor):
    guard_args(node, _DELETE_OK)
    lines = [_verb_line("DELETE FROM", anchor, Seg(_table_with_alias(node.this, dialect)))]
    # `DELETE FROM t USING u WHERE ...`: Postgres's join-in-a-delete. It is one
    # clause line of table references, the same shape MERGE's USING already had,
    # and it sits between the verb and the WHERE that references it.
    using = node.args.get("using")
    if using:
        refs = using if isinstance(using, list) else [using]
        lines.append(Line(anchor, [Seg("USING"),
                                   Seg(", ".join(_table_with_alias(r, dialect)
                                                 for r in refs))]))
    where = node.args.get("where")
    if where is not None:
        lines += condition_block("WHERE", where.this, f"where@{id(node)}", anchor, dialect, width)
    if _returning_first(dialect):
        return lines
    return lines + _returning_lines(node, dialect, anchor)


def _merge_lines(node, dialect, width, anchor):
    guard_args(node, _MERGE_OK)
    lines = [
        _verb_line("MERGE INTO", anchor, Seg(_table_with_alias(node.this, dialect))),
        Line(anchor, [Seg("USING"), Seg(_table_with_alias(node.args["using"], dialect))]),
    ]
    lines += condition_block("ON", node.args["on"], f"on@{id(node)}", anchor + 2, dialect, width)
    for wh in node.args["whens"].expressions:
        if wh.args.get("condition") is not None:
            raise Unsupported("MERGE conditional WHEN")
        keyword = "WHEN MATCHED" if wh.args.get("matched") else "WHEN NOT MATCHED"
        lines.append(Line(anchor, [Seg(keyword)]))
        then = wh.args.get("then")
        if isinstance(then, exp.Update):
            lines.append(Line(anchor, [Seg("THEN UPDATE")]))
            lines += _set_lines(then.expressions, anchor, f"mset@{id(wh)}", dialect)
        elif isinstance(then, exp.Insert):
            lines.append(Line(anchor, [Seg("THEN INSERT")]))
            cols = then.this.expressions if hasattr(then.this, "expressions") else []
            lines += _paren_list([render_expr(c, dialect) for c in cols],
                                 anchor, close_own_line=False)
            lines.append(Line(anchor, [Seg("VALUES")]))
            vals = then.expression.expressions
            lines += _paren_list([render_expr(v, dialect) for v in vals],
                                 anchor, close_own_line=True)
        elif isinstance(then, exp.Var) and str(then.this).upper() == "DELETE":
            # `WHEN MATCHED THEN DELETE`: the whole action is the keyword, and
            # sqlglot keeps it as a Var carrying the case it was written in, so
            # it needs casing here rather than a render (`THEN delete`).
            lines.append(Line(anchor, [Seg("THEN DELETE")]))
        else:
            raise Unsupported("MERGE THEN " + type(then).__name__)
    return lines
