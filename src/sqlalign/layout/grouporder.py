"""GROUP BY / ORDER BY / LIMIT / OFFSET geometry.

GROUP BY and ORDER BY share one comma-stacked shape: the first term rides the
keyword line, later terms hang under it with a leading comma two columns before
the term (the same relationship SELECT uses, but with the 9-wide "GROUP BY "/
"ORDER BY " keyword). A GROUP BY that is purely positional (`GROUP BY 1, 2`) or a
single term stays inline. LIMIT and OFFSET share one line (`LIMIT n OFFSET m`)
when both are present; each stays a single line on its own.

Split into `group_lines` and `order_limit_lines` (rather than one combined
function) so select.py can splice a HAVING block — via conditions.py's
`condition_block`: between GROUP BY and ORDER BY.
"""
import functools

import sqlglot
from sqlglot import exp

from sqlalign.casing import render_expr
from sqlalign.ir import Line, Seg
from sqlalign.layout import Unsupported, clause_head, comma_clause, guard_args


def _clause_line(keyword, tail, anchor):
    """`(indent, segs)` for a one-line clause, honouring the river."""
    kw_anchor, kw_text = clause_head(keyword, anchor)
    return kw_anchor, [Seg(kw_text), Seg(tail)]


@functools.cache
def _offset_rows(dialect: str) -> str:
    """` ROWS` where the dialect's OFFSET requires it, otherwise empty.

    `Offset` records only the number -- whether the author wrote `OFFSET 10` or
    `OFFSET 10 ROWS` is gone by the time the layout sees it. T-SQL requires the
    keyword, so omitting it emitted SQL Server rejects, and the AST check could
    not tell: both spellings parse to that same node.

    Asked of sqlglot's generator rather than tested against a dialect name, so a
    dialect whose grammar differs is followed.
    """
    rendered = sqlglot.parse_one(
        "SELECT a FROM t ORDER BY a OFFSET 1 ROWS FETCH NEXT 1 ROWS ONLY",
        dialect=dialect).sql(dialect).upper()
    return " ROWS" if "OFFSET 1 ROWS" in rendered else ""


def _fetch_tail(fetch, dialect):
    """Everything after `FETCH` in `FETCH FIRST n [PERCENT] ROWS {ONLY|WITH TIES}`.

    Rebuilt from the parsed parts rather than passed through verbatim, so it
    cases with the rest of the output. Every piece is a fixed keyword except the
    count, so there is nothing here sqlalign could get wrong that the re-parse
    check would not catch.
    """
    opts = fetch.args.get("limit_options")
    parts = [fetch.args.get("direction") or "FIRST"]
    count = fetch.args.get("count")
    if count is not None:
        parts.append(render_expr(count, dialect))
    if opts is not None and opts.args.get("percent"):
        parts.append("PERCENT")
    if opts is None or opts.args.get("rows"):
        parts.append("ROWS")
    parts.append("WITH TIES" if opts is not None and opts.args.get("with_ties") else "ONLY")
    return " ".join(parts)


def _comment_pairs(nodes):
    """`[(lead, trail)]` per node, from the comment engine's annotations.

    These clauses are laid out from rendered TEXT rather than from nodes, so the
    pairs have to be lifted off here and handed to `comma_clause`, which is
    exactly why comments in GROUP BY / ORDER BY declined until now.
    """
    return [(n.meta.get("sqlalign_lead"), n.meta.get("sqlalign_trail")) for n in nodes]


