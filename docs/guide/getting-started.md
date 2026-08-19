# Getting started

This page takes you from nothing installed to sqlalign running over a whole
repository and gating your CI.

sqlalign formats **presentation only**. Every statement it touches is re-parsed
and AST-compared against your input; if the output would mean anything different
— or if the engine does not fully model the construct — that statement is passed
through **byte-identical** with a warning instead. It does not lint, and it will
not rewrite your SQL for you.

## Install

sqlalign needs **Python 3.10 or newer**. Its one runtime dependency is
`sqlglot`, accepted as `>=30.14,<31`. The layout engine reads exact AST shapes,
so the range is not a passive float: `tests/test_sqlglot_conformance.py` asserts
each of those shapes by name, and CI runs the whole suite against the newest
release the range allows as well as the locked one. On 3.10 it also installs
`tomli`, which is `tomllib` from the standard library of every later version.

```sh
pip install sqlalign
```

That installs a `sqlalign` command on your PATH. Check it:

```sh
sqlalign --help
```

`uv tool install sqlalign` does the same while keeping it out of your project's
environment.

For development on sqlalign itself, work from a clone instead — this adds the
dev tools (`pytest`, `ruff`, `sqlfluff`):

```sh
git clone https://github.com/pua2/sqlalign && cd sqlalign
uv sync
```

## Your first format

Save this as `revenue.sql`:

```sql
select c.customer_id, c.email, sum(o.total) as lifetime_value, count(*) as order_count
from customers c join orders o on o.customer_id = c.customer_id
where o.status = 'complete' and o.order_date >= '2026-01-01'
group by c.customer_id, c.email
having sum(o.total) > 500
order by lifetime_value desc;
```

Print the formatted result without touching the file:

```sh
sqlalign --stdout revenue.sql
```

```sql
SELECT c.customer_id
     , c.email
     , SUM(o.total) AS lifetime_value
     , COUNT(*)     AS order_count
FROM customers c
JOIN orders    o ON o.customer_id = c.customer_id
WHERE o.status      = 'complete'
  AND o.order_date >= '2026-01-01'
GROUP BY c.customer_id
       , c.email
HAVING SUM(o.total) > 500
ORDER BY lifetime_value DESC;
```

The `AS` clauses, the operators in `WHERE`, and the table aliases in the `FROM`
block are each padded into a column. That is the house style; it is a fixpoint
resolver pass, not a search-and-replace over printed text, so it composes with
line wrapping instead of fighting it.

Now rewrite the file for real. In-place is the default — no flag:

```sh
sqlalign revenue.sql
```

It prints nothing on success. Run it again and nothing changes: formatting is
idempotent, and the golden test suite asserts that.

## Look before you write

Three flags control where the output goes. They are **mutually exclusive** — pass
at most one.

| Flag | Writes the file? | Prints | Exit code |
|---|---|---|---|
| *(none)* | yes | nothing | `0` |
| `--stdout` | no | the formatted SQL | `0` |
| `--check` | no | `would reformat <path>` per file | `1` if any file would change |
| `--diff` | no | a unified diff per file | `1` if any file would change |

`--check` is for CI logs you want to stay readable; `--diff` is for when you want
to see the change itself:

```sh
sqlalign --diff revenue.sql
```

```diff
--- revenue.sql
+++ revenue.sql (formatted)
@@ -1,6 +1,12 @@
-select c.customer_id, c.email, sum(o.total) as lifetime_value, count(*) as order_count
-from customers c join orders o on o.customer_id = c.customer_id
-where o.status = 'complete' and o.order_date >= '2026-01-01'
-group by c.customer_id, c.email
-having sum(o.total) > 500
-order by lifetime_value desc;
+SELECT c.customer_id
+     , c.email
+     , SUM(o.total) AS lifetime_value
+     , COUNT(*)     AS order_count
+FROM customers c
+JOIN orders    o ON o.customer_id = c.customer_id
+WHERE o.status      = 'complete'
+  AND o.order_date >= '2026-01-01'
+GROUP BY c.customer_id
+       , c.email
+HAVING SUM(o.total) > 500
+ORDER BY lifetime_value DESC;
```

## Formatting a directory

Pass a directory and sqlalign walks it recursively for `*.sql`, in sorted order
so a run is reproducible:

```sh
sqlalign --check .
```

```
would reformat models/gen_out.gen.sql
would reformat models/marts/rollup.sql
would reformat models/users.sql
would reformat vendor/legacy.sql
```

### Excluding files

