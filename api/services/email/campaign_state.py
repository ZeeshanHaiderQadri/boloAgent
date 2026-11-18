"""Allowed status transitions for email campaigns and campaign messages.

The rules live here, in one place, so a route, a worker or an admin tool cannot
invent a shortcut. Both state sets are total: every known state is a key and
every reachable next state is listed.

Campaign states::

    draft -> ready -> launching -> sending -> completed
      ^        |          |           |
      +--------+          +-----------+--> failed / cancelled

``ready`` may return to ``draft`` so the operator can change the sender,
template or audience after a preview. ``launching`` is the freeze step; it
resolves the audience and creates recipient message rows. ``sending`` starts
when those rows are released to the worker.

Message states::

    held -> queued -> sending -> sent -> bounced / complained
                        |
                        +-> failed
    held / queued -> cancelled

Only the campaign launch step may move a row from ``held`` to ``queued``, and
it does so in the same transaction that ends the freeze and only after every
launch precondition passed. The send worker never claims ``held`` rows. It
claims ``queued`` rows and moves them through ``sending`` to ``sent`` or
``failed``; provider feedback later moves ``sent`` to ``bounced`` or
``complained``.

Deletion rules
--------------

* A user may delete a campaign only while it is ``draft`` or ``ready``. Once
  it is ``launching``, recipient rows exist and the campaign is the record of
  them; delete is refused. The database cascade from ``email_campaigns`` to
  ``email_messages`` therefore remains only for organization teardown, never
  as a user-facing delete path.
* A sender is "in use" while any campaign is ``draft``, ``ready``,
  ``launching`` or ``sending``. The sender delete endpoint must refuse with a
  conflict in that case. When every campaign referencing it is ``completed``,
  ``cancelled`` or ``failed``, the endpoint may null those ``sender_id``
  values and then delete the sender: the campaign keeps its snapshot columns,
  and the foreign key is ``NO ACTION`` so no other code path can delete a
  sender that is still referenced.
"""

from __future__ import annotations

from typing import Final, Mapping

from api.enums import EmailCampaignStatus, EmailMessageStatus

CAMPAIGN_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = {
    EmailCampaignStatus.DRAFT.value: frozenset(
        {EmailCampaignStatus.READY.value, EmailCampaignStatus.CANCELLED.value}
    ),
    EmailCampaignStatus.READY.value: frozenset(
        {
            EmailCampaignStatus.DRAFT.value,
            EmailCampaignStatus.LAUNCHING.value,
            EmailCampaignStatus.CANCELLED.value,
        }
    ),
    EmailCampaignStatus.LAUNCHING.value: frozenset(
        {
            EmailCampaignStatus.SENDING.value,
            EmailCampaignStatus.FAILED.value,
            EmailCampaignStatus.CANCELLED.value,
        }
    ),
    EmailCampaignStatus.SENDING.value: frozenset(
        {
            EmailCampaignStatus.COMPLETED.value,
            EmailCampaignStatus.FAILED.value,
            EmailCampaignStatus.CANCELLED.value,
        }
    ),
    EmailCampaignStatus.COMPLETED.value: frozenset(),
    EmailCampaignStatus.CANCELLED.value: frozenset(),
    EmailCampaignStatus.FAILED.value: frozenset(),
}

MESSAGE_TRANSITIONS: Final[Mapping[str, frozenset[str]]] = {
    EmailMessageStatus.HELD.value: frozenset(
        {EmailMessageStatus.QUEUED.value, EmailMessageStatus.CANCELLED.value}
    ),
    EmailMessageStatus.QUEUED.value: frozenset(
        {EmailMessageStatus.SENDING.value, EmailMessageStatus.CANCELLED.value}
    ),
    EmailMessageStatus.SENDING.value: frozenset(
        {EmailMessageStatus.SENT.value, EmailMessageStatus.FAILED.value}
    ),
    EmailMessageStatus.SENT.value: frozenset(
        {EmailMessageStatus.BOUNCED.value, EmailMessageStatus.COMPLAINED.value}
    ),
    EmailMessageStatus.FAILED.value: frozenset(),
    EmailMessageStatus.BOUNCED.value: frozenset(),
    EmailMessageStatus.COMPLAINED.value: frozenset(),
    EmailMessageStatus.CANCELLED.value: frozenset(),
}

