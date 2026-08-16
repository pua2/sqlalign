# Dialects

sqlalign parses and prints three dialects. Pick one with `--dialect`:

```sh
sqlalign query.sql                      # postgres (the default)
sqlalign --dialect redshift ddl.sql
sqlalign --dialect tsql report.sql
```

| Dialect | Flag | Covers |
|---|---|---|
| Postgres | `--dialect postgres` (default) | Postgres SQL, including `$$`-quoted plpgsql function and procedure bodies |
| Redshift | `--dialect redshift` | Redshift SQL: `ENCODE`, `DISTSTYLE`/`DISTKEY`/`SORTKEY`, `LISTAGG … WITHIN GROUP`, plpgsql stored procedures |
| SQL Server | `--dialect tsql` | T-SQL: `TOP`, `[bracketed]` identifiers, `@variables`, `AS BEGIN … END` procedures, `GO` batches |

Three things to know before you read the per-dialect sections:

**`--dialect` is a command-line flag only.** It is not a config-file key. Putting
`dialect = "redshift"` in `.sqlalign.toml` is an error, not a silent no-op:

```console
$ sqlalign --stdout q.sql
sqlalign: /home/you/warehouse/.sqlalign.toml: unknown setting(s) ['dialect']; valid: ['align', 'align_targets', 'blank_lines_between_statements', 'boolean_operator_position', 'comma_position', 'decimal_style', 'exclude', 'format_dollar_bodies', 'keyword_case', 'neq_style', 'on_placement', 'preset', 'protect_templating', 'width']
```

If a repository holds SQL for more than one engine, run sqlalign once per
directory with the right flag, or use `--exclude` to split the run.

**There is no dialect auto-detection.** sqlalign will not sniff your file, and
naming the wrong dialect is not covered by the safety guarantee — that guarantee
is scoped to the dialect you asked for, and the layout handlers emit *that*
dialect's keywords. Pass the flag that matches the engine the file will run on.

**A dialect outside the three is refused, not attempted.** The CLI rejects the
argument outright:

```console
$ sqlalign --dialect mysql q.sql
sqlalign: error: argument --dialect: invalid choice: 'mysql' (choose from postgres, redshift, tsql)
```

and the Python API raises rather than producing output:

```console
ValueError: unsupported dialect 'snowflake'; sqlalign supports postgres, redshift, tsql
```

The reason that refusal is loud, rather than a best-effort attempt, is the
subject of the last section on this page.

## Postgres

The default, and the dialect the style was designed against. Twenty-four of the
twenty-nine golden fixtures are Postgres: selects, joins, `WHERE` with
`AND`/`OR`/`IN`/`BETWEEN`/`LIKE`, aggregates, CTEs, subqueries, `CASE`, window
functions, set operations, `INSERT`/`UPDATE`/`DELETE`/`MERGE`, nested expressions
and comments, `CREATE TABLE`, CTAS, views and materialized views, `CREATE
FUNCTION` and `CREATE PROCEDURE`, `TRUNCATE`, `CREATE INDEX` and `GRANT`.

```console
$ sqlalign --stdout query.sql
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

Both cast forms survive as written, at every nesting depth — sqlalign will not
unify them for you:

```console
$ sqlalign --stdout casts.sql
SELECT x::NUMERIC      AS v
     , CAST(y AS DATE) AS d
FROM t;
```

Redshift behaves identically here. T-SQL does not, because it has no `::`
operator at all — see below.

### Dollar-quoted bodies

A `$$ … $$` plpgsql body is parsed and formatted with the same engine as
top-level SQL, not treated as an opaque string:

```console
$ sqlalign --stdout function.sql
CREATE OR REPLACE FUNCTION get_customer_ltv(p_customer_id INT)
RETURNS NUMERIC
LANGUAGE plpgsql
AS $$

DECLARE v_ltv NUMERIC;

BEGIN

SELECT SUM(total) INTO v_ltv
FROM orders
WHERE customer_id = p_customer_id
  AND status      = 'complete';

IF v_ltv IS NULL
  THEN v_ltv := 0;
END IF;

RETURN v_ltv;