Generated SQL and vendored SQL usually should not be reformatted. `--exclude`
takes a glob and is repeatable:

```sh
sqlalign --check --exclude 'vendor/*' --exclude '*.gen.sql' .
```

```
would reformat models/marts/rollup.sql
would reformat models/users.sql
```

Two things to know:

- Patterns are matched against each file's path **relative to the directory you
  named**, and also against its bare filename — so `vendor/*` and `*.gen.sql`
  both work above.
- **A file you name explicitly on the command line is never excluded.** Asking
  for it by name is a clearer signal than a pattern in a config file:

  ```sh
  sqlalign --check --exclude 'vendor/*' vendor/legacy.sql
  # would reformat vendor/legacy.sql
  ```

Once the patterns are right, drop `--check` to actually write:

```sh
sqlalign --exclude 'vendor/*' --exclude '*.gen.sql' .
```

Rather than repeat those flags forever, commit them. sqlalign reads a
`.sqlalign.toml` (or a `[tool.sqlalign]` table in `pyproject.toml`) discovered by
walking up from each file:

```toml
# .sqlalign.toml
exclude = ["vendor/*", "*.gen.sql"]
```

Run `sqlalign --show-config <path>` at any point to print the settings that would
actually apply, as TOML, with the config file they came from on the first line.

## Skipping one statement

Put `-- sqlalign: skip` on the line above a statement and that statement passes
through byte-identical. Its neighbours still format:

```sql
-- sqlalign: skip
select   a,b    from t;
select c, d from u;
```

```sh
sqlalign --stdout skip.sql
```

```sql
-- sqlalign: skip
select   a,b    from t;
SELECT c
     , d
FROM u;
```

## When sqlalign declines

sqlalign would rather leave your SQL alone than render it wrong. A statement it
cannot reproduce exactly is emitted unchanged, with a warning on **stderr**:

```sh
sqlalign --stdout passthru.sql
```

```
sqlalign: passthru.sql: unsupported construct (PIVOT: this dialect has no such syntax), passed through: select * from t pivot (sum(x) for y in (
```

```sql
select * from t pivot (sum(x) for y in (1, 2)) p;
```

Unparseable input behaves the same way rather than blowing up the run:

```
sqlalign: broken.sql: passthrough (parse error line 1): this is not sql at all ((( ;
```

There is a second kind of message, and it means something different:

```
sqlalign: q.sql: formatting would change semantics, passed through unformatted: ...
```

That one is a **bug report**, not a decline. It means sqlalign rendered the
statement, read its own output back, and found it no longer meant the same
thing — so it threw the output away. The construct is not unsupported; the
renderer is wrong. Two shipped that way and both were found by accident (every
lowercase user-defined function, and every quoted column alias), so the test
suite now sweeps for that wording specifically. If you see it, it is worth
reporting.

**A passthrough is not a failure.** Both of those runs exit `0` — the file is
valid output, just unformatted.

That is safe but invisible: a CI run stays green with any fraction of a
repository unformatted, and nothing tells you which fraction. `--report`
counts it:

```console
$ sqlalign --report --check models/
  1,204 statements   1,151 formatted (95.6%)   53 declined

  declined by cause
      38  unsupported  Pivot
      12  unsupported  Subquery
       3  parse        parse error
```

`--max-declines N` turns that into a gate — exit `1` if more than N statements
were passed through. Start it at whatever your repository reports today and
ratchet it down; it stops new unformattable SQL arriving unnoticed.

The causes are ranked, so the list doubles as a priority order for what to
implement next — measured on your SQL rather than guessed at. A
`-- sqlalign: skip` counts too, under its own `skipped` kind, so a deliberate
opt-out is distinguishable from a gap in the tool.

## Linting inside `$$` bodies

sqlfluff cannot lint a plpgsql body. To its parser the whole body is one string
literal, so a function full of badly-written SQL passes clean — and in a
repository that keeps logic in functions, that is a large blind spot. It is not
a rule sqlfluff is missing; it is the shape of the parse.

sqlalign already has to find those statements in order to format them, so
`--lint` hands them to sqlfluff as well. Given this function:

```sql
CREATE FUNCTION report() RETURNS int AS $$
BEGIN

SELECT * FROM orders o, customers c WHERE o.cid = c.id;

RETURN 1;

END;
$$ LANGUAGE plpgsql;
```

sqlfluff on its own sees the header and nothing else:

```
L:   1 | P:  17 | CP03 | Function names must be upper case.
L:   1 | P:  34 | CP05 | Datatypes must be upper case. [capitalisation.types]
All Finished!
```

