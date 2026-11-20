"""Track social-profile scan retries on leads.

A transient fetch failure must not erase profiles found earlier or leave the
lead in a permanent retry loop. ``socials_attempts`` counts consecutive
failures and ``socials_next_attempt_at`` backs the next attempt off.

Revision ID: d8b1e4c70f21
Revises: c4f9a2b61e07
Create Date: 2026-11-20 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8b1e4c70f21"
down_revision: Union[str, None] = "c4f9a2b61e07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column(
            "socials_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "leads",
        sa.Column("socials_next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("leads", "socials_next_attempt_at")
    op.drop_column("leads", "socials_attempts")
