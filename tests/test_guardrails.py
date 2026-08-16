from ai.guardrails import route_message


def test_route_message_recurring_phrase_is_finance():
    decision = route_message("Add Netflix 19.99 monthly on 7th using Amex Gold")
    assert decision.mode == "finance_agent"
    assert decision.category == "finance"


def test_route_message_generic_out_of_scope_declines():
    decision = route_message("write me a poem about mountains")
    assert decision.mode == "decline"
    assert decision.category in ("out_of_scope", "sensitive_out_of_scope")
