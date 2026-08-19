"""DDL layout: CREATE TABLE / CTAS / VIEW / MATERIALIZED VIEW,
TRUNCATE, CREATE INDEX, GRANT.

CREATE TABLE columns align name -> type -> constraints. Postgres keeps every
constraint (NOT NULL / DEFAULT) in one column after the type; Redshift, which
can carry both a nullability and an ENCODE on one column, splits them into a
nullability column and an ENCODE column (per-kind alignment, ). All
column alignment is resolver-tagged (the fixpoint pass composes the dependent
name/type/constraint columns); no manual padding.

CTAS / CREATE [MATERIALIZED] VIEW render a `CREATE ... AS` header line then
reuse `layout_statement` for the SELECT body. TRUNCATE / CREATE INDEX / GRANT
are utility one-liners (INDEX breaks its partial `WHERE` when over width;
TRUNCATE drops its RESTART/CASCADE options to a second line).
"""
import functools

import sqlglot
from sqlglot import exp

from sqlalign.casing import render_expr
from sqlalign.commas import COMMA_KIND
from sqlalign.ir import Line, Seg
from sqlalign.layout import Unsupported, guard_args, layout_statement

_CREATE_TABLE_OK = {"this", "kind", "expression", "properties", "replace", "exists"}
_ALTER_OK = {"this", "kind", "actions", "exists", "only"}


def ddl_lines(node, dialect, width, anchor=0):
    if isinstance(node, exp.Create):
        return _create_lines(node, dialect, width, anchor)
    if isinstance(node, exp.TruncateTable):
        return _truncate_lines(node, dialect, anchor)
    if isinstance(node, (exp.Grant, exp.Revoke, exp.Command)):
        return _grant_lines(node, dialect, anchor)
    if isinstance(node, exp.Alter):
        return _alter_lines(node, dialect, anchor)
    if isinstance(node, exp.Drop):
        return _one_line(node, dialect, anchor)
    if isinstance(node, exp.Comment):
        return _comment_lines(node, dialect, anchor)
    if isinstance(node, exp.Declare):
        return _declare_lines(node, dialect, anchor)
    if isinstance(node, exp.Copy):
        # Redshift's bulk loader. One line, spelled by sqlglot, which uppercases
        # the structural keywords. Its options (`csv gzip ignoreheader 1`) are
        # bare Vars keeping the case the author wrote: casing them would need a
        # `_CASEFOLD_VAR_ARGS` entry, which makes the AST check blind to that
        # difference over a vocabulary far wider than this models.
        return _one_line(node, dialect, anchor)
    raise Unsupported(type(node).__name__)


# ---- CREATE dispatch -------------------------------------------------------

def _create_lines(node, dialect, width, anchor):
    kind = (node.args.get("kind") or "").upper()
    if kind == "INDEX":
        return _create_index_lines(node, dialect, width, anchor)
    if kind == "VIEW":
        return _create_as_lines(node, dialect, width, anchor)
    if kind == "TABLE":
        if node.args.get("expression") is not None:          # CREATE TABLE ... AS SELECT
            return _create_as_lines(node, dialect, width, anchor)
        return _create_table_lines(node, dialect, width, anchor)
    if kind == "PROCEDURE" and dialect == "tsql":
        return _create_procedure_lines(node, dialect, width, anchor)
    if kind == "SCHEMA":
        return _one_line(node, dialect, anchor)
    raise Unsupported(f"CREATE {kind}")


_PROCEDURE_OK = {"this", "kind", "expression", "begin"}


