"""Curation service end to end (N8): LLM-actor proposals pass through the same code
guards as humans; the transition applies only when guards pass."""

import pytest
from sqlalchemy import select

from fhrs.core.models import Sample
from fhrs.curation.policy import ActorKind, AllowedAction
from fhrs.curation.service import PolicyViolation, propose


def _curation_sample(session):
    return session.scalar(
        select(Sample).where(Sample.canonical_accession == "SAMD3330001")
    )


def test_agent_geo_approval_follows_the_guard(session, corpus):
    sample = _curation_sample(session)
    event = propose(
        session,
        sample,
        AllowedAction.APPROVE_GEO,
        actor="geo-agent",
        actor_kind=ActorKind.AGENT,
        rationale="institution evidence matches a national university",
    )
    assert event.status == "applied"
    assert sample.visibility == "public"
    assert sample.needs_geo_curation is False


def test_agent_cannot_hide(session, corpus):
    sample = session.scalar(
        select(Sample).where(Sample.canonical_accession == "SAMEA1110001")
    )
    with pytest.raises(PolicyViolation) as exc:
        propose(
            session, sample, AllowedAction.HIDE, actor="geo-agent",
            actor_kind=ActorKind.AGENT,
        )
    assert any("human" in v for v in exc.value.violations)
    assert sample.visibility == "public"


def test_geo_approval_rejected_for_tier_a(session, corpus):
    sample = session.scalar(
        select(Sample).where(Sample.canonical_accession == "SAMEA1110001")
    )
    with pytest.raises(PolicyViolation):
        propose(
            session, sample, AllowedAction.APPROVE_GEO, actor="curator",
            actor_kind=ActorKind.HUMAN,
        )
