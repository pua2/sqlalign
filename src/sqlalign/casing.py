import contextvars
from contextlib import contextmanager
from typing import ClassVar, NamedTuple

from sqlglot import exp
from sqlglot.dialects.postgres import Postgres
from sqlglot.dialects.redshift import Redshift
from sqlglot.dialects.tsql import TSQL

from sqlalign.style import HOUSE, Style

# The ambient render style. `render_expr` is called from ~57 sites across every
# layout handler, all of which care only about LAYOUT knobs (width), so the two
# pure OUTPUT-SPELLING knobs (neq_style, decimal_style) are read from here rather
# than threaded through every handler signature. `format_sql` sets it once per
# call; nesting and recursion are safe because the token is reset on exit.
_RENDER_STYLE: contextvars.ContextVar[Style] = contextvars.ContextVar("sqlalign_render_style")


def active_style() -> Style:
    """The style in effect for rendering; HOUSE when nothing set it."""
    return _RENDER_STYLE.get(HOUSE)


@contextmanager
def render_style(style: Style):
    token = _RENDER_STYLE.set(style)
    try:
        yield
    finally:
        _RENDER_STYLE.reset(token)


class Source(NamedTuple):
    """The statement being rendered, as the author wrote it."""

    sql: str
    dialect: str
    literals: dict[str, str]        # uppercase form -> spelling, see `as_written`


# What sqlglot normalised away is still in the source, and this is how the
# renderer reaches it. `INTERVAL '14 days'` parses to `Var(this=DAYS)`, so the
# author's spelling is gone by the time the generator runs; `SET x TO y` has no
# node recording that `TO` was written rather than `=`.
#
# Ambient for the same reason the style is: the generator is reached through
# ~57 `render_expr` sites, none of which have the source to pass down.
_SOURCE: contextvars.ContextVar[Source | None] = contextvars.ContextVar(
    "sqlalign_source", default=None)


def set_source(sql: str, dialect: str):
    """Make `sql` the statement `as_written` and `source_sql` answer for.

    A set/reset pair rather than a context manager: the caller is the
    per-statement loop in `_format_all`, which already has the `finally` that
    advances its position, and threading a `with` through that block would
    reindent it without making anything clearer.
    """
    from sqlalign.spelling import literal_spellings
    return _SOURCE.set(Source(sql, dialect, literal_spellings(sql, dialect)))


def reset_source(token) -> None:
    _SOURCE.reset(token)


def source_sql() -> str | None:
    """The source of the statement being rendered, when one is set."""
    source = _SOURCE.get()
    return source.sql if source is not None else None


def as_written(literal: str) -> str:
    """`literal` respelt as the source spelt it, when the source spelt it.

    Case only: the lookup is by uppercase form, so a literal the source does not
    contain comes back unchanged and a substitution can never introduce content
    that was not written. A statement writing one literal two ways offers
    neither spelling, so this returns such a literal unchanged.
    """
    source = _SOURCE.get()
    return literal if source is None else source.literals.get(literal.upper(), literal)


# Cast-form discriminator (v30.14, version-pinned; revisit on sqlglot upgrade):
# `::` sources parse to a Cast node carrying only {'this', 'to'}. CAST()/
# TRY_CAST() sources always carry these four CAST-specific keys too, even
# when their values are None. Key PRESENCE: not len(node.args) and not
# truthiness is the reliable discriminator. Under the redshift dialect
# every Cast node (including `::`-form ones) additionally carries an
# incidental 'join_mark' key, which breaks a raw `len(args) <= 2` check.
_CAST_FORM_KEYS = ("format", "safe", "action", "default")


class _HouseCastMixin:
    """Cast/type-name house rules, applied at every AST nesting depth.

    Mixed into dialect-specific Generator subclasses (below) so the dialect's
    own cast_sql/datatype_sql overrides (e.g. Postgres's superfluous-DIV()-
    cast unwrap, Redshift's JSON-cast noop and TEXT->VARCHAR(MAX)) still run
    via super(), with the house rules layered on top.
    """

    def cast_sql(self, expression: exp.Cast, safe_prefix: str | None = None) -> str:
        if any(key in expression.args for key in _CAST_FORM_KEYS):
            return super().cast_sql(expression, safe_prefix=safe_prefix)
        return f"{self.sql(expression, 'this')}::{self.sql(expression, 'to')}"

    def datatype_sql(self, expression: exp.DataType) -> str:
        sql = super().datatype_sql(expression)
        # DECIMAL and NUMERIC are exact synonyms in postgres/redshift; sqlglot
        # parses both to one node, erasing the source spelling, so one of them
        # has to be printed: Style.decimal_style picks which (house: NUMERIC).
        target = active_style().decimal_style
        if sql == "DECIMAL" or sql.startswith("DECIMAL("):
            return target + sql[len("DECIMAL"):]
        return sql

    def interval_sql(self, expression: exp.Interval) -> str:
        """`INTERVAL '14 days'`, not `INTERVAL '14 DAYS'`.

        sqlglot uppercases the unit while parsing, so rendering from the node
        alone always respells a lowercase interval. Since 1.2's token census
        compares string literals byte-for-byte, that respelling is caught -- and
        `interval '14 days'` is common enough that catching it means the whole
        statement stops formatting. The source spelling is put back instead.
        """
        sql = super().interval_sql(expression)
        head, quote, rest = sql.partition("'")
        if not quote:
            return sql
        body, quote, tail = rest.rpartition("'")
        return f"{head}'{as_written(body)}'{tail}"

    def neq_sql(self, expression: exp.NEQ) -> str:
        # sqlglot erases the source `<>`/`!=` spelling at parse, so a form must be
        # chosen when printing: Style.neq_style (house: `!=`). conditions.py's
        # laid-out path reads the same knob, so both stay consistent.
        return self.binary(expression, active_style().neq_style)


