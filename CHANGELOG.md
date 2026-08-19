# Changelog

## 1.3.0

The release is in two halves. The first is what sqlalign now does that it did
not: statements it used to pass through now format, and a selection can be
formatted on its own. The second is four things it was doing to your files that
it should not have been — all found by auditing rather than reported, all silent,
and all now either fixed or declined.

### Statements sqlglot cannot print as written now format anyway

1.2 stopped sqlalign respelling statements, by passing them through when the
render would have changed a spelling. Honest, and it meant `ALTER COLUMN x TYPE
text`, `DROP FUNCTION f()`, `ADD COLUMN c integer array` and `SET search_path TO
public` did not format at all.

They now format the one way that cannot lose a spelling: from the source text,
changing nothing but the case of the keywords. Nothing is rebuilt, so nothing
can be respelt.

Telling a keyword from an identifier is the whole of it, and neither obvious
answer works -- the tokenizer types `add`, `to` and `local` as ordinary words in
exactly these statements, and the keyword table says `year`, `name` and `value`
are keywords, so it would rename your columns. The parse tree separates them:
what you wrote as a name or a value is a node in it, and the grammar around it
lives in sqlglot's generator, on no node at all. A word you used both ways keeps
the case you gave it.

`SET ROLE reporting` and its relatives still pass through. sqlglot cannot parse
them, so there is no tree to ask, and every word in one would read as grammar --
which would rename the role.

### `--lines`: format a selection

```console
$ sqlalign --lines 40:58 models/orders.sql        # just those lines
$ sqlalign --lines 12 --lines 40:58 orders.sql    # two selections
$ sqlalign --diff --lines 40:58 orders.sql        # review before writing
```

The unit is the statement, not the line: half a statement does not parse, so a
range starting or ending inside one formats it entire. Everything outside comes
back byte-identical, blank lines included -- asking about line 12 is not asking
for the spacing at line 40 to be normalised.

This is for adopting sqlalign on a repository nobody wants to reformat in one
commit: format the lines your change touches, and the review stays about your
change. An editor that hands over the selected text rather than line numbers --
a visual selection piped through `:!` in vim -- wants `sqlalign -` instead.

### `--sqlfluff-config`

    sqlalign --lint --sqlfluff-config ~/company/.sqlfluff models/

Points `--lint` at a config outside the repository, which is the shape a shared
company ruleset actually has: one file, many checkouts, no copy per repo. Without
it, `--lint` discovers a config by walking up from the file and falls back to a
generated one. `--lint` reports; it has never called `sqlfluff fix`.

### A comment on its own line no longer costs you the whole procedure

An own-line comment inside a `$$` body sent the entire procedure through
unformatted. The clause splitter glues such a comment to the statement below it,
so the plain-clause renderer saw a comment with code after it -- which has no
faithful one-line rendering -- and declined. A procedure that reads

    begin
      -- clear the staging table
      delete from staging;

is the ordinary way people write plpgsql, and this made it unformattable.

It is faithfully renderable, just not on one line. The comment now stays on its
own line above the code, exactly as written. The same merged comment was also
hiding the keyword the body parser dispatches on, so an `IF` behind a comment
was neither recognised as a block nor found as the `END`, and the procedure
declined a second time as malformed.

What still declines is the one shape with no faithful rendering: a comment with
code after it on the same line.

### Declines are named in SQL rather than in Python

`SET ROLE reporting` and `RESET ROLE` reported as `unsupported construct
(Command)`. `Command` is sqlglot's catch-all for syntax its parser does not
model, so it named the fallback rather than the statement, and named every
unrelated construct that lands there the same way. `SET search_path TO public`
had the milder version: `Set`, a class name where SQL was meant.

They are now named for the keyword the statement starts with, checked against
the dialect's own vocabulary. sqlglot's accompanying log line -- `'SET ROLE
reporting' contains unsupported syntax. Falling back to parsing as a
'Command'.` -- no longer reaches the CLI's stderr, since sqlalign reports the
same statement by name and with the text it passed through. The library leaves
that logger alone.

