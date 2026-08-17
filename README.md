# sqlalign

A SQL formatter for **Postgres**, **Redshift** and **SQL Server** that **cannot
change what your SQL means**. Every statement it formats is re-parsed and
compared against the input as a syntax tree, and that tree is what "means" is
measured by here. If the output would differ, or would carry different comments,
or if the engine doesn't fully model the construct, the statement is passed
through **byte-identical** with a warning instead.

It reformats presentation and nothing else: identifiers, string literals, cast
form (`::` vs `CAST`), `GROUP BY` references, comments, and alias choices all
survive exactly as written. Two spellings do collapse at parse time and are
therefore sqlalign's to pick rather than yours — `!=` against `<>`, and
`decimal` against `numeric` — so both are settings rather than accidents.

It formats *inside* dollar-quoted (`$$`) plpgsql procedure and function bodies,
and **`--lint` reads inside them too, which sqlfluff cannot**: to its parser a
body is a single string literal, so nothing within one is ever linted.

Its layout is **columnar alignment**: operators, aliases, `AS` clauses, and
`ON`/`AND` conditions are padded into vertical columns by a fixpoint resolver —
alignment is the layout engine itself, not a post-pass over already-printed
text.

```sql
FROM customers               cust
INNER JOIN orders            ord        ON ord.customer_id     = cust.customer_id
LEFT JOIN order_line_items   line_items ON line_items.order_id = ord.order_id
LEFT JOIN shipping_addresses addr       ON addr.order_id       = ord.order_id
                                       AND addr.address_type   = 'shipping'
```

## How it compares

Alignment is not unique to sqlalign, and this README won't pretend otherwise:
**sqlfluff** aligns column aliases (`layout:type:alias_expression` with
`spacing_before = align`), SSMS ships alignment options, and DataGrip aligns a
good deal including inside `$$` bodies. **pgFormatter**, **DataGrip**, and
**prettier-plugin-sql-cst** all format `$$` bodies too.

What is actually distinctive here:

- **The `ON`/`AND` condition column.** Aligning every JOIN's `ON` and `AND`
  conditions into one column spanning the whole `FROM` block (see above) is not
  something the surveyed tools do — their analogues break the line instead of
  padding it.
- **Alignment as the engine, not a bolt-on.** sqlalign lays out lines first and
  resolves every alignment column in a second fixpoint pass, so alignment
  composes with wrapping instead of fighting it. For contrast, sql-formatter
  removed its `tabulateAlias` and `commaPosition` options in v14 — both were
  post-hoc rewrites over already-printed text.
- **The semantic guarantee above.** Formatters that also apply lint fixes can
  rewrite your SQL — running `sqlfluff fix` on a query will happily turn `JOIN`
  into `INNER JOIN`, insert `AS`, and reorder your `ON` operands. sqlalign
  structurally cannot: a changed AST means the statement is passed through
  untouched.

sqlalign deliberately does **not** lint. It won't unify your cast styles, force
aliases, or make `GROUP BY` references consistent — that's sqlfluff's job, and
the two are designed to run together.

**Want only some of the alignment?** `--align-targets` picks which columns are
padded; anything left out collapses to a single space. `--no-align` is the
shorthand for none of them.

| Target | Aligns |
|---|---|
| `aliases` | both of the next two |
| `column_aliases` | `AS x` in a select list |
| `table_aliases` | the alias in `FROM orders o` |
| `table_names` | the table after each `FROM`/`JOIN`, padding the keyword out to a shared column — **opt-in**, not part of the house style |
| `operators` | `=`, `!=`, `<`, `LIKE`, `IS` … in `WHERE`/`ON`/`HAVING`/`CASE` |
| `join_conditions` | the `ON`/`AND` column across a whole `FROM` block |
| `case_results` | `THEN` in a short-form `CASE` |
| `column_types` | column types in `CREATE TABLE` |
| `column_constraints` | `NOT NULL`/`DEFAULT`, and Redshift `ENCODE` |

```sh
sqlalign --align-targets aliases,operators query.sql
```

`table_names` is the one target the default set leaves out, because it changes
the shape of the `FROM` block rather than refining it:

