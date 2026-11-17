from api.services.leads.call_outcomes import call_outcome, next_crm_status


def test_call_outcome_exposes_disposition_tags_and_conversation_notes():
    outcome = call_outcome(
        {
            "gathered_context": {
                "call_status": "user_hangup",
                "call_disposition": "completed",
                "mapped_call_disposition": "XFER",
                "call_tags": ["pricing", "callback"],
                "extracted_variables": {
                    "budget": "5000",
                    "callback_time": "Monday morning",
                    "empty": "",
                },
            }
        }
    )

    assert outcome["mapped_call_disposition"] == "XFER"
    assert outcome["call_status"] == "user_hangup"
    assert outcome["call_tags"] == ["pricing", "callback"]
    assert outcome["summary"] == (
        "Budget: 5000 · Callback time: Monday morning"
    )


def test_call_outcome_without_notes_uses_tags_then_none():
    tagged = call_outcome({"gathered_context": {"call_tags": ["voicemail"]}})
    assert tagged["summary"] == "voicemail"
    assert call_outcome({"gathered_context": {}})["summary"] is None


def test_next_crm_status_only_moves_new_leads_and_honours_opt_outs():
    assert next_crm_status("NO_ANSWER", "new") == "contacted"
    assert next_crm_status("NO_ANSWER", "interested") == "interested"
    assert next_crm_status("DNC", "interested") == "do_not_call"
    assert next_crm_status(None, None) == "contacted"