class _HousePostgresGenerator(_HouseCastMixin, Postgres.Generator):
    pass


class _HouseRedshiftGenerator(_HouseCastMixin, Redshift.Generator):
    pass


class _HouseTSQLGenerator(_HouseCastMixin, TSQL.Generator):
    """T-SQL wants the mixin's neq/decimal handling but NOT its cast form.

    `_HouseCastMixin.cast_sql` emits the `::` shorthand when the source used it —
    correct for postgres/redshift, and INVALID T-SQL, which has no `::` operator at
    all. Restoring the dialect's own cast_sql means every cast renders as
    `CAST(x AS T)`, the only form SQL Server accepts. There is correspondingly no
    "preserve the source cast form" behaviour here: T-SQL only has one form.
    """

    def cast_sql(self, expression: exp.Cast, safe_prefix: str | None = None) -> str:
        return TSQL.Generator.cast_sql(self, expression, safe_prefix=safe_prefix)


# dialect name (lowercase) -> (Dialect class, house Generator class)
_HOUSE_GENERATORS = {
    "postgres": (Postgres, _HousePostgresGenerator),
    "redshift": (Redshift, _HouseRedshiftGenerator),
    "tsql": (TSQL, _HouseTSQLGenerator),
}


def _house_date_trunc(args):
    """DATE_TRUNC('unit', expr) -> exp.Anonymous, not exp.TimestampTrunc.

    Empirically (pinned sqlglot v30.14): TimestampTrunc's base mixin, TimeUnit,
    unconditionally re-cases its `unit` arg to an uppercase exp.Var the instant
    the node is CONSTRUCTED (TimeUnit.__init__) -- 'month'/'MONTH'/'Month' all
    collapse to Var(this=MONTH), destroying the source spelling before any
    renderer ever sees the node. House style is "identifiers, aliases, string
    literals: untouched", so parsing DATE_TRUNC as a plain function
    call instead sidesteps TimeUnit entirely: the unit stays the literal
    sqlglot tokenized, case intact, and render_expr's normal Anonymous path
    renders it verbatim (the function name itself is hardcoded upper here,
    matching house function-name casing regardless of source spelling).

    Used only by `parse_dialect`'s house dialects, which formatter.py's
    `_format_statement` uses for the ONE parse that builds the layout tree.
    The plain "postgres"/"redshift" dialect strings used elsewhere (ast_equal,
    CLI, tests) are untouched: both sides of an ast_equal comparison still
    parse DATE_TRUNC the stock (TimestampTrunc) way, so case-insensitive unit
    equivalence there is unaffected either way.
    """
    return exp.Anonymous(this="DATE_TRUNC", expressions=list(args))


class _HousePostgresParser(Postgres.Parser):
    FUNCTIONS: ClassVar[dict] = {**Postgres.Parser.FUNCTIONS, "DATE_TRUNC": _house_date_trunc}


class _HouseRedshiftParser(Redshift.Parser):
    FUNCTIONS: ClassVar[dict] = {**Redshift.Parser.FUNCTIONS, "DATE_TRUNC": _house_date_trunc}


class _HousePostgresParseDialect(Postgres):
    Parser = _HousePostgresParser


class _HouseRedshiftParseDialect(Redshift):
    Parser = _HouseRedshiftParser


# dialect name (lowercase) -> house Dialect class carrying parse-time overrides
_HOUSE_PARSE_DIALECTS = {
    "postgres": _HousePostgresParseDialect,
    "redshift": _HouseRedshiftParseDialect,
    # T-SQL has no DATE_TRUNC, so it needs no parse-time override: the stock
    # dialect is used, which parse_dialect falls back to for any unlisted name.
}


def parse_dialect(dialect: str):
    """Resolve a plain dialect string to the house Dialect instance that
    carries parse-time house rules (see `_house_date_trunc`) on top of the
    stock dialect's own parsing. Falls back to the bare string for any
    dialect without a house override, so
    `sqlglot.parse(text, read=parse_dialect(d))` is always a safe drop-in for
    `sqlglot.parse(text, read=d)`.
    """
    cls = _HOUSE_PARSE_DIALECTS.get(dialect.lower())
    return cls() if cls is not None else dialect


# Generator settings both render paths below must share: function names upper,
# never add quotes an identifier did not have, and no sqlglot line breaking --
# layout owns every newline.
_RENDER_OPTIONS = {"normalize_functions": "upper", "identify": False, "pretty": False}


def render_expr(node: exp.Expression, dialect: str) -> str:
    """Render an inline expression with house casing; layout does NOT re-wrap this."""
    house = _HOUSE_GENERATORS.get(dialect.lower())
    if house is not None:
        dialect_cls, generator_cls = house
        generator = generator_cls(dialect=dialect_cls(), **_RENDER_OPTIONS)
        return generator.generate(node, copy=True)

    # Any dialect without a house Generator (none ship today) still renders,
    # just without the cast/decimal/neq house rules.
    return node.sql(dialect=dialect, **_RENDER_OPTIONS)
