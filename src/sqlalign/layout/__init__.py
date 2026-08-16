"""Layout dispatch.

`layout_statement` is the single entry point every later task extends: it maps a
parsed sqlglot expression to a list of `Line`s (the alignment resolver then turns
those into text). Node types this layer does not yet lay out raise `Unsupported`,
which the formatter catches and turns into a byte-identical passthrough + warning.

Dispatch order matters: a `with_` arg (a CTE-bearing statement) can sit on either
a Select or a SetOperation (Union/Intersect/Except), so that check runs first and
routes to layout/cte.py, which strips `with_` and recurses back into this same
function for the bare query underneath. SetOperation is checked before Select
since Union et al. are not Select subclasses.

`Unsupported` and `guard_args` (the arg-level form of the same decline, shared by
ddl.py and dml.py) live here because declining is the contract every layout
module is written against, not a detail of any one of them.
"""
from sqlglot import exp

from sqlalign.casing import active_style, render_expr
from sqlalign.commas import COMMA_KIND
from sqlalign.ir import Line, Seg, comment_seg


class Unsupported(Exception):
    """Raised for any construct this layout layer cannot render byte-exactly.

    Always caught by the formatter, which then passes the statement through
    verbatim. Raising it is therefore the safe way to *decline* a construct;
    never emit a partial/guessed layout for something we do not fully handle.
    """


def guard_args(node: exp.Expression, allowed: set[str], label: str | None = None) -> None:
    """Decline `node` if it carries any arg outside `allowed`.

    A statement's shape is modeled arg by arg, so an arg we do not read (INSERT
    ... ON CONFLICT, UPDATE ... FROM, a CREATE TABLE option, ...) would be
    silently dropped from the output. Raising instead turns it into a
    byte-identical passthrough. An arg that is present but empty (None/[]/False)
    carries nothing, so it never trips the guard. `label` names the construct in
    the message where the node type alone is too coarse (`CREATE TABLE` and
    `CREATE PROCEDURE` are both `Create`).
    """
    for name, value in node.args.items():
        if value not in (None, [], False) and name not in allowed:
            raise Unsupported(f"{label or type(node).__name__} arg: {name}")


def layout_statement(node: exp.Expression, dialect: str, width, anchor: int = 0) -> list[Line]:
    from sqlalign.layout import cte as _cte
    from sqlalign.layout import ddl as _ddl
    from sqlalign.layout import dml as _dml
    from sqlalign.layout import select as _select
    from sqlalign.layout import setop as _setop

    if node.args.get("with_") is not None:
        return _cte.layout_with(node, dialect, width)
    if isinstance(node, (exp.Create, exp.TruncateTable, exp.Grant, exp.Alter,
                         exp.Drop, exp.Comment, exp.Declare, exp.Copy)):
        return _ddl.ddl_lines(node, dialect, width, anchor)
    # A GRANT sqlglot could only parse as an unsupported-syntax Command
    # (e.g. GRANT ... ON ALL TABLES IN SCHEMA ...) still gets keyword casing.
    if isinstance(node, exp.Command) and str(node.this).upper() == "GRANT":
        return _ddl.ddl_lines(node, dialect, width, anchor)
    if isinstance(node, exp.Values):
        return _dml.values_lines(node, dialect, anchor)
    if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
        return _dml.dml_lines(node, dialect, width, anchor)
    if isinstance(node, exp.SetOperation):
        return _setop.layout_setop(node, dialect, width, anchor)
    if isinstance(node, exp.Select):
        return _select.layout_select(node, dialect, width, anchor)
    raise Unsupported(type(node).__name__)


def join_keyword(join: exp.Join) -> str:
    """The join keyword rebuilt from `method`/`side`/`kind` ("NATURAL", "LEFT",
    "INNER", "LEFT OUTER"), ordered the way sqlglot's own generator orders it:
    method, side, kind, "JOIN" -- so `NATURAL LEFT JOIN` comes out in that order
    (verified empirically against pinned sqlglot v30.14).

    Lives here rather than in fromjoin.py because subquery.py needs it too and
    fromjoin.py already imports FROM subquery.py: a shared home is what breaks
    that cycle, and it previously cost a duplicated copy in each module.
    """
    parts = [join.args.get("method"), join.args.get("side"), join.args.get("kind"), "JOIN"]
    return " ".join(p for p in parts if p)


COMMA_ROW = object()          # sentinel: this row is the legacy comma form


