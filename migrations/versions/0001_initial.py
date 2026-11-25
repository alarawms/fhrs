"""Initial schema.

The initial migration delegates to the ORM metadata so the schema and the models
cannot drift apart (a drift class that caused real pain in the predecessor system).
Subsequent migrations must be explicit op.* operations; never edit this one — add a
new revision.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-25
"""

from alembic import op

from fhrs.core.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
