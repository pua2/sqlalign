import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import ast_equal


# Every fixture pair is AST-equivalent, including the dollar-quoted plpgsql
# bodies (#19/#20) now that `ast_equal` is dollar-quote-aware.
@pytest.mark.parametrize("sid", SAMPLES)
def test_input_and_expected_are_ast_identical(sid):
    inp, exp = load_pair(sid)
    dialect = DIALECTS.get(sid, "postgres")
    assert ast_equal(inp, exp, dialect), f"fixture pair {sid} diverges semantically"
