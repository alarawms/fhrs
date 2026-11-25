"""unmapped_checklists.last_event_id — replay idempotence for the N5 queue.

Explicit migration (the convention for every revision after 0001_initial).

Revision ID: 0002_unmapped_last_event
Revises: 0001_initial
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_unmapped_last_event"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "unmapped_checklists",
        sa.Column("last_event_id", sa.Integer(), sa.ForeignKey("source_events.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("unmapped_checklists", "last_event_id")
