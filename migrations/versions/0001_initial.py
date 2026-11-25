"""Initial schema.

Explicit operations, frozen at this revision — migrations must never reference live
ORM metadata, or a later model change silently rewrites history (0002's column would
have been created twice). Add new revisions for new changes; never edit this one.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

DT = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "source_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("accession", sa.String(64), nullable=False),
        sa.Column("fetched_at", DT, nullable=False),
        sa.Column("payload_path", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("tier", sa.String(1), nullable=True),
        sa.Column("reprocessed_at", DT, nullable=True),
        sa.Column("created_at", DT, nullable=False),
    )
    op.create_index("ix_source_events_source", "source_events", ["source"])
    op.create_index("ix_source_events_accession", "source_events", ["accession"])

    op.create_table(
        "studies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("accession", sa.String(64), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("visibility", sa.String(16), nullable=False),
    )
    op.create_index("ix_studies_accession", "studies", ["accession"])

    op.create_table(
        "samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canonical_accession", sa.String(64), nullable=False, unique=True),
        sa.Column("study_id", sa.Integer(), sa.ForeignKey("studies.id"), nullable=True),
        sa.Column("tier", sa.String(1), nullable=False),
        sa.Column("visibility", sa.String(16), nullable=False),
        sa.Column("marine_eez", sa.Boolean(), nullable=True),
        sa.Column("needs_geo_curation", sa.Boolean(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("checklist_id", sa.String(128), nullable=True),
        sa.Column("content_bucket", sa.String(32), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("collection_date", sa.String(32), nullable=True),
        sa.Column("scientific_name", sa.Text(), nullable=True),
        sa.Column("library_strategy", sa.String(64), nullable=True),
        sa.Column("country_raw", sa.Text(), nullable=True),
        sa.Column("center_name", sa.Text(), nullable=True),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("updated_at", DT, nullable=False),
    )
    op.create_index("ix_samples_canonical_accession", "samples", ["canonical_accession"])

    op.create_table(
        "sample_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("accession", sa.String(64), nullable=False, unique=True),
        sa.Column("sample_id", sa.Integer(), sa.ForeignKey("samples.id"), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
    )
    op.create_index("ix_sample_aliases_accession", "sample_aliases", ["accession"])

    op.create_table(
        "merge_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "canonical_sample_id", sa.Integer(), sa.ForeignKey("samples.id"), nullable=False
        ),
        sa.Column("merged_accession", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("source_events.id"), nullable=True),
        sa.Column("created_at", DT, nullable=False),
    )

    op.create_table(
        "sample_attributes",
        sa.Column("sample_id", sa.Integer(), sa.ForeignKey("samples.id"), primary_key=True),
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("source_events.id"), nullable=True),
        sa.Column("updated_at", DT, nullable=False),
    )

    op.create_table(
        "attribute_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sample_id", sa.Integer(), sa.ForeignKey("samples.id"), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("source_events.id"), nullable=True),
        sa.Column("changed_at", DT, nullable=False),
    )
    op.create_index("ix_attribute_history_sample_id", "attribute_history", ["sample_id"])

    op.create_table(
        "unmapped_checklists",
        sa.Column("checklist_id", sa.String(128), primary_key=True),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("example_accession", sa.String(64), nullable=True),
        sa.Column("first_seen", DT, nullable=False),
        sa.Column("last_seen", DT, nullable=False),
    )

    op.create_table(
        "curation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sample_id", sa.Integer(), sa.ForeignKey("samples.id"), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", DT, nullable=False),
        sa.Column("resolved_at", DT, nullable=True),
    )
    op.create_index("ix_curation_events_sample_id", "curation_events", ["sample_id"])

    op.create_table(
        "stats_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("built_at", DT, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_stats_snapshots_country_code", "stats_snapshots", ["country_code"])


def downgrade() -> None:
    for table in (
        "stats_snapshots",
        "curation_events",
        "unmapped_checklists",
        "attribute_history",
        "sample_attributes",
        "merge_events",
        "sample_aliases",
        "samples",
        "studies",
        "source_events",
    ):
        op.drop_table(table)
