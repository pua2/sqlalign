"""sqlfluff lint gate: every standard-SQL postgres expected fixture
must lint clean under the repo `.sqlfluff`, proving the formatter's output is not
just byte-exact to the goldens but also acceptable to an independent linter.

Excluded from the gate (documented, not formatter bugs):
- **#15** — Redshift-only DDL (`ENCODE`/`DISTKEY`/`SORTKEY`); excluded via the
  `DIALECTS` dialect filter below (it is `redshift`, not `postgres`). It would
  also fail as a `PRS` error under the postgres-dialect linter. Spec §7.
- **#19/#20** — plpgsql `CREATE FUNCTION`/`PROCEDURE`: sqlfluff's `CP03` flags the
  function *name being defined* (a declared identifier, not a call) as needing
  uppercase, and it does not model dollar-quoted bodies. A house function
  definition keeps its given name, so this is a linter false positive, not a
  formatting defect.

Three lint rules the house style deliberately conflicts with are excluded in
`.sqlfluff` itself (each documented there): `ambiguous.column_references` (AM06 —
house preserves the source GROUP BY reference form), `aliasing.expression`
(AL03 — house does not force aliases), and `convention.casting_style` (CV11 —
house preserves the source `::`/`CAST` form). The formatter aligns and cases; it
never rewrites the author's valid stylistic choices, and the gate honors that.
"""
import subprocess
import sys

import pytest
from conftest import DIALECTS, FIXTURES, SAMPLES

pytest.importorskip("sqlfluff")

_CONFIG = FIXTURES.parent.parent / ".sqlfluff"       # repo-root .sqlfluff
_GATE_EXCLUDED = {"19", "20"}                        # plpgsql — see module docstring

POSTGRES = [
    sid for sid in SAMPLES
    if DIALECTS.get(sid, "postgres") == "postgres" and sid not in _GATE_EXCLUDED
]


@pytest.mark.parametrize("sid", POSTGRES)
def test_expected_fixture_lints_clean(sid):
    result = subprocess.run(
        [sys.executable, "-m", "sqlfluff", "lint",
         "--config", str(_CONFIG), str(FIXTURES / "expected" / f"{sid}.sql")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout or result.stderr
