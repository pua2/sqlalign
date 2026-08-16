# Changelog

## 1.0.0

First release.

sqlalign formats SQL into a columnar, aligned house style, and re-parses every
statement it writes to prove it did not change what the SQL means.

### The guarantee

Every statement is re-parsed after formatting and compared against the input as
a syntax tree. If the output would differ semantically — or if the engine does
not fully model the construct — that statement is passed through byte-identical
with a warning instead, and the run still exits `0`.

A passthrough is not a failure. It is what makes the tool safe to run across a
repository you did not write.

### Formatting

- **Postgres, Redshift and SQL Server**, selected with `--dialect`. Adding a
  dialect requires auditing every keyword the layout emits, because the AST
  check cannot catch output that is valid SQL but invalid for the target
  engine — so the list is short and deliberate.
- **plpgsql `$$` bodies are formatted**, statement by statement, rather than
  treated as an opaque string.
- **Jinja and dbt templating** is masked before parsing, so a templated model
  formats instead of failing to parse.
- Comments are reproduced faithfully or the statement declines — never moved,
  never restyled.

### Configuration

- `.sqlalign.toml`, with CLI flags overriding it for a single run.
- **18 style options**, each documented with the same SQL rendered under each of
  its values.
- **Six presets**: `house`, `compact`, `trailing`, `dbt`, `gitlab`, `river`. The
  last two reproduce their source style guides byte for byte and are pinned to
  those documents by tests.
- Alignment is one mechanism with a list of targets, so a team can keep the
  join-condition column and drop the rest — or turn padding off entirely with
  `--no-align` and keep the line structure, which is what most published style
  guides ask for.

### Running it

- Files or directories, with `--exclude` globs.
- `--check` for CI, `--diff` to preview, `--stdout` to pipe.
- `--report` counts what declined and ranks the causes, so a passthrough stops
  being invisible; `--max-declines N` turns that into a gate.
- `--gui` opens a settings panel with a live preview. **Experimental** — the
  CLI and config file are the supported surface.

### Alongside sqlfluff

- `--print-sqlfluff-config` generates a config that stops the two tools
  fighting: sqlalign owns layout, so the whole `layout` rule group is excluded
  by name, and every semantic rule stays on.
- `--lint` runs sqlfluff over the formatted result in the same command.
- **`--lint` reads inside `$$` bodies, which sqlfluff cannot.** To its parser a
  plpgsql body is a single string literal, so nothing inside one is ever linted.
  sqlalign already locates those statements in order to format them, and hands
  them over with their real line and column numbers.

### Known limits

These decline rather than guess, and say why:

- `PIVOT`/`UNPIVOT` under Postgres, which has no such syntax.
- T-SQL `IF`/`WHILE` inside a procedure body: sqlglot leaves part of the body
  unparsed, including the whole `ELSE` branch, so laying out the tree would drop
  it silently.
- A comment buried inside an expression, or in a positional `GROUP BY 1, 2` —
  neither has a row of its own to sit on.
- Constructs sqlglot cannot parse or cannot round-trip through its own
  generator. `--report` names each one.
