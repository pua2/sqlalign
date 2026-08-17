# Command-line reference

```
sqlalign [OPTIONS] FILE_OR_DIRECTORY...
```

Every flag below is the complete set — this page is written against
`sqlalign --help` and nothing is omitted. New to the tool? Start with
[Getting started](getting-started.md); this page is for looking things up. For
committing these settings to a config file rather than typing them, see
[Configuration](configuration.md).

Defaults marked *(house)* are what you get with no config file and no flags.

## Positional arguments

| Argument | Default | What it does |
|---|---|---|
| `files …` | required | One or more files or directories. A **directory** is searched recursively for `*.sql`, in sorted order so a run is reproducible. At least one path is required, including with `--show-config`. |
| `-h`, `--help` | — | Print the usage summary and exit. |

A single `-` reads stdin and writes the result to stdout, which is what an
editor's format-on-save runs through a generic external-formatter setting:

```sh
cat query.sql | sqlalign -
sqlalign - --dialect tsql < query.sql
```

`--check` and `--diff` still report rather than write when the input is `-`,
so `sqlalign --check -` is a gate on piped SQL. `-` cannot be combined with
file arguments: stdin is read once, so that has no sensible reading.

## Output mode

By default sqlalign **rewrites each file in place** and prints nothing. These
flags change that.

| Flag | Default | What it does |
|---|---|---|
| `--check` | off | Write nothing. Print `would reformat <path>` for each file that is not already formatted. Exit `1` if any is. |
| `--stdout` | off | Write the formatted result to stdout instead of rewriting the file. Exit `0`. |
| `--diff` | off | Write nothing. Print a unified diff of what would change. Exit `1` if anything would. |
| `--line-ending {auto,lf,crlf}` | `auto` | Line endings to write. `auto` preserves each file's own — a CRLF file stays CRLF, and an already-formatted CRLF file does not report a spurious diff. |

**`--check`, `--stdout` and `--diff` are mutually exclusive.** Passing two is an
argument error, not a silent precedence rule:

```
sqlalign: error: argument --stdout: not allowed with argument --check
```

A file with lone `\r` (classic-Mac) line endings is not a shape sqlalign models;
it is passed through untouched with a warning.

## File selection

| Flag | Default | What it does |
|---|---|---|
| `--exclude GLOB` | none | Skip files matching this glob **when expanding a directory**. Repeatable. Matched against the path relative to the directory you named (posix separators, `fnmatch` semantics) and against the bare filename. Also settable as `exclude` in a config file. |

A file you name explicitly on the command line is **never** excluded — naming it
is a clearer signal of intent than a pattern in a config file.

Exclusions are resolved per directory argument, before per-file style
resolution, from `--exclude` plus the `exclude` key of the config discovered at
that directory.

## Configuration

sqlalign reads a `.sqlalign.toml`, or a `[tool.sqlalign]` table in a
`pyproject.toml`, discovered by walking up from each file being formatted.
Precedence is **built-in defaults → preset → config file → command-line flags**.

| Flag | Default | What it does |
|---|---|---|
| `--config PATH` | discovered | Use this config file instead of discovering one. |
| `--isolated` | off | Ignore any config file and use the built-in defaults. |
| `--show-config` | off | Print the effective settings as TOML and exit `0`. Formats nothing. The first line is the config file they came from, or `# built-in defaults (no config file found)`. |
| `--no-strict-config` | off | Warn on unknown config keys instead of failing. By default an unknown key is a hard **error** (exit `2`) — a typo in a committed config would otherwise mean a team believes it has a setting it does not have. |
| `--dialect {postgres,redshift,tsql}` | `postgres` | Dialect to parse and emit. **CLI only** — there is no `dialect` config key. |

`--show-config` resolves the config against the **first** path you give it, so
in a repo with per-directory configs, ask about the directory you care about.
Its output is valid TOML you can paste straight into a config file:

```sh
sqlalign --show-config .
```

