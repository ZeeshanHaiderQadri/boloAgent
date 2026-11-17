"""Add lead generation and lightweight CRM tables.

Revision ID: 9b1f0c2d4e77
Revises: f3a9c21b7e40
"""

import sqlalchemy as sa
from alembic import op

revision = "9b1f0c2d4e77"
down_revision = "f3a9c21b7e40"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lead_searches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("external_job_id", sa.String(length=191), nullable=True),
        sa.Column("results_count", sa.Integer(), nullable=False),
        sa.Column("new_leads_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lead_searches_id", "lead_searches", ["id"])
    op.create_index("ix_lead_searches_org_id", "lead_searches", ["organization_id"])
    op.create_index(
        "ix_lead_searches_org_status",
        "lead_searches",
        ["organization_id", "status"],
    )
    op.create_index("ix_lead_searches_created_at", "lead_searches", ["created_at"])

    op.create_table(
        "leads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=191), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("city", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=255), nullable=True),
        sa.Column("country", sa.String(length=255), nullable=True),
        sa.Column("postal_code", sa.String(length=32), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("phone_e164", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("website", sa.String(length=1024), nullable=True),
        sa.Column("place_id", sa.String(length=255), nullable=True),
        sa.Column("cid", sa.String(length=64), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("review_count", sa.Integer(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("open_hours", sa.JSON(), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.Column("crm_status", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_follow_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "dedupe_key",
            name="uq_leads_organization_dedupe_key",
        ),
    )
    op.create_index("ix_leads_id", "leads", ["id"])
    op.create_index("ix_leads_org_status", "leads", ["organization_id", "crm_status"])
    op.create_index("ix_leads_org_phone", "leads", ["organization_id", "phone_e164"])
    op.create_index(
        "ix_leads_org_created_at", "leads", ["organization_id", "created_at"]
    )
    op.create_index("ix_leads_org_owner", "leads", ["organization_id", "owner_user_id"])

    op.create_table(
        "lead_search_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("search_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["search_id"], ["lead_searches.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "search_id", "lead_id", name="uq_lead_search_results_pair"
        ),
    )
    op.create_index("ix_lead_search_results_id", "lead_search_results", ["id"])
    op.create_index(
        "ix_lead_search_results_search_id", "lead_search_results", ["search_id"]
    )
    op.create_index(
        "ix_lead_search_results_lead_id", "lead_search_results", ["lead_id"]
    )

    op.create_table(
        "lead_activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("activity_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lead_activities_id", "lead_activities", ["id"])
    op.create_index(
        "ix_lead_activities_lead_created",
        "lead_activities",
        ["lead_id", "created_at"],
    )
    op.create_index("ix_lead_activities_org_id", "lead_activities", ["organization_id"])

    op.create_table(
        "lead_campaign_dispatches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("source_uuid", sa.String(length=191), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["leads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "campaign_id",
            "source_uuid",
            name="uq_lead_dispatch_campaign_source",
        ),
    )
    op.create_index("ix_lead_campaign_dispatches_id", "lead_campaign_dispatches", ["id"])
    op.create_index(
        "ix_lead_campaign_dispatches_lead", "lead_campaign_dispatches", ["lead_id"]
    )
    op.create_index(
        "ix_lead_campaign_dispatches_campaign",
        "lead_campaign_dispatches",
        ["campaign_id"],
    )


def downgrade():
    op.drop_table("lead_campaign_dispatches")
    op.drop_table("lead_activities")
    op.drop_table("lead_search_results")
    op.drop_table("leads")
    op.drop_table("lead_searches")
