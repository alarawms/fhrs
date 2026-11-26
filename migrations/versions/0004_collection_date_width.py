"""Widen samples.collection_date 32 → 96.

Live ENA harvest crashed (2026-11-26): INSDC collection_date can be an ISO 8601
interval ("2019-06-20T07:55:00Z/2019-06-20T12:20:00Z", 41 chars). 96 covers two
full timestamps plus the separator; the raw value is source truth (N1 — never
mangle, never silently truncate).
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_collection_date_width"
down_revision = "0003_harvest_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "samples",
        "collection_date",
        existing_type=sa.String(length=32),
        type_=sa.String(length=96),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "samples",
        "collection_date",
        existing_type=sa.String(length=96),
        type_=sa.String(length=32),
        existing_nullable=True,
    )