def _create_procedure_lines(node, dialect, width, anchor):
    """T-SQL `CREATE PROCEDURE name [params] AS BEGIN ... END`.

    Styled by analogy with a plpgsql `$$` body: the block's statements sit at column 1
    with no extra indent, separated by blank lines, between `BEGIN` and `END;`
    alone on their own lines. T-SQL's BEGIN/END is the structural counterpart of
    the dollar-quoted body, so it inherits that geometry rather than inventing one.
    """
    guard_args(node, _PROCEDURE_OK, "CREATE PROCEDURE")
    block = node.args.get("expression")
    if block is None or type(block).__name__ != "Block":
        raise Unsupported("CREATE PROCEDURE without a BEGIN...END block")

    # sqlglot does not parse T-SQL control flow. Where an `IF` or `WHILE`
    # appears, part of the body comes back as `exp.Command` holding raw source
    # text: for an IF/ELSE, the entire ELSE branch. An `IfBlock` with an ELSE
    # is indistinguishable from one without, so laying out the tree would drop
    # it silently. Detected by looking for the unparsed remainder rather than by
    # listing node types, so it lifts itself if sqlglot ever parses these.
    if any(isinstance(n, exp.Command) for n in block.walk()):
        raise Unsupported("CREATE PROCEDURE: sqlglot left part of the body unparsed")

    lines = [
        Line(anchor, [Seg(f"CREATE PROCEDURE {render_expr(node.this, dialect)}")]),
        Line(anchor, [Seg("AS")]),
        Line(anchor, [Seg("BEGIN")]),
        Line(anchor, []),
    ]
    for statement in block.expressions:
        if type(statement).__name__ == "EndStatement":       # the block's own END
            continue
        body = layout_statement(statement, dialect, width, anchor)
        body[-1].segs[-1].text += ";"                        # each body statement terminates
        lines += body
        lines.append(Line(anchor, []))
    lines.append(Line(anchor, [Seg("END;")]))
    return lines


@functools.cache
def _replace_clause(dialect: str, kind: str) -> str:
    """How `dialect` spells the `replace` flag for a CREATE of this kind.

    sqlglot records `CREATE OR REPLACE VIEW` and T-SQL's `CREATE OR ALTER VIEW`
    as the same `replace=True`, so the source spelling is not in the tree and
    printing a fixed "OR REPLACE" emitted Postgres syntax into a T-SQL file --
    valid-looking output the AST check cannot reject, because both spellings
    parse to that one flag.

    Asked of sqlglot's own generator rather than listed here, so a dialect whose
    spelling differs is followed rather than silently disagreed with.
    """
    rendered = sqlglot.parse_one(
        f"CREATE OR REPLACE {kind} x AS SELECT 1", dialect=dialect).sql(dialect)
    return "OR ALTER" if "OR ALTER" in rendered.upper() else "OR REPLACE"


def _create_as_lines(node, dialect, width, anchor):
    """CTAS / CREATE [OR REPLACE] [MATERIALIZED] VIEW name AS <select>."""
    kind = node.args.get("kind").upper()
    parts = ["CREATE"]
    if node.args.get("replace"):
        parts.append(_replace_clause(dialect, kind))
    if _has_property(node, exp.MaterializedProperty):
        parts.append("MATERIALIZED")
    parts.append(kind)
    parts.append(render_expr(node.this, dialect))            # name (Schema/Table)
    parts.append("AS")
    header = Line(anchor, [Seg(" ".join(parts))])
    body = node.args.get("expression")
    lines = [header, *layout_statement(body, dialect, width, anchor)]

    # `WITH DATA` / `WITH NO DATA` closes a CTAS, and `WITH NO DATA` creates an
    # empty table. Any other property declines rather than being dropped.
    for prop in _properties(node):
        if isinstance(prop, exp.WithDataProperty):
            lines.append(Line(anchor, [Seg("WITH NO DATA" if prop.args.get("no")
                                           else "WITH DATA")]))
        elif not isinstance(prop, exp.MaterializedProperty):
            # Anything else would go the same silent way, so say so instead.
            raise Unsupported(f"CREATE ... AS property: {type(prop).__name__}")
    return lines


def _properties(node):
    props = node.args.get("properties")
    return props.expressions if props is not None else []


def _has_property(node, prop_type):
    return any(isinstance(p, prop_type) for p in _properties(node))


# ---- CREATE TABLE ----------------------------------------------------------