Through sqlalign, the body is linted too:

```console
$ sqlalign --check --lint report.sql
L:   1 | P:  17 | CP03 | Function names must be upper case.
All Finished!
== [report.sql] inside $$ bodies (sqlfluff cannot reach these on its own)
L:   8 | P:   1 | AM04 | Query produces an unknown number of result columns. [ambiguous.column_count]
L:   8 | P:   8 | RF02 | Unqualified reference '*' found in select with more than one referenced table/view. [references.qualification]
```

Line 8 is line 8 of the file. sqlalign lints a *view* of it — the same text with
the plpgsql scaffolding replaced by spaces, so every line and column stays where
it was. Blanking rather than deleting is the point: delete, and every column
after it shifts, and a finding would point at the wrong place.

Two things follow from how this works:

- **The body is linted after formatting**, like the rest of the file, so
  whitespace and keyword-case findings inside a body are already fixed by the
  time sqlfluff sees it. What surfaces is what formatting cannot fix — the
  semantic rules.
- **Statements inside an `IF … THEN` branch are not covered.** The layout
  models a single-statement branch, so there is no statement span to lint.

## Running sqlfluff alongside it

sqlalign and sqlfluff overlap, and left alone they fight — `sqlfluff fix` will
undo the alignment on every run:

```sql
-- sqlalign's output                  -- after stock `sqlfluff fix`
SELECT cust.customer_id               SELECT
     , cust.email                         cust.customer_id,
FROM customers    cust                    cust.email
INNER JOIN orders ord ON …            FROM customers AS cust
WHERE ord.total    > 0                INNER JOIN orders AS ord ON …
  AND cust.segment = 'ent';           WHERE
                                          ord.total > 0
                                          AND cust.segment = 'ent';
```

The fix is the one Prettier and `eslint-config-prettier` settled on: the
formatter owns layout, and the linter is told to stop having opinions about it.

```console
sqlalign --print-sqlfluff-config > .sqlfluff
```

With that in place `sqlfluff fix` leaves sqlalign's output byte-identical.

The generated config does two things. It excludes sqlfluff's whole `layout`
rule **group** — by group rather than by name, so a sqlfluff upgrade that adds a
layout rule cannot start failing your formatted SQL. And it translates the
settings where both tools have an opinion (`keyword_case`, `table_alias_style`,
`neq_style`) into the matching sqlfluff rule config, so the two agree instead of
contradicting each other.

Every **semantic** rule stays on. Those lint your SQL rather than sqlalign's
whitespace, and sqlalign preserves the choices they are about — it would be the
wrong trade to silence them for a formatting truce.

It reads your effective settings, so run it where your config lives:

```console
sqlalign --print-sqlfluff-config models/orders.sql > .sqlfluff
```

### One command instead of two

`--lint` formats and then runs sqlfluff over the result:

```console
$ pip install 'sqlalign[lint]'
$ sqlalign --lint models/orders.sql
== [models/orders.sql] FAIL
L:   4 | P:   1 | AM05 | Join clauses should be fully qualified. [ambiguous.join]
All Finished!
```

sqlfluff stays an **optional** dependency — sqlalign's own guarantee does not
rest on a linter, so neither does its install.

It lints what *would* be written, so `--lint --check` reports on the formatted
result rather than on whatever is currently on disk. Exit codes are `0` clean,
`1` findings (or `--check` changes), `2` an error.

A committed `.sqlfluff` wins — your file is a decision someone made, and
overriding it silently would be the wrong kind of helpful. The generated
coexistence config is only the fallback when there is nothing to respect. If
you write one by hand rather than generating it, expect `AL01` noise: sqlfluff
defaults to requiring `AS` on table aliases, and sqlalign's default omits it.

### A shared sqlfluff config

`--lint` uses the `.sqlfluff` it finds beside your file, walking upwards the way
sqlfluff itself does. A config your team keeps outside the repository is not on
that path, so name it:

```sh
sqlalign --lint --sqlfluff-config ~/team/shared.sqlfluff models/
```

It wins over anything discovered. One editor action can then format and report
your team's findings together — in a JetBrains External Tool, that is
`--lint --sqlfluff-config /path/to/shared.sqlfluff "$FilePath$"`.

It is refused without `--lint`, and refused if the path does not exist: taking a
config nobody reads would leave you believing your rules had run.