def row_keyword(join):
    """The keyword that opens a join's row, or `COMMA_ROW` for the legacy form.

    `FROM a, b` parses as a join with no condition and no kind: structurally
    identical to `CROSS JOIN`, so rebuilding it through `join_keyword` would
    emit `FROM a JOIN b`, which Postgres rejects. sqlglot prints the comma form
    instead, and so do we: it is the author's own syntax, and the alternative is
    invalid SQL the re-parse check cannot catch (sqlglot reads its own lenient
    output back happily).

    `FROM a JOIN b` with no condition is itself a syntax error, so nothing that
    parses this way in practice is anything BUT the comma form.
    """
    if (join.args.get("on") is None and not join.args.get("using")
            and not join.args.get("kind") and not join.args.get("method")):
        return COMMA_ROW
    return join_keyword(join)



def table_name(table: exp.Expression, dialect: str) -> str:
    """A table reference rendered WITHOUT its alias, which the callers position
    in their own alignment column. Shared by select.py and fromjoin.py for the
    same cycle-breaking reason as `join_keyword`."""
    bare = table.copy()
    bare.set("alias", None)
    return render_expr(bare, dialect)


def comma_clause(keyword, texts, anchor, comments=None):
    """Keyword + comma-stacked terms, one per line with a leading comma two
    columns before the term; a single term stays inline.

    Shared by grouporder.py (GROUP BY / ORDER BY) and dml.py (an INSERT's VALUES
    rows), which is why it lives here rather than in either: the same reason
    `join_keyword` and `table_name` do.

    `comments` is an optional `[(lead, trail)]`, one pair per term, from the
    comment engine. It is passed rather than read off the nodes because this
    function is handed rendered TEXT: the terms arrive as strings, which is
    exactly why comments in these clauses declined until the pair came with
    them.
    """
    kw_anchor, kw_text = clause_head(keyword, anchor)
    pairs = list(comments or [(None, None)] * len(texts))
    # Nothing precedes the FIRST term inside this clause, so an off-row comment
    # leading it takes the row above the keyword. Without this it rode the
    # keyword row and a `--` swallowed the term after it.
    lines = []
    first_lead = pairs[0][0]
    if _off_row(first_lead):
        lines.append(Line(anchor, [comment_seg(first_lead)]))
        first_lead = None
    head = Line(kw_anchor, [Seg(kw_text), *_lead_segs(first_lead), Seg(texts[0])])
    if len(texts) <= 1:
        return _with_trail([*lines, head], pairs[0][1], kw_anchor, first_lead)
    # The comma sits two columns before the term, wherever the keyword put it.
    term_col = kw_anchor + len(kw_text) + 1
    lead = " " * (term_col - anchor - 2)
    lines.append(head)
    lines = _with_trail(lines, pairs[0][1], kw_anchor, first_lead)
    for t, (item_lead, item_trail) in zip(texts[1:], pairs[1:], strict=False):
        # A `--` or multi-line comment cannot share a row with what follows it,
        # so it takes the end of the row above: the same rule select.py and
        # conditions.py use, and the only idempotent one.
        if _off_row(item_lead):
            lines[-1].segs.append(comment_seg(item_lead))
            item_lead = None
        # Comma as its own Seg (relocatable by commas.py); render's inter-seg
        # space reproduces the house `lead + ", " + term` exactly.
        lines.append(Line(anchor, [Seg(lead + ",", kind=COMMA_KIND),
                                   *_lead_segs(item_lead), Seg(t)]))
        lines = _with_trail(lines, item_trail, anchor, item_lead)
    return lines


def attach_comments(lines, node, anchor, before=None):
    """Apply `node`'s comment-engine annotations to the rows just emitted.

    `lines` are this row's own lines; `before` is whatever precedes them, so an
    off-row comment can take the end of the row above: the placement every
    other site uses, and the only idempotent one. With nothing above, it takes a
    row of its own at `anchor`.
    """
    lead = node.meta.get("sqlalign_lead")
    trail = node.meta.get("sqlalign_trail")
    out = list(lines)
    if lead is not None:
        if _off_row(lead):
            if before:
                before[-1].segs.append(comment_seg(lead))
            else:
                out.insert(0, Line(anchor, [comment_seg(lead)]))
        else:
            out[0].segs.insert(1, comment_seg(lead))
    if trail is not None:
        out[-1].segs.append(comment_seg(trail))
    return out


def _off_row(comment) -> bool:
    """Whether `comment` must take a row of its own rather than ride one."""
    return comment is not None and ("\n" in comment or comment.startswith("--"))


def _lead_segs(comment):
    return [comment_seg(comment)] if comment is not None else []