### Corrections

Each of these changed a file without saying so.

**If you ran 1.2 or earlier over procedures or intervals, your files may already
carry these changes.** Measured against 1.2.0 rather than described from memory:

| What 1.2 and earlier did | Still valid SQL | Meaning changed |
|---|---|---|
| `interval '14 days'` came back as `INTERVAL '14 DAYS'` | yes | no — interval units are case-insensitive |
| `LANGUAGE 'plpgsql'` came back as `LANGUAGE plpgsql` | yes | no |
| `$BODY$` came back as `$body$`, under `keyword_case = "lower"` only | yes | no — both ends moved together |
| `DECLARE n int; -- note` came back with the comment keyword-cased and a `;` inside it | yes | no — comment text only |
| A `LANGUAGE sql` body's last statement lost its terminator into a trailing comment | yes | no — a final statement needs none |
| **`$café$ … $café$` had its CONTENTS reformatted** | yes | **yes — the string literal changed** |

Only the last one altered anything a query would return, and only if you use a
dollar-quote tag containing non-ASCII characters, which is legal and rare. The
rest are byte differences in valid SQL that means what it meant.

To find the last one, look for a non-ASCII dollar tag:

```console
$ grep -rlP '\$[^\x00-\x7F$]+\$' --include='*.sql' .
```

For the rest, your version control already has the answer — the diff from the
commit where sqlalign first ran over a file is the complete list of what it
changed, and none of it needs undoing.

### The token guard was blind to case inside a string literal

1.2 added a token census so a statement sqlglot's parser silently respells is
passed through instead. It uppercased every token before counting, string
literals included -- so a change inside one was invisible to it, and to the other
two checks as well: `INTERVAL '14 days'` parses to `Var(DAYS)`, identical tree
either way, so `ast_equal` could not see it and neither could the census.

`INTERVAL '14 days'` had been going out as `INTERVAL '14 DAYS'` in every release
up to and including 1.2, with nothing declining. Literal content is now compared
as written.

Closing that alone would have stopped `interval '14 days'` formatting at all,
since the spelling is gone from the tree by the time the renderer runs. It is
restored from the statement's own source instead, so the common case formats and
keeps what the author typed. Case only, looked up in the statement being
rendered -- a substitution cannot introduce a spelling the author did not write,
and if one somehow did, the census now sees it. A statement that writes the same
literal two ways offers neither spelling, since either choice would respell the
other, and a `$$` body is exempt from the census that would otherwise catch it.

### The rewrite guard now reaches inside a procedure body

1.2's census could not run on a statement carrying a `$$` body. The tokenizer
sees a body as one token, so comparing the whole statement sees nothing inside
it, and comparing the body's own tokens compares a laid-out body against an
unformatted one. So bodies were excluded, and a respelling inside a procedure
had nothing catching it -- which is how `INTERVAL '14 days'` shipped as
`'14 DAYS'` from inside one.

Each clause is now compared against the clause it was rendered from, using the
split the structural check already relies on. Three things had to change first,
and the third is a behaviour change worth naming: **`LANGUAGE 'plpgsql'` keeps
its quoting.** It was being normalised to the bare form, which is a respelling,
and this was the last place the engine still performed one on your behalf.

A procedure that respells is passed through rather than cased from source: one
clause would cost every other clause in the body its layout.

### Three fixes around dollar-quoted bodies

Tagged dollar quotes (`$func$` rather than bare `$$`) were already a working
case, and auditing that path found three defects — two of which had nothing to
do with tags.

**A non-ASCII tag changed your data.** Postgres says a dollar-quote tag follows
the rules for an unquoted identifier, and those are not ASCII-only: `$café$` and
`$ñ$` are legal. sqlalign's pattern was ASCII-only, so it did not see the region
as a quoted one at all — the file was cut at a `;` *inside* the literal and the
fragments formatted as SQL. A `<>` inside a `$ñ$ … $ñ$` string came back as `!=`,
with the warnings still reporting "passthrough". The pattern now follows
Postgres, and it has one definition rather than the two that had to be found and
widened separately.