def _create_table_lines(node, dialect, width, anchor):
    guard_args(node, _CREATE_TABLE_OK, "CREATE TABLE")
    schema = node.this                                       # exp.Schema
    if not isinstance(schema, exp.Schema):
        # `CREATE TABLE t PARTITION OF p FOR VALUES IN (1)`: a partition
        # declares no columns of its own; it inherits the parent's. There is no
        # column list to align, so it is a one-liner spelled by sqlglot, whose
        # `properties` carry the PARTITION OF and the bound.
        if _has_property(node, exp.PartitionedOfProperty):
            return _one_line(node, dialect, anchor)
        raise Unsupported("CREATE TABLE without column list")
    table = render_expr(schema.this, dialect)
    defs = schema.expressions
    encode_mode = any(_has_encode(d) for d in defs if isinstance(d, exp.ColumnDef))

    lines = [Line(anchor, [Seg(f"CREATE TABLE {table} (")])]
    scope = f"ddlcol@{id(node)}"
    for i, d in enumerate(defs):
        # render() adds one separator space after `head`, so 3-char heads put
        # the column name at anchor+4: first line 3 spaces; else `  ,` (leading
        # comma at anchor+2).
        head = Seg(" " * 3) if i == 0 else Seg("  ,", kind=COMMA_KIND)
        if isinstance(d, exp.ColumnDef):
            lines.append(Line(anchor, [head, *_column_segs(d, scope, encode_mode, dialect)]))
        else:                                    # table-level constraint (PRIMARY KEY, ...)
            lines.append(Line(anchor, [head, Seg(render_expr(d, dialect))]))
    lines.append(Line(anchor, [Seg(")")]))
    lines += _table_attributes(node, dialect, anchor)
    return lines


def _column_segs(col, scope, encode_mode, dialect):
    name = render_expr(col.this, dialect)
    type_text = render_expr(col.args["kind"], dialect)
    segs = [Seg(name), Seg(type_text, scope=scope, kind="type")]
    if encode_mode:
        nul, enc = _split_redshift_constraints(col, dialect)
        if nul:
            segs.append(Seg(nul, scope=scope, kind="nul"))
        if enc:
            segs.append(Seg(enc, scope=scope, kind="enc"))
    else:
        text = " ".join(render_expr(c, dialect) for c in col.args.get("constraints", []))
        if text:
            segs.append(Seg(text, scope=scope, kind="constraint"))
    return segs


def _has_encode(col):
    return any(isinstance(c.args.get("kind"), exp.EncodeColumnConstraint)
               for c in col.args.get("constraints", []))


def _split_redshift_constraints(col, dialect):
    """Redshift's two constraint columns: (nullability, ENCODE)."""
    nul, enc = "", ""
    for c in col.args.get("constraints", []):
        k = c.args.get("kind")
        if isinstance(k, exp.NotNullColumnConstraint):
            nul = "NULL" if k.args.get("allow_null") else "NOT NULL"
        elif isinstance(k, exp.EncodeColumnConstraint):
            enc = render_expr(c, dialect)
        else:
            raise Unsupported(f"redshift column constraint: {type(k).__name__}")
    return nul, enc


def _table_attributes(node, dialect, anchor):
    """Redshift table attributes after the closing paren: DISTSTYLE + DISTKEY
    share one line, SORTKEY takes the next."""
    props = node.args.get("properties")
    if props is None:
        return []
    diststyle = distkey = sortkey = None
    for p in props.expressions:
        if isinstance(p, exp.DistStyleProperty):
            diststyle = "DISTSTYLE " + str(p.this).upper()
        elif isinstance(p, exp.DistKeyProperty):
            distkey = _space_before_paren(render_expr(p, dialect))
        elif isinstance(p, exp.SortKeyProperty):
            sortkey = _space_before_paren(render_expr(p, dialect))
        else:
            raise Unsupported(f"table property: {type(p).__name__}")
    lines = []
    if diststyle or distkey:
        lines.append(Line(anchor, [Seg(" ".join(x for x in (diststyle, distkey) if x))]))
    if sortkey:
        lines.append(Line(anchor, [Seg(sortkey)]))
    return lines


