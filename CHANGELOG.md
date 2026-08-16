# Changelog

## 1.0.2

### Fixed

**An interrupted run could truncate the file it was formatting.** Writing with
`open(path, "w")` empties the file before the replacement is written, so a run
stopped part way through -- Ctrl-C over a large repository, a full disk, a
killed process -- left some file holding a prefix of its formatted self, with
the original gone. Files are now written to a neighbour and renamed into place,
so what is on disk is either the whole old file or the whole new one. Symlinks
are written through rather than replaced, and permissions are carried across.

**An unwritable file raised a traceback and aborted the run.** A read-only file
now reports `sqlalign: [Errno 13] Permission denied: <path>`, exits `2`, and
leaves the remaining files to process -- which is what the exit-code table
already documented.

`py.typed` was missing, so every type checker skipped the package while the
`Typing :: Typed` classifier said the opposite. sqlalign is annotated and
`format_sql` is importable; both now work as advertised.

`--gui`'s missing-tkinter message went to stdout rather than stderr.

`--lint` no longer warns about a sqlfluff patch release. The coexistence config
is checked against a known sqlfluff, and any difference warned; sqlfluff 4.3.0
then shipped, so every run printed a note about a release that had moved nothing
sqlalign uses. The check now compares the series, and is silent for a sqlfluff
older than the checked one -- the floor on the `lint` extra bounds that, and CI
now runs the coexistence tests against it. The mappings are verified against
sqlfluff 4.3.0.

### Documentation

`--report` and `--max-declines` add output without changing the mode, so on
their own they rewrite the files they counted. The dialects guide told you to
run `sqlalign --report` over your own SQL to see what declined, which would have
reformatted the repository you were surveying. Both the guide and the flag's
help now say to pair it with `--check`.

Install instructions said `uv tool install .`, which installs from a clone. That
was the only option before the package was published; it is now
`pip install sqlalign`. The CI example in the guide told you to run that same
command inside your own repository, where it would have tried to install your
SQL repo as a Python package.

Also corrected: `--preset` documented four of its six values (`gitlab` and
`river` were missing), three places still said the project had no CI, and the
README linked the settings reference as a raw HTML file rather than to the
published site. Documented choice lists are now checked against the argument
parser by a test.

## 1.0.1

No functional change. `1.0.0` was tagged before the package was ever published,
so this is the first version to reach an index.

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
