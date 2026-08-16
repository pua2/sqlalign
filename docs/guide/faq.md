# FAQ

## Why did my statement not get formatted?

Because sqlalign declined it, and it told you so on stderr. A statement it cannot
reproduce exactly is emitted **byte-identical** rather than approximated:

```console
$ sqlalign --stdout mixed.sql
sqlalign: mixed.sql: unsupported construct, passed through: select distinct on (a) a, b from t;
select distinct on (a) a, b from t;
SELECT x
     , y
FROM z
WHERE a  = 1
  AND bb = 2;
```

Note what did *not* happen: the file did not fail, the other statement still
formatted, and the exit code is `0`. Declining is per statement.

There are five warning wordings, deliberately different so you can tell an
expected decline from a bug:

| Warning | What it means | What to do |
|---|---|---|
| `passthrough (parse error line N): …` | sqlglot could not parse it. Usually a genuine syntax error, sometimes vendor syntax the parser does not know | Fix the SQL, or leave it — it is safe either way |
| `unsupported construct, passed through: …` | It parsed, but no layout handler models it | Nothing to do. File an issue if it is common in your codebase |
| `formatting would change semantics, passed through unformatted: …` | The output would not AST-compare equal to the input. The safety net caught it | Nothing to do. This is the guarantee working |
| `templating not maskable, passed through: …` | A Jinja expression is too short to mask (see below) | Pad the expression, or accept the passthrough |
| `internal formatter error, passed through (please report): …` | A bug in sqlalign. Your file is still intact | Please report it |

A syntax error partway through a file does not stop the rest of it:

```console
$ sqlalign --stdout syntaxerr.sql
sqlalign: syntaxerr.sql: passthrough (parse error line 2): selct oops from t;
SELECT a
     , b
FROM t
WHERE a = 1;
selct oops from t;
SELECT c
     , d
FROM u;
```

One caveat on that: an **unclosed** string literal or `$$` quote swallows the
rest of the file into a single unparseable statement, so everything after it
passes through until the quote is fixed.

The declines you are most likely to meet in ordinary Postgres SQL:

| Construct | Note |
|---|---|
| Cursors, `FOR` loops, `EXCEPTION` blocks in a `$$` body | declines per statement inside the body, so the rest of the procedure still formats |
| T-SQL `IF`/`WHILE` inside a procedure body | sqlglot leaves part of the body unparsed, so there is no tree to lay out — see [Dialects](dialects.md) |
| A comment buried inside an expression, or in a positional `GROUP BY 1, 2` | the whole statement passes through rather than the comment being moved or restyled. Every clause that gets one row per term — the select list, `FROM`, `WHERE`/`HAVING`/`QUALIFY`, `GROUP BY`, `ORDER BY` — is modelled |

Per-dialect decline lists are on the [Dialects](dialects.md) page.

## Why won't it fix my SQL?

Because it is not a linter, and it is built so that it structurally cannot be
one. sqlalign will not:

- unify your cast styles (`::` vs `CAST(...)`)
- add missing `AS` keywords
- turn `JOIN` into `INNER JOIN`
- normalise `GROUP BY 1, 2` to named columns, or the reverse
- reorder your `ON` operands
- rename or requote your identifiers

Every one of those changes the AST, and a changed AST means the statement is
passed through untouched. The two knobs where sqlalign does pick a spelling for
you — `--neq-style` and `--decimal-style` — exist only because sqlglot's parser
collapses each of those pairs to a single node, so a form has to be chosen when
printing. Both are configurable.

Linting is sqlfluff's job. The two are designed to run together.

## How do I run it alongside sqlfluff?

Run **`sqlfluff fix` first, then sqlalign.** sqlfluff rewrites your SQL — that is
the point of a linter:

```sql
-- q.sql, before
SELECT c.id, o.total
FROM customers c
JOIN orders o ON c.id = o.customer_id
WHERE o.status = 'complete';
```

```console
$ sqlfluff fix --dialect postgres --rules ambiguous.join,aliasing.table q.sql
== [q.sql] FIXED
3 fixable linting violations found
```

```sql
-- q.sql, after
SELECT c.id, o.total
FROM customers AS c
INNER JOIN orders AS o ON c.id = o.customer_id
WHERE o.status = 'complete';
```

sqlalign then lays out whatever sqlfluff decided, and the final layout is
sqlalign's. Doing it the other way round means sqlfluff's fixes land last and
your alignment columns are gone.

Then teach sqlfluff to stop fighting over layout. Out of the box it will flag
every line of sqlalign's output:

```console
$ sqlfluff lint --dialect postgres formatted.sql
L:   2 | P:   1 | LT09 | Select targets should be on a new line unless there is
                       | only one select target. [layout.select_targets]
L:   3 | P:   6 | LT04 | Found leading comma ','. Expected only trailing near
                       | line breaks. [layout.commas]
L:   9 | P:  15 | LT01 | Expected only single space before naked identifier.
                       | Found '               '. [layout.spacing]
```

This is the sqlalign-related part of a `.sqlfluff`. It hands layout to sqlalign
and stands down the three rules the house style deliberately conflicts with:

