import sys
from pathlib import Path

# tomllib is stdlib only from 3.11. Registering tomli under that name here --
# conftest imports before any test module -- lets every test write a plain
# `import tomllib` and still run on 3.10, instead of each module carrying its
# own shim and the one that forgets breaking only on the 3.10 CI leg.
try:
    import tomllib
except ModuleNotFoundError:            # Python 3.10
    import tomli as tomllib
sys.modules.setdefault("tomllib", tomllib)


FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES = sorted(p.stem for p in (FIXTURES / "expected").glob("*.sql"))
DIALECTS = {
    "15": "redshift",
    # T-SQL goldens: TOP + brackets, CREATE TABLE, a BEGIN/END
    # procedure, and GO batches. They pin the dialect's style byte-exact, the
    # same way the postgres samples pin the house style.
    "26": "tsql", "27": "tsql", "28": "tsql", "29": "tsql",
}

# Every golden is implemented, so the per-task `IMPLEMENTED` set and the
# strict-xfail scaffolding (`xfail_unless`) it fed have been retired: all of
# SAMPLES runs as plain, must-pass params.


def load_pair(sid: str) -> tuple[str, str]:
    return ((FIXTURES / "input" / f"{sid}.sql").read_text(),
            (FIXTURES / "expected" / f"{sid}.sql").read_text())
