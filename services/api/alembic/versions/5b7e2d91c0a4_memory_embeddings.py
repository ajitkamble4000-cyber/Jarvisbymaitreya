"""memory embeddings: pgvector extension and memories.embedding vector(768)

Revision ID: 5b7e2d91c0a4
Revises: 44121059e6e8
Create Date: 2026-10-20 09:00:00.000000

Production note: ``CREATE EXTENSION vector`` needs a role that may create the extension. Depending on
how pgvector was packaged, that is a superuser, or a role with the privilege for trusted extensions.
Run this migration with such a role, or create the extension beforehand as a superuser:
``CREATE EXTENSION IF NOT EXISTS vector;`` in the application database. See docs/operations.md.

The column is NULL for memories that have not been embedded. Dimensions are fixed at 768 to match
``app.memory.embeddings.VECTOR_DIMENSIONS``. Changing them needs a new migration and a re-embed.

Downgrade drops the column only. The extension stays installed, because other objects may use it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5b7e2d91c0a4"
down_revision: str | None = "44121059e6e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE memories ADD COLUMN embedding vector(768)")


def downgrade() -> None:
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS embedding")