```toml
# built-in defaults (no config file found)
width = 100
align = true
align_targets = ["aliases", "case_results", "column_aliases", "column_constraints", "column_types", "join_conditions", "operators", "table_aliases"]
comma_position = "leading"
boolean_operator_position = "leading"
on_placement = "inline"
select_placement = "inline"
select_indent = 2
clause_keyword_align = "left"
river_gutter = 6
format_dollar_bodies = true
neq_style = "!="
decimal_style = "NUMERIC"
table_alias_style = "bare"
keyword_case = "upper"
protect_templating = true
# blank_lines_between_statements is unset: one blank line between two
# multi-line statements, none otherwise. Set an integer to force a count.
```

### Valid config keys

Every key below is accepted in `.sqlalign.toml` or `[tool.sqlalign]`. Anything
else is an error unless you pass `--no-strict-config`.

`align` · `align_targets` · `blank_lines_between_statements` ·
`boolean_operator_position` · `clause_keyword_align` · `comma_position` ·
`decimal_style` · `exclude` · `format_dollar_bodies` · `keyword_case` ·
`neq_style` · `on_placement` · `preset` · `protect_templating` · `river_gutter` ·
`select_indent` · `select_placement` · `table_alias_style` · `width`

`--dialect` and `--line-ending` are the only settings with no config key. Three
of the four `--no-*` flags map onto the positive keys: `--no-align` is
`align = false`, `--no-protect-templating` is `protect_templating = false`, and
`--no-format-bodies` is `format_dollar_bodies = false`. The fourth,
`--no-strict-config`, is not a style setting at all — it changes how the config
file itself is read.

## Style

| Flag | Default | What it does |
|---|---|---|
| `--preset {compact,dbt,gitlab,house,river,trailing}` | none (the `house` defaults) | Named starting point. Sets a **base** that config keys and flags then layer on top of, so `--preset compact --comma-position trailing` means both. Not passing it leaves any `preset` key in your config file in force. |
| `--width WIDTH` | `100` | Target line width for wrapping decisions. Not a hard cap: a construct anchored deep in an indent gets a floor of `anchor + 60`, plus 5 characters of grace, so alignment is never sacrificed to shave two columns. |
| `--blank-lines-between-statements N` | unset | Force N blank lines between every pair of statements. Unset is the house rule: exactly one blank line between two **multi-line** statements and none otherwise, so a run of one-line `GRANT`s stays a block. `0` removes them all. |
| `--no-align` | off *(aligned)* | Emit one space between tokens instead of padding them into columns. Same line structure, no padding — this is what 9 of 10 published SQL style guides call for. |
| `--align-targets a,b,…` | all but `table_names` | Comma-separated alignment columns to keep. Anything left out collapses to a single space. See the table below. |
| `--comma-position {leading,trailing}` | `leading` *(house)* | Where the separator comma sits in a stacked list — select items, `GROUP BY`/`ORDER BY` terms, `INSERT` columns, `UPDATE SET` assignments, `CREATE TABLE` columns, window terms. |
| `--boolean-operator-position {leading,trailing}` | `leading` *(house)* | Where `AND`/`OR` sit when a predicate spans lines. The condition column is identical either way — only the operator moves. |
| `--on-placement {inline,own_line}` | `inline` *(house)* | Whether a `JOIN`'s `ON` rides the table line or drops below it. **`own_line` retires the FROM-block-wide `ON` column** — there is no longer an `ON` after each alias to align — so `join_conditions` has nothing to act on. |
| `--keyword-case {upper,lower}` | `upper` *(house)* | Case for keywords, function names and type names. Your identifiers and string literals are never touched. |
| `--neq-style {!=,<>}` | `!=` | Spelling for the not-equal operator. |
| `--decimal-style {NUMERIC,DECIMAL}` | `NUMERIC` | Spelling for the `NUMERIC`/`DECIMAL` type. |
| `--no-protect-templating` | off *(protected)* | Do not mask Jinja/dbt template expressions (`{{ … }}`, `{% … %}`, `{# … #}`) before formatting. With protection on, each expression becomes a same-width placeholder, so alignment is computed against the real text width. |
| `--no-format-bodies` | off *(formatted)* | Leave dollar-quoted (`$$`) procedure and function bodies untouched. On by default; off passes the whole `CREATE FUNCTION`/`PROCEDURE` through byte-identical. |

