"""Tests for the template authoring migration's unsubscribe backfill.

The migration is unmerged, so it is amended in place. This test runs its
backfill statement against the real ``email_templates`` table inside the test
transaction and checks that only an anchor href flags a template.

The migration module is imported by path, like
``test_backfill_model_catalog_v3_migration.py``, so the SQL under test is the
SQL that ships.
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select, text

from api.db.models import EmailTemplateModel

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "b7e3f1a2c9d4_add_template_authoring_fields.py"
)
_spec = importlib.util.spec_from_file_location(
    "add_template_authoring_fields", _MIGRATION_PATH
)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)

_CASES = {
    "plain-anchor": ('<p><a href="{{unsubscribe_link}}">Unsubscribe</a></p>', True),
    "spaced-anchor": ('<p><a href="{{ unsubscribe_link }}">Unsubscribe</a></p>', True),
    "single-quoted-anchor": (
        "<p><a href='{{unsubscribe_link}}'>Unsubscribe</a></p>",
        True,
    ),
    "anchor-with-other-attributes": (
        '<p><a class="x" href="{{unsubscribe_link}}" rel="noopener">Unsubscribe</a></p>',
        True,
    ),
    "plain-text": ("<p>{{unsubscribe_link}}</p>", False),
    "comment": ("<p>Hello</p><!-- {{unsubscribe_link}} -->", False),
    "title-attribute": ('<p title="{{unsubscribe_link}}">Hello</p>', False),
    "anchor-without-placeholder": ('<a href="https://boloagent.com">Home</a>', False),
    "mj-title": ("<mj-title>{{unsubscribe_link}}</mj-title>", False),
}


@pytest.mark.asyncio
async def test_backfill_flags_only_an_anchor_href_placeholder(db_session):
    user, _ = await db_session.get_or_create_user_by_provider_id(
        "template_backfill_user"
    )
    organization, _ = await db_session.get_or_create_organization_by_provider_id(
        "template_backfill_org", user.id
    )
    # Keep the id; expire_all() below would expire the ORM object itself.
    organization_id = organization.id

    async with db_session.async_session() as session:
        for name, (html_body, _expected) in _CASES.items():
            session.add(
                EmailTemplateModel(
                    organization_id=organization_id,
                    name=name,
                    # The subject mentions the placeholder in every case, so a
                    # subject-based rule would incorrectly flag all of them.
                    subject="Hi {{unsubscribe_link}}",
                    source_format="html",
                    source_body=html_body,
                    html_body=html_body,
                    has_unsubscribe_placeholder=False,
                )
            )
        await session.flush()

        await session.execute(text(migration._UNSUBSCRIBE_BACKFILL_SQL))
        session.expire_all()

        rows = dict(
            (
                await session.execute(
                    select(
                        EmailTemplateModel.name,
                        EmailTemplateModel.has_unsubscribe_placeholder,
                    ).where(EmailTemplateModel.organization_id == organization_id)
                )
            ).all()
        )

    assert rows == {name: expected for name, (_, expected) in _CASES.items()}