# User-initiated deletion is limited to campaigns that have not frozen an
# audience yet. See the module docstring.
DELETABLE_CAMPAIGN_STATUSES: Final[frozenset[str]] = frozenset(
    {EmailCampaignStatus.DRAFT.value, EmailCampaignStatus.READY.value}
)

# A sender referenced by any of these campaigns must not be deleted (the
# endpoint answers 409). Terminal campaigns do not block deletion: the
# endpoint nulls their ``sender_id`` and keeps the snapshot columns.
SENDER_IN_USE_CAMPAIGN_STATUSES: Final[frozenset[str]] = frozenset(
    {
        EmailCampaignStatus.DRAFT.value,
        EmailCampaignStatus.READY.value,
        EmailCampaignStatus.LAUNCHING.value,
        EmailCampaignStatus.SENDING.value,
    }
)


class CampaignStateError(ValueError):
    """A transition that the state machine does not allow."""


def can_transition(
    transitions: Mapping[str, frozenset[str]], current: str, target: str
) -> bool:
    """Return whether ``current -> target`` is listed.

    An unknown state (a typo or a value from an older build) is a bug, not a
    denial, so it raises instead of quietly returning ``False``.
    """
    if current not in transitions:
        raise CampaignStateError(f"Unknown current state: {current!r}")
    if target not in transitions:
        raise CampaignStateError(f"Unknown target state: {target!r}")
    return target in transitions[current]


def assert_transition(
    transitions: Mapping[str, frozenset[str]],
    current: str,
    target: str,
    *,
    subject: str,
) -> None:
    """Raise :class:`CampaignStateError` unless the transition is allowed."""
    if not can_transition(transitions, current, target):
        raise CampaignStateError(
            f"Cannot move {subject} from {current!r} to {target!r}."
        )


def can_transition_campaign(current: str, target: str) -> bool:
    return can_transition(CAMPAIGN_TRANSITIONS, current, target)


def assert_campaign_transition(current: str, target: str) -> None:
    assert_transition(CAMPAIGN_TRANSITIONS, current, target, subject="the campaign")


def can_transition_message(current: str, target: str) -> bool:
    return can_transition(MESSAGE_TRANSITIONS, current, target)


def assert_message_transition(current: str, target: str) -> None:
    assert_transition(MESSAGE_TRANSITIONS, current, target, subject="the message")


def _assert_known_campaign_state(status: str) -> None:
    if status not in CAMPAIGN_TRANSITIONS:
        raise CampaignStateError(f"Unknown campaign state: {status!r}")


def can_delete_campaign(status: str) -> bool:
    """Whether a user may delete a campaign in this state.

    Only ``draft`` and ``ready`` may be deleted. An unknown state raises.
    """
    _assert_known_campaign_state(status)
    return status in DELETABLE_CAMPAIGN_STATUSES


def assert_campaign_deletable(status: str) -> None:
    """Raise :class:`CampaignStateError` unless a user may delete the campaign."""
    if not can_delete_campaign(status):
        raise CampaignStateError(
            f"Cannot delete a campaign in {status!r}; only draft or ready "
            "campaigns may be deleted."
        )


def campaign_blocks_sender_deletion(status: str) -> bool:
    """Whether a campaign in this state forbids deleting its sender.

    ``True`` while the campaign is draft, ready, launching or sending;
    ``False`` for terminal campaigns, whose sender may be detached. An unknown
    state raises.
    """
    _assert_known_campaign_state(status)
    return status in SENDER_IN_USE_CAMPAIGN_STATUSES
