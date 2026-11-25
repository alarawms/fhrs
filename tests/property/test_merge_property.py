"""N1 as a property: merge never erases, never raises (acceptance invariant 1)."""

from hypothesis import given
from hypothesis import strategies as st

from fhrs.rules.merge import _is_empty, decide

TEXT = st.one_of(st.none(), st.text())


@given(existing=TEXT, incoming=TEXT)
def test_never_erases(existing, incoming):
    outcome = decide(existing, incoming)
    if not _is_empty(existing) and _is_empty(incoming):
        # a known value survives any empty/missing-shaped update
        assert outcome.action == "keep"
        assert outcome.value == existing.strip()


@given(existing=TEXT, incoming=TEXT)
def test_fill_when_known_arrives(existing, incoming):
    outcome = decide(existing, incoming)
    if _is_empty(existing) and not _is_empty(incoming):
        assert outcome.action == "fill"
        assert outcome.value == incoming.strip()


@given(existing=TEXT, incoming=TEXT)
def test_result_is_deterministic_and_typed(existing, incoming):
    outcome = decide(existing, incoming)
    assert outcome.action in {"keep", "fill", "conflict"}
    assert isinstance(outcome.value, str)
    # identical inputs never conflict
    if existing is not None and incoming is not None and existing.strip() == incoming.strip():
        assert outcome.action == "keep"
