"""Pure merge decisions (N1)."""

import pytest

from fhrs.rules.merge import decide


@pytest.mark.parametrize(
    ("existing", "incoming", "action", "value"),
    [
        ("2023-05-14", None, "keep", "2023-05-14"),       # never erased (N1)
        ("2023-05-14", "", "keep", "2023-05-14"),
        ("2023-05-14", "missing", "keep", "2023-05-14"),
        (None, "2023-05-14", "fill", "2023-05-14"),
        ("", "2023-05-14", "fill", "2023-05-14"),
        (None, None, "keep", ""),
        ("a", "a", "keep", "a"),
        ("Saudi Arabia: Thuwal", "Saudi Arabia", "conflict", "Saudi Arabia"),
    ],
)
def test_decide(existing, incoming, action, value):
    outcome = decide(existing, incoming)
    assert outcome.action == action
    assert outcome.value == value
