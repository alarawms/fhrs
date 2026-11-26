"""Index the FK columns referencing source_events (0005).

Live incident 2026-11-26: a 150k-row DELETE on source_events ran for ~50 minutes
because every FK check (attribute_history / sample_attributes / merge_events /
unmapped_checklists event_id) did an unindexed seq scan per batch. The indexes
were created directly in the live database to unblock the purge; this migration
makes them schema-as-code. SQLite/Postgres both accept the same DDL.
"""

from alembic import op

revision = "0005_event_fk_indexes"
down_revision = "0004_collection_date_width"
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_attribute_history_event_id", "attribute_history", "event_id"),
    ("ix_merge_events_event_id", "merge_events", "event_id"),
    ("ix_sample_attributes_event_id", "sample_attributes", "event_id"),
    ("ix_unmapped_checklists_last_event_id", "unmapped_checklists", "last_event_id"),
]


def upgrade() -> None:
    for name, table, column in INDEXES:
        op.create_index(name, table, [column])


def downgrade() -> None:
    for name, _, _ in INDEXES:
        op.drop_index(name)
