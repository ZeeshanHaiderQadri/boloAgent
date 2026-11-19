"""Campaign audience resolution: buckets, precedence, dedupe and masking."""

import pytest

from api.db.models import EmailSuppressionModel, LeadModel, LeadProjectModel
from api.enums import EmailSuppressionReason
from api.services.email.audience import (
    EXCLUSION_ORDER,
    MAX_EXPLICIT_LEAD_IDS,
    AudienceError,
    AudienceRule,
    mask_address,
    resolve_audience,
    validate_audience_rule,
)


async def _user_and_org(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    return user, org


async def _lead(
    session,
    org,
    key: str,
    *,
    email=None,
    consent="unknown",
    updated_at=None,
    project_id=None,
    tags=None,
    country=None,
):
    lead = LeadModel(
        organization_id=org.id,
        dedupe_key=key,
        title=f"Lead {key}",
        email=email,
        email_consent=consent,
        updated_at=updated_at,
        project_id=project_id,
        tags=tags or [],
        country=country,
    )
    session.add(lead)
    await session.flush()
    return lead


ALL_LEADS = AudienceRule(audience_type="leads", lead_ids=())


def _ids_rule(*ids: int) -> AudienceRule:
    return AudienceRule(audience_type="leads", lead_ids=tuple(ids))


@pytest.mark.asyncio
async def test_every_bucket_and_the_exclusion_order(db_session):
    _, org = await _user_and_org(db_session, "audience_buckets")

    async with db_session.async_session() as session:
        no_email = await _lead(session, org, "no-email", email=None)
        invalid = await _lead(session, org, "invalid", email="logo@2x.webp")
        suppressed = await _lead(
            session,
            org,
            "suppressed",
            email="suppressed@acme-plumbing.com",
            consent="opted_in",
        )
        opted_out = await _lead(
            session,
            org,
            "opted-out",
            email="out@acme-plumbing.com",
            consent="opted_out",
        )
        unknown = await _lead(
            session,
            org,
            "unknown",
            email="unknown@acme-plumbing.com",
            consent="unknown",
        )
        eligible = await _lead(
            session,
            org,
            "eligible",
            email="owner@acme-plumbing.com",
            consent="opted_in",
        )
        session.add(
            EmailSuppressionModel(
                organization_id=org.id,
                email="suppressed@acme-plumbing.com",
                reason=EmailSuppressionReason.MANUAL.value,
            )
        )
        await session.flush()
        ids = [
            lead.id
            for lead in (no_email, invalid, suppressed, opted_out, unknown, eligible)
        ]

    result = await resolve_audience(
        org.id, _ids_rule(*ids), consent_policy="opted_in_only"
    )

    assert result.recipient_count == 6
    assert result.excluded_no_email_count == 1
    assert result.excluded_invalid_email_count == 1
    assert result.excluded_suppressed_count == 1
    assert result.excluded_opted_out_count == 1
    assert result.excluded_unknown_count == 1
    assert result.eligible_count == 1
    assert result.duplicates_collapsed_count == 0
    assert result.unknown_lead_count == 1


@pytest.mark.asyncio
async def test_a_lead_that_fits_several_buckets_lands_in_the_first(db_session):
    """Suppressed beats opted-out: one lead, one bucket, in the fixed order."""
    _, org = await _user_and_org(db_session, "audience_precedence")

    async with db_session.async_session() as session:
        lead = await _lead(
            session,
            org,
            "both",
            email="both@acme-plumbing.com",
            consent="opted_out",
        )
        session.add(
            EmailSuppressionModel(
                organization_id=org.id,
                email="both@acme-plumbing.com",
                reason=EmailSuppressionReason.HARD_BOUNCE.value,
            )
        )
        await session.flush()
        lead_id = lead.id

    result = await resolve_audience(
        org.id, _ids_rule(lead_id), consent_policy="opted_in_only"
    )

    assert result.recipient_count == 1
    assert result.excluded_suppressed_count == 1
    assert result.excluded_opted_out_count == 0


@pytest.mark.asyncio
async def test_duplicate_addresses_collapse_to_one_recipient(db_session):
    _, org = await _user_and_org(db_session, "audience_duplicates")

    async with db_session.async_session() as session:
        first = await _lead(
            session,
            org,
            "dup-1",
            email="Owner@Acme-Plumbing.com",
            consent="opted_in",
        )
        second = await _lead(
            session,
            org,
            "dup-2",
            email=" owner@acme-plumbing.com ",
            consent="opted_in",
        )
        ids = [first.id, second.id]

    result = await resolve_audience(
        org.id, _ids_rule(*ids), consent_policy="opted_in_only"
    )

    assert result.recipient_count == 2
    assert result.eligible_count == 1
    assert result.duplicates_collapsed_count == 1
    assert all(
        "@" in address and address.count("*") == 3 for address in result.masked_sample
    )


@pytest.mark.asyncio
async def test_include_unknown_admits_unknown_leads_and_still_dedupes(db_session):
    _, org = await _user_and_org(db_session, "audience_include_unknown")

    async with db_session.async_session() as session:
        opted_in = await _lead(
            session,
            org,
            "known",
            email="owner@acme-plumbing.com",
            consent="opted_in",
        )
        unknown = await _lead(
            session,
            org,
            "unknown-same",
            email="Owner@Acme-Plumbing.com",
            consent="unknown",
        )
        ids = [opted_in.id, unknown.id]

    default = await resolve_audience(
        org.id, _ids_rule(*ids), consent_policy="opted_in_only"
    )
    assert default.eligible_count == 1
    assert default.excluded_unknown_count == 1

    override = await resolve_audience(
        org.id, _ids_rule(*ids), consent_policy="include_unknown"
    )
    assert override.eligible_count == 1
    assert override.duplicates_collapsed_count == 1
    assert override.excluded_unknown_count == 0
    assert override.unknown_lead_count == 1


@pytest.mark.asyncio
async def test_preview_never_returns_a_full_address(db_session):
    _, org = await _user_and_org(db_session, "audience_masking")

    async with db_session.async_session() as session:
        lead = await _lead(
            session,
            org,
            "mask",
            email="owner@acme-plumbing.com",
            consent="opted_in",
        )
        lead_id = lead.id

    result = await resolve_audience(
        org.id, _ids_rule(lead_id), consent_policy="opted_in_only"
    )

    assert result.masked_sample == ["o***@acme-plumbing.com"]
    assert "owner@" not in result.masked_sample[0]
    assert mask_address("owner@acme-plumbing.com") == "o***@acme-plumbing.com"


@pytest.mark.asyncio
async def test_explicit_lead_ids_are_capped(db_session):
    _, org = await _user_and_org(db_session, "audience_cap")
    too_many = tuple(range(1, MAX_EXPLICIT_LEAD_IDS + 2))

    with pytest.raises(AudienceError, match="at most"):
        await resolve_audience(
            org.id,
            AudienceRule(audience_type="leads", lead_ids=too_many),
            consent_policy="opted_in_only",
        )


@pytest.mark.asyncio
async def test_lead_ids_from_another_organization_are_rejected(db_session):
    _, org = await _user_and_org(db_session, "audience_cross_org_a")
    _, other_org = await _user_and_org(db_session, "audience_cross_org_b")

    async with db_session.async_session() as session:
        foreign = await _lead(
            session,
            other_org,
            "foreign",
            email="foreign@acme-plumbing.com",
            consent="opted_in",
        )
        lead_id = foreign.id

    with pytest.raises(AudienceError, match="do not belong to this organization"):
        await resolve_audience(
            org.id, _ids_rule(lead_id), consent_policy="opted_in_only"
        )


@pytest.mark.asyncio
async def test_project_tag_and_filter_rules_select_the_same_leads(db_session):
    _, org = await _user_and_org(db_session, "audience_targets")

    async with db_session.async_session() as session:
        project = LeadProjectModel(organization_id=org.id, name="Plumbers")
        session.add(project)
        await session.flush()
        in_project = await _lead(
            session,
            org,
            "project-lead",
            email="p@acme-plumbing.com",
            consent="opted_in",
            project_id=project.id,
            tags=["vip"],
            country="Pakistan",
        )
        tagged = await _lead(
            session,
            org,
            "tag-lead",
            email="t@acme-plumbing.com",
            consent="opted_in",
            tags=["vip"],
        )
        await _lead(
            session,
            org,
            "untagged",
            email="u@acme-plumbing.com",
            consent="opted_in",
            country="Pakistan",
        )
        project_id = project.id

    by_project = await resolve_audience(
        org.id,
        AudienceRule(audience_type="project", project_id=project_id),
        consent_policy="opted_in_only",
    )
    assert by_project.eligible_count == 1
    assert by_project.recipient_count == 1

    by_tag = await resolve_audience(
        org.id,
        AudienceRule(audience_type="tag", tag="vip"),
        consent_policy="opted_in_only",
    )
    assert by_tag.recipient_count == 2
    assert by_tag.eligible_count == 2

    by_filter = await resolve_audience(
        org.id,
        AudienceRule(
            audience_type="filter",
            filters={"country": "Pakistan", "all_matching": True},
        ),
        consent_policy="opted_in_only",
    )
    assert by_filter.recipient_count == 2
    assert by_filter.eligible_count == 2


@pytest.mark.asyncio
async def test_filter_rule_rejects_unknown_fields_and_page_only_mode(db_session):
    _, org = await _user_and_org(db_session, "audience_filter_errors")

    with pytest.raises(AudienceError, match="Unknown filter field"):
        await resolve_audience(
            org.id,
            AudienceRule(audience_type="filter", filters={"bogus": 1}),
            consent_policy="opted_in_only",
        )

    with pytest.raises(AudienceError, match="every lead matching"):
        await resolve_audience(
            org.id,
            AudienceRule(audience_type="filter", filters={"all_matching": False}),
            consent_policy="opted_in_only",
        )


@pytest.mark.asyncio
async def test_unknown_consent_policy_is_rejected(db_session):
    _, org = await _user_and_org(db_session, "audience_policy")

    with pytest.raises(AudienceError, match="Unknown consent policy"):
        await resolve_audience(
            org.id,
            AudienceRule(audience_type="leads", lead_ids=(1,)),
            consent_policy="opted_in_only-ish",
        )


@pytest.mark.asyncio
async def test_manually_excluded_lead_is_its_own_bucket(db_session):
    """A lead that passes every gate but is in excluded_lead_ids lands in the
    manually_excluded bucket, not eligible."""
    _, org = await _user_and_org(db_session, "audience_manual")

    async with db_session.async_session() as session:
        a = await _lead(
            session, org, "manual-a", email="a@acme-plumbing.com", consent="opted_in"
        )
        b = await _lead(
            session, org, "manual-b", email="b@acme-plumbing.com", consent="opted_in"
        )
        c = await _lead(
            session, org, "manual-c", email="c@acme-plumbing.com", consent="opted_in"
        )
        await session.commit()
        ids = [a.id, b.id, c.id]

    rule = AudienceRule(
        audience_type="leads",
        lead_ids=tuple(ids),
        excluded_lead_ids=(b.id,),
    )
    result = await resolve_audience(org.id, rule, consent_policy="opted_in_only")

    assert result.recipient_count == 3
    assert result.eligible_count == 2
    assert result.excluded_manual_count == 1
    assert result.duplicates_collapsed_count == 0


@pytest.mark.asyncio
async def test_manual_exclusion_order_after_unknown_consent(db_session):
    """An unknown-consent lead admitted by include_unknown is still manually
    excludable; the exclusion takes precedence over eligibility."""
    _, org = await _user_and_org(db_session, "audience_manual_unknown")

    async with db_session.async_session() as session:
        eligible = await _lead(
            session, org, "mu-elig", email="e@acme-plumbing.com", consent="opted_in"
        )
        unknown = await _lead(
            session, org, "mu-unknown", email="u@acme-plumbing.com", consent="unknown"
        )
        await session.commit()
        ids = [eligible.id, unknown.id]

    rule = AudienceRule(
        audience_type="leads",
        lead_ids=tuple(ids),
        excluded_lead_ids=(unknown.id,),
    )
    result = await resolve_audience(org.id, rule, consent_policy="include_unknown")

    assert result.eligible_count == 1
    assert result.excluded_manual_count == 1
    assert result.unknown_lead_count == 1
    assert result.excluded_unknown_count == 0


def test_exclusion_order_includes_manually_excluded():
    """The documented EXCLUSION_ORDER has manually_excluded between unknown and eligible."""
    assert (
        EXCLUSION_ORDER.index("manually_excluded")
        == EXCLUSION_ORDER.index("unknown") + 1
    )
    assert (
        EXCLUSION_ORDER.index("manually_excluded")
        == EXCLUSION_ORDER.index("eligible") - 1
    )


def test_manual_exclusion_cap():
    """Excluded lead ids are capped at MAX_MANUAL_EXCLUSIONS."""
    from api.services.email.audience import MAX_MANUAL_EXCLUSIONS

    rule = AudienceRule(
        audience_type="leads",
        lead_ids=(1,),
        excluded_lead_ids=tuple(range(MAX_MANUAL_EXCLUSIONS + 1)),
    )
    with pytest.raises(AudienceError, match="manually excluded"):
        validate_audience_rule(rule)