```ini
[sqlfluff]
dialect = postgres
exclude_rules =
    # Layout is sqlalign's job. sqlfluff cannot express this style,
    # so these rules have nothing useful to say about its output.
    layout.indent,
    layout.keyword_newline,
    layout.operators,
    layout.select_targets,
    layout.spacing,
    convention.not_equal,
    convention.terminator,
    # House-style conflicts: sqlalign preserves your valid choices rather
    # than normalising them, so these three would always fire.
    ambiguous.column_references,
    aliasing.expression,
    convention.casting_style
max_line_length = -1

# Leading commas are the house default. Drop this block if you run
# --comma-position trailing.
[sqlfluff:layout:type:comma]
spacing_before = touch
line_position = leading

# sqlalign writes implicit table aliases (no AS).
[sqlfluff:rules:aliasing.table]
aliasing = implicit
```

Linting the 22 Postgres golden fixtures under exactly that config produces **zero
`LT*` layout violations**. What remains are ordinary lint opinions with nothing to
do with formatting — `AM05` (fully qualify joins), `RF02`/`RF04` (references and
keyword-shaped identifiers), `ST05`/`ST06`/`ST09` (subquery placement, column
order, `ON` operand order), `PG01` (excessive locks). Those are for you and your
team to settle; sqlalign neither causes nor fixes them, because it preserves
whatever you wrote.

The repo's own `.sqlfluff` goes further and excludes that second set too, which is
why the project's lint gate passes on every one of those 22 fixtures:

```console
$ sqlfluff lint tests/fixtures/expected/13.sql
All Finished!
```

Read it as a worked example, not as a config to copy wholesale — most of what is
in there is one team's taste in linting.

Wire both into CI in whichever order you like — they do not interact at that
point:

```sh
sqlfluff lint .
sqlalign --check .
```

## How do I make sqlalign leave one statement alone?

Put `-- sqlalign: skip` on the line above it. That statement is passed through
with no parse, no layout and deliberately no warning:

```console
$ sqlalign --stdout skip.sql
-- sqlalign: skip
select a,   b from generated_by_a_tool where a=1;
SELECT c
     , d
FROM t
WHERE c = 2;
```

A trailing same-line comment after the terminating `;` works too, which is
easier to read for a one-liner:

```console
$ sqlalign --stdout skip_trailing.sql
select a,   b from generated where a=1; -- sqlalign: skip
SELECT c
     , d
FROM t
WHERE c = 2;
```

For larger scopes:

| Scope | How |
|---|---|
| One statement | `-- sqlalign: skip` above it, or after its `;` |
| Every `$$` procedure body in the run | `--no-format-bodies` |
| Whole files | `--exclude 'vendor/*'`, or an `exclude` list in `.sqlalign.toml` |

## Does it work on Windows / with CRLF files?

Yes. A CRLF file is normalised to LF for the engine and written back with its own
line endings, so the diff is your formatting change and not a whole-file `\r`
churn:

```console
$ file crlf.sql
crlf.sql: ASCII text, with CRLF line terminators
$ sqlalign crlf.sql
$ file crlf.sql
crlf.sql: ASCII text, with CRLF line terminators
```

`--check` compares after restoring the endings, so an already-formatted CRLF file
reports clean rather than showing a spurious diff on every line.

Override the choice with `--line-ending {auto,lf,crlf}`. `auto` is the default and
preserves each file's own.

Lone `CR` endings (classic Mac) are not a line ending sqlalign models. Those files
pass through untouched with a warning rather than being silently rewritten:

```console
sqlalign: old.sql: lone CR line endings — passed through untouched
```

## Does it handle dbt and Jinja?

Inline template expressions, yes. Block tags that wrap SQL structure, no.

Templated SQL is not valid SQL, so most formatters decline it outright. sqlalign
masks each template expression with a **same-width** placeholder, formats the
result as ordinary SQL, then puts the original text back. Same width is the whole
trick: alignment columns are computed against the real rendered width, so nothing
shifts when the originals are restored.

```console
$ sqlalign --preset dbt --stdout model.sql
select o.id,
       o.total,
       c.email
from {{ ref('orders') }} o
join {{ ref('customers') }} c on c.id = o.customer_id
where o.status = 'complete';
```

`{{ … }}`, `{% … %}` and `{# … #}` are all recognised. `--no-protect-templating`
turns the masking off.

Three limits, all of which degrade to a passthrough rather than a wrong render:

**A template expression shorter than 12 characters cannot be masked.** The
placeholder needs room for a unique stem, and approximating a width would shift
every column in the file. So `{{ ref('orders') }}` (19 characters) is fine, and
`{{ this }}` (10) and `{% endif %}` (11) are not:

```console
$ sqlalign --stdout incremental.sql
sqlalign: incremental.sql: templating not maskable, passed through: template expression too short to mask: '{{ this }}'
```

Padding the expression to 12 characters (`{{  this  }}`) gets past that specific
check, at the cost of writing SQL that looks odd.

**Block tags that wrap SQL clauses make the statement unparseable.** Take the
standard incremental pattern:

```sql
select a, b from t
{% if is_incremental() %}
where a > (select max(a) from {{ this }})
{% endif %}
;
```

