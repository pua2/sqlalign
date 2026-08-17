# Configuration

Commit your team's style once instead of passing flags on every run. Put a
`.sqlalign.toml` at the root of your repo, or a `[tool.sqlalign]` table in your
`pyproject.toml`, and every `sqlalign` invocation under that directory picks it
up.

```toml
# .sqlalign.toml
preset         = "trailing"
width          = 100
keyword_case   = "upper"
exclude        = ["vendor/*", "*.gen.sql"]
```

```sh
sqlalign --show-config models/orders.sql   # what will actually be applied
sqlalign --check .                         # CI gate, using the committed config
```

This page covers where sqlalign looks for that file, how it merges with presets
and flags, every key you can put in it, and what happens when you get it wrong.
For the flags themselves see [the CLI reference](cli.md); for what each setting
does to your SQL see [the style guide](style.md).

## Where sqlalign looks

sqlalign resolves a config **per file**, by walking up from the directory of the
file it is about to format. At each directory it takes the first of:

| Order | File | Shape |
|---|---|---|
| 1 | `.sqlalign.toml` | keys at the top level |
| 2 | `pyproject.toml` | keys under a `[tool.sqlalign]` table |

The first directory that has either one wins, and it stops there — settings are
**not** merged across levels. A `pyproject.toml` without a `[tool.sqlalign]`
table is skipped entirely, so a repo that already has a `pyproject.toml` for
other tools is unaffected until you add the table.

Two consequences worth knowing:

- **A subdirectory overrides its parent.** Drop a `.sqlalign.toml` in
  `legacy/` and everything under it formats by that file's rules, while the rest
  of the repo uses the root config. Because resolution is per file, one
  invocation can span both:

  ```sh
  sqlalign --stdout models/q.sql legacy/q.sql   # two files, two configs
  ```

- **The walk goes all the way to the filesystem root.** If no config exists in
  your project, a stray `.sqlalign.toml` in a parent directory or in your home
  directory will be found. `sqlalign --show-config` tells you which file was
  used; `--isolated` guarantees none is.

Both file forms accept the same keys, and the two forms mean the same thing:

```toml
# .sqlalign.toml
comma_position = "trailing"
```

```toml
# pyproject.toml
[tool.sqlalign]
comma_position = "trailing"
```

## Precedence

Settings compose in this order, each layer winning over the one before it:

```
built-in defaults  <  preset  <  config file  <  command-line flags
```

A **preset only supplies a base**. Any key you set explicitly — in the config
file or on the command line — layers on top of it, so `preset` plus an override
means both, not one or the other:

```toml
preset         = "compact"    # base: no alignment padding
comma_position = "trailing"   # ...and trailing commas on top
```

Two details follow from "a preset is only a base":

- **A `--preset` flag beats a `preset =` key**, because it is the newer answer to
  the same question. Config file `preset = "trailing"` plus
  `sqlalign --preset compact` gives you `compact`.
- **An explicit key beats a preset from either source.** A config file with
  `comma_position = "leading"`, run with `--preset dbt`, gives you dbt's
  lowercase keywords and unpadded output but keeps your leading commas. The
  relevant lines of `sqlalign --preset dbt --show-config q.sql`:

  ```
  # /home/you/warehouse/.sqlalign.toml
  align = false
  comma_position = "leading"
  keyword_case = "lower"
  ```

A flag you do not pass never overrides the config file. sqlalign distinguishes
"not passed" from "passed the value that happens to be the default", so
`--comma-position leading` forces leading commas even when the config says
trailing, while omitting the flag leaves the config's choice alone.

## Key reference

