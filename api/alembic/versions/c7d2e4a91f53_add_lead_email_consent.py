"""Add marketing-email consent to leads.

Existing leads start as ``unknown``: they have not opted in, so they are never
marketing-eligible until a form, import, or unsubscribe link records a
decision. Split out from the Email module migration so the change to the
existing leads table has its own named revision and can be reviewed or
reverted on its own.

Revision ID: c7d2e4a91f53
Revises: e2a7c4f91b30
Create Date: 2026-11-18 08:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d2e4a91f53"
down_revision: Union[str, None] = "e2a7c4f91b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    consent = sa.Enum("unknown", "opted_in", "opted_out", name="lead_email_consent")
    consent.create(op.get_bind())

    op.add_column(
        "leads",
        sa.Column(
            "email_consent",
            sa.Enum("unknown", "opted_in", "opted_out", name="lead_email_consent"),
            server_default=sa.text("'unknown'"),
            nullable=False,
        ),
    )
    op.add_column(
        "leads",
        sa.Column("email_consent_source", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "leads",
        sa.Column("email_consent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_leads_org_email_consent",
        "leads",
        ["organization_id", "email_consent"],
    )


def downgrade() -> None:
    op.drop_index("ix_leads_org_email_consent", table_name="leads")
    op.drop_column("leads", "email_consent_at")
    op.drop_column("leads", "email_consent_source")
    op.drop_column("leads", "email_consent")
    op.execute("DROP TYPE IF EXISTS lead_email_consent")
