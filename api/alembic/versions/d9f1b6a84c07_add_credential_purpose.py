"""Mark which feature owns an external credential.

The existing ``/api/v1/credentials`` endpoints exist for webhook credentials.
Email sender credentials are stored in the same encrypted table (so the
rotation sweep covers them) but must not appear in that list, so the list now
filters on ``purpose = 'webhook'``. Existing rows default to webhook.

Revision ID: d9f1b6a84c07
Revises: c7d2e4a91f53
Create Date: 2026-11-18 08:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9f1b6a84c07"
down_revision: Union[str, None] = "c7d2e4a91f53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    purpose = sa.Enum("webhook", "email_sender", name="credential_purpose")
    purpose.create(op.get_bind())

    op.add_column(
        "external_credentials",
        sa.Column(
            "purpose",
            sa.Enum("webhook", "email_sender", name="credential_purpose"),
            server_default=sa.text("'webhook'"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_webhook_credentials_org_purpose",
        "external_credentials",
        ["organization_id", "purpose"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_webhook_credentials_org_purpose", table_name="external_credentials"
    )
    op.drop_column("external_credentials", "purpose")
    op.execute("DROP TYPE IF EXISTS credential_purpose")
