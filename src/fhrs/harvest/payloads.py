"""Content-addressed payload store (N2). Every fetched payload is persisted before
normalization; any parser improvement is a local replay, never a re-harvest.
"""

import hashlib
from pathlib import Path


def store_payload(root: Path, source: str, accession: str, data: bytes) -> tuple[Path, str]:
    sha = hashlib.sha256(data).hexdigest()
    path = Path(root) / source / accession / f"{sha}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)
    return path, sha


def load_payload(path: Path) -> bytes:
    return Path(path).read_bytes()
