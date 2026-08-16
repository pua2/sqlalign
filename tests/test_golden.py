import pytest
from conftest import DIALECTS, SAMPLES, load_pair

from sqlalign.formatter import format_sql


# Every hand-formatted sample is implemented, so each must format byte-exact: no
# xfail gating remains, and a golden that regresses is a hard failure.
@pytest.mark.parametrize("sid", SAMPLES)
def test_golden(sid):
    inp, expected = load_pair(sid)
    got = format_sql(inp, DIALECTS.get(sid, "postgres")).text
    assert got == expected


# Formatting is a fixed point: feeding the engine its own output must return it
# unchanged. This catches a rule that keeps nudging a line on every pass (the
# input → expected test above cannot see it, since it only ever runs one pass).
@pytest.mark.parametrize("sid", SAMPLES)
def test_idempotent(sid):
    expected = load_pair(sid)[1]
    assert format_sql(expected, DIALECTS.get(sid, "postgres")).text == expected