def _space_before_paren(text, after=""):
    """Insert the house space between a keyword and its `(` — `DISTKEY (col)`,
    `SORTKEY (col)`, `ON orders (col)`, which the generator emits tight.

    `after` restricts the fix to the first `(` past that marker, so a paren
    earlier in the text (a CREATE INDEX name, say) is left alone.
    """
    start = text.find(after) if after else 0
    if start == -1:
        return text
    i = text.find("(", start)
    return text if i <= 0 or text[i - 1] == " " else text[:i] + " " + text[i:]


# ---- TRUNCATE / INDEX / GRANT ---------------------------------------------

def _truncate_lines(node, dialect, anchor):
    tables = ", ".join(render_expr(t, dialect) for t in node.args["expressions"])
    lines = [Line(anchor, [Seg(f"TRUNCATE TABLE {tables}")])]
    opts = []
    identity = node.args.get("identity")
    if identity:
        opts.append(f"{identity.upper()} IDENTITY")
    option = node.args.get("option")
    if option:
        opts.append(option.upper())
    if opts:
        lines.append(Line(anchor, [Seg(" ".join(opts))]))
    return lines


def _create_index_lines(node, dialect, width, anchor):
    text = render_expr(node, dialect)
    # Restore the house space between the indexed table and its column list
    # (`ON orders (cols)`), which the generator emits tight, and split off a
    # trailing partial-index WHERE so it can drop to its own line.
    where_idx = text.find(" WHERE ")
    head, where = (text[:where_idx], text[where_idx + 1:]) if where_idx != -1 else (text, "")
    head = _space_before_paren(head, " ON ")
    lines = [Line(anchor, [Seg(head)])]
    if where and (len(head) > width.width or len(f"{head} {where}") > width.limit(anchor)):
        lines.append(Line(anchor, [Seg(where)]))
    elif where:
        lines[0] = Line(anchor, [Seg(f"{head} {where}")])
    return lines


def _grant_lines(node, dialect, anchor):
    """GRANT and REVOKE, one line each, spelled by sqlglot.

    REVOKE was reachable here for a year before it was routed in: it parses to
    its own node and renders on one line exactly as GRANT does, but was absent
    from the dispatch, so every REVOKE in a permissions script declined while
    the GRANT above it formatted.
    """
    if isinstance(node, exp.Command):
        return [Line(anchor, [Seg(_uppercase_keywords(node, dialect))])]
    return [Line(anchor, [Seg(render_expr(node, dialect))])]


# Keywords that appear in a GRANT ... ON ALL <objs> IN SCHEMA ... TO ...
# statement but that sqlglot's tokenizer does not list as single-word keywords
# (it has TABLE, not TABLES; no TO). Best-effort supplement for the Command path.
_GRANT_KEYWORDS = {"TO", "TABLES", "SEQUENCES", "FUNCTIONS", "PROCEDURES", "ROUTINES"}


def _uppercase_keywords(command, dialect):
    """Best-effort keyword uppercasing for a GRANT that sqlglot could only
    parse as an unsupported-syntax `Command` (e.g. `GRANT ... ON ALL TABLES IN
    SCHEMA ...`): uppercase every whitespace-separated word that is a known SQL
    keyword, preserve identifiers. ast_equal casefolds Command text, so this is
    safety-net-neutral."""
    from sqlglot.tokens import Tokenizer
    keywords = {kw for kw in Tokenizer(dialect=dialect).KEYWORDS if " " not in kw}
    keywords |= _GRANT_KEYWORDS
    raw = f"{command.this} {command.args.get('expression', '')}"
    return " ".join(w.upper() if w.upper() in keywords else w for w in raw.split())


# ---- ALTER -----------------------------------------------------------------

