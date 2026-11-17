"""Add saved lead projects (lists) and exclusive lead membership.

Revision ID: 8f2c1a7d4e90
Revises: 4c7d8e9f1a2b
"""

import sqlalchemy as sa
from alembic import op

revision = "8f2c1a7d4e90"
down_revision = "4c7d8e9f1a2b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lead_projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_lead_projects_org_name"
        ),
    )
    op.create_index("ix_lead_projects_id", "lead_projects", ["id"])
    op.create_index(
        "ix_lead_projects_organization_id", "lead_projects", ["organization_id"]
    )
    op.create_index(
        "ix_lead_projects_org_created",
        "lead_projects",
        ["organization_id", "created_at"],
    )

    op.add_column("leads", sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_leads_project_id",
        "leads",
        "lead_projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_leads_project_id", "leads", ["project_id"])
    op.create_index(
        "ix_leads_org_project", "leads", ["organization_id", "project_id"]
    )


def downgrade():
    op.drop_index("ix_leads_org_project", table_name="leads")
    op.drop_index("ix_leads_project_id", table_name="leads")
    op.drop_constraint("fk_leads_project_id", "leads", type_="foreignkey")
    op.drop_column("leads", "project_id")

    op.drop_index("ix_lead_projects_org_created", table_name="lead_projects")
    op.drop_index(
        "ix_lead_projects_organization_id", table_name="lead_projects"
    )
    op.drop_index("ix_lead_projects_id", table_name="lead_projects")
    op.drop_table("lead_projects")
