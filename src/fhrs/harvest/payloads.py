"""Content-addressed payload store (N2). Every fetched payload is persisted before
normalization; any parser improvement is a local replay, never a re-harvest.

Events store the path RELATIVE to the payload root, so the store can move between
environments (host checkout, container volume, object storage gateway) without
invalidating the event log.
"""

import hashlib
from pathlib import Path


def store_payload(root: Path, source: str, accession: str, data: bytes) -> tuple[Path, str]:
    """Write payload; return (path relative to root, sha256)."""
    sha = hashlib.sha256(data).hexdigest()
    relative = Path(source) / accession / f"{sha}.json"
    absolute = Path(root) / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    if not absolute.exists():
        absolute.write_bytes(data)
    return relative, sha


def load_payload(root: Path, relative: str | Path) -> bytes:
    return (Path(root) / relative).read_bytes()
