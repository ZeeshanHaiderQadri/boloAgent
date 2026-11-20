"""Add WhatsApp disclaimer acceptances.

Records that one user in one organization saw and accepted the click-to-chat
risk notice. The lead-links endpoint refuses to build links until the current
wording has been accepted; nothing here sends or calls WhatsApp.

Revision ID: c4f9a1e27b60
Revises: f2b6c8d41a97
Create Date: 2026-11-20 16:45:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f9a1e27b60"
down_revision: Union[str, None] = "f2b6c8d41a97"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_disclaimer_acceptances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("text_version", sa.String(length=32), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_whatsapp_disclaimer_org_user",
        ),
    )
    op.create_index(
        "ix_whatsapp_disclaimer_organization_id",
        "whatsapp_disclaimer_acceptances",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_whatsapp_disclaimer_organization_id",
        table_name="whatsapp_disclaimer_acceptances",
    )
    op.drop_table("whatsapp_disclaimer_acceptances")
