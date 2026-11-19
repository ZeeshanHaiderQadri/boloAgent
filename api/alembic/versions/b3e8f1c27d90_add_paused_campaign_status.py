"""Add the ``paused`` email campaign status.

Pausing stops the send worker from claiming new recipient rows. Rows already
queued stay queued, so resuming continues exactly where the campaign stopped.

Revision ID: b3e8f1c27d90
Revises: a7d2e5c94f18
Create Date: 2026-11-19 09:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3e8f1c27d90"
down_revision: Union[str, None] = "a7d2e5c94f18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE email_campaign_status ADD VALUE IF NOT EXISTS 'paused'")


def downgrade() -> None:
    # Postgres cannot drop a single enum label in place. Recreating the type
    # would require rewriting every campaign row, so removal is deliberately
    # out of scope; no campaign can hold ``paused`` after a downgrade because
    # the endpoints that set it no longer exist.
    pass
