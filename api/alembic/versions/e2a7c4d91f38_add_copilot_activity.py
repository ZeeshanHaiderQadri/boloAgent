"""Add the copilot activity log rendered by the agent workspace.

Revision ID: e2a7c4d91f38
Revises: b7c1d2e3f4a5
Create Date: 2026-11-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e2a7c4d91f38"
down_revision: Union[str, Sequence[str], None] = "b7c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "copilot_activity",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=True),
        sa.Column("risk", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["copilot_conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_copilot_activity_id", "copilot_activity", ["id"])
    op.create_index(
        "ix_copilot_activity_conversation_id",
        "copilot_activity",
        ["conversation_id", "id"],
    )
    op.create_index(
        "ix_copilot_activity_organization_id",
        "copilot_activity",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_copilot_activity_organization_id", table_name="copilot_activity")
    op.drop_index("ix_copilot_activity_conversation_id", table_name="copilot_activity")
    op.drop_index("ix_copilot_activity_id", table_name="copilot_activity")
    op.drop_table("copilot_activity")
