"""Checklist registry access (PROMPT.md N5): the checklist → content-type mapping is
versioned data. A checklist absent from the registry is a loud, countable event — the
unmapped queue — never a silent fallback into "other".
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# "ERC000011:v2" → "ERC000011"; named NCBI packages have no version suffix.
_BASE = re.compile(r"^([^:]+)")


@dataclass(frozen=True)
class ChecklistRegistry:
    buckets: frozenset[str]
    mapping: dict[str, str]
    source_path: Path

    def base_id(self, checklist_id: str) -> str:
        m = _BASE.match(checklist_id.strip())
        return m.group(1) if m else checklist_id.strip()

    def bucket_for(self, checklist_id: str | None) -> str | None:
        """Bucket for a checklist id, or None when unmapped/unknown/absent.

        None means "loud": ingest must route it to the unmapped queue and count it.
        "other" is a deliberate registry choice, not a fallback.
        """
        if not checklist_id:
            return None
        return self.mapping.get(self.base_id(checklist_id))


def load_registry(path: Path) -> ChecklistRegistry:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    buckets = frozenset(doc["buckets"])
    mapping = dict(doc["map"])
    bad = {k: v for k, v in mapping.items() if v not in buckets}
    if bad:
        raise ValueError(f"registry entries with undeclared buckets: {bad}")
    return ChecklistRegistry(buckets=buckets, mapping=mapping, source_path=Path(path))
