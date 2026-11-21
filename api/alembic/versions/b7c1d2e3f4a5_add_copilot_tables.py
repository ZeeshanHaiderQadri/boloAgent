"""Add copilot conversation and tool-audit tables.

Revision ID: b7c1d2e3f4a5
Revises: c4f9a1e27b60
Create Date: 2026-11-21

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "c4f9a1e27b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "copilot_conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "title",
            sa.String(length=120),
            nullable=False,
            server_default="New conversation",
        ),
        sa.Column("last_request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "last_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_copilot_conversations_org_user_updated",
        "copilot_conversations",
        ["organization_id", "user_id", "updated_at"],
    )

    op.create_table(
        "copilot_tool_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("risk", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
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
    op.create_index(
        "ix_copilot_tool_calls_conversation_created",
        "copilot_tool_calls",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_copilot_tool_calls_organization_id",
        "copilot_tool_calls",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_copilot_tool_calls_organization_id", table_name="copilot_tool_calls"
    )
    op.drop_index(
        "ix_copilot_tool_calls_conversation_created", table_name="copilot_tool_calls"
    )
    op.drop_table("copilot_tool_calls")
    op.drop_index(
        "ix_copilot_conversations_org_user_updated",
        table_name="copilot_conversations",
    )
    op.drop_table("copilot_conversations")
