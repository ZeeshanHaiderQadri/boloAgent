"""Campaign and message status transitions are enforced in one place."""

import pytest

from api.enums import EmailCampaignStatus, EmailMessageStatus
from api.services.email.campaign_state import (
    CAMPAIGN_TRANSITIONS,
    MESSAGE_TRANSITIONS,
    CampaignStateError,
    assert_campaign_transition,
    assert_message_transition,
    can_transition_campaign,
    can_transition_message,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("draft", "ready"),
        ("draft", "cancelled"),
        ("ready", "draft"),
        ("ready", "launching"),
        ("ready", "cancelled"),
        ("launching", "sending"),
        ("launching", "failed"),
        ("launching", "cancelled"),
        ("sending", "completed"),
        ("sending", "failed"),
        ("sending", "cancelled"),
    ],
)
def test_allowed_campaign_transitions(current, target):
    assert can_transition_campaign(current, target)
    assert_campaign_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("draft", "sending"),
        ("draft", "launching"),
        ("ready", "sending"),
        ("launching", "completed"),
        ("completed", "sending"),
        ("cancelled", "draft"),
        ("failed", "draft"),
    ],
)
def test_denied_campaign_transitions(current, target):
    assert not can_transition_campaign(current, target)
    with pytest.raises(CampaignStateError, match="campaign"):
        assert_campaign_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("held", "queued"),
        ("held", "cancelled"),
        ("queued", "sending"),
        ("queued", "cancelled"),
        ("sending", "sent"),
        ("sending", "failed"),
        ("sent", "bounced"),
        ("sent", "complained"),
    ],
)
def test_allowed_message_transitions(current, target):
    assert can_transition_message(current, target)
    assert_message_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("held", "sending"),
        ("held", "sent"),
        ("queued", "held"),
        ("queued", "sent"),
        ("sent", "sending"),
        ("failed", "queued"),
        ("cancelled", "queued"),
        ("bounced", "sent"),
    ],
)
def test_denied_message_transitions(current, target):
    assert not can_transition_message(current, target)
    with pytest.raises(CampaignStateError, match="message"):
        assert_message_transition(current, target)


def test_transition_maps_cover_every_enum_value():
    assert set(CAMPAIGN_TRANSITIONS) == {s.value for s in EmailCampaignStatus}
    assert set(MESSAGE_TRANSITIONS) == {s.value for s in EmailMessageStatus}
    for allowed in CAMPAIGN_TRANSITIONS.values():
        assert allowed <= set(CAMPAIGN_TRANSITIONS)
    for allowed in MESSAGE_TRANSITIONS.values():
        assert allowed <= set(MESSAGE_TRANSITIONS)


def test_held_is_reserved_for_the_launch_step():
    # Only the launch step releases held rows; the worker claims queued rows.
    assert can_transition_message("held", "queued")
    assert not can_transition_message("queued", "held")


def test_unknown_states_are_a_bug_not_a_denial():
    with pytest.raises(CampaignStateError, match="Unknown current state"):
        can_transition_campaign("paused", "ready")
    with pytest.raises(CampaignStateError, match="Unknown target state"):
        can_transition_message("queued", "delivered")