def _with_trail(lines, trail, anchor, lead_used):
    """Append a trailing comment to the row just emitted."""
    if trail is not None:
        lines[-1].segs.append(comment_seg(trail))
    return lines


def clause_head(keyword: str, anchor: int, *, hang: bool = False) -> tuple[int, str]:
    """`(line indent, keyword text)` for a ROOT clause keyword.

    Left-aligned (house) this is just `(anchor, keyword)`. In a river the
    keyword is right-aligned so its last character lands on the gutter, which
    puts every clause body in one column:

        SELECT a.foo
          FROM apple a
         WHERE a.id > 0

    Two things do not fit that, and they are not the same case:

    - A **JOIN** goes on the far side of the river, at `gutter + 1`, whatever its
      width (`hang=True`). This is Holywell's own documented rule, and it is
      about joins rather than about long keywords: a bare 4-character `JOIN`
      sitting in the gutter beside a hanging `LEFT JOIN` would read as a
      different clause.
    - An over-long **root** keyword: `GROUP BY` and `ORDER BY` are 8 against a
      gutter of 6: stays at the margin and overhangs the gutter. Sending those
      to the far side too would indent them past `WHERE`, which reads as though
      they were subordinate to it. They are not; they are root clauses, and no
      river guide prints them any other way.

    The gutter is FIXED rather than resolved from the widest keyword present.
    That matters: every clause handler computes its body column at layout time,
    so a resolver-determined keyword width would leave those columns stale --
    the detachment bug subquery.py's docstring records. A constant keeps the
    whole thing layout-time knowable.
    """
    style = active_style()
    if style.clause_keyword_align != "river":
        return anchor, keyword
    gutter = style.river_gutter
    if hang:
        return anchor + gutter + 1, keyword
    if len(keyword) > gutter:
        return anchor, keyword          # overhangs the gutter, stays a root clause
    return anchor, keyword.rjust(gutter)


def select_item_col(anchor: int) -> int:
    """The column every select item's own content starts at.

    Shared by case.py, window.py, expr.py and select.py, which all need the column
a select-list item starts at.
"""
    style = active_style()
    if style.select_placement == "own_line":
        return anchor + style.select_indent
    if style.clause_keyword_align == "river":
        return anchor + style.river_gutter + 1
    return anchor + len("SELECT ")


def column_alias(item: exp.Alias, dialect: str) -> str:
    """`item`'s alias as it should be PRINTED, quoting included.

    Same bug `table_alias` documents, one node type over and found much later:
    sqlglot's `.alias` returns the identifier's NAME, so `AS "Total Revenue"`
    came out as bare `AS Total Revenue`. Every quoted column alias: ordinary
    reporting SQL: was rejected by the re-parse guard and passed through
    untouched, which is a silent decline behind the safety net where the house
    rule is to decline explicitly or not at all.

    Quoting is not cosmetic here: unquoted, `Total Revenue` is a syntax error,
    and `"b"` and `b` are different columns in Postgres. The guard was right to
    reject the output; the renderer was the thing that was wrong.
    """
    return render_expr(item.args["alias"], dialect)


def table_alias(table: exp.Expression, dialect: str) -> str | None:
    """`table`'s alias as it should be PRINTED, or None if it has none.

    `FROM t AS a` and `FROM t a` parse to an identical AST, so which spelling
    comes out is sqlalign's decision rather than the author's -- see
    `Style.table_alias_style`. Every site that prints a table alias goes through
    here so the two spellings cannot drift apart between FROM, JOIN, a derived
    table, and the DML verbs.

    Rendered through `render_expr` rather than read off `.name`, which returns
    the identifier UNQUOTED: `FROM a "My Alias"` came out as bare `My Alias`, and
    the only thing that caught it was the re-parse guard noticing the quoting had
    changed: a silent decline behind the safety net, where the house rule is to
    decline explicitly or not at all. Rendering keeps the quotes, so those
    statements now format instead of passing through.

    Returning the `AS` inside the string (rather than as its own segment) keeps
    the alias column aligning on whatever actually starts the alias, which is
    what a reader tracks down the block.
    """
    alias = table.args.get("alias")
    if alias is None:
        return None
    if isinstance(alias, exp.TableAlias) and alias.this is None:
        return None
    # The whole TableAlias, not just its identifier, so `AS g(n)` keeps its
    # alias list, which `generate_series(1, 10) AS g(n)` and
    # `(VALUES (1, 2)) AS v(a, b)` both need.
    name = render_expr(alias, dialect) if isinstance(alias, exp.TableAlias) else \
        render_expr(alias.this, dialect)
    return f"AS {name}" if active_style().table_alias_style == "as" else name