`--neq-style` and `--decimal-style` exist because sqlglot's parser collapses each
of those pairs into a single AST node, so a spelling has to be chosen when
printing. They are the only two places sqlalign picks for you.

### Presets

| Preset | Sets |
|---|---|
| `house` | nothing — the built-in columnar default: aligned, leading separators, `ON` inline, uppercase keywords |
| `compact` | `align = false` |
| `trailing` | `comma_position = "trailing"`, `boolean_operator_position = "trailing"` |
| `dbt` | `keyword_case = "lower"`, `comma_position = "trailing"`, `align = false` |

`dbt` ships with its one remaining deviation stated: dbt indents nested blocks 4,
sqlalign indents a CTE body 2.

### Alignment targets

| Target | Aligns |
|---|---|
| `aliases` | `AS x` in a select list, and table aliases in `FROM`/`JOIN` |
| `operators` | `=`, `!=`, `<`, `LIKE`, `IS` … in `WHERE`/`ON`/`HAVING`/`CASE` |
| `join_conditions` | the `ON`/`AND` column across a whole `FROM` block |
| `case_results` | `THEN` in a short-form `CASE` |
| `column_types` | column types in `CREATE TABLE` |
| `column_constraints` | `NOT NULL`/`DEFAULT`, and Redshift `ENCODE` |
| `column_aliases` | the alias column alone, inside a select list |
| `table_aliases` | the alias column alone, across a `FROM`/`JOIN` block |
| `table_names` | table names padded to a shared column — opt-in, not in the default set |

An unknown name is an error, not a silent no-op:

```
sqlalign: unknown align_targets ['alias']; valid: ['aliases', 'case_results', 'column_aliases', 'column_constraints', 'column_types', 'join_conditions', 'operators', 'table_aliases', 'table_names']
```

`--no-align` is the shorthand for switching every target off.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success, including `--check`/`--diff` finding nothing to change, and including statements that passed through untouched with a warning. |
| `1` | Something a gate should fail on: `--check` or `--diff` found a file that would change, `--max-declines` was exceeded, or `--lint` reported findings. |
| `2` | Unreadable file, invalid config file, invalid argument, or an unexpected engine error on one file. |
| `141` | The reader closed the pipe (`sqlalign - \| head`). The shell's spelling of death-by-SIGPIPE; not an error. |

Per-file failures do not abort the run: the file is reported on stderr and
skipped, the rest still process, and the worst code seen is returned.

```sh
sqlalign --check nope.sql still.sql
# sqlalign: [Errno 2] No such file or directory: 'nope.sql'
# would reformat still.sql
# exit 2
```

## Worked invocations

Each block below is real output. The input for the style examples is
`tests/fixtures/input/13.sql`; the default rendering of it is in the
[README](../../README.md).

### Adopt the line structure without the padding

```sh
sqlalign --stdout --no-align 13.sql
```

```sql
-- #13: multi-character aliases (mixed lengths)
SELECT cust.customer_id
     , cust.email
     , ord.order_id
     , ord.total
     , line_items.product_id
     , line_items.quantity
     , addr.city
FROM customers cust
INNER JOIN orders ord ON ord.customer_id = cust.customer_id
LEFT JOIN order_line_items line_items ON line_items.order_id = ord.order_id
LEFT JOIN shipping_addresses addr ON addr.order_id = ord.order_id
  AND addr.address_type = 'shipping'
WHERE ord.order_date >= '2026-07-01'
  AND cust.segment = 'enterprise';
```

### Keep the alignment, move the separators to end of line

```sh
sqlalign --stdout --preset trailing 13.sql
```