def group_lines(select, anchor, dialect):
    """GROUP BY lines for `select`, or [] if absent."""
    group = select.args.get("group")
    if group is None:
        return []
    guard_args(group, {"expressions", "grouping_sets", "cube", "rollup", "all"}, "GROUP BY")
    # `GROUP BY ALL`: group by every non-aggregated select item, without
    # naming them. A DuckDB/Snowflake/Databricks convenience that Redshift and
    # Postgres do not have; it is one keyword and the whole clause.
    if group.args.get("all"):
        kw_anchor, kw_text = clause_head("GROUP BY", anchor)
        return [Line(kw_anchor, [Seg(kw_text), Seg("ALL")])]
    exprs = group.expressions
    # GROUPING SETS / CUBE / ROLLUP are further terms of the same list, not
    # separate clauses. sqlglot emits them after the plain expressions and in
    # this order; reproducing that order matters because reordering the source
    # would be a rewrite, even where it is semantically inert.
    terms = list(exprs)
    for key in ("grouping_sets", "cube", "rollup"):
        terms += list(group.args.get(key) or [])
    texts = [render_expr(t, dialect) for t in terms]
    pairs = _comment_pairs(terms)
    positional = bool(exprs) and all(isinstance(e, exp.Literal) and e.is_int for e in exprs)
    if positional and len(texts) > 1:
        # A positional GROUP BY keeps every term on one line, so a comment
        # attached to a term has no row of its own to sit on. This engine
        # reproduces a comment faithfully or declines; it never guesses.
        # A SINGLE term is not this case: it is one term on one row, which
        # `comma_clause` places a comment against exactly as ORDER BY does.
        if any(c for pair in pairs for c in pair):
            raise Unsupported("comment: positional GROUP BY is one line")
        kw_anchor, kw_text = clause_head("GROUP BY", anchor)
        return [Line(kw_anchor, [Seg(kw_text), Seg(", ".join(texts))])]
    return comma_clause("GROUP BY", texts, anchor, pairs)


def order_limit_lines(select, anchor, dialect, width):
    """ORDER BY / LIMIT / OFFSET lines for `select`, in order."""
    lines = []
    order = select.args.get("order")
    if order is not None:
        texts = [render_expr(e, dialect) for e in order.expressions]
        lines += comma_clause("ORDER BY", texts, anchor,
                              _comment_pairs(order.expressions))
    # LIMIT and OFFSET share one line when both are present;
    # LIMIT-only and OFFSET-only stay a single line each. The `limit` arg holds
    # either an exp.Limit (`LIMIT n`) or an exp.Fetch (`FETCH FIRST n ROWS ONLY`,
    # the ANSI spelling of the same thing).
    limit = select.args.get("limit")
    offset = select.args.get("offset")
    segs = []
    if isinstance(limit, exp.Fetch):
        # A shop that writes FETCH writes it everywhere, so this is all-or-
        # nothing rather than an edge case. It keeps its own line: OFFSET
        # precedes it in the ANSI form (`OFFSET n ROWS FETCH NEXT m ROWS ONLY`)
        # rather than following it the way it follows LIMIT.
        if offset is not None:
            lines.append(Line(*_clause_line(
                "OFFSET", render_expr(offset.expression, dialect) + _offset_rows(dialect),
                anchor)))
            offset = None
        lines.append(Line(*_clause_line("FETCH", _fetch_tail(limit, dialect), anchor)))
        limit = None
    if limit is not None and dialect != "tsql":
        # T-SQL has no LIMIT: the row count is `SELECT TOP n`, emitted by
        # select.py into the SELECT head instead. Emitting LIMIT here produced
        # SQL that SQL Server rejects, and the AST safety net could not see it
        # (sqlglot parses both spellings to the same Limit node): see
        # docs/.../2026-08-11-tsql-findings.md.
        segs += [Seg("LIMIT"), Seg(render_expr(limit.expression, dialect))]
    if offset is not None:
        # Same requirement as the FETCH branch above: T-SQL's OFFSET needs ROWS
        # whether or not a FETCH follows it.
        segs += [Seg("OFFSET"),
                 Seg(render_expr(offset.expression, dialect) + _offset_rows(dialect))]
    if segs:
        kw_anchor, kw_text = clause_head(segs[0].text, anchor)
        segs[0] = Seg(kw_text)
        lines.append(Line(kw_anchor, segs))
    return lines


