"""Property: canonical_sample_accession preserves the numeric identity regardless
of archive prefix, zero padding, or case, and is idempotent."""

import hypothesis.strategies as st
from hypothesis import given

from fhrs.connectors.insdc import canonical_sample_accession

PREFIXES = st.sampled_from(["SAMEA", "SAMN", "SAMD", "SAME", "SAM", "samea", "samn"])


@given(
    num=st.integers(min_value=0, max_value=10**12),
    prefix=PREFIXES,
    pad=st.integers(min_value=0, max_value=4),
)
def test_canonical_identity(num, prefix, pad):
    acc = f"{prefix}{str(num).zfill(pad + len(str(num)))}"
    canon = canonical_sample_accession(acc)
    assert canon == f"SAMEA{num}"
    # idempotent
    assert canonical_sample_accession(canon) == canon


@given(text=st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:._-", min_size=0, max_size=30))
def test_never_mangles_or_raises(text):
    canon = canonical_sample_accession(text)
    # non-BioSample forms pass through unchanged
    if not any(
        text.upper().startswith(p) and text[len(p) :].isdigit()
        for p in ("SAMEA", "SAMN", "SAMD", "SAME", "SAM")
    ):
        assert canon == text