**`--lint` reports; it never fixes.** `sqlfluff fix` rewrites SQL — qualifying
joins, changing cast styles — which is the thing sqlalign does not do, and a
flag that reads like a reporter is a poor place to hide a rewriter. Run it
yourself as a second step if you want it; the generated `.sqlfluff` excludes the
whole `layout` group, so `sqlfluff fix` will not undo the alignment.

## The settings panel

> **The panel is experimental.** It is useful and it is tested, but it is the
> one part of sqlalign not covered by any stability promise: its layout and
> behaviour may change without a major version. The CLI and the config file are
> the supported surface.

Eighteen knobs is more than anyone wants to read about. `sqlalign --gui` opens a
panel next to a live preview — every control re-runs the real engine on the text
in the pane, so what you see is what the CLI would write:

```console
sqlalign --gui
sqlalign --gui --dialect tsql
```

Left: dialect, preset, and every setting. Right: an editable input pane over the
formatted result, with a status line counting statements and naming anything that
declined. A setting that currently does nothing is greyed out, with a line saying
which control it is waiting on rather than leaving it looking broken.

**Save settings** writes a `.sqlalign.toml` with the values written out live,
because they are choices you just made — the opposite of what `--init` writes,
and for the opposite reason. The file records which preset it matches, if any,
and the dialect you previewed with as a comment, since `dialect` has no config
key. **Open** loads a real `.sql` file in place of the bundled samples.

**Open SQL…** (`⌘O`) loads a file, and the window is named for it from then on.
**Save formatted…** (`⌘S`) defaults to that same file, so formatting one in place
is the one-click path and matches what `sqlalign <file>` writes byte for byte;
the dialog still asks before overwriting. **Copy formatted** is `⌘⇧C` rather than
`⌘C`, which stays yours for ordinary copying.

**Save config…** writes a `.sqlalign.toml` — the same text `--show-config`
prints — so the panel is a way to *find* a style, not a second place to keep
one.

It is Tkinter, from the standard library, so it needs no extra install. On Debian
and Ubuntu that means `apt install python3-tk`; the flag says so if it is missing
rather than failing with a traceback.

## Editors

sqlalign has no editor plugin and does not need one: it reads stdin, writes
stdout, and starts in about a tenth of a second, which is what every editor's
"external formatter" setting expects.

**Filter the buffer** (vim, Neovim, Helix, and anything with a `!` filter):

```vim
:%!sqlalign -
```

`-` resolves `.sqlalign.toml` from the working directory, so a committed config
is picked up. `/dev/stdin` also works as a path, but config discovery walks up
from the *path you named* and `/dev/stdin` is not in your repository — so that
route needs an explicit `--config` and `-` does not.

**Format on save.** Point any "run a command on save" extension at the file and
let the editor reload it:

```sh
sqlalign "$FILE"
```

