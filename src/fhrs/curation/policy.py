"""Policy: the complete, code-enforced set of curation actions (N8).

A proposal is data: (action, actor, rationale). Guards are pure predicates over the
target sample; a proposal with any guard violation is rejected by code. Prompt text
is never a safety boundary — an LLM "approver" has exactly the authority this file
grants, nothing more.
"""

import enum
from collections.abc import Callable
from dataclasses import dataclass


class AllowedAction(str, enum.Enum):
    APPROVE_GEO = "approve_geo"      # tier C curation sample → public
    REJECT = "reject"                # curation sample → out of scope (hidden)
    FLAG_FOR_REVIEW = "flag_for_review"  # any sample → open review (always safe)
    HIDE = "hide"                    # public sample → hidden (humans only)
    UNHIDE = "unhide"                # hidden sample → public (humans only)


class ActorKind(str, enum.Enum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"


@dataclass(frozen=True)
class SampleContext:
    """The guard-relevant view of a sample."""

    tier: str
    visibility: str
    needs_geo_curation: bool
    evidence_rules: frozenset[str]


def _in_curation(ctx: SampleContext) -> bool:
    return ctx.visibility == "curation"


def _has_institution_evidence(ctx: SampleContext) -> bool:
    return "institution" in ctx.evidence_rules or "institution_hint_only" in ctx.evidence_rules


# Guards return a list of violation strings; empty means allowed.
GUARDS: dict[AllowedAction, Callable[[SampleContext, ActorKind], list[str]]] = {
    AllowedAction.APPROVE_GEO: lambda ctx, actor: [
        *([] if _in_curation(ctx) else ["sample is not in the curation queue"]),
        *([] if ctx.tier == "C" else ["only tier C (inferred) samples are geo-approvable"]),
        *(
            []
            if _has_institution_evidence(ctx)
            else ["no institution evidence on record — nothing to approve"]
        ),
    ],
    AllowedAction.REJECT: lambda ctx, actor: [
        *([] if _in_curation(ctx) else ["only curation-queue samples can be rejected"]),
    ],
    AllowedAction.FLAG_FOR_REVIEW: lambda ctx, actor: [],
    AllowedAction.HIDE: lambda ctx, actor: [
        *([] if actor is ActorKind.HUMAN else ["hide requires a human actor"]),
        *([] if ctx.visibility == "public" else ["only public samples can be hidden"]),
    ],
    AllowedAction.UNHIDE: lambda ctx, actor: [
        *([] if actor is ActorKind.HUMAN else ["unhide requires a human actor"]),
        *([] if ctx.visibility == "hidden" else ["only hidden samples can be unhidden"]),
    ],
}


def check(
    action: AllowedAction, ctx: SampleContext, actor: ActorKind = ActorKind.HUMAN
) -> list[str]:
    return GUARDS[action](ctx, actor)


# The state transition each action performs when applied. Anything not listed here
# is not something curation can do to a sample, full stop.
TRANSITIONS: dict[AllowedAction, tuple[str | None, bool | None]] = {
    # action: (new visibility, new needs_geo_curation)
    AllowedAction.APPROVE_GEO: ("public", False),
    AllowedAction.REJECT: ("hidden", False),
    AllowedAction.FLAG_FOR_REVIEW: (None, None),
    AllowedAction.HIDE: ("hidden", None),
    AllowedAction.UNHIDE: ("public", None),
}
