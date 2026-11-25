"""Curation service: validate → persist proposal → apply transition. The guard check
(N8) runs identically for human and LLM actors; a failing proposal is rejected with
its violations, never queued.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from fhrs.core.models import CurationEvent, Sample
from fhrs.curation.policy import (
    TRANSITIONS,
    ActorKind,
    AllowedAction,
    SampleContext,
    check,
)


class PolicyViolation(Exception):
    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__("guard violations: " + "; ".join(violations))


def _context(sample: Sample) -> SampleContext:
    rules = frozenset(e.get("rule", "") for e in (sample.evidence or []))
    return SampleContext(
        tier=sample.tier,
        visibility=sample.visibility,
        needs_geo_curation=sample.needs_geo_curation,
        evidence_rules=rules,
    )


def propose(
    session: Session,
    sample: Sample,
    action: AllowedAction,
    *,
    actor: str,
    actor_kind: ActorKind = ActorKind.HUMAN,
    rationale: str = "",
    apply: bool = True,
) -> CurationEvent:
    violations = check(action, _context(sample), actor_kind)
    if violations:
        raise PolicyViolation(violations)
    event = CurationEvent(
        sample_id=sample.id, action=action.value, actor=actor, rationale=rationale
    )
    session.add(event)
    session.flush()
    if apply:
        resolve(session, event, sample, approved=True)
    return event


def resolve(session: Session, event: CurationEvent, sample: Sample, *, approved: bool) -> None:
    event.status = "applied" if approved else "rejected"
    event.resolved_at = datetime.now(UTC)
    if not approved:
        return
    new_visibility, new_needs = TRANSITIONS[AllowedAction(event.action)]
    if new_visibility is not None:
        sample.visibility = new_visibility
    if new_needs is not None:
        sample.needs_geo_curation = new_needs
