"""sqlalign -- a SQL formatter for the house columnar-alignment style.

The supported Python entry points are the two functions below. Everything under
`sqlalign.*` beyond them is internal and moves between releases.

    >>> import sqlalign
    >>> print(sqlalign.format("select a,b from t;"))
    SELECT a
         , b
    FROM t;

Imports are deferred into the function bodies so that `import sqlalign` stays
cheap. Pulling in sqlglot costs most of the startup time of a run, and a
`--version` or a `__version__` read should not pay for it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # imported for annotations only
    from sqlalign.formatter import FormatResult
    from sqlalign.style import Style

__all__ = ["format", "format_result"]


def format(sql: str, *, dialect: str = "postgres",
           style: Style | None = None) -> str:
    """Format `sql` and return the result.

    A statement sqlalign cannot model is returned byte-identical rather than
    raising: passing through is the contract, not a failure. That does mean this
    function cannot tell you it happened -- use `format_result` when you need to
    know, which is most of the time in a pipeline that gates on something.

    `dialect` is one of `postgres`, `redshift` or `tsql`; anything else raises
    `ValueError` rather than being formatted with keywords the target engine may
    not accept. `style` defaults to the house style; build one with
    `sqlalign.style.Style` or `sqlalign.style.preset_style("compact")`.

    Named `format` because that is what it does. It shadows the builtin inside
    this module and nowhere else, since callers reach it as `sqlalign.format`.
    """
    return format_result(sql, dialect=dialect, style=style).text


def format_result(sql: str, *, dialect: str = "postgres",
                  style: Style | None = None) -> FormatResult:
    """Format `sql` and return the text together with what happened to it.

    The result carries `text`, `warnings`, `statements` and `declines`. A
    decline names the construct that was passed through and why, which is what
    `--report` counts; reading it is how an embedding tells "formatted" apart
    from "left alone", since both come back as valid SQL.
    """
    from sqlalign.formatter import format_sql
    from sqlalign.style import HOUSE

    # The same line-ending handling the CLI does, so the API is not a second,
    # subtly different tool: the engine is LF-only, CRLF is normalized in and
    # restored on the way out, and a lone CR -- which the engine does not
    # model -- passes through with a warning rather than surfacing as a
    # baffling per-statement decline.
    ending = "\r\n" if "\r\n" in sql else "\n"
    normalized = sql.replace("\r\n", "\n")
    if "\r" in normalized:
        from sqlalign.formatter import FormatResult
        return FormatResult(sql, ["lone CR line endings — passed through untouched"])

    result = format_sql(normalized, dialect, HOUSE if style is None else style)
    if ending == "\r\n":
        result = result._replace(text=result.text.replace("\n", "\r\n"))
    return result


def __getattr__(name: str) -> str:
    """Resolve `__version__` on first access rather than at import.

    Read from the installed distribution rather than written here: the literal
    this replaces still said "0.1.0" at release 1.1.0, because nothing forces
    the two to be edited together. pyproject.toml is now the single place a
    release bumps.
    """
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("sqlalign")
        except PackageNotFoundError:    # a source tree that was never installed
            return "0.0.0+unknown"
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
