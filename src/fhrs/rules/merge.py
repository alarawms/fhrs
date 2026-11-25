"""Per-key attribute merge decision (PROMPT.md N1). Pure.

Null/empty never overwrites a known value. Conflicts (both sides known, differing)
are surfaced for the caller to resolve — harvest resolves by source recency and
records a history row — because only the caller knows event times.
"""

from dataclasses import dataclass
from typing import Literal

MergeAction = Literal["keep", "fill", "conflict"]

_EMPTY = {"", "missing", "not collected", "not applicable", "na", "n/a", "unknown"}


def _is_empty(v: str | None) -> bool:
    return v is None or v.strip().lower() in _EMPTY


@dataclass(frozen=True)
class MergeOutcome:
    action: MergeAction
    value: str  # the winning value (existing on "keep", incoming otherwise)


def decide(existing: str | None, incoming: str | None) -> MergeOutcome:
    """Invariants (property-tested):
    - decide(known, empty) == keep(known)   — never erased
    - decide(empty, known) == fill(known)
    - decide(v, v) == keep(v)
    - otherwise "conflict" with the incoming value proposed.
    """
    if _is_empty(incoming):
        return MergeOutcome("keep", (existing or "").strip())
    inc = incoming.strip()  # type: ignore[union-attr]
    if _is_empty(existing):
        return MergeOutcome("fill", inc)
    ex = existing.strip()  # type: ignore[union-attr]
    if ex == inc:
        return MergeOutcome("keep", ex)
    return MergeOutcome("conflict", inc)