def _alter_lines(node, dialect, anchor):
    """`ALTER TABLE t ADD COLUMN c INT` and its relatives.

    One action rides the head line; two or more stack under it in the house's
    leading-comma list, at the same item/comma columns a CREATE TABLE column
    list uses. There is no list with one item, which is the whole reason for the
    split: a lone action has no column to hold.

    Each action's text is DERIVED, by rendering an ALTER that carries only that
    action and removing the head. A bare `ColumnDef` renders as `c INT`: the
    `ADD COLUMN` prefix lives in sqlglot's ALTER generator, not on the node, and
    the action vocabulary is wide enough (ADD COLUMN, DROP, RENAME TO, ALTER
    COLUMN ... SET/DROP, ADD CONSTRAINT, ...) that a prefix table here would
    fall behind. The head is computed and then CHECKED against what sqlglot
    actually produced, so a spelling this does not predict declines instead of
    being silently mangled.

    `kind` is None when sqlglot could not parse the action at all (`ALTER TABLE
    t OWNER TO bob` falls back to a Command), which `guard_args` cannot catch
    because the arg is simply absent.
    """
    guard_args(node, _ALTER_OK, label="ALTER")
    kind = node.args.get("kind")
    actions = node.args.get("actions") or []
    if not kind or not actions:
        raise Unsupported("ALTER: unparsed action")

    exists = " IF EXISTS" if node.args.get("exists") else ""
    only = " ONLY" if node.args.get("only") else ""
    head = f"ALTER {kind.upper()}{exists}{only} {render_expr(node.this, dialect)}"

    tails = []
    for action in actions:
        one = node.copy()
        one.set("actions", [action])
        whole = render_expr(one, dialect)
        if not whole.startswith(head + " "):
            raise Unsupported("ALTER: unexpected head spelling")
        tails.append(whole[len(head) + 1:])

    if len(tails) == 1:
        return [Line(anchor, [Seg(head), Seg(tails[0])])]

    lines = [Line(anchor, [Seg(head)])]
    for i, tail in enumerate(tails):
        if i == 0:
            lines.append(Line(anchor + 4, [Seg(tail)]))
        else:
            lines.append(Line(anchor + 2, [Seg(",", kind=COMMA_KIND), Seg(tail)]))
    return lines


# ---- one-line utility statements -------------------------------------------

def _one_line(node, dialect, anchor):
    """A statement with no internal structure to align: `DROP TABLE IF EXISTS t
    CASCADE`, `CREATE SCHEMA s`. sqlglot's generator spells the whole thing, and
    there is nothing here for the resolver to hold a column for: laying it out
    by hand would only be a chance to get a keyword wrong.
    """
    return [Line(anchor, [Seg(render_expr(node, dialect))])]


def _comment_lines(node, dialect, anchor):
    """`COMMENT ON TABLE t IS 'x'`.

    Not `_one_line`, because sqlglot echoes the object kind in whatever case it
    was WRITTEN in: `comment on table t` renders `COMMENT ON table t`, with a
    keyword left lowercase in the middle of an uppercased statement. The kind is
    a keyword like any other, so it is cased here before rendering.
    """
    kind = node.args.get("kind")
    if isinstance(kind, str):
        node = node.copy()
        node.set("kind", kind.upper())
    return [Line(anchor, [Seg(render_expr(node, dialect))])]


def _declare_lines(node, dialect, anchor):
    """T-SQL `DECLARE @x INT = 5`.

    One declaration rides the head line; several stack in the house's
    leading-comma list, the same split `_alter_lines` makes for the same reason
   , there is no list with one item.

    Each declaration's text is DERIVED, by rendering a DECLARE that carries only
    it and removing the head, so the spelling of a type or a default is
    sqlglot's rather than reconstructed here.
    """
    items = node.expressions
    if not items:
        raise Unsupported("DECLARE without declarations")
    head = "DECLARE"
    tails = []
    for item in items:
        one = node.copy()
        one.set("expressions", [item])
        whole = render_expr(one, dialect)
        if not whole.startswith(head + " "):
            raise Unsupported("DECLARE: unexpected head spelling")
        tails.append(whole[len(head) + 1:])

    if len(tails) == 1:
        return [Line(anchor, [Seg(head), Seg(tails[0])])]
    lines = [Line(anchor, [Seg(head)])]
    for i, tail in enumerate(tails):
        lines.append(Line(anchor + 4, [Seg(tail)]) if i == 0
                     else Line(anchor + 2, [Seg(",", kind=COMMA_KIND), Seg(tail)]))
    return lines
