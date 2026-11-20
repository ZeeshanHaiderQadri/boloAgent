"""Add WhatsApp templates and senders.

PR (b) is click-to-chat only: these tables describe message copy and the
operator's own saved number. Nothing here sends through the WhatsApp Business
API, and no worker reads these rows.

Revision ID: f2b6c8d41a97
Revises: d8b1e4c70f21
Create Date: 2026-11-20 13:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2b6c8d41a97"
down_revision: Union[str, None] = "d8b1e4c70f21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_uuid", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "language",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'en'"),
        ),
        sa.Column(
            "category",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'marketing'"),
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "variables",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_whatsapp_templates_org_name"
        ),
        sa.CheckConstraint(
            "category IN ('marketing', 'utility', 'authentication')",
            name="ck_whatsapp_templates_category",
        ),
    )
    op.create_index("ix_whatsapp_templates_id", "whatsapp_templates", ["id"])
    op.create_index(
        "ix_whatsapp_templates_template_uuid",
        "whatsapp_templates",
        ["template_uuid"],
        unique=True,
    )
    op.create_index(
        "ix_whatsapp_templates_organization_id",
        "whatsapp_templates",
        ["organization_id"],
    )

    op.create_table(
        "whatsapp_senders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sender_uuid", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("phone_e164", sa.String(length=32), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "phone_e164", name="uq_whatsapp_senders_org_phone"
        ),
    )
    op.create_index("ix_whatsapp_senders_id", "whatsapp_senders", ["id"])
    op.create_index(
        "ix_whatsapp_senders_sender_uuid",
        "whatsapp_senders",
        ["sender_uuid"],
        unique=True,
    )
    op.create_index(
        "ix_whatsapp_senders_organization_id",
        "whatsapp_senders",
        ["organization_id"],
    )
    op.create_index(
        "ix_whatsapp_senders_one_default",
        "whatsapp_senders",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_whatsapp_senders_one_default",
        table_name="whatsapp_senders",
        postgresql_where=sa.text("is_default"),
    )
    op.drop_index("ix_whatsapp_senders_organization_id", table_name="whatsapp_senders")
    op.drop_index("ix_whatsapp_senders_sender_uuid", table_name="whatsapp_senders")
    op.drop_index("ix_whatsapp_senders_id", table_name="whatsapp_senders")
    op.drop_table("whatsapp_senders")

    op.drop_index(
        "ix_whatsapp_templates_organization_id", table_name="whatsapp_templates"
    )
    op.drop_index(
        "ix_whatsapp_templates_template_uuid", table_name="whatsapp_templates"
    )
    op.drop_index("ix_whatsapp_templates_id", table_name="whatsapp_templates")
    op.drop_table("whatsapp_templates")