```sql
-- house                        -- with table_names
FROM customers      cust        FROM       customers cust
LEFT JOIN orders    ord         LEFT JOIN  orders    ord
INNER JOIN payments pay         INNER JOIN payments  pay
```

```sh
sqlalign --align-targets aliases,table_names,operators,join_conditions query.sql
```

**Prefer trailing commas?** `--comma-position trailing` moves every separator
comma to the end of the preceding line (including onto the last line of a
multi-line item, and onto a CTE's closing paren):

```sql
SELECT cust.customer_id,
       cust.email,
       ord.total
FROM customers cust
```

**Don't want the alignment?** `--no-align` keeps sqlalign's line structure and
drops the padding, which is what most published SQL style guides call for:

```sql
FROM customers cust
INNER JOIN orders ord ON ord.customer_id = cust.customer_id
LEFT JOIN order_line_items line_items ON line_items.order_id = ord.order_id
LEFT JOIN shipping_addresses addr ON addr.order_id = ord.order_id
  AND addr.address_type = 'shipping'
```

## Install

```sh
pip install sqlalign
```

That puts a `sqlalign` command on your PATH.

If your environment already carries sqlglot — a dbt or SQLMesh project usually
does — install sqlalign in isolation instead, so the two version ranges cannot
collide:

```sh
uv tool install sqlalign
```

Releases are published from CI with [signed provenance](https://pypi.org/project/sqlalign/),
so PyPI records which repository and workflow built each artifact. For a tool
that rewrites your source files, that is worth checking.

`--lint` runs sqlfluff over the formatted result and needs the optional extra:

```sh
pip install 'sqlalign[lint]'
```

Requires Python ≥ 3.10. Runtime dependency: `sqlglot` (`>=30.14,<31` — the
layout engine reads exact AST shapes, which a test suite asserts by name across
the range; see `pyproject.toml`), plus `tomli` on 3.10, which later versions ship
as `tomllib`.

To work on sqlalign itself, install from a clone instead — that adds the dev
tools (`pytest`, `ruff`, `sqlfluff`):

```sh
git clone https://github.com/pua2/sqlalign && cd sqlalign
uv sync
```

## Usage

Look before you leap — this is a formatter with an opinion, and the first thing
worth knowing is what it would do to your SQL:

```sh
sqlalign --diff .                  # show what would change, write nothing
sqlalign --check .                 # exit non-zero if anything would change
```

Then, once you have seen it:

```sh
sqlalign query.sql                 # format in place (rewrites the file)
sqlalign .                         # every *.sql under here, recursively
sqlalign --stdout query.sql        # print the formatted result, leave the file
cat query.sql | sqlalign -         # read stdin, write stdout
sqlalign --dialect redshift ddl.sql
sqlalign --dialect tsql query.sql   # SQL Server: TOP, [brackets]
sqlalign --width 120 query.sql
```

### Before → after

Input (`tests/fixtures/input/13.sql`):

```sql
-- #13: multi-character aliases (mixed lengths)
select cust.customer_id, cust.email, ord.order_id, ord.total, line_items.product_id, line_items.quantity, addr.city from customers cust inner join orders ord on ord.customer_id = cust.customer_id left join order_line_items line_items on line_items.order_id = ord.order_id left join shipping_addresses addr on addr.order_id = ord.order_id and addr.address_type = 'shipping' where ord.order_date >= '2026-07-01' and cust.segment = 'enterprise';
```

Output:

```sql
-- #13: multi-character aliases (mixed lengths)
SELECT cust.customer_id
     , cust.email
     , ord.order_id
     , ord.total
     , line_items.product_id
     , line_items.quantity
     , addr.city
FROM customers               cust
INNER JOIN orders            ord        ON ord.customer_id     = cust.customer_id
LEFT JOIN order_line_items   line_items ON line_items.order_id = ord.order_id
LEFT JOIN shipping_addresses addr       ON addr.order_id       = ord.order_id
                                       AND addr.address_type   = 'shipping'
WHERE ord.order_date >= '2026-07-01'
  AND cust.segment    = 'enterprise';
```

## In a pipeline

```yaml
# .pre-commit-config.yaml                    # GitHub Actions
repos:                                       - uses: pua2/sqlalign@v1.1.0
  - repo: https://github.com/pua2/sqlalign   #   defaults to --check: a gate,
    rev: v1.1.0                              #   not a formatter
    hooks:
      - id: sqlalign          # or sqlalign-check to report only
```

Details and editor recipes are in the [guide](docs/guide/getting-started.md).

## dbt / Jinja

**Scope first:** the templating is handled, the warehouses mostly are not.
sqlalign parses Postgres, Redshift and SQL Server, so a dbt project on Snowflake
or BigQuery will parse some models and decline others. Snowflake is the next
dialect. If your warehouse is one of the three, read on.

Templated SQL isn't valid SQL, so most formatters decline it. sqlalign masks each
template expression with a **same-width** placeholder, formats normally, then puts
the original back — so alignment is computed against the real text width:

```sql
SELECT o.id
     , o.total
FROM {{ ref('orders') }}    o
JOIN {{ ref('customers') }} c ON c.id = o.customer_id
```

`{{ … }}`, `{% … %}` and `{# … #}` are recognised. An expression too short to hold
a unique placeholder (`{{x}}`) makes the file pass through untouched rather than be
approximated. Turn it off with `--no-protect-templating`.

## Configuration

Commit your team's style rather than passing flags every time. sqlalign reads a
`.sqlalign.toml`, or a `[tool.sqlalign]` table in `pyproject.toml`, discovered by
walking up from each file being formatted — so a repo can hold one config at its
root and a subdirectory can override it.

```toml
# .sqlalign.toml
width                     = 100            # 0 turns wrapping off
align                     = true
align_targets             = ["aliases", "operators", "join_conditions"]
comma_position            = "leading"      # or "trailing"
boolean_operator_position = "leading"      # or "trailing"
on_placement              = "inline"       # or "own_line"
format_dollar_bodies      = true
protect_templating        = true       # mask Jinja/dbt before formatting
exclude                   = ["vendor/*", "*.gen.sql"]
# blank_lines_between_statements = 1   # unset = one blank line only between
#                                      # two multi-line statements
neq_style                 = "!="           # or "<>"
decimal_style             = "NUMERIC"      # or "DECIMAL"
keyword_case              = "upper"        # or "lower"
table_alias_style         = "bare"         # or "as" -> FROM orders AS o
select_placement          = "inline"       # or "own_line"
select_indent             = 2              # when the list starts below SELECT
clause_keyword_align      = "left"         # or "river"
body_blank_lines          = 1              # inside a $$ body
river_gutter              = 6
```

Start from a **preset** and override what you want. If your team already follows
a published style guide, start at `compact` or the guide's own preset rather than
at `house` — the alignment is the house opinion, not a prerequisite:

| Preset | What it is |
|---|---|
| `house` | the columnar default — aligned, leading separators, `ON` inline |
| `compact` | **the one to start from if you follow a published guide** — same line structure, no alignment padding, which is what 9 of 10 of them ask for |
| `trailing` | keeps the alignment, moves commas and `AND`/`OR` to end of line |
| `dbt` | lowercase keywords, list stacked under a bare `select` at 4, trailing commas, no padding (one deviation: a CTE body indents 2, not 4) |
| `river` | [Holywell's guide](https://www.sqlstyle.guide/) — root keywords right-aligned to a 6-column gutter, joins on the far side of it, otherwise unpadded |
| `gitlab` | [GitLab's published guide](https://handbook.gitlab.com/handbook/enterprise-data/platform/sql-style-guide/) — list stacked at 2, trailing commas, `ON` on its own line, `AS` on table aliases, and column aliases the only thing aligned |

One consequence worth knowing before you adopt `house`: **alignment padding means
a diff is wider than the line you edited.** Rename a column to something longer
and every row in that block shifts to keep the column, so a one-token change can
touch twenty lines. That is inherent to columnar alignment rather than a bug in
this implementation — `compact` and `--no-align` keep the line structure and drop
the padding, and both make a diff show only what changed.

```toml
preset         = "compact"
comma_position = "trailing"   # layers on top of the preset
```

Precedence is **built-in defaults → preset → config file → command-line flags**.
`sqlalign --show-config file.sql` prints the effective settings (as TOML you can
paste into a config) and says which file they came from. `--isolated` ignores any
config; `--config PATH` uses a specific one.

An unknown key is an **error**, not a silent no-op — a typo in a committed config
would otherwise mean a team believes it has a setting it doesn't have.
`--no-strict-config` downgrades that to a warning.

## Flags

| Flag | Default | Effect |
|------|---------|--------|
| `files …` | — | files or directories (a directory is searched recursively for `*.sql`) |
| `--exclude GLOB` | none | skip files matching this glob (repeatable; also `exclude` in config) |
| `--blank-lines-between-statements N` | auto | force N blank lines between every pair of statements |
| `--check` | off | write nothing; name the files that would change; exit `1` if any would |
| `--diff` | off | write nothing; print a unified diff of what would change; exit `1` if any would |
| `--stdout` | off | write the result to stdout instead of rewriting the file |
| `--dialect {postgres,redshift,tsql}` | `postgres` | parse/emit dialect |
| `--width N` | `100` | target line width for wrapping decisions; `0` turns wrapping off |
| `--no-align` | off | emit one space between tokens instead of padding them into columns — same line structure, no alignment |
| `--align-targets a,b,…` | all but `table_names` | which alignment columns to keep (see below) |
| `--line-ending {auto,lf,crlf}` | `auto` | line endings to write; `auto` preserves each file's own |
| `--comma-position {leading,trailing}` | `leading` | where the separator comma sits in a stacked list |
| `--boolean-operator-position {leading,trailing}` | `leading` | where `AND`/`OR` sit when a predicate spans lines |
| `--on-placement {inline,own_line}` | `inline` | whether a JOIN's `ON` rides the table line or drops below it |
| `--no-format-bodies` | off | leave dollar-quoted (`$$`) procedure/function bodies untouched |
| `--no-protect-templating` | off | don't mask Jinja/dbt template expressions |
| `--neq-style {!=,<>}` | `!=` | spelling for the not-equal operator |
| `--decimal-style {NUMERIC,DECIMAL}` | `NUMERIC` | spelling for the NUMERIC/DECIMAL type |
| `--table-alias-style {bare,as}` | `bare` | print a table alias as `t a` or `t AS a` |
| `--select-placement {inline,own_line}` | `inline` | whether the first select item rides the `SELECT` line |
| `--select-indent N` | `2` | columns the select list indents when it starts below `SELECT` |
| `--clause-keyword-align {left,river}` | `left` | right-align root clause keywords to a gutter |
| `--river-gutter N` | `6` | the column a river aligns them to |
| `--keyword-case {upper,lower}` | `upper` | case for keywords, function names and types |
| `--preset {compact,dbt,gitlab,house,river,trailing}` | `house` | named starting point (flags and config keys layer on top) |
| `--config PATH` | discovered | use a specific config file |
| `--isolated` | off | ignore any config file |
| `--print-sqlfluff-config` | — | print a `.sqlfluff` that lets sqlfluff run alongside sqlalign, and exit |
| `--lint` | off | after formatting, run sqlfluff over the result (needs `pip install 'sqlalign[lint]'`) |
| `--body-blank-lines N` | `1` | blank lines between the elements of a `$$` body |
| `--gui` | — | *(experimental)* open a settings panel with a live preview, and exit |
| `--report` | off | print a coverage summary: how many statements formatted, and what the rest declined on. Adds output without changing the mode — pair with `--check` to survey without writing |
| `--max-declines N` | — | exit 1 if more than N statements pass through unformatted (implies `--report`) |
| `--show-config` | off | print effective settings as TOML and exit |
| `--no-strict-config` | off | warn on unknown config keys instead of failing |

`--neq-style` and `--decimal-style` exist because sqlglot's parser collapses each
of those pairs to a single node, so a spelling has to be chosen when printing.
They are the *only* two places sqlalign picks for you — everything the parser
preserves is passed through as written.

`--check` and `--stdout` are mutually exclusive. Exit codes: `0` success (or
`--check` clean); `1` a `--check` file differs; `2` an unreadable file or a
safety abort (other files in the same run still process).

## Linting inside `$$` bodies

sqlfluff cannot lint a plpgsql body — to its parser the whole body is one string
literal, so a function full of badly-written SQL passes clean. `--lint` closes
that: sqlalign already locates those statements in order to format them, so it
hands them to sqlfluff too, and a finding's line and column are the real ones.

```console
$ sqlalign --check --lint report.sql
L:   1 | P:  17 | CP03 | Function names must be upper case.
All Finished!
== [report.sql] inside $$ bodies (sqlfluff cannot reach these on its own)
L:   8 | P:   1 | AM04 | Query produces an unknown number of result columns. [ambiguous.column_count]
```

See the [guide](docs/guide/getting-started.md) for how it works and what it does
not cover.

## Guarantees & scope

- **Byte-for-byte golden fixtures** — 29 hand-formatted samples in
  `tests/fixtures/expected/` are the executable specification of the style; the
  suite asserts `format(input) == expected` for every one, plus idempotency
  (`format(expected) == expected`) and AST-equivalence.
- **sqlfluff lint gate** — every standard-SQL Postgres expected fixture lints
  clean under `.sqlfluff`. Three rules the house style deliberately conflicts
  with are excluded there, each documented: `ambiguous.column_references` (the
  source GROUP BY reference form is preserved), `aliasing.expression` (aliases
  are not forced), and `convention.casting_style` (`::` vs `CAST(...)` is
  preserved as written). The Redshift and plpgsql fixtures in
  `samples/queries.sql` are outside the gate (dialect / linter-model limits) —
  see `tests/test_sqlfluff_gate.py`.
- **v1 limitations** — constructs the engine does not model (e.g. some `MERGE`
  variants, cursors/`FOR` loops in plpgsql bodies, comments buried inside an
  expression) pass through byte-identical with a warning rather than being
  reformatted. This is by design: correctness over coverage.

## Adding a new golden sample

The fixtures are the spec — grow them, never weaken them:

1. Add the **hand-formatted** query to `samples/queries.sql` under a new
   `-- #N: description` header.
2. Regenerate the split fixtures: `python scripts/build_fixtures.py samples/queries.sql tests/fixtures/expected`.
   Add the corresponding messy `tests/fixtures/input/N.sql`.
3. Run `PYTHONPATH=src pytest -q`. If the new golden fails, either the sample
   exposes a real gap (fix the layout handler) or it's an unmodeled construct
   (it should pass through — confirm the warning, not a wrong render).
4. If the new expected output should lint clean, confirm the sqlfluff gate still
   passes (or document a new exclusion in `.sqlfluff` with a comment).

## Documentation

The documentation site is at **<https://sqlalign.lumaru.app/>**.

The [settings reference](https://sqlalign.lumaru.app/v1/settings.html) is the page to start with: it
shows every setting with the same SQL rendered under each of its values.

To read the site locally:

```sh
python3 -m http.server -d docs 8000    # then open http://localhost:8000/
```

**The site is generated and committed.** GitHub Pages runs no build step, so the
HTML in `docs/v1/` is what ships. Prose is authored in
`docs/guide/*.md`; every configuration example is produced by *running the
formatter* at build time, so no example on the site can drift from what the tool
does. After editing a guide page, a setting, or anything that changes output:

```sh
uv run python tools/build_docs.py     # rebuild docs/v1/
```

`tests/test_docs_site.py` fails if the committed site does not match what the
generator produces, so a forgotten rebuild cannot ship.

Versions are directories. `docs/v1/` is the only one today; a new version means
building into a new directory and adding it to `VERSIONS` in the generator.
Nothing in a built page points outside its own version, so an old one keeps
working untouched.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: `uv sync`, then `uv run pytest
-q` and `uv run ruff check .` must both be clean — CI runs the same checks on
every pull request. The goldens in `tests/fixtures/expected/` are the
specification, not snapshots.

**The style does not change in a patch release.** The goldens in
`tests/fixtures/expected/` are compared byte for byte in their own CI job, so an
unintended layout change cannot reach a release; an intended one waits for a
minor version and is named in the changelog. See
[Stability](docs/guide/style.md).

Release notes are in [CHANGELOG.md](CHANGELOG.md).

## Reference

- **Style reference:** `samples/queries.sql` — the hand-formatted goldens.
- **Lint config:** `.sqlfluff`.
