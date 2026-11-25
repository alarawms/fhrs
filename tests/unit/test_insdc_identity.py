"""Cross-archive accession identity (N3): the shared INSDC numeric suffix is the
dedup unit, not the archive-native string."""

from fhrs.connectors.insdc import canonical_sample_accession, normalize


def test_canonicalizes_each_member_prefix():
    assert canonical_sample_accession("SAMN10033096") == "SAMEA10033096"
    assert canonical_sample_accession("SAMD10033096") == "SAMEA10033096"
    assert canonical_sample_accession("SAMEA10033096") == "SAMEA10033096"
    assert canonical_sample_accession("SAME10033096") == "SAMEA10033096"
    assert canonical_sample_accession("SAM10033096") == "SAMEA10033096"


def test_strips_inconsistent_zero_padding():
    # verified live 2026-10-13: NCBI pads, ENA/DDBJ do not — the numeric value is
    # the dedup unit, not the string.
    assert canonical_sample_accession("SAMN03995865") == "SAMEA3995865"
    assert canonical_sample_accession("SAMEA3995865") == "SAMEA3995865"
    assert canonical_sample_accession("SAMD00000000") == "SAMEA0"


def test_non_biosample_accessions_untouched():
    assert canonical_sample_accession("PRJNA12345") == "PRJNA12345"
    assert canonical_sample_accession("ERS123") == "ERS123"
    assert canonical_sample_accession(None) is None
    assert canonical_sample_accession("") == ""
    assert canonical_sample_accession("samn10033096") == "SAMEA10033096"  # case


def test_normalize_canonicalizes_primary_and_keeps_native_alias():
    rec = normalize("ncbi", {"biosample_accession": "SAMN03995865"})
    assert rec.primary_accession == "SAMEA3995865"
    assert {"SAMEA3995865", "SAMN03995865"} <= set(rec.accessions)


def test_normalize_cross_archive_intersection():
    """An ENA row and an NCBI row for the same physical sample share a canonical
    accession — the SampleAlias intersection that N3 resolves on."""
    ena = normalize("ena", {"sample_accession": "SAMEA3995865"})
    ncbi = normalize("ncbi", {"biosample_accession": "SAMN03995865"})
    assert ena.accessions & ncbi.accessions
    assert ena.primary_accession == ncbi.primary_accession
