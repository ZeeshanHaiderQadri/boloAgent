"""Track lead search targets and automatic outbound campaigns.

Revision ID: 4c7d8e9f1a2b
Revises: 9b1f0c2d4e77
"""

import sqlalchemy as sa
from alembic import op

revision = "4c7d8e9f1a2b"
down_revision = "9b1f0c2d4e77"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "lead_searches", sa.Column("target_results", sa.Integer(), nullable=True)
    )
    op.add_column(
        "lead_searches",
        sa.Column("auto_call_campaign_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "lead_searches", sa.Column("auto_call_error", sa.Text(), nullable=True)
    )
    op.create_foreign_key(
        "fk_lead_searches_auto_call_campaign",
        "lead_searches",
        "campaigns",
        ["auto_call_campaign_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint(
        "fk_lead_searches_auto_call_campaign", "lead_searches", type_="foreignkey"
    )
    op.drop_column("lead_searches", "auto_call_error")
    op.drop_column("lead_searches", "auto_call_campaign_id")
    op.drop_column("lead_searches", "target_results")
