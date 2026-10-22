"""merge task run deadline and conversation summary marker

Revision ID: d7a09bd3dc82
Revises: 7c3f0a9d2e61, a8c3e5f1d7b2
Create Date: 2026-10-21 17:16:31.718613
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op



revision: str = 'd7a09bd3dc82'
down_revision: str | None = ('7c3f0a9d2e61', 'a8c3e5f1d7b2')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