**`keyword_case = "lower"` lowered the tag itself.** `$BODY$` shipped as
`$body$` at both ends, silently: valid SQL, which is why nothing objected, but
not what you wrote. Neither safety layer could see it — sqlglot's `Heredoc` does
not record the tag and the census excludes it. The `dbt` preset sets that case,
so it reached everyone using it.

**A comment inside a body was outside the comment guard.** `comment_text`
returned `[]` for an entire procedure, because a body is one token and it did not
descend. `comments_equal` was therefore vacuously true for anything in one, and
`DECLARE n int; -- count from the source table` shipped as
`-- count FROM the source TABLE;` — keyword-cased, with the terminator inside the
comment. Both the guard and `_render_declare` are fixed; this one affected bare
`$$` too.

Putting the guard in place immediately caught a second instance on the other
rendering path: `_render_sql_stmt` appended the terminator to the raw clause, so
a `LANGUAGE sql` body reading `select 1 -- why` shipped as `SELECT 1 -- why;`
with the statement left unterminated. Also fixed.

### Fixed

- **`REVOKE` declined while the `GRANT` above it formatted.** Not a modelling
  gap -- REVOKE parses to its own node and renders on one line exactly as GRANT
  does. It was missing from the layout dispatch, and a permissions script is
  where the asymmetry shows.

## 1.2.0

### sqlalign no longer respells your statements silently

The safety net compares output against input as a syntax tree, under the same
sqlglot. That is blind to a rewrite sqlglot's own parser performs: two spellings
collapse to one node, so printing either compares equal. Twenty statements went
through changed, with no decline and no warning -- `ADD COLUMN c INTEGER ARRAY`
came back as `INT`, `DROP FUNCTION f()` lost the signature that names the
overload, `WITH CSV HEADER FORCE QUOTE a, b` became three options where the
author wrote one, and T-SQL `SET (LOCK_ESCALATION = AUTO)` gained an invented
`WITH (...)` that SQL Server does not accept.

Each statement's tokens are now counted either side of formatting, and one that
gained or lost a token is passed through byte-identical instead. Nothing here is
a hand-kept list of allowed differences: type synonyms are derived by asking
sqlglot which spellings parse to the same node, so a new dialect or a renamed
type is followed rather than disagreed with, and the three settings that choose
a spelling are normalised away because choosing is their job.

Order is deliberately not compared. The tree already catches anything that
moved, so the two checks divide the space -- and comparing order here would
decline `GROUP BY ROLLUP(a, b), c` printed as `GROUP BY c, ROLLUP(a, b)`, which
is the same grouping sets.

### Fixed

- **T-SQL paging emitted invalid SQL.** `OFFSET 10 ROWS` came back as
  `OFFSET 10`, which SQL Server rejects. `Offset` records only the number, so
  the AST check could not tell the spellings apart; the keyword is now emitted
  where the dialect requires it, asked of sqlglot rather than hardcoded.

### `dialect` is a config key

```toml
# redshift-warehouse/.sqlalign.toml
dialect = "redshift"
```

Resolved per file from the config nearest it, so one command -- one editor
action, one pre-commit hook -- can span a Postgres repository and a Redshift one
and be right in both. `--dialect` still overrides for a one-off run.

This is not the auto-detection sqlalign refuses to do. Sniffing a file cannot be
covered by the safety guarantee; declaring the engine in a file your team
reviews is the opposite of a guess, and more explicit than a flag typed into an
editor's tool settings and never looked at again.

### Fixed

- `CREATE OR ALTER VIEW` came back as `CREATE OR REPLACE VIEW` under T-SQL --
  Postgres syntax written into a T-SQL file, which the AST check could not
  reject because both spellings parse to the same `replace` flag.

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
