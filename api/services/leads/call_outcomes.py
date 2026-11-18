"""Readable call outcomes for a lead.

Completed calls store their result in ``workflow_runs.gathered_context``.
The CRM needs a compact, human-readable version: which disposition the agent
reached, any tags the workflow applied, and the variables it collected during
the conversation.
"""

from __future__ import annotations

from typing import Any

_DNC_DISPOSITIONS = {
    "DNC",
    "DO_NOT_CALL",
    "DO NOT CALL",
    "OPT_OUT",
    "OPTOUT",
}


def _pretty_key(key: str) -> str:
    return str(key).replace("_", " ").strip().capitalize()


def _labels(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def call_outcome(call: dict[str, Any]) -> dict[str, Any]:
    """Normalize stored call data into fields the CRM can render.

    Accepts either the raw database row (with ``gathered_context``) or the
    flattened call dictionary returned by ``list_lead_calls``.
    """
    gathered = call.get("gathered_context")
    if not isinstance(gathered, dict):
        gathered = call

    tags = _labels(gathered.get("call_tags"))
    extracted = gathered.get("extracted_variables")
    if not isinstance(extracted, dict):
        extracted = {}

    notes: list[str] = []
    for key, value in extracted.items():
        if value in (None, "", [], {}):
            continue
        text = (
            ", ".join(str(item) for item in value)
            if isinstance(value, (list, tuple))
            else str(value)
        )
        notes.append(f"{_pretty_key(key)}: {text}")
        if len(notes) >= 6:
            break

    if notes:
        summary = " · ".join(notes)
    elif tags:
        summary = ", ".join(tags)
    else:
        summary = None

    return {
        "call_status": gathered.get("call_status"),
        "call_disposition": gathered.get("call_disposition"),
        "mapped_call_disposition": (
            gathered.get("mapped_call_disposition") or gathered.get("call_disposition")
        ),
        "call_tags": tags,
        "summary": summary,
    }


def next_crm_status(
    mapped_disposition: str | None,
    current_status: str | None,
) -> str:
    """Advance a lead's pipeline status after a completed call.

    Only the conservative transitions are automatic: opt-outs are honoured
    immediately and untouched leads become "contacted". A status the operator
    already set (interested, won, …) is never overwritten.
    """
    normalized = (mapped_disposition or "").strip().upper()
    if normalized in _DNC_DISPOSITIONS:
        return "do_not_call"
    if not current_status or current_status == "new":
        return "contacted"
    return current_status
