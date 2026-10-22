"""conversation pinned flag

Revision ID: e1f4b8c2a9d3
Revises: d7a09bd3dc82
Create Date: 2026-10-21 18:00:00.000000

Adds conversations.pinned, NOT NULL with server default false, so existing rows become unpinned. The list
route orders pinned conversations first. Downgrade drops the column.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1f4b8c2a9d3"
down_revision: str | None = "d7a09bd3dc82"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("pinned", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("conversations", "pinned")
