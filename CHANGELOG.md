# Changelog

## 1.1.0

### The guarantee now covers comments

Every statement was already re-parsed and compared as a syntax tree. Comments are
not in that tree — sqlglot hangs them off tokens — so the check passed whether or
not a comment survived, and the two worst bugs in this project's history lived in
that gap and were semantic rather than cosmetic: `SELECT a -- c,` lost a
separator, and `SELECT a -- note;` left the statement unterminated so it swallowed
the next one.

Each statement is now also compared on the comments it carries, and one that
would differ is passed through byte-identical like any other. Text and order are
compared, not position: the layout deliberately moves a comment to the end of the
row above. Comments inside a `$$` body remain outside this check; those
statements are compared structurally instead.

### Running it where you already work

- **pre-commit hooks and a GitHub Action.** `sqlalign` rewrites and fails so you
  restage; `sqlalign-check` only reports. The action defaults to `--check`,
  because an action that silently reformats a checkout is not what a gate is for.
- **`sqlalign -`** reads stdin and writes stdout, which is what an editor's
  format-on-save runs. `--check` and `--diff` still report rather than write.
- **A Python API.** `sqlalign.format` returns the text; `sqlalign.format_result`
  also returns what happened. Both exist because a statement sqlalign cannot
  model comes back byte-identical rather than raising, so the simple function
  cannot tell "formatted" from "left alone".
- **`sqlalign --init`** writes a starter `.sqlalign.toml`, every setting
  commented out. A starter that pinned eighteen settings would freeze you on the
  day's defaults and call it a decision.

### Wider support

- **Python 3.10 and 3.11.** Nothing in the engine needed porting; `tomllib` gets
  a shim below 3.11. CI runs all four versions, and a test compares the
  classifiers, the `requires-python` floor and the CI matrix against each other.
- **sqlglot `>=30.14,<31`** rather than a single patch line. The range is safe
  because `tests/test_sqlglot_conformance.py` asserts each AST shape the layout
  reads, and CI runs the whole suite against the newest release the range allows.
  The safety net cannot catch an upstream shape change on its own: it compares
  output to input under the same sqlglot, so both sides move together.

### Fixed

- **Comments inside a `$$` body were keyword-cased, with the terminator spliced
  into the comment.** `-- log it for the user` shipped as
  `-- LOG it FOR the user;` with no decline. A same-line trailing comment is now
  reproduced verbatim; a comment position the body renderer does not model
  declines instead of guessing — the same contract the SQL comment engine
  follows, now held on both sides of the dollar quotes.
- **`--report` and `--max-declines` were blind to templated files.** The
  Jinja/dbt path dropped the statement and decline counts, so most of a dbt
  project was invisible to the coverage gate.
- **A quoted identifier immunised the keyword it was spelled as.**
  `SELECT a AS "FROM"` under `keyword_case = "lower"` left the real `FROM`
  upper in an otherwise lowercase file.
- **`sqlalign - | head` dumped a traceback.** The command now exits `141`, the
  shell's spelling of death-by-SIGPIPE.
- `WITH "cte" AS (...)` declined. The CTE name printed from `.alias`, which
  strips quoting, so the output named a different relation. Machine-generated SQL
  quotes every identifier, so this affected a whole class of input.
- `WHERE NOT b IS NULL` declined under Postgres. The renderer rewrote it to
  `b IS NOT NULL` — the same meaning, but not what was written. Whether the two
  spellings can be told apart is a property of the dialect, so it is now probed
  rather than assumed.

Both were found by a new corpus suite that runs the formatter over third-party
SQL — sqlglot's own fixtures, dbt's example project, and a macro-heavy dbt
package — vendored at pinned commits.

### Also

- `--report` names the construct declining most often and links a pre-filled
  issue, so a coverage gap measured on your SQL has somewhere to go.
- The settings panel says why a control is greyed out, and the config it writes
  records where it came from.
- A published benchmark (`tools/benchmark.py`): about 180 files/s, which is why
  there is no `--jobs` flag.
- The style stability policy is written down: no style change in a patch release,
  enforced by byte-exact goldens in their own CI job.

## 1.0.2

### Fixed

**An interrupted run could truncate the file it was formatting.** Writing with
`open(path, "w")` empties the file before the replacement is written, so a run
stopped part way through -- Ctrl-C over a large repository, a full disk, a
killed process -- left some file holding a prefix of its formatted self, with
the original gone. Files are now written to a neighbour and renamed into place,
so what is on disk is either the whole old file or the whole new one. Symlinks
are written through rather than replaced, and permissions are carried across.

**A file that was not UTF-8 raised a traceback and aborted the run.** The read
is UTF-8, and a `UnicodeDecodeError` is not an `OSError`, so it escaped the
per-file handler and took the whole invocation with it — every file after the
first undecodable one was silently never formatted. Such a file is now reported
and left exactly as it is: guessing an encoding would mean writing it back in a
different one, which is the kind of change sqlalign exists not to make.

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
