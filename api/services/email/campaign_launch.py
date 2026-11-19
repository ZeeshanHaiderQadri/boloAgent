"""The single launch-precondition check for an email campaign.

Launch itself lives behind ``EMAIL_CAMPAIGN_SENDING_ENABLED`` and, in this
release, sends nothing and creates no recipient rows. The checks still live in
one place so the launch endpoint, the UI and the future sending engine can not
drift apart.

Every failure is reported at once, in plain language, so the operator can fix
all of them in one round trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from api.db.models import EmailCampaignModel, EmailSenderModel, EmailTemplateModel
from api.enums import EmailCampaignConsentPolicy, EmailSenderStatus
from api.services.email.audience import AudienceResolution
from api.services.email.templates import has_unsubscribe_placeholder

_LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


@dataclass(frozen=True)
class LaunchReadiness:
    failures: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.failures


def _public_base_url_problem(public_base_url: str | None) -> str | None:
    if not public_base_url:
        return (
            "PUBLIC_BASE_URL is not set. Unsubscribe links cannot be built, so "
            "launch is blocked."
        )
    parsed = urlparse(public_base_url)
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return "PUBLIC_BASE_URL must be an absolute http(s) URL, not a bare host name."
    if host in _LOCAL_HOSTNAMES or host.endswith(".localhost"):
        return (
            "PUBLIC_BASE_URL points at localhost. Recipients could not open the "
            "unsubscribe link, so launch is blocked."
        )
    return None


def check_launch_preconditions(
    *,
    campaign: EmailCampaignModel,
    sender: EmailSenderModel | None,
    template: EmailTemplateModel | None,
    resolution: AudienceResolution,
    public_base_url: str | None,
) -> LaunchReadiness:
    failures: list[str] = []

    if sender is None:
        failures.append("Choose an email sender before launching.")
    elif sender.status != EmailSenderStatus.VERIFIED.value:
        failures.append("The sender is not verified. Verify it before launching.")

    html = campaign.frozen_html_body or (template.html_body if template else "")
    if template is None:
        failures.append("Choose an email template before launching.")
    elif not template.has_unsubscribe_placeholder:
        failures.append(
            "The template has no unsubscribe link; marketing email must let "
            "people opt out."
        )
    elif not has_unsubscribe_placeholder(html):
        # The stored flag could be stale if the HTML was edited outside the
        # normal path; the sanitized HTML is authoritative.
        failures.append(
            "The template's unsubscribe link is missing from its sanitized "
            "HTML; save the template again before launching."
        )

    url_problem = _public_base_url_problem(public_base_url)
    if url_problem:
        failures.append(url_problem)

    if resolution.eligible_count < 1:
        failures.append(
            "There are no eligible recipients. Adjust the audience or consent "
            "policy first."
        )

    if (
        campaign.consent_policy == EmailCampaignConsentPolicy.INCLUDE_UNKNOWN.value
        and resolution.unknown_lead_count > 0
        and not (campaign.consent_override_reason or "").strip()
    ):
        failures.append(
            "Unknown-consent leads are included, but no written reason was recorded."
        )

    return LaunchReadiness(failures=failures)
