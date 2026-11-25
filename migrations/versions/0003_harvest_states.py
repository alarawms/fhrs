"""harvest_states — per-(source, query) harvest cursors for Stage-1 connectors.

Revision ID: 0003_harvest_states
Revises: 0002_unmapped_last_event
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_harvest_states"
down_revision = "0002_unmapped_last_event"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "harvest_states",
        sa.Column("source", sa.String(16), primary_key=True),
        sa.Column("query_key", sa.String(128), primary_key=True),
        sa.Column("offset", sa.Integer(), nullable=False),
        sa.Column("high_water", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("harvest_states")
