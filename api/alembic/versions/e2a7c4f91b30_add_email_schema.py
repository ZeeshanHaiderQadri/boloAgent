"""Add the Email module schema: senders, templates, messages, suppressions.

Every sender belongs to one organization and references an encrypted
``external_credentials`` row; Bolo never ships or uses a shared sending
account. Messages keep the rendered subject and bodies they were sent with so
later template edits never rewrite delivery history.

Revision ID: e2a7c4f91b30
Revises: d7c4e1a96b20
Create Date: 2026-11-18 07:05:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e2a7c4f91b30"
down_revision: Union[str, None] = "d7c4e1a96b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_senders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sender_uuid", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                "smtp",
                "ses",
                "mailgun",
                "mandrill",
                name="email_provider",
            ),
            nullable=False,
        ),
        sa.Column("from_name", sa.String(length=255), nullable=False),
        sa.Column("from_email", sa.String(length=320), nullable=False),
        sa.Column("reply_to_email", sa.String(length=320), nullable=True),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("credential_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "verified",
                "disabled",
                name="email_sender_status",
            ),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("daily_cap", sa.Integer(), nullable=True),
        sa.Column("rate_per_second", sa.Float(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["external_credentials.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_email_senders_org_name"
        ),
    )
    op.create_index("ix_email_senders_id", "email_senders", ["id"])
    op.create_index(
        "ix_email_senders_sender_uuid", "email_senders", ["sender_uuid"], unique=True
    )
    op.create_index(
        "ix_email_senders_organization_id", "email_senders", ["organization_id"]
    )
    op.create_index(
        "ix_email_senders_one_default",
        "email_senders",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.create_table(
        "email_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_uuid", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=998), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_email_templates_org_name"
        ),
    )
    op.create_index("ix_email_templates_id", "email_templates", ["id"])
    op.create_index(
        "ix_email_templates_template_uuid",
        "email_templates",
        ["template_uuid"],
        unique=True,
    )
    op.create_index(
        "ix_email_templates_organization_id", "email_templates", ["organization_id"]
    )

    op.create_table(
        "email_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_uuid", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("to_email", sa.String(length=320), nullable=False),
        sa.Column("to_name", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.String(length=998), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=True),
        sa.Column(
            "headers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "sending",
                "sent",
                "failed",
                "bounced",
                "complained",
                "cancelled",
                name="email_message_status",
            ),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"], ["email_senders.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["template_id"], ["email_templates.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_messages_id", "email_messages", ["id"])
    op.create_index(
        "ix_email_messages_message_uuid",
        "email_messages",
        ["message_uuid"],
        unique=True,
    )
    op.create_index(
        "ix_email_messages_organization_id", "email_messages", ["organization_id"]
    )
    op.create_index(
        "ix_email_messages_org_created",
        "email_messages",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_email_messages_status_scheduled",
        "email_messages",
        ["status", "scheduled_at"],
    )

    op.create_table(
        "email_suppressions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "reason",
            sa.Enum(
                "unsubscribed",
                "hard_bounce",
                "complaint",
                "manual",
                name="email_suppression_reason",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "email", name="uq_email_suppressions_org_email"
        ),
        sa.CheckConstraint(
            "email = lower(email)", name="ck_email_suppressions_email_lowercase"
        ),
    )
    op.create_index("ix_email_suppressions_id", "email_suppressions", ["id"])
    op.create_index(
        "ix_email_suppressions_organization_id",
        "email_suppressions",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_suppressions_organization_id", table_name="email_suppressions"
    )
    op.drop_index("ix_email_suppressions_id", table_name="email_suppressions")
    op.drop_table("email_suppressions")
    op.execute("DROP TYPE IF EXISTS email_suppression_reason")

    op.drop_index("ix_email_messages_status_scheduled", table_name="email_messages")
    op.drop_index("ix_email_messages_org_created", table_name="email_messages")
    op.drop_index("ix_email_messages_organization_id", table_name="email_messages")
    op.drop_index("ix_email_messages_message_uuid", table_name="email_messages")
    op.drop_index("ix_email_messages_id", table_name="email_messages")
    op.drop_table("email_messages")
    op.execute("DROP TYPE IF EXISTS email_message_status")

    op.drop_index("ix_email_templates_organization_id", table_name="email_templates")
    op.drop_index("ix_email_templates_template_uuid", table_name="email_templates")
    op.drop_index("ix_email_templates_id", table_name="email_templates")
    op.drop_table("email_templates")

    op.drop_index("ix_email_senders_one_default", table_name="email_senders")
    op.drop_index("ix_email_senders_organization_id", table_name="email_senders")
    op.drop_index("ix_email_senders_sender_uuid", table_name="email_senders")
    op.drop_index("ix_email_senders_id", table_name="email_senders")
    op.drop_table("email_senders")
    op.execute("DROP TYPE IF EXISTS email_sender_status")
    op.execute("DROP TYPE IF EXISTS email_provider")
