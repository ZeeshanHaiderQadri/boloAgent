"""Add the template authoring fields.

``email_templates`` previously stored only the rendered HTML. Authoring needs
the format the author typed in (HTML or the MJML subset Bolo compiles), the
source itself, and a stored flag for the required unsubscribe placeholder so a
campaign launch can refuse a template that lacks one.

Existing rows are HTML templates; their current body becomes the source.

Revision ID: b7e3f1a2c9d4
Revises: a4c9d2e17f60
Create Date: 2026-11-18 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e3f1a2c9d4"
down_revision: Union[str, None] = "a4c9d2e17f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Mirrors ``services.email.templates.has_unsubscribe_placeholder``: only an
# anchor whose href contains the placeholder counts, with optional spaces
# inside the braces. Everything else (text, comments, scripts, title
# attributes, the subject) is ignored.
_UNSUBSCRIBE_BACKFILL_SQL = r"""
UPDATE email_templates
SET has_unsubscribe_placeholder = true
WHERE html_body ~* '<a[[:space:]][^>]*href[[:space:]]*=[[:space:]]*["'']?[^>]*\{\{[[:space:]]*unsubscribe_link[[:space:]]*\}\}'
"""


def upgrade() -> None:
    op.add_column(
        "email_templates",
        sa.Column(
            "source_format",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'html'"),
        ),
    )
    op.add_column(
        "email_templates",
        sa.Column("source_body", sa.Text(), nullable=True),
    )
    op.add_column(
        "email_templates",
        sa.Column(
            "has_unsubscribe_placeholder",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.execute(
        "UPDATE email_templates SET source_body = html_body WHERE source_body IS NULL"
    )
    op.execute(_UNSUBSCRIBE_BACKFILL_SQL)
    op.alter_column(
        "email_templates",
        "source_body",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_email_templates_source_format",
        "email_templates",
        "source_format IN ('html', 'mjml')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_email_templates_source_format", "email_templates", type_="check"
    )
    op.drop_column("email_templates", "has_unsubscribe_placeholder")
    op.drop_column("email_templates", "source_body")
    op.drop_column("email_templates", "source_format")