END;
$$;
```

`--no-format-bodies` leaves every `$$` body byte-identical if you would rather it
did not.

Unmodelled plpgsql constructs — cursors, `FOR` loops, `EXCEPTION` blocks — pass
through per statement inside the body, so the rest of the procedure still
formats.

`LANGUAGE sql` bodies format too. They are a bare statement list rather than a
DECLARE/BEGIN/END block, so they get their own branch — but each statement goes
through the same renderer a plpgsql body's statements do, so the two cannot
drift apart. A body in a language that is not SQL at all (`plpythonu`,
`plperl`) is declined at the header.

### What Postgres declines

Anything without a layout handler passes through byte-identical with a warning.
`PIVOT` is a current example:

```console
$ sqlalign --stdout declines.sql
sqlalign: declines.sql: unsupported construct, passed through: select * from t pivot (sum(x) for y in (
select * from t pivot (sum(x) for y in (1, 2)) p;
INSERT INTO staging
VALUES (1, 2);

INSERT INTO staging
(  a)
SELECT a
FROM source;
```

`PIVOT`/`UNPIVOT` declines **under Postgres only**, and not because it is hard:
Postgres has no such syntax. sqlglot's Postgres generator drops the clause
silently — `SELECT * FROM t PIVOT(...)` comes back as `SELECT * FROM t`, the
whole thing gone — so declining is the only safe answer, and the re-parse guard
is what noticed. Under `--dialect redshift` and `--dialect tsql`, which do have
it, `PIVOT` formats:

```sql
SELECT *
FROM (SELECT a
           , b
           , c
      FROM src
     ) s PIVOT(AVG(c) FOR b IN (1, 2)) AS p;
```

The check renders the node and looks at what comes out, rather than consulting a
list of dialects — so if sqlglot ever grows Postgres support, this stops
declining on its own.

Run `sqlalign --report` over your own SQL rather than trusting this list — it
counts what actually declined, ranked, so you can see whether any of it matters
to you.

The legacy comma join — `FROM a, b` — formats, and is worth a note because it
was once declined. It parses as a join with no condition, exactly like
`CROSS JOIN` does, and rebuilding it through the join keyword would emit
`FROM a JOIN b`, which Postgres rejects: a bare `JOIN` requires a condition.
The re-parse check cannot catch that either, because sqlglot reads its own
lenient output back without complaint. The option that was missed is the
obvious one — emit the comma, which is both valid and what the author wrote:

```sql
SELECT 1
FROM a x
   , b y
WHERE x.id = y.id;
```

## Redshift

Use `--dialect redshift` for Redshift DDL in particular — the column encodings
and table attributes get their own aligned columns and clause lines:

```console
$ sqlalign --stdout --dialect redshift fact_orders.sql
CREATE TABLE fact_orders (
    order_id    BIGINT NOT NULL
  , customer_id BIGINT NOT NULL ENCODE az64
  , order_date  DATE   NOT NULL ENCODE az64
  , total       NUMERIC(12, 2)  ENCODE az64
  , channel     VARCHAR(32)     ENCODE lzo
)
DISTSTYLE KEY DISTKEY (customer_id)
COMPOUND SORTKEY (order_date, customer_id);
```

Two details worth noting in that output. The `ENCODE` keyword is uppercased but
its *value* is passed through as written — `az64` stays `az64`, and `AZ64` would
stay `AZ64`. And the constraint alignment is per kind: the `NOT NULL` column pads
over only the lines that carry `NOT NULL`, and the `ENCODE` column pads over only
the lines that carry `ENCODE`, so a row with no nullability constraint does not
drag the encoding column right.

One type spelling changes under Redshift that does not under Postgres: `int`
renders as `INTEGER`, because sqlglot's Redshift generator prints the long form.
The two are the same type. `DECIMAL` renders as `NUMERIC` here as it does under
Postgres, and `--decimal-style DECIMAL` brings it back.

`LISTAGG(…) WITHIN GROUP (ORDER BY …)` formats, with a space before the keyword's
paren, the same family as `OVER (`:

```console
$ sqlalign --stdout --dialect redshift listagg.sql
SELECT customer_id
     , LISTAGG(channel, ',') WITHIN GROUP (ORDER BY channel) AS channels
FROM orders
GROUP BY customer_id;
```

Redshift stored procedures use the same `$$` body machinery as Postgres:

```console
$ sqlalign --stdout --dialect redshift proc.sql
CREATE OR REPLACE PROCEDURE refresh_totals()
LANGUAGE plpgsql
AS $$

DECLARE v_rows INT;

BEGIN

DELETE FROM daily_totals
WHERE report_date = CURRENT_DATE;

INSERT INTO daily_totals
SELECT order_date
     , SUM(total)
FROM orders
WHERE order_date = CURRENT_DATE
GROUP BY order_date;

END;
$$;
```

### `TEXT` declines, and why

Redshift `TEXT` is an alias for `VARCHAR(256)`. sqlglot renders it as
`VARCHAR(MAX)`, which is `VARCHAR(65535)` — a different column width. That is a
change to your schema, not to its presentation, so the safety net rejects it and
the statement is passed through untouched:

```console
$ sqlalign --stdout --dialect redshift table.sql
sqlalign: table.sql: sqlglot cannot round-trip this statement, passed through unformatted: create table t (a text not null, b int);
create table t (a text not null, b int);
```

This one is caught rather than declined on sight because `TEXT` and
`VARCHAR(MAX)` parse to *different* AST nodes — the rewrite is visible to the
comparison, so the general guarantee handles it. (Contrast the T-SQL type
collapses below, which are not visible and need a separate guard.)

`CAST(x AS TEXT)` declines the same way. If you want those statements formatted,
write the width you mean: `VARCHAR(256)`.

### Other Redshift declines

| Construct | What happens |
|---|---|
| `UNLOAD ('…') TO 's3://…'` | `unsupported construct, passed through` |
| Python/Perl UDFs (`LANGUAGE plpythonu`) | `unsupported construct, passed through` — the body is not SQL |
| `TEXT` in a type position | `formatting would change semantics, passed through unformatted` |

## SQL Server (T-SQL)

`--dialect tsql` covers ordinary queries and DDL, plus three T-SQL specifics:
`TOP` with bracketed identifiers, `AS BEGIN … END` procedures, and `GO` batches.
It is the newest and narrowest of the three — the declines below are longer than
the other dialects' for that reason.

### `TOP` and bracketed identifiers

`TOP n` rides the `SELECT` line; continuation commas keep their usual column, and
`[bracketed]` identifiers are preserved verbatim like every other identifier:

```console
$ sqlalign --stdout --dialect tsql orders.sql
SELECT TOP 10 [Order Id]
     , cust.[Full Name]
     , ord.total
FROM [Sales Orders]  ord
INNER JOIN customers cust ON cust.id = ord.customer_id
WHERE ord.status = 'complete'
  AND ord.total  > 100
ORDER BY ord.total DESC;
```

The statement splitter understands brackets, so a semicolon inside one
(`[my;col]`) does not cut the statement in half, and `]]` escapes a literal
bracket.

`TOP` is worth calling out because it is where the T-SQL support began. sqlglot
parses `TOP n` into the same `Limit` node Postgres uses, so a dialect-agnostic
layout handler emitted `LIMIT n` — valid SQL to sqlglot, rejected by SQL Server.
See [Adding a dialect](#adding-a-dialect-is-more-than-registering-a-parser)
below.

### `CREATE TABLE`

```console
$ sqlalign --stdout --dialect tsql daily_revenue.sql
CREATE TABLE daily_revenue (
    report_id   INTEGER      NOT NULL
  , report_date DATE         NOT NULL
  , channel     NVARCHAR(50) NOT NULL
  , revenue     NUMERIC(12, 2)
  , PRIMARY KEY (report_id)
);
```

Two spelling changes in that output are worth knowing about, because both are
sqlglot parse-time collapses that sqlalign cannot undo:

| You wrote | You get | Why it is safe |
|---|---|---|
| `int` | `INTEGER` | `INT` and `INTEGER` are the same type |
| `decimal(12,2)` | `NUMERIC(12, 2)` | SQL Server treats `decimal` and `numeric` as synonyms |

Under `--dialect tsql`, `--decimal-style DECIMAL` does **not** bring `DECIMAL`
back — the T-SQL generator has already printed `NUMERIC` before the knob is
consulted. `--neq-style` does work under T-SQL. If preserving the `DECIMAL`
spelling matters to you, put those statements behind `-- sqlalign: skip`.

### `BEGIN`/`END` procedures

A procedure body is ordinary T-SQL — there is no dollar quoting — so it goes
through the main engine. Statements sit at column 1 inside the block, separated
by blank lines:

```console
$ sqlalign --stdout --dialect tsql refresh_daily.sql
CREATE PROCEDURE refresh_daily @target DATE
AS
BEGIN

DELETE FROM daily_revenue
WHERE report_date = @target;

INSERT INTO daily_revenue
(  report_date
 , channel
 , revenue)
SELECT order_date
     , channel
     , SUM(total)
FROM orders
WHERE order_date = @target
GROUP BY order_date
       , channel;

END;
```

The splitter keeps the whole routine as one statement rather than cutting at the
semicolons inside it. It does that with a small stack that records what opened
each block, so a `CASE … END` inside the body does not look like the end of the
procedure, and `BEGIN TRANSACTION` — which never gets a matching `END` — does not
open one.

sqlalign closes the block with `END;` whichever way the source spells it — with
or without a terminating semicolon:

```console
$ sqlalign --stdout --dialect tsql proc.sql
CREATE PROCEDURE refresh_daily @target DATE
AS
BEGIN

DELETE FROM daily_revenue
WHERE report_date = @target;

END;
```

A source that wrote `END;` used to decline here, which this guide described as a
sharp edge. It was a bug, not an edge: the layout owns that semicolon, and the
statement emitter appended the source's own on top of it, producing `END;;` —
which re-parses differently, so the safety net stopped it. Fixed.

### `GO` batches

`GO` is a client directive, not SQL. sqlglot cannot parse it, and worse, it
swallows the following statement into the `GO` as a string literal. sqlalign
splits the file at `GO` lines before anything is parsed, formats each batch
independently, and passes the `GO` line through verbatim:

```console
$ sqlalign --stdout --dialect tsql batches.sql
TRUNCATE TABLE staging;
GO
INSERT INTO staging
(  a
 , b)
SELECT a
     , b
FROM source
WHERE a IS NOT NULL;
GO
```

The safety check is batch-aware too: it compares each batch's AST separately and
compares the separators themselves, so a dropped or added `GO` is still caught.
`GO 5` (with a repeat count) is recognised.

### `REAL` and `NTEXT` decline on sight

These two are the reason T-SQL needs a guard the other dialects do not.

```console
$ sqlalign --stdout --dialect tsql types.sql
sqlalign: types.sql: unsupported construct, passed through: create table t (a real not null, b int);
sqlalign: types.sql: unsupported construct, passed through: select cast(x as ntext) from t;
create table t (a real not null, b int);
select cast(x as ntext) from t;
```

sqlglot collapses `REAL`/`FLOAT` to one node, and `NTEXT`/`TEXT` to one node, **at
parse time**. In T-SQL those pairs are not synonyms:

| Pair | Difference |
|---|---|
| `REAL` vs `FLOAT` | `REAL` is `FLOAT(24)`, `FLOAT` is `FLOAT(53)` — rewriting one widens precision |
| `NTEXT` vs `TEXT` | `NTEXT` is Unicode, `TEXT` is not |

The AST safety net is useless here, and not because it is weak: the distinction is
destroyed *before the AST exists*, so both sides of the comparison collapse the
same way and `ast_equal` returns `True` on a rewrite that changed your schema.
The only defence left is the raw source, so a statement whose text contains `REAL`
or `NTEXT` under `--dialect tsql` is declined by a regex over the input before any
layout runs.

`INT`/`INTEGER`, `DECIMAL`/`NUMERIC` and `TIMESTAMP`/`ROWVERSION` also collapse,
but those *are* synonyms, so canonicalising them is harmless and they still
format.

### Other T-SQL declines

| Construct | Example |
|---|---|
| `IF`/`WHILE` inside a procedure body | sqlglot does not parse it — part of the body comes back as raw text, and an `IF … ELSE` is indistinguishable from an `IF` without one, so laying out the tree would drop the ELSE |
| `TRY`/`CATCH` blocks | `create procedure q as begin begin try … end try begin catch … end catch end` |
| `REAL`, `NTEXT` in a type position | see above |

All of these pass through byte-identical with a warning and exit code `0`.
Procedure bodies containing only DML and `SELECT` format; a body containing
control flow declines as a whole.

## Adding a dialect is more than registering a parser

`SUPPORTED_DIALECTS` is a whitelist of three, and it is not the list of dialects
sqlglot can parse — sqlglot parses many more. It is the list whose *emitted
keywords* have been audited against a real engine.

The distinction is not theoretical. It shipped once. The chain:

1. sqlglot parses T-SQL `SELECT TOP 10 id FROM users` into the same `Limit` node
   it uses for Postgres `LIMIT 10`.
2. The layout handler emitted its hard-coded `LIMIT` keyword. Handlers are
   dialect-agnostic, which is true for Postgres and Redshift — they share the
   syntax — and false for SQL Server.
3. sqlglot then parsed that `LIMIT` back *as T-SQL*, because its grammar accepts
   it, so `ast_equal` compared the two trees **equal** and reported nothing.

Valid SQL in, SQL Server rejects the output, no warning. Two of the twenty-six
keywords the handlers emit diverge for T-SQL (`LIMIT` and `OFFSET`); finding that
out required going through them.

The lesson generalises, and it is the one real limit on sqlalign's guarantee:
**the AST safety net cannot detect dialect-invalid output.** The round trip never
leaves sqlglot's permissive grammar, so a keyword that is valid to sqlglot and
invalid to your engine is invisible to the comparison. "sqlalign cannot change
what your SQL means" holds *within* a verified dialect.

That is why `format_sql` raises for an unlisted dialect instead of doing its best.
A passthrough would be the wrong response — an unsupported dialect is a caller
error, not an unmodelled statement, so it fails where you can see it rather than
silently mid-file.

If you want a fourth dialect, the work is: audit every keyword the layout
handlers emit against that engine's grammar, hunt for parse-time collapses whose
two spellings are not synonyms (the `REAL`/`NTEXT` class), and add byte-exact
golden fixtures pinning the result. Registering the parser is the easy part.

## See also

- [Configuration](configuration.md) — the config keys, and why `dialect` is not one
- [Command-line reference](cli.md) — every flag and the exit codes
- [Architecture](architecture.md) — the safety model and the decline contract in full
- [FAQ](faq.md) — why a statement did not get formatted, and what to do about it
