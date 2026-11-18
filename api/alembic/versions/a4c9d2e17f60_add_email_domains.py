"""Add sending-domain records for the Email module.

A domain belongs to one organization and points at the sender whose provider
credentials are used to check it. SES exposes a verification API, so its
status and DKIM tokens can be refreshed; SMTP hosts verify domains in their own
dashboard, which is why the row is still useful as a record there.

Revision ID: a4c9d2e17f60
Revises: d9f1b6a84c07
Create Date: 2026-11-18 09:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a4c9d2e17f60"
down_revision: Union[str, None] = "d9f1b6a84c07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_domains",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("domain_uuid", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=True),
        sa.Column("domain", sa.String(length=253), nullable=False),
        sa.Column(
            "provider",
            postgresql.ENUM(
                "smtp",
                "ses",
                "mailgun",
                "mandrill",
                name="email_provider",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "verified",
                "failed",
                name="email_domain_status",
            ),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "verification",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"], ["email_senders.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "domain", name="uq_email_domains_org_domain"
        ),
        sa.CheckConstraint(
            "domain = lower(domain)", name="ck_email_domains_domain_lowercase"
        ),
    )
    op.create_index("ix_email_domains_id", "email_domains", ["id"])
    op.create_index(
        "ix_email_domains_domain_uuid", "email_domains", ["domain_uuid"], unique=True
    )
    op.create_index(
        "ix_email_domains_organization_id", "email_domains", ["organization_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_email_domains_organization_id", table_name="email_domains")
    op.drop_index("ix_email_domains_domain_uuid", table_name="email_domains")
    op.drop_index("ix_email_domains_id", table_name="email_domains")
    op.drop_table("email_domains")
    op.execute("DROP TYPE IF EXISTS email_domain_status")
