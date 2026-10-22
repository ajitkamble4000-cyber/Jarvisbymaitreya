"""conversation summary marker: conversations.summary_through_message_id

Revision ID: a8c3e5f1d7b2
Revises: 5b7e2d91c0a4
Create Date: 2026-10-21 09:00:00.000000

Records the newest message covered by the latest automatic conversation summary, so the next summary
counts only messages written after it. Nullable: conversations without a summary keep NULL. It is not a
foreign key, so removing that message (edit or regenerate) leaves the conversation row unchanged.

Downgrade drops the column only.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a8c3e5f1d7b2"
down_revision: str | None = "5b7e2d91c0a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("summary_through_message_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "summary_through_message_id")
