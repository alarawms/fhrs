"""N8: every guard rejects deliberately illegal proposals, for human and LLM actors."""

import pytest

from fhrs.curation.policy import ActorKind, AllowedAction, SampleContext, check

TIER_C = SampleContext(
    tier="C",
    visibility="curation",
    needs_geo_curation=True,
    evidence_rules=frozenset({"institution"}),
)
PUBLIC_A = SampleContext(
    tier="A", visibility="public", needs_geo_curation=False, evidence_rules=frozenset()
)
HIDDEN = SampleContext(
    tier="A", visibility="hidden", needs_geo_curation=False, evidence_rules=frozenset()
)


@pytest.mark.parametrize("actor", [ActorKind.HUMAN, ActorKind.AGENT, ActorKind.SYSTEM])
def test_approve_geo_allowed_for_tier_c_curation(actor):
    assert check(AllowedAction.APPROVE_GEO, TIER_C, actor) == []


@pytest.mark.parametrize("actor", [ActorKind.HUMAN, ActorKind.AGENT])
def test_approve_geo_rejected_for_public_tier_a(actor):
    violations = check(AllowedAction.APPROVE_GEO, PUBLIC_A, actor)
    assert violations


def test_approve_geo_rejected_without_institution_evidence():
    ctx = SampleContext(
        tier="C", visibility="curation", needs_geo_curation=True, evidence_rules=frozenset()
    )
    assert "no institution evidence" in "; ".join(
        check(AllowedAction.APPROVE_GEO, ctx, ActorKind.AGENT)
    )


def test_hide_requires_human():
    assert check(AllowedAction.HIDE, PUBLIC_A, ActorKind.AGENT)
    assert check(AllowedAction.HIDE, PUBLIC_A, ActorKind.HUMAN) == []


def test_unhide_requires_human_and_hidden():
    assert check(AllowedAction.UNHIDE, PUBLIC_A, ActorKind.HUMAN)
    assert check(AllowedAction.UNHIDE, HIDDEN, ActorKind.HUMAN) == []


def test_flag_for_review_always_allowed():
    assert check(AllowedAction.FLAG_FOR_REVIEW, PUBLIC_A, ActorKind.AGENT) == []
