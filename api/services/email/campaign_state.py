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
