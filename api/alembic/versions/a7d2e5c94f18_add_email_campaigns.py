"""Add email campaigns and held campaign recipients.

A campaign stores its audience RULE (explicit lead ids, a project, a tag, or a
leads-list filter), the chosen sender and template, and a frozen copy of the
template and sender identity once it launches. Recipient rows reuse
``email_messages``: they get ``campaign_id``/``lead_id`` and are created in the
new ``held`` status, which the send worker never claims. Only the campaign
launch step may move a held row to ``queued``.

The ``held`` value is added to the existing ``email_message_status`` enum with
``ALTER TYPE ... ADD VALUE IF NOT EXISTS``. PostgreSQL 12+ allows that inside
the migration transaction as long as the value is not used before the
transaction commits, which is the case here: this revision only adds the label.
PostgreSQL cannot remove an enum label, so ``downgrade`` intentionally leaves
``held`` in place; it is unused and harmless, and re-upgrading is a no-op for
the enum.

Campaign recipient addresses are normalized by the resolver: trimmed and
lowercased. The database enforces the same rule for campaign rows only
(``to_email = btrim(lower(to_email))``), so a padded address cannot slip past
the per-campaign uniqueness index.

WARNING: downgrading is destructive for campaign data. Roll the application
back to this revision's predecessor *before* running ``alembic downgrade`` and
keep the newer application stopped while it runs. The downgrade:

* converts every ``held`` recipient row to ``cancelled`` (they were never
  sent), so no row is left in a state the older code does not know;
* drops ``email_campaigns`` with all campaign rows, their counters and audit
  fields;
* drops ``email_messages.campaign_id``/``lead_id`` and the campaign-only
  indexes and check;
* leaves the ``held`` enum label in place (PostgreSQL cannot remove a label).

Ordinary (non-campaign) ``email_messages`` rows keep their content, address
casing and spacing; only campaign-created rows are affected.

Revision ID: a7d2e5c94f18
Revises: b7e3f1a2c9d4
Create Date: 2026-11-19 01:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a7d2e5c94f18"
down_revision: Union[str, None] = "b7e3f1a2c9d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive and never used inside this transaction, so it is safe to run
    # within the migration on PostgreSQL 12+.
    op.execute("ALTER TYPE email_message_status ADD VALUE IF NOT EXISTS 'held'")

    op.create_table(
        "email_campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_uuid", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "ready",
                "launching",
                "sending",
                "completed",
                "cancelled",
                "failed",
                name="email_campaign_status",
            ),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("sender_id", sa.Integer(), nullable=True),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("frozen_subject", sa.String(length=998), nullable=True),
        sa.Column("frozen_html_body", sa.Text(), nullable=True),
        sa.Column("frozen_text_body", sa.Text(), nullable=True),
        sa.Column("frozen_has_unsubscribe", sa.Boolean(), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sender_from_name", sa.String(length=255), nullable=True),
        sa.Column("sender_from_email", sa.String(length=320), nullable=True),
        sa.Column("sender_reply_to_email", sa.String(length=320), nullable=True),
        sa.Column(
            "audience_type",
            sa.Enum(
                "leads",
                "project",
                "tag",
                "filter",
                name="email_campaign_audience_type",
            ),
            server_default=sa.text("'leads'"),
            nullable=False,
        ),
        sa.Column(
            "audience",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "consent_policy",
            sa.Enum(
                "opted_in_only",
                "include_unknown",
                name="email_campaign_consent_policy",
            ),
            server_default=sa.text("'opted_in_only'"),
            nullable=False,
        ),
        sa.Column("consent_override_reason", sa.Text(), nullable=True),
        sa.Column("consent_override_user_id", sa.Integer(), nullable=True),
        sa.Column("consent_override_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_override_warning", sa.Text(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("launched_by", sa.Integer(), nullable=True),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.Integer(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "recipient_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "eligible_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "excluded_opted_out_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "excluded_unknown_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "excluded_suppressed_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "excluded_invalid_email_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "excluded_no_email_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "duplicates_collapsed_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "sent_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "failed_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "bounced_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "complained_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "unsubscribed_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        # NO ACTION, not RESTRICT: organization teardown cascades through both
        # email_campaigns and email_senders in one statement, which RESTRICT
        # would reject. A direct sender delete is still refused; the sender
        # endpoint (PR b) detaches finished campaigns first.
        sa.ForeignKeyConstraint(
            ["sender_id"], ["email_senders.id"], ondelete="NO ACTION"
        ),
        sa.ForeignKeyConstraint(
            ["template_id"], ["email_templates.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["consent_override_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["launched_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["cancelled_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_email_campaigns_org_name"
        ),
    )
    op.create_index("ix_email_campaigns_id", "email_campaigns", ["id"])
    op.create_index(
        "ix_email_campaigns_campaign_uuid",
        "email_campaigns",
        ["campaign_uuid"],
        unique=True,
    )
    op.create_index(
        "ix_email_campaigns_organization_id",
        "email_campaigns",
        ["organization_id"],
    )
    op.create_index(
        "ix_email_campaigns_org_status",
        "email_campaigns",
        ["organization_id", "status"],
    )

    op.add_column(
        "email_messages", sa.Column("campaign_id", sa.Integer(), nullable=True)
    )
    op.add_column("email_messages", sa.Column("lead_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_email_messages_campaign_id",
        "email_messages",
        "email_campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_email_messages_lead_id",
        "email_messages",
        "leads",
        ["lead_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_email_messages_campaign_id", "email_messages", ["campaign_id"])
    op.create_index(
        "ix_email_messages_campaign_status", "email_messages", ["campaign_id", "status"]
    )
    op.create_index("ix_email_messages_lead_id", "email_messages", ["lead_id"])
    op.create_index(
        "uq_email_messages_campaign_email",
        "email_messages",
        ["campaign_id", "to_email"],
        unique=True,
        postgresql_where=sa.text("campaign_id IS NOT NULL"),
    )
    # btrim rejects surrounding spaces; lower() rejects mixed case. The resolver
    # must apply the same trim+lowercase before inserting campaign recipients.
    op.create_check_constraint(
        "ck_email_messages_campaign_to_email_lowercase",
        "email_messages",
        "campaign_id IS NULL OR to_email = btrim(lower(to_email))",
    )


def downgrade() -> None:
    # DESTRUCTIVE. Read the module docstring: roll the application back first.
    # A held row was never sent and the older code does not know the status, so
    # convert it to cancelled before the campaign context (campaign_id) is
    # dropped. This is deterministic and leaves no row in an unknown state.
    op.execute("UPDATE email_messages SET status = 'cancelled' WHERE status = 'held'")

    op.drop_constraint(
        "ck_email_messages_campaign_to_email_lowercase",
        "email_messages",
        type_="check",
    )
    op.drop_index("uq_email_messages_campaign_email", table_name="email_messages")
    # IF EXISTS: databases that ran the earlier, pre-final shape of this same
    # revision never created these two indexes. Everything else below exists
    # in both shapes.
    op.execute("DROP INDEX IF EXISTS ix_email_messages_lead_id")
    op.execute("DROP INDEX IF EXISTS ix_email_messages_campaign_status")
    op.drop_index("ix_email_messages_campaign_id", table_name="email_messages")
    op.drop_constraint(
        "fk_email_messages_campaign_id", "email_messages", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_email_messages_lead_id", "email_messages", type_="foreignkey"
    )
    op.drop_column("email_messages", "lead_id")
    op.drop_column("email_messages", "campaign_id")

    op.drop_index("ix_email_campaigns_org_status", table_name="email_campaigns")
    op.drop_index("ix_email_campaigns_organization_id", table_name="email_campaigns")
    op.drop_index("ix_email_campaigns_campaign_uuid", table_name="email_campaigns")
    op.drop_index("ix_email_campaigns_id", table_name="email_campaigns")
    op.drop_table("email_campaigns")

    # The enum label cannot be removed in PostgreSQL and is unused after this
    # downgrade. Leaving it keeps re-upgrade a no-op and avoids a table rewrite.
    op.execute("DROP TYPE IF EXISTS email_campaign_consent_policy")
    op.execute("DROP TYPE IF EXISTS email_campaign_audience_type")
    op.execute("DROP TYPE IF EXISTS email_campaign_status")