Padding every tag out to 12 characters gets past the masking check, and the
statement still does not format — a masked placeholder standing where a `WHERE`
clause should be is not valid SQL:

```console
$ sqlalign --stdout incremental.sql
sqlalign: incremental.sql: passthrough (parse error line 1): select a,   b from t
_sqla_tpl_0_xxxxxxx
```

The masking trick substitutes for *expressions*, not for control flow. Jinja that
decides which clauses exist is beyond it.

**A leading `{{ config(...) }}` block merges with the query that follows it.**
There is no semicolon between them, so the two become one statement, and the
masked form does not parse:

```console
$ sqlalign --stdout model.sql
sqlalign: model.sql: passthrough (parse error line 1): _sqla_tpl_0_xxxxxxxxxxxxxxxxxxxxxx
```

In practice: sqlalign formats dbt models whose Jinja is `ref`/`source`/`var`
substitution inside an otherwise ordinary query. Models built out of Jinja control
flow are passed through untouched. Nothing is corrupted either way.

There is a `dbt` preset (lowercase keywords, trailing commas, no alignment
padding) if you want output that reads like the rest of a dbt project.

## Is it safe to run on my whole repo?

The design answer is yes, and the reason is the safety net rather than a promise:
every statement is re-parsed after rendering and its AST compared against the
input's. If they differ, the original bytes are kept and you get a warning. A
statement is either laid out or passed through byte-identical — there is no third
outcome.

Run it in this order anyway, because trust should be earned per repo:

```sh
sqlalign --check .          # which files would change, exit 1 if any would
sqlalign --diff . | less    # exactly what would change
sqlalign .                  # commit this on its own branch
```

`--check` and `--diff` write nothing. Both exit `1` if anything would change,
which is what makes `--check` a CI gate.

```console
$ sqlalign --check q.sql
would reformat q.sql
$ echo $?
1
```

Commit the reformat as its own commit, with no logic changes in it, so reviewers
can skip it and `git log --follow` stays usable. `git blame` has
`--ignore-rev`/`.git-blame-ignore-revs` for exactly this.

Three honest caveats on the guarantee:

1. **It is a within-dialect guarantee.** The AST net cannot detect output that is
   valid to sqlglot but invalid to your engine, because the round trip never
   leaves sqlglot's grammar. That is why only three audited dialects are offered —
   see [Dialects](dialects.md#adding-a-dialect-is-more-than-registering-a-parser).
2. **Comments are excluded from the AST comparison.** A comment cannot change
   meaning, so stripping it is correct for semantics — but it means a dropped or
   restyled comment would be silent as far as the safety net is concerned. The
   byte-exact golden fixtures are the guard there, which is why the comment engine
   declines any comment position it does not model rather than guessing.
3. **Some distinctions are destroyed before the comparison exists.** Where
   sqlglot collapses two spellings into one node, both sides collapse identically.
   Where the two are true synonyms this is harmless; where they are not, a
   separate raw-source guard is needed — see T-SQL `REAL`/`NTEXT` in
   [Dialects](dialects.md#real-and-ntext-decline-on-sight).

Formatting is idempotent: running sqlalign on its own output is a no-op, and the
test suite asserts that byte-for-byte for all 29 golden fixtures.

## How fast is it?

Fast enough for a pre-commit hook on a whole repo. `sqlalign --check` over corpora
built from the golden fixtures, on an Apple M3 Pro:

| Workload | Wall time |
|---|---|
| One small file | 0.09 s |
| 480 files, ~6,500 lines total | 1.1 s |
| One file, 500 medium join statements | 1.7 s |

About 0.09 s of every run is Python startup plus importing sqlglot, so a
one-file-per-invocation hook pays that each time. If you are wiring up a
pre-commit hook, pass all the changed files in one invocation rather than looping.

sqlalign never connects to a database. It parses, lays out, and re-parses — that
is all it does, and it works on files that reference tables you do not have.

## What is this warning that doesn't say `sqlalign:`?

```console
'GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO readonly_user' contains unsupported syntax. Falling back to parsing as a 'Command'.
```

That line comes from sqlglot's logger, not from sqlalign, and it is not a
decline. sqlglot keeps such a statement as raw text it did not fully parse;
sqlalign still formats it, and `--check` reports the file clean. Every message
sqlalign itself emits is prefixed `sqlalign: <file>:`.

## Why did the exit code come back non-zero?

| Code | Meaning |
|---|---|
| `0` | Success. Also the code when statements were declined — a passthrough is not an error |
| `1` | `--check` or `--diff` found a file that would change |
| `2` | An unreadable file, or a broken config file. Other files in the same run still process |

Declines do not affect the exit code. A file consisting entirely of statements
sqlalign cannot format is reported clean by `--check`, because it is: nothing
would change.

## See also

- [Getting started](getting-started.md) — install and first run
- [Command-line reference](cli.md) — every flag, with worked invocations
- [Configuration](configuration.md) — committing your team's style
- [Dialects](dialects.md) — what each dialect supports and declines
- [Architecture](architecture.md) — the safety model and decline contract in full
