"""task run deadline: tasks.run_deadline_at

Revision ID: 7c3f0a9d2e61
Revises: 5b7e2d91c0a4
Create Date: 2026-10-21 09:00:00.000000

Stores when a dispatched device command counts as timed out. The task engine writes it at dispatch from the
user's ``automation.default_task_timeout_seconds`` setting, and the timeout sweep reads it. The column is NULL
for tasks that were never sent and for rows dispatched before this migration; the sweep falls back to
``started_at`` plus the configured device command timeout for those rows.

Downgrade drops the column only.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7c3f0a9d2e61"
down_revision: str | None = "5b7e2d91c0a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS run_deadline_at TIMESTAMP WITH TIME ZONE")


def downgrade() -> None:
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS run_deadline_at")