These are the complete set of keys a config file accepts. Anything else is an
error (see [Unknown keys](#unknown-keys-are-an-error)).

| Key | Type | Default | What it does |
|---|---|---|---|
| `preset` | `"house"` \| `"compact"` \| `"trailing"` \| `"dbt"` | none | Named starting point. Every other key layers on top of it. |
| `width` | integer | `100` | Target line width for wrapping decisions. A target, not a hard cap — see below. |
| `align` | boolean | `true` | Master alignment switch. `false` emits one space between tokens and keeps the same line structure. |
| `align_targets` | list of strings | all but `table_names` | Which alignment columns are padded — the nine names in the [CLI reference's target table](cli.md#alignment-targets). Anything omitted collapses to a single space. |
| `comma_position` | `"leading"` \| `"trailing"` | `"leading"` | Where the separator comma sits in a stacked list. |
| `boolean_operator_position` | `"leading"` \| `"trailing"` | `"leading"` | Where `AND`/`OR` sit when a predicate spans lines. |
| `on_placement` | `"inline"` \| `"own_line"` | `"inline"` | Whether a JOIN's `ON` rides the table line or drops below it. |
| `select_placement` | `"inline"` \| `"own_line"` | `"inline"` | Whether the first select item rides the `SELECT` line or the list starts below it. |
| `select_indent` | integer | `2` | Columns the select list indents when it starts below `SELECT`. Ignored when inline. |
| `clause_keyword_align` | `"left"` \| `"river"` | `"left"` | Root clause keywords flush left, or right-aligned so their last character lands on the gutter. |
| `river_gutter` | integer | `6` | The column a river aligns them to. 6 is the width of `SELECT`. |
| `table_alias_style` | `"bare"` \| `"as"` | `"bare"` | `FROM orders o` or `FROM orders AS o`. sqlglot destroys the distinction at parse time, so sqlalign must pick one. |
| `keyword_case` | `"upper"` \| `"lower"` | `"upper"` | Case for keywords, function names and type names. Your identifiers and string literals are never touched. |
| `neq_style` | `"!="` \| `"<>"` | `"!="` | Spelling for the not-equal operator. |
| `decimal_style` | `"NUMERIC"` \| `"DECIMAL"` | `"NUMERIC"` | Spelling for the `NUMERIC`/`DECIMAL` type. |
| `format_dollar_bodies` | boolean | `true` | Format inside dollar-quoted (`$$`) procedure and function bodies. `false` passes the whole `CREATE FUNCTION` through byte-identical. |
| `protect_templating` | boolean | `true` | Mask Jinja/dbt template expressions (`{{ }}`, `{% %}`, `{# #}`) before formatting so a templated model can be formatted at all. |
| `blank_lines_between_statements` | integer, or unset | unset | Force N blank lines between every pair of statements. Unset means the house rule: one blank line between two multi-line statements, none otherwise. |
| `exclude` | list of glob strings (a bare string is also accepted) | none | Skip matching files when a directory is expanded. Selects files, not style — see [Excluding files](#excluding-files). |

`neq_style` and `decimal_style` exist because the parser collapses each of those
pairs to a single node, so a spelling has to be chosen when printing. They are
the only two places sqlalign picks for you.

### Not config keys

`--dialect` and `--line-ending` are command-line only. Putting `dialect` in a
config file is a hard error:

```
sqlalign: /home/you/warehouse/sqlalign.toml: unknown setting(s) ['dialect']; valid: ['align', 'align_targets', ...]
```

(the real message lists every valid key; elided here for width).

### `width` is a target, not a limit

`width` drives wrapping decisions, but a construct anchored deep in an indent is
allowed some room past it rather than being shredded. On the multi-join sample,
`--width 60` produces output byte-identical to the default `--width 100`, whose
longest line is 81 characters. Lower `width` when you want more aggressive
wrapping; do not treat it as a hard maximum your lines cannot cross.

### `on_placement = "own_line"` retires the `ON` column

With `own_line`, the `ON` drops below the table reference (`FROM` block shown):

```sql
FROM customers               cust
INNER JOIN orders            ord
   ON ord.customer_id     = cust.customer_id
LEFT JOIN order_line_items   line_items
   ON line_items.order_id = ord.order_id
LEFT JOIN shipping_addresses addr
   ON addr.order_id       = ord.order_id
  AND addr.address_type   = 'shipping'
```

There is no longer an `ON` sitting after each alias, so the `join_conditions`
target has nothing to act on. Set `on_placement = "own_line"` and
`join_conditions` together and only the first has an effect.

## Presets

A preset is one word instead of a handful of keys. Pick the one closest to your
team's existing style and override the rest.

| Preset | Sets | For |
|---|---|---|
| `house` | nothing — the built-in defaults | Teams adopting sqlalign's columnar style as-is: aligned, leading separators, `ON` inline, uppercase keywords. |
| `compact` | `align = false` | Teams that want sqlalign's line structure without the padding. 9 of 10 published SQL style guides produce unpadded output, so this is the widest-reach starting point. |
| `trailing` | `comma_position = "trailing"`, `boolean_operator_position = "trailing"` | Teams that want the alignment but write trailing commas (8 of 10 guides) and trailing booleans (7 of 10). |
| `dbt` | `keyword_case = "lower"`, `comma_position = "trailing"`, `align = false` | dbt and analytics-engineering repos. |

Everything a preset does not set stays at the built-in default — `dbt` leaves
`boolean_operator_position` at `leading`, for instance. Run
`sqlalign --isolated --preset NAME --show-config file.sql` to see the full
picture for any of them.

The `house`, `compact` and `trailing` blocks below are all the same input query,
formatted under each preset; `dbt` uses a CTE query so its one deviation is
visible.

### `house`

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

### `compact`

Same line structure, every run of padding collapsed to one space.

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

### `trailing`

Alignment intact; both separators move to the end of the line.

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

### `dbt`, and its one deviation

```sql
-- #6: CTEs
with monthly_revenue as (
  select customer_id,
         date_trunc('month', order_date) as month,
         sum(total) as revenue
  from orders
  group by 1, 2
),

top_customers as (
  select customer_id
  from monthly_revenue
  group by customer_id
  having sum(revenue) > 10000
)

select m.customer_id,
       m.month,
       m.revenue
from monthly_revenue m
join top_customers t on t.customer_id = m.customer_id
order by m.customer_id,
         m.month;
```

**The deviation:** dbt's own style guide indents a nested block 4 spaces.
sqlalign indents a CTE body **2**, as you can see above. sqlalign's indent
literals are three different concepts internally rather than one knob, so there
is no setting that changes this. If your dbt project enforces 4-space CTE
bodies in review, this preset will fight you on exactly that one point and
nothing else.

## Unknown keys are an error

A key sqlalign does not recognise stops the run. The file is left untouched and
the exit code is `2`:

```
$ sqlalign q.sql
sqlalign: /home/you/warehouse/.sqlalign.toml: unknown setting(s) ['comma_postion']; valid: ['align', 'align_targets', 'blank_lines_between_statements', 'boolean_operator_position', 'comma_position', 'decimal_style', 'exclude', 'format_dollar_bodies', 'keyword_case', 'neq_style', 'on_placement', 'preset', 'protect_templating', 'width']
```

This is deliberate, and it is the opposite of what most tools do. A typo in a
committed config that is silently ignored means a whole team believes it has a
setting it does not have, and finds out from a surprising diff months later. A
failure at the moment you commit the typo costs you thirty seconds; a silent
no-op costs you the trust you had in the file.

The same strictness applies to values, not just key names. Every one of these
exits `2` without touching a file:

| Config | Error |
|---|---|
| `comma_position = "sideways"` | `comma_position must be 'leading' or 'trailing', got 'sideways'` |
| `width = "wide"` | `width must be an integer, got 'wide'` |
| `align_targets = ["aliases", "typo"]` | `unknown align_targets ['typo']; valid: ['aliases', 'case_results', 'column_aliases', 'column_constraints', 'column_types', 'join_conditions', 'operators', 'table_aliases', 'table_names']` |
| `preset = "nice"` | `unknown preset 'nice'; valid: ['compact', 'dbt', 'gitlab', 'house', 'river', 'trailing']` |
| malformed TOML | `invalid TOML: ...` |

### `--no-strict-config`

Pass `--no-strict-config` to downgrade the unknown-key failure to a warning. The
unrecognised key is dropped and formatting proceeds:

```
$ sqlalign --no-strict-config --stdout q.sql
sqlalign: /home/you/warehouse/.sqlalign.toml: unknown setting(s) ['comma_postion']; valid: [...]
SELECT a
     , b
FROM t
WHERE a = 1
  AND b = 2;
```

Use it when one config file is shared with a **newer** sqlalign that knows keys
your version does not — a mixed-version team, or a rollout in progress. Do not
use it as a default in CI: that gives you the silent no-op the strict behaviour
exists to prevent. Note that it only relaxes unknown **keys**; a bad *value* is
still a hard error either way.

## Inspecting what will be applied

`--show-config` prints the effective settings and the file they came from, then
exits without formatting anything:

```
$ sqlalign --show-config models/orders.sql
# /home/you/warehouse/.sqlalign.toml
width = 100
align = true
align_targets = ["aliases", "join_conditions", "operators"]
comma_position = "trailing"
boolean_operator_position = "trailing"
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

The body is valid TOML you can paste straight into a `.sqlalign.toml` — that
round trip is a tested guarantee, which is why an unset
`blank_lines_between_statements` is emitted commented out rather than as
something with no TOML spelling.

Three things to know about it:

- It reports on the **first path** you give it, since config resolution is per
  file. To compare two directories, run it twice.
- The path does not have to be a file that exists, or a file at all. A directory
  works, and so does a filename you are about to create — sqlalign only needs a
  location to start the walk from.
- With `--isolated` it prints `# built-in defaults (no config file found)` even
  when a config file exists nearby. That header means "none was consulted", not
  "none is there".

## Overriding discovery

| Flag | Effect |
|---|---|
| `--config PATH` | Use this file instead of discovering one. Any filename works — it does not have to be called `.sqlalign.toml`. A `pyproject.toml` passed this way is still read from its `[tool.sqlalign]` table. |
| `--isolated` | Ignore every config file and start from the built-in defaults. Presets and flags still apply, so `--isolated --preset dbt` is meaningful. |

`--config` pointing at a file that does not exist is an error, not a fallback to
discovery:

```
$ sqlalign --config /tmp/nope.toml --check q.sql
sqlalign: /tmp/nope.toml: [Errno 2] No such file or directory: '/tmp/nope.toml'
```

Reach for `--isolated` when you want to prove what sqlalign does with no local
influence — reproducing a bug report, or checking whether a config is the reason
for a diff you did not expect.

## Excluding files

`exclude` skips files when you hand sqlalign a **directory**. It selects files
rather than style, so it never reaches the formatter.

```toml
exclude = ["vendor/*", "*.gen.sql"]
```

Given that config and a tree holding `models/orders.sql`,
`models/build.gen.sql`, `vendor/v.sql` and `vendor/deep/d.sql`, only the first is
considered:

```sh
$ sqlalign --check .
would reformat models/orders.sql
```

The rules:

- **Patterns are `fnmatch` globs**, matched against each file's path relative to
  the directory you named, and also against its bare filename. `*.gen.sql`
  therefore matches at any depth, and `*` spans `/`, so `vendor/*` also excludes
  `vendor/deep/d.sql`.
- **Relative to the directory you named, not the repo root.** With the config
  above, `sqlalign --check .` from the repo root skips `vendor/`. But
  `sqlalign --check vendor` expands `vendor` as the root, so the file's relative
  path is `v.sql`, `vendor/*` does not match, and it gets formatted. Name the
  root you wrote the patterns against.
- **A file named explicitly on the command line is never excluded.** Asking for
  it by name is a clearer signal of intent than a pattern in a config, so
  `sqlalign vendor/v.sql` formats it regardless.
- **`--exclude GLOB` adds to the config's patterns** rather than replacing them,
  and is repeatable.
- **`--isolated` drops the config's patterns too**, since it consults no config
  at all. Expect it to pick up files your normal runs skip.

A bare string is accepted where you would write a one-element list, so
`exclude = "*.gen.sql"` works.

## A realistic committed config

This is the shape most repos end up with: a preset, two or three deliberate
overrides, and the excludes.

```toml
# .sqlalign.toml — committed at the repo root
#
# Style is `trailing`: sqlalign's columnar alignment, but with the commas and
# AND/OR at end of line, which is what our existing SQL already does.
preset        = "trailing"

# Alignment we actually want. `case_results`, `column_types` and
# `column_constraints` are left out, so those collapse to single spaces.
align_targets = ["aliases", "operators", "join_conditions"]

width         = 100

# Generated and third-party SQL is not ours to restyle.
exclude       = ["vendor/*", "*.gen.sql"]
```

Verify it before you commit:

```
$ sqlalign --show-config models/orders.sql
# /home/you/warehouse/.sqlalign.toml
width = 100
align = true
align_targets = ["aliases", "join_conditions", "operators"]
comma_position = "trailing"
boolean_operator_position = "trailing"
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

And what it produces:

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

Then gate it in CI with `sqlalign --check .`, which writes nothing and exits `1`
if any file would change.

## Staging adoption across a legacy repo

Two keys exist for repos you cannot reformat all at once:

- `format_dollar_bodies = false` leaves `$$` procedure and function bodies
  alone, so you can adopt sqlalign for queries before you take on stored
  procedures.
- A nested `.sqlalign.toml` scopes a different style to a subtree. Put
  `preset = "compact"` in `legacy/` and the rest of the repo keeps the house
  style, with no flag juggling at the call site.

## Error reference

Every config failure exits `2` and prints to stderr. A broken config never gets
guessed past: the file it applies to is left untouched, but because resolution is
per file, **other files in the same run still process**. Formatting two files
where only one has a broken config gives you the good file's output, the error on
stderr, and exit `2`.

| Message | Cause | Fix |
|---|---|---|
| `unknown setting(s) [...]; valid: [...]` | A key sqlalign does not recognise | Fix the spelling, or `--no-strict-config` for a mixed-version rollout |
| `<key> must be ...` | A valid key with an invalid value | Use one of the listed values |
| `width must be an integer, got ...` | `width` given a string or float | Write it unquoted: `width = 100` |
| `align_targets must be a list, got ...` | `align_targets` given a non-list | Write it as a TOML array |
| `unknown align_targets [...]` | A misspelled target name | Use one of the six listed |
| `unknown preset '...'` | A misspelled preset | `house`, `compact`, `trailing`, `dbt` |
| `blank_lines_between_statements must be a non-negative integer or unset, got ...` | A negative or non-integer count | Use `0` or more, or delete the key |
| `invalid TOML: ...` | The file does not parse | Fix the syntax |
| `<path>: [Errno 2] No such file or directory` | `--config` points at nothing | Correct the path |
| `exclude must be a list of glob patterns` | `exclude` given a non-string, non-list | Write a string or an array of strings |

`exclude` is the one exception to per-file recovery. It is read while expanding
the directories you named, before any file is formatted, so a bad `exclude`
aborts the whole run with an argparse-style `sqlalign: error: ...` and a usage
banner rather than a per-file message.