```sql
-- #13: multi-character aliases (mixed lengths)
SELECT cust.customer_id,
       cust.email,
       ord.order_id,
       ord.total,
       line_items.product_id,
       line_items.quantity,
       addr.city
FROM customers               cust
INNER JOIN orders            ord        ON ord.customer_id     = cust.customer_id
LEFT JOIN order_line_items   line_items ON line_items.order_id = ord.order_id
LEFT JOIN shipping_addresses addr       ON addr.order_id       = ord.order_id AND
                                           addr.address_type   = 'shipping'
WHERE ord.order_date >= '2026-07-01' AND
      cust.segment    = 'enterprise';
```

### Drop `ON` to its own line

Note what this costs: the `ON` conditions are now justified within each join
instead of into one column spanning the whole `FROM` block.

```sh
sqlalign --stdout --on-placement own_line 13.sql
```

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
INNER JOIN orders            ord
   ON ord.customer_id     = cust.customer_id
LEFT JOIN order_line_items   line_items
   ON line_items.order_id = ord.order_id
LEFT JOIN shipping_addresses addr
   ON addr.order_id       = ord.order_id
  AND addr.address_type   = 'shipping'
WHERE ord.order_date >= '2026-07-01'
  AND cust.segment    = 'enterprise';
```

### Lowercase everything

```sh
sqlalign --stdout --keyword-case lower 13.sql
```

```sql
-- #13: multi-character aliases (mixed lengths)
select cust.customer_id
     , cust.email
     , ord.order_id
     , ord.total
     , line_items.product_id
     , line_items.quantity
     , addr.city
from customers               cust
inner join orders            ord        on ord.customer_id     = cust.customer_id
left join order_line_items   line_items on line_items.order_id = ord.order_id
left join shipping_addresses addr       on addr.order_id       = ord.order_id
                                       and addr.address_type   = 'shipping'
where ord.order_date >= '2026-07-01'
  and cust.segment    = 'enterprise';
```

Identifiers and string literals are untouched — the pass is AST-driven.

### Format a dbt model

`--preset dbt` is `keyword_case = "lower"` + trailing commas + no padding.
Jinja survives because it is masked with same-width placeholders before parsing.

```sh
sqlalign --stdout --preset dbt orders.sql
```

```sql
select o.id,
       o.total,
       c.email
from {{ ref('orders') }} o
join {{ ref('customers') }} c on c.id = o.customer_id
where o.status = 'complete';
```

### Give a long window function room

`--width` moves the wrapping threshold. Same query, two widths:

```sh
sqlalign --stdout wide.sql            # default width 100
```

```sql
SELECT customer_id
     , SUM(total) OVER (PARTITION BY customer_id ORDER BY order_date
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total
FROM orders;
```

```sh
sqlalign --stdout --width 200 wide.sql
```

```sql
SELECT customer_id
     , SUM(total) OVER (PARTITION BY customer_id ORDER BY order_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total
FROM orders;
```

Lowering `--width` below the default often changes nothing, because the
`anchor + 60` floor keeps a deeply-indented construct readable regardless.

### Space out a migration script

```sh
sqlalign --stdout --blank-lines-between-statements 1 multi.sql
```

```sql
TRUNCATE TABLE staging_orders;

TRUNCATE TABLE staging_customers;

SELECT a
     , b
FROM t
WHERE a = 1
  AND b = 2;

SELECT c
FROM u;
```

With the flag unset, the two `TRUNCATE`s stay packed together and only the
multi-line statements get air:

```sql
TRUNCATE TABLE staging_orders;
TRUNCATE TABLE staging_customers;
SELECT a
     , b
FROM t
WHERE a = 1
  AND b = 2;

SELECT c
FROM u;
```

### Gate a repository in CI

```sh
sqlalign --check --exclude 'vendor/*' --exclude '*.gen.sql' .
```

```
would reformat models/marts/rollup.sql
would reformat models/users.sql
```

Exit `1`. Swap `--check` for `--diff` when you want the log to show the change
rather than only the filename.
