"""Add discovered social profiles to leads.

``social_links`` holds only profiles found on the business's own website
(Instagram, X, TikTok, YouTube), keyed by platform. ``socials_checked_at``
records the last enrichment attempt so a search does not rescan the same site
on every rerun; a NULL value means the lead has never been scanned.

Revision ID: c4f9a2b61e07
Revises: b3e8f1c27d90
Create Date: 2026-11-20 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f9a2b61e07"
down_revision: Union[str, None] = "b3e8f1c27d90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column(
            "social_links",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "leads",
        sa.Column("socials_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("leads", "socials_checked_at")
    op.drop_column("leads", "social_links")