**VS Code.** sqlalign reads stdin, so it can be registered as a real formatter
rather than as a command bolted onto save — which is what makes `Format
Document`, format-on-save and format-selection all work through the same path.
With the [Custom Local Formatters](https://marketplace.visualstudio.com/items?itemName=jkillian.custom-local-formatters)
extension:

```json
{
  "customLocalFormatters.formatters": [
    { "command": "sqlalign -", "languages": ["sql"] }
  ],
  "[sql]": { "editor.formatOnSave": true }
}
```

Any "run a command on save" extension works too, pointed at `$FILE` as above —
but it formats the file behind the editor's back and makes you reload, where the
formatter route does not.

**JetBrains (DataGrip, PyCharm, IntelliJ).** Use the bundled **File Watchers**
plugin rather than looking for a marketplace listing — Settings → Tools → File
Watchers → `+` → `<custom>`:

If your projects target different engines, put `dialect = "redshift"` (or
`postgres`, `tsql`) in each one's `.sqlalign.toml` — the watcher then needs no
`--dialect` and is right in every project.

| Field | Value |
|---|---|
| File type | SQL |
| Program | `sqlalign` |
| Arguments | `$FilePath$` |
| Output paths to refresh | `$FilePath$` |
| Advanced → Auto-save edited files | off |

Turning auto-save off is what stops the watcher firing on every keystroke.

## Git pre-commit hook

The repository ships hooks for [pre-commit](https://pre-commit.com), which is
the no-maintenance route:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/pua2/sqlalign
    rev: v1.1.0
    hooks:
      - id: sqlalign          # rewrites and restages
      # - id: sqlalign-check  # or: only report, change nothing
```

`sqlalign` rewrites and fails so you restage — the pre-commit convention for a
formatter. `sqlalign-check` only reports, which is what a repository you do not
own wants.

For CI there is a published action, which defaults to `--check` because an
action that silently reformats a checkout is not what a gate is for:

```yaml
- uses: pua2/sqlalign@v1.1.0
  # with:
  #   args: --diff          # show the change, not just the file names
  #   paths: models/
```

Without the framework, a hand-rolled hook that formats staged SQL and re-stages
what changed:

```sh
#!/bin/sh
# .githooks/pre-commit
files=$(git diff --cached --name-only --diff-filter=ACM -- '*.sql')
[ -z "$files" ] && exit 0
sqlalign $files || exit $?
git add $files
```

```sh
chmod +x .githooks/pre-commit
git config core.hooksPath .githooks
```

If you would rather **reject** an unformatted commit than fix it, swap the body
for `sqlalign --check $files` and drop the `git add`.

## CI

One command, no wrapper needed. `--check` exits `1` when anything would change,
which is exactly what a CI runner treats as a failure:

```sh
sqlalign --check .
```

Wrapped in a job step:

```yaml
- name: Check SQL formatting
  run: |
    pip install sqlalign
    sqlalign --check .
```

Use `--diff` instead of `--check` if you want the log to show what is wrong
rather than only which files are:

```sh
sqlalign --diff .
```

## Starting a config

`sqlalign --init` writes a `.sqlalign.toml` next to you:

```sh
sqlalign --init                     # house defaults
sqlalign --init --preset compact    # start from a published style guide
```

Every setting is written **commented out**, showing the value currently in
effect, so the file changes nothing until you uncomment something. That is
deliberate: a starter that arrived pinning all eighteen settings would freeze
you on whatever the defaults were the day you ran it and call that a decision.

`--preset` is the exception and is written live, because choosing one is the
decision you just made. The commented values below it then show what that preset
does, which makes the file worth reading as well as editing.

It refuses to overwrite an existing config.

## Calling it from Python

The CLI is the supported surface for a repository. For a notebook, a dbt hook or
a code generator, there are two functions:

```python
import sqlalign

sqlalign.format("select a,b from t;")
# 'SELECT a\n     , b\nFROM t;'

result = sqlalign.format_result(sql, dialect="redshift")
result.text          # the formatted SQL
result.statements    # how many statements were seen
result.declines      # what was passed through, and why
```

`format` returns the text. `format_result` also tells you what happened, which
matters more than it sounds: a statement sqlalign cannot model comes back
**byte-identical rather than raising**, so `format` alone cannot distinguish
"formatted" from "left alone" — both are valid SQL. Anything that gates on the
result should read `declines`.

`style` takes a `Style`, so a preset carries across:

```python
from sqlalign.style import preset_style

sqlalign.format(sql, style=preset_style("compact"))
```

An unsupported `dialect` raises `ValueError` rather than being formatted with
keywords the target engine may not accept.

These two names are the API. Everything else under `sqlalign.*` is internal and
moves between releases.

Because sqlalign does not lint, it slots in beside a linter rather than replacing
one — running `sqlfluff lint` in a neighbouring step is the intended setup.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. Files were formatted, or `--check`/`--diff` found nothing to change. Statements that passed through untouched still exit `0`. |
| `1` | `--check` or `--diff` found at least one file that would change. `--max-declines` and `--lint` return `1` on their own findings too — `1` always means a gate found something, never a malfunction. |
| `2` | A file could not be read, a config file is invalid, or an argument is invalid (an unsupported `--dialect`, an unknown `--align-targets` name, two output modes at once). Also an unexpected engine error on a single file. |

Exit `2` is per-file where it can be: an unreadable or misconfigured file is
reported on stderr and skipped, and **the rest of the run still processes**. The
final exit code is the worst one seen.

```sh
sqlalign nope.sql
# sqlalign: [Errno 2] No such file or directory: 'nope.sql'
# exit 2
```

## Where to go next

- **The full flag reference:** [`cli.md`](cli.md) — every flag, its default, and
  which ones conflict.
- **Committing a team style:** [`configuration.md`](configuration.md) — config
  file discovery, presets, and precedence.
- **What the output looks like, construct by construct:** [`style.md`](style.md).
- **The style itself, in the repo:** `samples/queries.sql` is 29 hand-formatted
  goldens that the test suite asserts byte-for-byte. It is the executable spec.
- **Positioning, presets and the honest comparison against other formatters:**
  the [README](../../README.md).
- **Architecture, the alignment formula, the safety model:**
  the [Architecture](architecture.md) page.
